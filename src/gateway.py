import os
import uuid
import pika
import json
import time
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
from kafka import KafkaProducer

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

from src.build_index import classify

app = FastAPI()

RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")
DEEP_CHECK_QUEUE = "sentinel.deep_check"
DEEP_CHECK_TIMEOUT_SEC = 10

print("Loading DeBERTa Tokenizer and ONNX Model...")
tokenizer = AutoTokenizer.frompretrained("microsoft/deberta-v3-small") if False else None
# Lazy load properly for global to avoid crash if called before init
tokenizer = AutoTokenizer.from_pretrained("microsoft/deberta-v3-small")
session = ort.InferenceSession("models/sentinel-classifier-quantized.onnx")

def on_kafka_error(exc):
    print(f"[!] Kafka Producer Error: {exc}", flush=True)

producer = KafkaProducer(
    bootstrap_servers=[os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")],
    value_serializer=lambda v: json.dumps(v).encode('utf-8')
)

class CheckRequest(BaseModel):
    text: str

def run_classifier(text: str):
    inputs = tokenizer(text, return_tensors="np", truncation=True, padding=True, max_length=512)
    ort_inputs = {
        "input_ids": inputs["input_ids"].astype(np.int64),
        "attention_mask": inputs["attention_mask"].astype(np.int64)
    }
    logits = session.run(None, ort_inputs)[0]
    exp_logits = np.exp(logits - np.max(logits, axis=1, keepdims=True))
    probs = exp_logits / np.sum(exp_logits, axis=1, keepdims=True)
    malicious_score = float(probs[0][1])
    verdict = "BLOCK" if malicious_score > 0.5 else "PASS"
    return verdict, malicious_score

@app.post("/check")
def check_prompt(req: CheckRequest) -> Dict[str, Any]:
    result = classify(req.text)
    phase1_score = result["score"]
    phase1_tier = result["tier"]
    
    classifier_verdict, classifier_confidence = run_classifier(req.text)
    
    judge_category = None
    judge_confidence = None
    final_decision = None
    decision_reason = None
    
    if phase1_tier == "BLOCK":
        final_decision = "block"
        decision_reason = "known_attack_match"
    elif phase1_tier == "FORWARD":
        if classifier_verdict == "BLOCK":
            # Classifier flags this as suspicious, but has not been
            # validated enough in production to grant it unilateral
            # block authority — escalate to the judge for confirmation,
            # same as Redis's own QUEUE tier does.
            phase1_tier = "QUEUE"
            final_decision = None
            decision_reason = "classifier_flagged_pending_judge"
            # Context will be passed into the RabbitMQ message payload
        else:
            final_decision = "allow"
            decision_reason = "classifier_allow"
            
    import re
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', req.text.strip()) if s.strip()]
    if (len(req.text) > 300 or len(sentences) >= 3) and final_decision == "allow":
        phase1_tier = "QUEUE"
        final_decision = None 
        
    if phase1_tier == "QUEUE" and final_decision is None:
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
            channel = connection.channel()
            reply_queue = channel.queue_declare(queue='', exclusive=True)
            callback_queue = reply_queue.method.queue
            
            correlation_id = str(uuid.uuid4())
            message = {
                "text": req.text,
                "score": phase1_score,
                "matched_source": result.get("matched_source", ""),
                "classifier_verdict": classifier_verdict,
                "classifier_confidence": float(classifier_confidence)
            }
            
            channel.basic_publish(
                exchange='',
                routing_key=DEEP_CHECK_QUEUE,
                properties=pika.BasicProperties(
                    reply_to=callback_queue,
                    correlation_id=correlation_id,
                ),
                body=json.dumps(message)
            )
            
            decision_resp = None
            for method, properties, body in channel.consume(queue=callback_queue, auto_ack=False, inactivity_timeout=DEEP_CHECK_TIMEOUT_SEC):
                if method is None:
                    break
                if properties.correlation_id == correlation_id:
                    decision_resp = json.loads(body)
                    channel.basic_ack(delivery_tag=method.delivery_tag)
                    break
            connection.close()
            
            if decision_resp:
                final_decision = decision_resp["decision"]
                decision_reason = decision_resp["reason"]
                judge_confidence = decision_resp.get("confidence")
                if decision_reason.startswith("llm_judge_"):
                    judge_category = decision_reason.replace("llm_judge_", "")
                else:
                    judge_category = decision_reason
            else:
                final_decision = "block"
                decision_reason = "judge_timeout_fallback"
                
        except Exception as e:
            print("Judge connection error:", e)
            final_decision = "block"
            decision_reason = "judge_timeout_fallback"

    request_id = str(uuid.uuid4())
    log_record = {
        "request_id": request_id,
        "request_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_text": req.text,
        "phase1_score": phase1_score,
        "phase1_tier": phase1_tier,
        "classifier_verdict": classifier_verdict,
        "classifier_confidence": float(classifier_confidence),
        "judge_category": judge_category,
        "judge_confidence": float(judge_confidence) if judge_confidence is not None else None,
        "final_decision": final_decision,
        "disagreement_flag": True if (classifier_verdict == "BLOCK" and float(classifier_confidence) > 0.99 and final_decision == "allow") else False
    }
    
    producer.send("sentinel.requests", log_record).add_errback(on_kafka_error)
    
    return {
        "decision": final_decision,
        "reason": decision_reason,
        "phase1_score": phase1_score,
        "phase1_tier": phase1_tier,
        "classifier_verdict": classifier_verdict,
        "classifier_score": float(classifier_confidence),
        "request_id": request_id
    }

# Sentinel Phase 3: Inline Classifier Architecture

## 1. Structural Flow Changes
Currently, the gateway relies on a two-step routing heuristic: **Similarity Check -> Length Override -> (Maybe) Deep Check**. 

With the introduction of a fine-tuned local classifier, the architecture will run the local classifier **ALONGSIDE** the existing Redis similarity index as a third, independent signal, rather than replacing it.

**New Request Flow:**
1. **Parallel Execution**: Upon receiving a prompt, the gateway queries the Redis Vector Index AND runs the local classifier simultaneously.
2. **Short-Circuit BLOCK**: If Redis returns a score `>= 0.85` (exact/obfuscated match), it blocks immediately. Redis is exceptionally good and cheap at catching known, noisy attacks (e.g., leetspeak, Base64).
3. **Classifier Override**: If Redis scores the prompt as `FORWARD`, but the local classifier predicts `malicious`, the prompt is immediately forced into the `QUEUE` for the LLM judge (or blocked, depending on confidence).
4. **Length Override**: The existing >300 char / 3+ sentence rule remains as a fallback safety net for narrative dilution.

**Reasoning:** Keeping Redis costs practically nothing (single-digit ms) and guarantees 100% recall on previously seen attacks. The local classifier adds semantic generalization to catch concise, novel attacks (the Batch B blindspot) that evade geometric similarity. 

## 2. Latency Budget in the Hot Path
Since this classifier will intercept 100% of traffic, it must sit in the synchronous hot-path without degrading API response times.

* **Estimated Latency Budget:** 15–30 ms per request.
* **Hardware Feasibility:** A quantized, small-parameter transformer (like DistilBERT or DeBERTa-v3-small, ~60M-80M parameters) running on a modern CPU via ONNX Runtime perfectly fits this budget. 
* **Conclusion:** CPU inference is entirely realistic for this workload. Serving it via GPUs would introduce unnecessary infrastructure complexity and cost, unless traffic throughput scales to tens of thousands of requests per second.

## 3. Training Data Strategy
To train the local classifier, we have the following labeled sources:
1. **`merged_dataset.csv`**: 15,452 rows of baseline open-source attacks and benign queries.
2. **Augmented Set**: 13,486 rows of obfuscated variants (Base64, Leetspeak, etc.).
3. **`judge_log.jsonl`**: The ongoing pipeline log containing organic `QUEUE` traffic and manual stress tests.

**The Data Deficit:**
Our manual stress testing revealed a massive blindspot: concise, narratively disguised, and non-command-style attacks (Batch B). Currently, `judge_log.jsonl` contains exactly **10 rows** of this specific failure mode. This is absolutely insufficient. 

**Proposal:** Before fine-tuning, we must synthetically generate or hand-curate a minimum of **2,000 to 5,000** new, high-quality examples of concise, novel-phrased attacks, balanced with structurally similar benign queries. 

## 4. Risks & Failure Modes
**The "Inherited Blind Spot" Risk:**
The single biggest risk in Phase 3 is that the local classifier inherits the exact same blind spots as Phase 1. 

If we fine-tune a model predominantly on the 15k rows from `merged_dataset.csv`, it will learn to flag explicit command-style phrasing (e.g., *"Ignore previous instructions"*). However, because the training distribution lacks novel framing (e.g., *"Translate this Elvish text"*, *"Write a script where a hacker..."*), the model will likely classify them as benign. 

Model distillation alone does not magically create semantic reasoning that isn't present in the dataset. If the fine-tuning data does not specifically target the Batch B generalization problem, the inline classifier will fail in the exact same way the Redis similarity index did.

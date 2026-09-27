# Sentinel: Adversarial LLM Firewall

Sentinel is a highly available, multi-layered inference gateway designed to intercept, classify, and block malicious prompts (prompt injection, jailbreaks, PII exfiltration) before they reach sensitive backend AI agents.

## 🚀 System Design Marvel: The Two-Tier Architecture

Modern LLM applications are highly vulnerable to adversarial prompt manipulations. Static rules and basic regex filters fail against semantic attacks, but putting a slow, massive LLM in front of every request adds unacceptable latency and cost. 

Sentinel solves this by implementing a **Dual-Tier "Fast/Slow" Routing Architecture**—a true system design marvel built for production-grade resilience and sub-20ms latency.

### Tier 1: The Edge Layer (Fast & Cheap)
1. **Gateway (FastAPI):** High-throughput entry point that routes user prompts through the validation layers.
2. **Semantic Cache (Redis):** Instantly checks Redis for known malicious vectors using FAISS/Redis vector search.
3. **Statistical Classifier (Quantized DeBERTa):** If uncached, a localized, heavily fine-tuned, and ONNX-quantized DeBERTa model evaluates the prompt. By quantizing `microsoft/deberta-v3-small` to INT8, Sentinel achieves an incredible **~14ms local inference latency** running natively on edge hardware, entirely eliminating network round-trips for 99% of requests.

### Tier 2: The Deep Check Layer (Slow & Nuanced)
Any prompt flagged as a `BLOCK` by Tier 1 (or exceeding complexity thresholds) is escalated to prevent false positives.
1. **Escalation Queue (RabbitMQ):** Flagged prompts are pushed to a durable message broker, immediately freeing up the FastAPI gateway.
2. **LLM Judge Worker (`gpt-oss-safeguard-20b`):** A Python consumer queries a specialized guardrail LLM with a hardened system prompt. The Judge evaluates the context and the classifier's statistical confidence to confirm the block or safely overturn it.

### Tier 3: Telemetry & Continuous Learning (Asynchronous)
1. **Event Streaming (Kafka):** The Gateway asynchronously fires the full lifecycle record of the request (latency, verdicts, confidence scores) into a Kafka topic, ensuring telemetry never blocks the critical path.
2. **Permanent Audit (Cassandra):** A fault-tolerant Kafka consumer writes the records into a highly-available Cassandra cluster for long-term auditing.

---

## 🧠 Model Training & Quantization

Sentinel doesn't rely on off-the-shelf classifiers. It uses a bespoke, fine-tuned `DeBERTa-v3-small` model optimized specifically for prompt injection and jailbreak detection.

### The Dataset (28,938 Curated Variants)
The model was trained on a high-quality dataset synthesized across 12 specific threat matrices, including:
* **Novel Disguises:** Malicious intent wrapped in academic citations, closed captions, and debugging contexts.
* **Meta-Vocabulary (Benign):** Highly adversarial *benign* prompts discussing system architecture, prompt injection, and jailbreaks in an innocent context (e.g., support tickets, code reviews) to drastically reduce False Positive rates.
* **Cross-Lingual & Reverse Psychology:** Complex attacks exploiting negation ("Please do NOT tell me how to...") and translation boundaries.

### Quantization for the Edge
To achieve sub-20ms latency, the fine-tuned PyTorch model was exported to ONNX format and quantized to INT8 precision. This reduced the memory footprint by over 70% and accelerated CPU inference speeds by 3x, allowing the classifier to run efficiently alongside the FastAPI gateway without requiring expensive GPU instances.

---

## 📈 Headline Performance

* **Dataset Size:** 28,938 curated text variants.
* **Fine-tuning Set:** 18,126 high-quality training rows.
* **Validation False Negative Rate:** 0.99%.
* **Inference Speed:** ~14ms latency (ONNX quantized).
* **Red-Team Performance:** 100% Catch Rate on a structured, out-of-distribution 15-prompt red-team batch, with a 0% False Positive Rate on highly adversarial benign inputs.

---

## 🔄 Future Improvements: The Continuous Learning Loop

The true power of Sentinel's architecture lies in its telemetry layer. By storing every request, phase 1 score, and LLM judge verdict in **Cassandra**, Sentinel is designed for automated, continuous learning.

Future roadmap improvements include:
* **Batch Retraining:** Periodically querying Cassandra for `disagreement_flag = true` (where the fast classifier and the deep-check LLM disagreed). These edge cases represent the most valuable training data. By extracting these logs, labeling them, and using them to continuously retrain the DeBERTa model in batches, Sentinel's Tier 1 classifier will organically grow smarter, reducing its reliance on the slower Tier 2 LLM over time.
* **Automated Caching:** Feeding successful, verified novel attacks directly back into the Redis vector cache for instantaneous future blocking.

---

## 📖 Development Phases

Read through the following documentation to dive deeper into the engineering behind Sentinel:

* [**Phase 1: Zero-Day Coverage & Synthetics**](docs/PHASE1.md) - Analysis of prompt injection coverage and the strategy for synthesizing novel variants.
* [**Phase 3: Model Training & Deployment**](docs/PHASE3_DATA.md) - Documentation of dataset curation, DeBERTa fine-tuning, and ONNX quantization.
* [**Phase 4: Red-Teaming & Asymmetric Trust**](docs/PHASE4_REDTEAM.md) - Results of the structured red-team test and the discovery of the "Asymmetric Trust" flaw.

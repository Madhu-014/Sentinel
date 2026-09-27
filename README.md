# Sentinel

Sentinel is a highly available, multi-layered inference gateway designed to intercept, classify, and block malicious prompts (prompt injection, jailbreaks, PII exfiltration) before they reach sensitive backend AI agents.

## Project Overview

Modern LLM applications are vulnerable to a wide array of adversarial prompt manipulations. Sentinel sits in front of the core LLM inference engine and enforces a rigorous, high-speed, and contextually-aware safety boundary. 

By combining lightning-fast statistical classification with a secondary Deep Check LLM for nuanced edge cases, Sentinel achieves extreme precision without paralyzing the system with false positives.

### Headline Performance
* **Dataset Size:** 28,938 curated text variants across 12 specific threat matrices.
* **DeBERTa Fine-tuning Set:** 18,126 high-quality training rows.
* **Initial False Negative Rate:** 0.99% on the validation set.
* **Inference Speed:** ~14ms latency running ONNX models natively on Apple Silicon.
* **Red-Team Performance:** 100% Catch Rate on a structured, out-of-distribution red-team batch, with a 0% False Positive Rate on highly adversarial benign inputs.

## Architecture

Sentinel uses a dual-tier "fast/slow" routing architecture powered by robust open-source infrastructure:

1. **Gateway (FastAPI):** Receives the user prompt and routes it through the validation layers.
2. **Phase 1 Classifier (Redis & DeBERTa ONNX):** 
   - A fast semantic cache checks Redis for known malicious vectors.
   - If uncached, a localized, fine-tuned DeBERTa model evaluates the prompt in ~14ms.
   - Any prompt flagged as a `BLOCK` (or exceeding complexity thresholds) is escalated.
3. **Phase 2 Deep Check (RabbitMQ & LLM Judge):** 
   - Escalated prompts are queued into RabbitMQ.
   - A Python worker consumes the queue, querying an LLM Judge (`gpt-oss-safeguard-20b`) with a hardened system prompt.
   - The Judge evaluates the context (and the classifier's verdict) to confirm the block or overturn it as a false positive.
4. **Telemetry & Auditing (Kafka & Cassandra):**
   - The Gateway asynchronously fires the full lifecycle record of the request into a Kafka topic.
   - A fault-tolerant Kafka consumer writes the records into a highly-available Cassandra cluster for long-term auditing and continuous training.

## Development Phases

Read through the following documentation to understand the evolution and security philosophy of Sentinel:

* [**Phase 1: Zero-Day Coverage & Synthetics**](docs/PHASE1.md) - Analysis of standard prompt injection coverage, the limitations of static rules, and the strategy for synthesizing novel variants to train a custom model.
* [**Phase 3: Model Training & Deployment**](docs/PHASE3_DATA.md) - Documentation of the training dataset curation (the "Elvish Translation" and "Thriller Novel" edge cases), model fine-tuning, and ONNX conversion for 14ms local inference.
* [**Phase 4: Red-Teaming & Asymmetric Trust**](docs/PHASE4_REDTEAM.md) - Results of the final 15-prompt structured red-team test. Details the discovery of the "Asymmetric Trust" flaw where a literal LLM Judge overruled a correct statistical classifier, and how we durably fixed it using a `disagreement_flag` and prompt hardening.

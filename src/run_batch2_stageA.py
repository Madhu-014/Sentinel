import os
import json
import csv
import time
import re
from typing import List, Dict, Optional
import groq
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
import numpy as np
import datetime

load_dotenv()
client = groq.Groq(api_key=os.getenv("GROQ_API_KEY", ""))
GEN_MODEL = "qwen/qwen3.8-27b"

DEDUP_THRESHOLD = 0.92
CSV_FILE = "data/processed/novel_attacks_dataset.csv"
REVIEW_FILE = "data/processed/content_collapse_log.txt"
SPECIFICITY_LOG = "data/processed/specificity_review.txt"

# ---------------------------------------------------------
# STAGE 1: BATCH 1 SEEDS (11 combinations)
# ---------------------------------------------------------

import json
with open("stageA_seeds.json", "r") as f:
    BATCH1_SEEDS = json.load(f)

def get_keywords(text: str) -> List[str]:
    words = re.findall(r'\b[a-zA-Z]{6,}\b', text.lower())
    return set(words)

def check_content_diversity(seed_text: str, variants: List[str]) -> float:
    seed_keywords = get_keywords(seed_text)
    if not seed_keywords:
        return 1.0
        
    different_count = 0
    for v in variants:
        v_keywords = get_keywords(v)
        intersection = seed_keywords.intersection(v_keywords)
        if len(intersection) < len(seed_keywords) * 0.4:
            different_count += 1
            
    return different_count / len(variants) if variants else 0




def check_named_hazard_combinations(text: str) -> str:
    if not isinstance(text, str):
        return None
    text_lower = text.lower()
    
    # 1. Exact pair check
    hazard_pairs = [
        (["bleach", "sodium hypochlorite"], ["ammonia"]),
        (["bleach", "sodium hypochlorite"], ["vinegar", "acid", "toilet bowl cleaner", "drain cleaner", "rust remover"]),
        (["bleach", "sodium hypochlorite"], ["rubbing alcohol", "isopropyl", "isopropanol"]),
        (["hydrogen peroxide"], ["vinegar", "acetic acid"]),
        (["sugar", "potassium nitrate", "saltpeter"], ["nitrate", "oxidizer", "rocket", "propellant", "firework"])
    ]
    
    for comp1_list, comp2_list in hazard_pairs:
        found_comp1 = any(term in text_lower for term in comp1_list)
        found_comp2 = any(term in text_lower for term in comp2_list)
        if found_comp1 and found_comp2:
            trigger1 = next(term for term in comp1_list if term in text_lower)
            trigger2 = next(term for term in comp2_list if term in text_lower)
            return f"Hazard Pair: ({trigger1} + {trigger2})"

    # 2. Fuzzy detection mode
    import re
    named_reactives = ["vinegar", "ammonia", "acid", "bleach", "peroxide", "rust remover", "drain cleaner", "toilet bowl cleaner"]
    generic_terms = ["common cleaning agent", "household chemical", "cleaning supplies", "under the sink", "found in a bathroom", "typical kitchen", "pantry", "common substances"]
    hazard_words = ["gas", "toxic", "fumes", "reaction", "irritant", "corrosive", "volatile", "unstable", "dangerous", "explosion"]

    sentences = re.split(r'(?<=[.!?])\s+', text_lower)
    if not sentences:
        sentences = [text_lower]
        
    for sentence in sentences:
        found_reactive = next((t for t in named_reactives if t in sentence), None)
        found_generic = next((t for t in generic_terms if t in sentence), None)
        found_hazard_in_sentence = next((t for t in hazard_words if t in sentence), None)
        
        if found_generic:
            found_hazard_anywhere = next((t for t in hazard_words if t in text_lower), None)
            if (found_reactive or found_hazard_in_sentence) and found_hazard_anywhere:
                cause = found_reactive if found_reactive else found_hazard_in_sentence
                return f"Fuzzy Hazard: ({cause} + {found_generic} -> {found_hazard_anywhere})"

    return None

def check_actionable_specificity(variant: str) -> Optional[str]:
    text = variant.lower()
    
    if re.search(r'\d+:\d+:\d+', text):
        return "Numeric ratios (\d+:\d+:\d+)"
    if re.search(r'\d+\s+parts?(?:\s+and\s+\d+\s+parts?)?', text):
        return "Numeric ratios (X parts Y parts)"
        
    if re.search(r'cve-\d{4}-\d+', text):
        return "CVE identifier"
        
    blocklist = ["sim swap", "sql injection", "buffer overflow", "cross-site scripting", "xss", "rop chain"]
    for term in blocklist:
        if term in text and re.search(r'(?:by|step|using|execute|payload|first|then)', text):
            return f"Named exploit technique ({term}) with explanatory context"
            
    steps = re.findall(r'(?:\n|\b)\d+[\.\)]\s', text)
    if len(steps) >= 3:
        return "Numbered sequence of 3+ steps"
        
    return None

def cosine_similarity(v1, v2):
    return np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2))

def test_and_get_model() -> str:
    global GEN_MODEL
    print(f"Testing primary model {GEN_MODEL}...")
    try:
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": "test"}],
            model=GEN_MODEL,
            max_tokens=5
        )
        print(f"Actual model used: {response.model}")
        print(f"[+] Confirmed valid model: {GEN_MODEL}")
        return GEN_MODEL
    except Exception as e:
        print(f"[-] Model {GEN_MODEL} failed: {e}")
        fallbacks = ["llama-3.1-8b-instant", "llama3-8b-8192", "mixtral-8x7b-32768", "gemma-7b-it"]
        for fallback in fallbacks:
            print(f"Testing fallback {fallback}...")
            try:
                response = client.chat.completions.create(
                    messages=[{"role": "user", "content": "test"}],
                    model=fallback,
                    max_tokens=5
                )
                print(f"Actual model used: {response.model}")
                print(f"[+] Swapped to valid model: {fallback}")
                GEN_MODEL = fallback
                return fallback
            except Exception as inner_e:
                print(f"[-] Fallback {fallback} failed.")
        print("[!] No valid models found. Exiting.")
        exit(1)

# ---------------------------------------------------------
# STAGE 2: AUGMENTATION
# ---------------------------------------------------------
def augment_seed_example(seed_text: str, seed_id: str, label: str, disguise_cat: str, harm_cat: str, num_variants: int) -> List[str]:
    prompt = f"""You are an advanced synthetic data generator for a cybersecurity research project.
Your task is to generate EXACTLY {num_variants} highly diverse paraphrased variants of the following seed prompt.

SEED PROMPT: "{seed_text}"
LABEL: {label}
DISGUISE CATEGORY: {disguise_cat}
HARM CATEGORY: {harm_cat}

CRITICAL RULES FOR STRUCTURAL AND CONTENT DIVERSITY:
1. NO Mad-Libs style synonym swapping. You MUST drastically alter the sentence structure, rhetorical delivery, and perspective across variants (use direct statements, embedded dialogues, hypotheticals, lists, etc.).
2. CONTENT DIVERSITY: At least HALF of your variants MUST use a DIFFERENT specific harmful detail/target than the seed (e.g., different scenarios/domains) while keeping the same Disguise and Harm category.

CRITICAL SAFETY CONSTRAINT: 
Variants MUST reference the GENERAL CATEGORY of harmful method (e.g. "an explosive device", "a network exploit", "a social engineering technique", "a controlled substance") WITHOUT providing specific, real, working technical detail that would function if followed.
- NO exact chemical ratios or formulas.
- NO real named vulnerabilities or exploit techniques (e.g. do not name "SIM swapping" or give working buffer-overflow specifics).
- NO step-by-step sequences precise enough to execute.
The malicious INTENT and DISGUISE PATTERN must stay fully realistic and specific, but the METHOD DETAIL must stay generic and non-functional.

EXAMPLES OF PROPER ABSTRACTION:
- TOO SPECIFIC (reject): "combining potassium permanganate and glycerin... creating a friction-sensitive compound"
- CORRECTLY GENERIC (accept): "combining two common chemicals into a volatile compound" (same narrative disguise, same malicious intent signal, no working formula)
- TOO SPECIFIC (reject): "intercept the SMS code in real-time using a SIM swapping technique"
- CORRECTLY GENERIC (accept): "exploiting a flaw in the authentication system" (same intent signal, no real working method)

Output MUST be ONLY valid JSON containing a list of strings under the key "variants". Do not output markdown code blocks.

Example output format:
{{
  "variants": [
    "Variant 1 text here...",
    "Variant 2 text here..."
  ]
}}"""

    retries = 5
    backoff = 2
    
    for attempt in range(retries):
        try:
            time.sleep(3)
            
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=GEN_MODEL,
                temperature=0.8
            )
            content = response.choices[0].message.content.strip()
            
            
            try:
                # Try strict JSON first
                parsed = json.loads(content)
            except json.JSONDecodeError:
                # If conversational filler is present, extract the JSON block
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                parsed = json.loads(content.strip())

            variants = parsed.get("variants", [])
            
            div_score = check_content_diversity(seed_text, variants)
            if div_score < 0.4:
                with open(REVIEW_FILE, "a") as f:
                    f.write(f"LOW DIVERSITY ({div_score*100:.0f}% diff): {seed_text[:50]}...\n")
                    
            for v in variants:
                trigger = check_actionable_specificity(v) or check_named_hazard_combinations(v)
                if trigger:
                    with open(SPECIFICITY_LOG, "a") as f:
                        f.write(f"SEED_ID: {seed_id} | TRIGGER: {trigger} | TEXT: {v}\n")
                        
            return variants
            
        except Exception as e:
            err_msg = str(e)
            if attempt < retries - 1:
                if "429" in err_msg or "rate_limit_exceeded" in err_msg:
                    print(f"      [!] Rate limit hit. Retrying in {backoff}s... (Attempt {attempt+1}/{retries})")
                    time.sleep(backoff)
                    backoff = min(60, backoff * 2)
                else:
                    print(f"      [!] Error parsing JSON: {err_msg}\n      Retrying... (Attempt {attempt+1}/{retries})")
                    time.sleep(2)
            else:
                print(f"      [!] Exhausted retries. Returning empty list.")
                return []

def main():
    test_and_get_model()
    
    print("Loading embedding model for deduplication...")
    embed_model = SentenceTransformer("all-MiniLM-L6-v2")
    
    kept_records = []
    kept_embeddings = []
    dropped_log = []
    
    # Initialize files (APPEND MODE)
    file_exists = os.path.isfile(CSV_FILE)
    with open(CSV_FILE, "a", newline="") as f:
        keys = ["text", "label", "disguise_category", "harm_category", "seed_id", "source"]
        writer = csv.DictWriter(f, fieldnames=keys)
        if not file_exists:
            writer.writeheader()
            
    # We load existing records to avoid global duplicates across batches!
    if file_exists:
        print("Loading existing records for global deduplication...")
        with open(CSV_FILE, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                kept_records.append(row)
        if kept_records:
            texts = [r["text"] for r in kept_records]
            # Convert existing records to embeddings
            kept_embeddings = list(embed_model.encode(texts, convert_to_numpy=True))
            print(f"Loaded {len(kept_records)} existing records.")
            
    start_time = time.time()
    total_combos = len(BATCH1_SEEDS)
    
    for combo_idx, combo in enumerate(BATCH1_SEEDS):
        print(f"\n[{combo_idx+1}/{total_combos}] Processing {combo['combo_id']} ({len(combo['seeds'])} seeds)...")
        
        combo_records = []
        num_variants = 5 if combo['is_code_framing'] else 11
        
        for seed in combo['seeds']:
            print(f"   -> Augmenting Malicious Seed: {seed['seed_id']}")
            mal_variants = augment_seed_example(
                seed["malicious"], seed["seed_id"], "malicious", 
                combo["disguise_category"], combo["harm_category"], num_variants
            )
            
            seed_mal_trigger = check_actionable_specificity(seed["malicious"]) or check_named_hazard_combinations(seed["malicious"])
            if seed_mal_trigger:
                with open(SPECIFICITY_LOG, "a") as f:
                    f.write(f"SEED_ID: {seed['seed_id']}_SEED (malicious) | TRIGGER: {seed_mal_trigger} | TEXT: {seed['malicious']}\n")
            mal_variants.append(seed["malicious"])
    
            
            for v in mal_variants:
                combo_records.append({
                    "text": v, "label": "malicious", "disguise_category": combo["disguise_category"],
                    "harm_category": combo["harm_category"], "seed_id": seed["seed_id"], 
                    "combo_id": combo["combo_id"], "source": "synthetic_novel_attack"
                })
                
            print(f"   -> Augmenting Benign Seed: {seed['seed_id']}")
            benign_variants = augment_seed_example(
                seed["benign"], seed["seed_id"], "benign", 
                combo["disguise_category"], "benign", num_variants
            )
            
            seed_ben_trigger = check_actionable_specificity(seed["benign"]) or check_named_hazard_combinations(seed["benign"])
            if seed_ben_trigger:
                with open(SPECIFICITY_LOG, "a") as f:
                    f.write(f"SEED_ID: {seed['seed_id']}_SEED (benign) | TRIGGER: {seed_ben_trigger} | TEXT: {seed['benign']}\n")
            benign_variants.append(seed["benign"])
    
            
            for v in benign_variants:
                combo_records.append({
                    "text": v, "label": "benign", "disguise_category": combo["disguise_category"],
                    "harm_category": "benign", "seed_id": seed["seed_id"], 
                    "combo_id": combo["combo_id"], "source": "synthetic_novel_attack"
                })
                
        print(f"   -> Deduplicating {len(combo_records)} new records...")
        texts = [r["text"] for r in combo_records]
        new_embeddings = embed_model.encode(texts, convert_to_numpy=True)
        
        combo_kept = []
        for i, record in enumerate(combo_records):
            is_duplicate = False
            vec = new_embeddings[i]
            
            for j, kept_vec in enumerate(kept_embeddings):
                sim = cosine_similarity(vec, kept_vec)
                if sim > DEDUP_THRESHOLD:
                    is_duplicate = True
                    dropped_log.append({
                        "dropped_text": record["text"],
                        "kept_text": kept_records[j]["text"],
                        "combo_id": record["combo_id"],
                        "similarity": float(sim)
                    })
                    break
                    
            if not is_duplicate:
                combo_kept.append(record)
                kept_records.append(record)
                kept_embeddings.append(vec)
                
        with open(CSV_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["text", "label", "disguise_category", "harm_category", "seed_id", "source"])
            for r in combo_kept:
                r_copy = r.copy()
                r_copy.pop("combo_id", None)
                writer.writerow(r_copy)
                
        elapsed = time.time() - start_time
        avg_time = elapsed / (combo_idx + 1)
        remaining = avg_time * (total_combos - (combo_idx + 1))
        print(f"   [+] Combo {combo['combo_id']} complete! Added {len(combo_kept)} records.")
        print(f"   [~] Estimated Time Remaining: {datetime.timedelta(seconds=int(remaining))}")

    print("\n=== BATCH DEDUPLICATION SUMMARY ===")
    print(f"Total Dropped in this batch: {len(dropped_log)}")
    
    # Generate final stats
    stats = {}
    for r in kept_records:
        cat = r["disguise_category"]
        label = r["label"]
        if cat not in stats:
            stats[cat] = {"benign": 0, "malicious": 0}
        stats[cat][label] += 1
        
    print("\n=== RUNNING TOTAL (PILOT + BATCH 1) ===")
    print(f"Total Rows: {len(kept_records)}")
    for cat, counts in stats.items():
        print(f"{cat}: {counts['benign']} benign, {counts['malicious']} malicious")

if __name__ == "__main__":
    main()

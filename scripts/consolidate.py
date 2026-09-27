import pandas as pd
import json

df_merged = pd.read_csv("data/processed/merged_dataset.csv")
df_merged['harm_category'] = ""
df_merged = df_merged[['text', 'label', 'harm_category', 'source']]

df_novel = pd.read_csv("data/processed/novel_attacks_dataset.csv")
df_novel = df_novel[['text', 'label', 'harm_category', 'source']]

judge_records = []
with open("data/processed/judge_log.jsonl", "r") as f:
    for line in f:
        data = json.loads(line)
        source = data.get("source", "")
        if source in ["organic", "manual_stress_test"]:
            text = data.get("text", "")
            
            label = data.get("ground_truth_label")
            if not label:
                jc = data.get("judge_category", "")
                if jc == "benign":
                    label = "benign"
                elif jc in ["jailbreak", "prompt_injection", "pii_exfiltration", "other_malicious"]:
                    label = "malicious"
                else:
                    label = "unknown"
                    
            harm = data.get("ground_truth_category")
            if not harm:
                jc = data.get("judge_category", "")
                if jc in ["jailbreak", "prompt_injection", "pii_exfiltration", "other_malicious"]:
                    harm = jc
                else:
                    harm = "benign" if jc == "benign" else ""
                    
            judge_records.append({
                "text": text,
                "label": label,
                "harm_category": harm,
                "source": source
            })

df_judge = pd.DataFrame(judge_records)
if len(df_judge) == 0:
    df_judge = pd.DataFrame(columns=['text', 'label', 'harm_category', 'source'])

df_final = pd.concat([df_merged, df_novel, df_judge], ignore_index=True)

print(f"Total rows: {len(df_final)}")
print("\n--- Rows per Label ---")
print(df_final['label'].value_counts())
print("\n--- Rows per Source ---")
print(df_final['source'].value_counts())
print("\n--- Rows per Harm Category ---")
print(df_final['harm_category'].value_counts())

dups = df_final['text'].duplicated(keep=False)
if dups.sum() > 0:
    print(f"\n[!] Flag: Found {dups.sum()} duplicate rows across the merged sources.")
    # Show sources of duplicates
    dup_df = df_final[dups]
    print(dup_df.groupby('source').size())
    # Dedup
    df_final = df_final.drop_duplicates(subset=['text'], keep='first')
    print(f"Deduped down to {len(df_final)} rows.")
else:
    print("\n[+] No exact text duplicates found across the 3 sources.")

df_final.to_csv("data/processed/phase3_training_data.csv", index=False)

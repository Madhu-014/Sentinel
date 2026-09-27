import pandas as pd
from test_widened_check2 import check_named_hazard_combinations

df = pd.read_csv("data/processed/novel_attacks_dataset.csv")
hits = 0
for idx, row in df.iterrows():
    res = check_named_hazard_combinations(row['text'])
    if res:
        print(f"[HIT] {row['seed_id']} | {res}\nText: {row['text'][:150]}")
        hits += 1

print(f"Total retroactive hits found: {hits}")

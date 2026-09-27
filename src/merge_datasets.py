import os
import pandas as pd
from datasets import load_dataset
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def load_and_map_deepset():
    logging.info("Loading deepset/prompt-injections...")
    ds = load_dataset("deepset/prompt-injections", split="train")
    df = ds.to_pandas()
    df['label'] = df['label'].map({1: "malicious", 0: "benign"})
    df['source'] = "deepset/prompt-injections"
    df['original_category'] = ""
    return df[['text', 'label', 'source', 'original_category']]

def load_and_map_jackhhao():
    logging.info("Loading jackhhao/jailbreak-classification...")
    ds = load_dataset("jackhhao/jailbreak-classification", split="train")
    df = ds.to_pandas()
    # It has ['prompt', 'type']. Rename 'prompt' to 'text'
    df = df.rename(columns={'prompt': 'text'})
    df['label'] = df['type'].apply(lambda x: "malicious" if x == "jailbreak" else "benign")
    df['source'] = "jackhhao/jailbreak-classification"
    df['original_category'] = ""
    return df[['text', 'label', 'source', 'original_category']]

def load_and_map_neuralchemy():
    logging.info("Loading neuralchemy/Prompt-injection-dataset...")
    ds = load_dataset("neuralchemy/Prompt-injection-dataset", "full", split="train")
    df = ds.to_pandas()
    df['label'] = df['label'].map({1: "malicious", 0: "benign"})
    df['source'] = "neuralchemy/Prompt-injection-dataset"
    df['original_category'] = df['category'].fillna("")
    return df[['text', 'label', 'source', 'original_category']]

def load_and_map_rubend18():
    logging.info("Loading rubend18/ChatGPT-Jailbreak-Prompts...")
    ds = load_dataset("rubend18/ChatGPT-Jailbreak-Prompts", split="train")
    df = ds.to_pandas()
    
    initial_rows = len(df)
    df['Votes'] = pd.to_numeric(df['Votes'], errors='coerce').fillna(0)
    df = df[df['Votes'] > 0]
    dropped = initial_rows - len(df)
    logging.info(f"rubend18/ChatGPT-Jailbreak-Prompts: dropped {dropped} rows where Votes <= 0")
    
    df = df.rename(columns={'Prompt': 'text'})
    df['label'] = "malicious"
    df['source'] = "rubend18/ChatGPT-Jailbreak-Prompts"
    df['original_category'] = ""
    return df[['text', 'label', 'source', 'original_category']]

def load_and_map_alpaca():
    logging.info("Loading tatsu-lab/alpaca...")
    ds = load_dataset("tatsu-lab/alpaca", split="train")
    ds = ds.shuffle(seed=42).select(range(4000))
    df = ds.to_pandas()
    
    # Alpaca has 'instruction', 'input', 'output', 'text'. 
    # Drop the existing 'text' column to avoid collisions when renaming 'instruction'
    if 'text' in df.columns:
        df = df.drop(columns=['text'])
        
    df = df.rename(columns={'instruction': 'text'})
    df['label'] = "benign"
    df['source'] = "tatsu-lab/alpaca"
    df['original_category'] = ""
    return df[['text', 'label', 'source', 'original_category']]

def clean_dataset(df):
    logging.info("--- Cleaning Step Summaries ---")
    initial_rows = len(df)
    
    # 1. Drop if text is null
    df_clean = df.dropna(subset=['text'])
    logging.info(f"Dropped {initial_rows - len(df_clean)} rows due to null text.")
    
    # 2. String conversion and strip whitespace
    df_clean = df_clean.copy()
    df_clean['text'] = df_clean['text'].astype(str).str.strip()
    
    # 3. Drop if length < 4
    prev_len = len(df_clean)
    df_clean = df_clean[df_clean['text'].str.len() >= 4]
    logging.info(f"Dropped {prev_len - len(df_clean)} rows due to text length < 4.")
    
    # 4. Cap text length at 2000 chars
    df_clean['text'] = df_clean['text'].str.slice(0, 2000)
    
    # 5. Drop invalid labels
    prev_len = len(df_clean)
    valid_labels_mask = df_clean['label'].isin(["benign", "malicious"])
    dropped_invalid_labels = df_clean[~valid_labels_mask].groupby('source').size()
    df_clean = df_clean[valid_labels_mask]
    
    logging.info(f"Dropped {prev_len - len(df_clean)} rows due to invalid labels.")
    if not dropped_invalid_labels.empty:
        for source, count in dropped_invalid_labels.items():
            logging.info(f"  - {source}: {count} rows")
            
    # 6. Deduplicate on lowercased, stripped text
    prev_len = len(df_clean)
    df_clean['text_lower'] = df_clean['text'].str.lower()
    df_clean = df_clean.drop_duplicates(subset=['text_lower'], keep='first')
    df_clean = df_clean.drop(columns=['text_lower'])
    logging.info(f"Dropped {prev_len - len(df_clean)} rows due to exact text deduplication.")
    
    logging.info(f"Total rows dropped during all cleaning steps: {initial_rows - len(df_clean)}")
    return df_clean

def main():
    dfs = []
    try:
        dfs.append(load_and_map_deepset())
        dfs.append(load_and_map_jackhhao())
        dfs.append(load_and_map_neuralchemy())
        dfs.append(load_and_map_rubend18())
        dfs.append(load_and_map_alpaca())
    except Exception as e:
        logging.error(f"Error loading datasets: {e}")
        return

    logging.info("Merging datasets...")
    merged_df = pd.concat(dfs, ignore_index=True)
    
    logging.info("Cleaning dataset...")
    cleaned_df = clean_dataset(merged_df)
    
    output_path = os.path.join("data", "processed", "merged_dataset.csv")
    cleaned_df.to_csv(output_path, index=False)
    logging.info(f"Saved merged dataset to {output_path}")
    
    logging.info("===================================")
    logging.info("--- Final Summary ---")
    logging.info(f"Total rows: {len(cleaned_df)}")
    logging.info("--- Rows per label ---")
    for k, v in cleaned_df['label'].value_counts().items():
        logging.info(f"  {k}: {v}")
    logging.info("--- Rows per source ---")
    for k, v in cleaned_df['source'].value_counts().items():
        logging.info(f"  {k}: {v}")

if __name__ == "__main__":
    main()

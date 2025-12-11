import sys
import os
import pandas as pd
import numpy as np

# Add local repo to path
repo_path = os.path.join(os.getcwd(), 'defeatbeta-repo')
if os.path.exists(repo_path):
    sys.path.insert(0, repo_path)

try:
    from defeatbeta_api.data.ticker import Ticker
except ImportError:
    print("Error importing defeatbeta_api")
    sys.exit(1)

tickers = [
    "MOB", "RLLCF", "APD", "CRK", "UL", "MRVL", "HL", "ITT", "TAWNF", "BE", 
    "ALNY", "DG", "GLBE", "LIF", "INCY", "CLPBY", "MOD", "VFC", "DKS", "SHECF",
    "GOOG"
]

print(f"Checking {len(tickers)} tickers for transcript availability...")

for symbol in tickers:
    try:
        t = Ticker(symbol)
        try:
            transcripts = t.earning_call_transcripts()
            df = transcripts.get_transcripts_list()
        except AttributeError:
             print(f"MISSING: {symbol} (API Error/No Data)")
             continue
        
        found = False
        if df is not None:
             if isinstance(df, pd.DataFrame) and not df.empty:
                if 'report_date' in df.columns:
                    df['report_date'] = pd.to_datetime(df['report_date'])
                    df = df.sort_values(by='report_date', ascending=False)
                    latest = df.iloc[0]
                    
                    # Check for content
                    content = latest.get('transcripts')
                    
                    # Calculate length
                    full_text = ""
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict):
                                text_part = item.get('content', '')
                                if not text_part:
                                    text_part = " ".join([str(v) for k,v in item.items() if k not in ['paragraph_number', 'speaker']])
                                full_text += text_part + " "
                            else:
                                full_text += str(item) + " "
                    elif isinstance(content, str):
                        full_text = content
                    elif isinstance(content, np.ndarray): # Handle numpy array if it happens
                        for item in content.flatten():
                             if isinstance(item, dict):
                                text_part = item.get('content', '')
                                if not text_part:
                                     text_part = " ".join([str(v) for k,v in item.items() if k not in ['paragraph_number', 'speaker']])
                                full_text += text_part + " "
                             else:
                                 full_text += str(item) + " "
                    
                    text_len = len(full_text)
                    word_count = len(full_text.split())
                    
                    if text_len > 100: # Arbitrary threshold for "real" content
                        print(f"FOUND: {symbol} | Date: {latest['report_date'].date()} | Length: {text_len} chars ({word_count} words)")
                        found = True
                    else:
                         print(f"MISSING: {symbol} (Empty content)")
             else:
                 pass # Empty df
        
        if not found and not (df is not None and isinstance(df, pd.DataFrame) and not df.empty and len(full_text) > 100):
            print(f"MISSING: {symbol}")
            
    except Exception as e:
        print(f"ERROR: {symbol} - {e}")

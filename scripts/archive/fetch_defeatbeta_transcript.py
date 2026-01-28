import sys
import os
import pandas as pd
import numpy as np

# Add local repo to path to ensure we use the latest code
repo_path = os.path.join(os.getcwd(), 'defeatbeta-repo')
if os.path.exists(repo_path):
    sys.path.insert(0, repo_path)

try:
    from defeatbeta_api.data.ticker import Ticker
except ImportError as e:
    print(f"Error importing defeatbeta_api: {e}")
    sys.exit(1)

def main():
    ticker_symbol = 'GOOG'
    print(f"Fetching transcript for {ticker_symbol}...")

    try:
        ticker = Ticker(ticker_symbol)
        transcripts_obj = ticker.earning_call_transcripts()
        
        # Get dataframe of transcripts
        df = transcripts_obj.get_transcripts_list()
        
        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
            print("Rejection: No transcripts received.")
            return

        # Sort by report_date
        if 'report_date' in df.columns:
            df['report_date'] = pd.to_datetime(df['report_date'])
            df = df.sort_values(by='report_date', ascending=False)
            
        latest_row = df.iloc[0]
        report_date = latest_row.get('report_date', 'Unknown')
        
        # Extract content
        content_data = latest_row.get('transcripts')
        
        full_text = ""
        
        # Normalize content to string
        if isinstance(content_data, list):
            for item in content_data:
                if isinstance(item, dict):
                    # Try 'content' key, fallback to joining values
                    text_part = item.get('content', '')
                    if not text_part:
                        text_part = " ".join([str(v) for k,v in item.items() if k not in ['paragraph_number', 'speaker']])
                    full_text += text_part + " "
                else:
                    full_text += str(item) + " "
        elif isinstance(content_data, str):
            full_text = content_data
        elif isinstance(content_data, np.ndarray):
             for item in content_data.tolist():
                if isinstance(item, dict):
                    text_part = item.get('content', '')
                    if not text_part:
                         text_part = " ".join([str(v) for k,v in item.items() if k not in ['paragraph_number', 'speaker']])
                    full_text += text_part + " "
                else:
                    full_text += str(item) + " "

        if full_text.strip():
            print(f"Confirmation: Transcript found for date {report_date}")
            print("--- BEGIN TRANSCRIPT ---")
            print(full_text)
            print("--- END TRANSCRIPT ---")
        else:
            print("Rejection: Transcript entry found but content was empty.")

    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

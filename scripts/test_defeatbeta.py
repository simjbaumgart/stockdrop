import sys
import os
import pandas as pd

# Add local repo to path to ensure we use the latest code
repo_path = os.path.join(os.getcwd(), 'defeatbeta-repo')
if os.path.exists(repo_path):
    sys.path.insert(0, repo_path)
    print(f"Added {repo_path} to sys.path")

try:
    from defeatbeta_api.data.ticker import Ticker
    # Debug info
    import defeatbeta_api
    print(f"Imported defeatbeta_api from: {os.path.dirname(defeatbeta_api.__file__)}")
except ImportError as e:
    print(f"Error importing defeatbeta_api: {e}")
    print(f"sys.path: {sys.path}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

def main():
    ticker_symbol = 'GOOG'
    print(f"Attempting to fetch earnings call transcripts for {ticker_symbol}...")

    try:
        ticker = Ticker(ticker_symbol)
        print(f"Available attributes on Ticker: {dir(ticker)}")
        transcripts_obj = ticker.earning_call_transcripts()
        
        # The readme says: transcripts.get_transcripts_list() returns the dataframe
        df = transcripts_obj.get_transcripts_list()
        
        if df is None or (isinstance(df, pd.DataFrame) and df.empty):
            print("No transcripts received.")
            return

        print(f"Received {len(df)} transcripts.")
        
        # Sort by report_date
        if 'report_date' in df.columns:
            df['report_date'] = pd.to_datetime(df['report_date'])
            df = df.sort_values(by='report_date', ascending=False)
            latest_row = df.iloc[0]
        else:
            # Fallback if no date column, use the last one as per example structure usually implying chronological
            latest_row = df.iloc[-1]

        report_date = latest_row.get('report_date', 'Unknown')
        print(f"Latest transcript found from date: {report_date}")
        print("Successfully received a transcript.")

        # Analyze for forecast
        # The 'transcripts' column contains a list of dictionaries according to the README example
        # [{'paragraph_number': 1, 'speaker': '...' ...}]
        
        print(f"DEBUG: Transcripts object dir: {dir(transcripts_obj)}")
        
        import numpy as np
        
        # Filter for rows that actually have content in 'transcripts'
        if 'transcripts' in df.columns:
            # Helper to check if it has content
            def has_content(x):
                if isinstance(x, list):
                    return len(x) > 0
                if isinstance(x, np.ndarray):
                    return x.size > 0
                return False

            df_with_content = df[df['transcripts'].apply(has_content)]
            
            if not df_with_content.empty:
                # Sort descending by date
                if 'report_date' in df_with_content.columns:
                     df_with_content['report_date'] = pd.to_datetime(df_with_content['report_date'])
                     df_with_content = df_with_content.sort_values(by='report_date', ascending=False)
                latest_row = df_with_content.iloc[0]
                content_data = latest_row.get('transcripts')
                report_date = latest_row.get('report_date', 'Unknown')
                print(f"Latest dictionary with actual content found from date: {report_date}")
            else:
                print("No rows with non-empty 'transcripts' list found.")
                content_data = df.iloc[0].get('transcripts')
        else:
            print("Column 'transcripts' not found in dataframe columns: {df.columns}")
            return

        print(f"DEBUG: Type of content_data: {type(content_data)}")
        
        found_forecast = False
        forecast_snippet = ""
        
        full_text = ""
        
        # Normalize to list
        if isinstance(content_data, np.ndarray):
            content_data = content_data.tolist()
            
        if isinstance(content_data, list) and len(content_data) > 0:
            print(f"DEBUG: First transcript chunk: {content_data[0]}")
            # It's a list of dicts
            for item in content_data:
                # Use 'content' key as seen in debug output
                text_part = item.get('content', '')
                if not text_part:
                     # Fallback
                     text_part = " ".join([str(v) for k,v in item.items() if k not in ['paragraph_number', 'speaker']])
                full_text += text_part + " "
                # We don't know the exact key for the text. Example shows 'speaker' and 'paragraph_number'.
                # Usually it's 'content' or 'text' or 'value'.
                # I'll try to dump the whole dict values to text if keys are unknown, or guess 'text'
                # Let's inspect the first item keys if we were interactive, but here I'll try to be robust
                
                # Construct text from all values excluding metadata
                text_part = " ".join([str(v) for k,v in item.items() if k not in ['paragraph_number', 'speaker']])
                full_text += text_part + " "
        elif isinstance(content_data, str):
            full_text = content_data
            
        # Search for forecast/outlook
        keywords = ["forecast", "guidance", "outlook", "future expectation", "looking ahead"]
        for kw in keywords:
            if kw.lower() in full_text.lower():
                found_forecast = True
                # Try to extract a snippet around it
                idx = full_text.lower().find(kw.lower())
                start = max(0, idx - 50)
                end = min(len(full_text), idx + 150)
                forecast_snippet = full_text[start:end]
                break
        
        if found_forecast:
            print("\nPotential forecast information found:")
            print(f"...{forecast_snippet}...")
            print("\nThe forecast idea is interesting!")
        else:
            print("\nDid not explicitly find 'forecast' or 'outlook' keywords in the content.")
            
        # Also print a small part of the text to verify we got content
        print(f"\nTranscript sample start: {full_text[:200]}...")

    except Exception as e:
        print(f"An error occurred during execution: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

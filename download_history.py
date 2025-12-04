import yfinance as yf
import pathlib
import time

# Indices to download
INDICES = {
    "S&P 500": "^GSPC",
    "STOXX 600": "^STOXX",
    "China (CSI 300)": "000300.SS",
    "India (Nifty 50)": "^NSEI",
    "Australia (ASX 200)": "^AXJO"
}

def download_history():
    print("Starting historical data download...")
    
    # Create directory if it doesn't exist
    history_dir = pathlib.Path("data/history")
    history_dir.mkdir(parents=True, exist_ok=True)
    
    for name, ticker_symbol in INDICES.items():
        print(f"Downloading history for {name} ({ticker_symbol})...")
        try:
            ticker = yf.Ticker(ticker_symbol)
            # Download max history
            hist = ticker.history(period="max")
            
            if hist.empty:
                print(f"Warning: No data found for {name}")
                continue
                
            # Save to CSV
            safe_name = name.replace(" ", "_").replace("(", "").replace(")", "")
            file_path = history_dir / f"{safe_name}_history.csv"
            hist.to_csv(file_path)
            print(f"Saved to {file_path}")
            
            # Be nice to the API
            time.sleep(1)
            
        except Exception as e:
            print(f"Error downloading {name}: {e}")

    print("Download complete.")

if __name__ == "__main__":
    download_history()

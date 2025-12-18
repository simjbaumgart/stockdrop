import os
import sys
from app.services.stock_service import StockService

# Initialize Service
service = StockService()

def test_fetch_and_save(symbol):
    print(f"\n--- Testing for {symbol} ---")
    try:
        # Fetch Transcript (returns dict now)
        result = service.get_latest_transcript(symbol)
        
        text = result.get("text", "")
        date = result.get("date")
        is_outdated = result.get("is_outdated")
        warning = result.get("warning")
        
        print(f"Transcript Found: {'Yes' if text else 'No'}")
        print(f"Date: {date}")
        print(f"Outdated: {is_outdated}")
        print(f"Warning: {warning}")
        
        if text:
            # Save to file
            output_dir = "/Users/simonbaumgart/Antigravity/Stock-Tracker/data/filings"
            filename = os.path.join(output_dir, f"{symbol}_transcript.txt")
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"Symbol: {symbol}\n")
                f.write(f"Date: {date}\n")
                f.write(f"Outdated: {is_outdated}\n")
                f.write(f"Warning: {warning}\n")
                f.write("-" * 40 + "\n")
                f.write(text)
            
            print(f"Saved to {filename}")
        else:
            print("No transcript content to save.")
            
    except Exception as e:
        print(f"Error processing {symbol}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # Test a few tickers
    tickers = ["MSFT", "TSLA", "NVDA", "AAPL"]
    
    for t in tickers:
        test_fetch_and_save(t)

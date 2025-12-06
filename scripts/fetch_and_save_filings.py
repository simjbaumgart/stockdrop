import sys
import os
import re

# Add the parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.finnhub_service import finnhub_service

def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def save_filings(symbols, count=2, output_dir="data/filings"):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    for symbol in symbols:
        print(f"\n--- Processing {symbol} ---")
        filings = finnhub_service.get_filings(symbol, from_date="2024-01-01")
        
        if not filings:
            print(f"No filings found for {symbol}")
            continue
            
        # Filter for major reports first (10-K, 10-Q), then 8-K if needed to fill count
        # Or just take the latest non-insider-trading forms (Form 4).
        # Let's prioritize 10-K and 10-Q.
        
        target_forms = ['10-K', '10-Q', '8-K', '20-F', '40-F'] # 20-F/40-F for international/Canadian
        
        selected_filings = []
        for filing in filings:
            if filing.get('form') in target_forms:
                selected_filings.append(filing)
                if len(selected_filings) >= count:
                    break
        
        if not selected_filings:
            print(f"No 10-K/10-Q/8-K found for {symbol}. First 5 forms found: {[f.get('form') for f in filings[:5]]}")
            continue
            
        print(f"Found {len(selected_filings)} relevant filings.")
        
        for filing in selected_filings:
            form = filing.get('form')
            date = filing.get('filedDate', 'unknown_date').split(' ')[0] # Take just YYYY-MM-DD
            url = filing.get('reportUrl')
            
            print(f"   Fetching {form} ({date}) from {url}...")
            
            text = finnhub_service.extract_filing_text(url)
            
            if text:
                filename = f"{symbol}_{form}_{date}.txt"
                filename = sanitize_filename(filename)
                filepath = os.path.join(output_dir, filename)
                
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(text)
                print(f"   Saved to {filepath} ({len(text)} chars)")
            else:
                print("   Failed to extract text.")

def main():
    symbols = ["PSN", "DOCS", "XP"]
    save_filings(symbols, count=2)

if __name__ == "__main__":
    main()

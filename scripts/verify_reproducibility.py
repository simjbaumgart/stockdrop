import sys
import os
import json

# Add the parent directory to sys.path to allow importing app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.finnhub_service import finnhub_service

def main():
    # Test reproducibility with a different stock, e.g., AAPL
    symbol = "AAPL"
    print(f"--- Reproducibility Test: Fetching 10-Q text for {symbol} ---")
    
    # 1. Fetch Filings
    filings = finnhub_service.get_filings(symbol, from_date="2024-01-01")
    if not filings:
        print(f"No filings found for {symbol}.")
        return

    # 2. Find latest 10-Q or 10-K
    target_filing = None
    for filing in filings:
        if filing.get('form') in ['10-Q', '10-K']:
            target_filing = filing
            break
            
    if not target_filing:
        print(f"No 10-Q or 10-K found for {symbol}.")
        return

    print(f"Found {target_filing.get('form')} filed on {target_filing.get('filedDate')}")
    url = target_filing.get('reportUrl')
    print(f"URL: {url}")

    # 3. Extract Text using the service method
    text = finnhub_service.extract_filing_text(url)
    
    if text:
        print(f"\nSuccessfully extracted {len(text)} characters.")
        
        # 4. Check for key sections
        keywords = ["Management’s Discussion", "Risk Factors", "Financial Statements"]
        print("\n--- Content Verification ---")
        for keyword in keywords:
            # Check widely for variations like straight vs curly quotes if needed, 
            # but simple check first is good.
            if keyword in text or keyword.replace("’", "'") in text:
                 print(f"[OK] Found '{keyword}'")
            else:
                 print(f"[FAIL] Could not find '{keyword}'")
                 
        # Save verification sample
        output_path = os.path.join(os.path.dirname(__file__), f"sec_verification_{symbol}.txt")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text[:5000]) # First 5k chars
        print(f"\nSaved sample verification text to: {output_path}")
        
    else:
        print("Failed to extract text.")

if __name__ == "__main__":
    main()

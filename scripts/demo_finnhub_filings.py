import sys
import os
import json

# Add the parent directory to sys.path to allow importing app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.finnhub_service import finnhub_service

def main():
    symbol = "NVDA"
    print(f"Fetching filings for {symbol}...")
    
    filings = finnhub_service.get_filings(symbol, from_date="2024-01-01")
    
    if not filings:
        print("No filings found or error occurred.")
    else:
        print(f"Found {len(filings)} filings.")
        
        # Print the first filing to inspect structure
        if len(filings) > 0:
            print("\n--- Sample Filing Structure (First Item) ---")
            print(json.dumps(filings[0], indent=2))
            
        # Look for a 10-K or 10-Q report to see if they have different fields
        print("\n--- Searching for 10-K or 10-Q ---")
        for filing in filings:
            if filing.get('form') in ['10-K', '10-Q']:
                print(f"\nFound {filing.get('form')}:")
                # print(json.dumps(filing, indent=2))
                print(f"URL: {filing.get('reportUrl')}")
                break
                
        # Check for PDF URLs
        print("\n--- checking for PDF URLs ---")
        pdf_count = 0
        for filing in filings:
            url = filing.get('reportUrl', '')
            if url:
                 pdf_count += 1
        
        if pdf_count == 0:
            print("No 'reportUrl' found in filings.")
        else:
            print(f"Found {pdf_count} filings with 'reportUrl'.")

    # Check International Filings (e.g., Vodafone on LSE)
    print("\n--- Checking International Filings (GB) ---")
    try:
        # Note: 'international_filings' uses (symbol, country)
        intl_filings = finnhub_service.client.international_filings(symbol='VOD', country='GB')
        if intl_filings:
            print(f"Found {len(intl_filings)} international filings.")
            print(json.dumps(intl_filings[0], indent=2))
        else:
            print("No international filings found for VOD/GB.")
    except Exception as e:
        print(f"Error fetching international filings: {e}")

if __name__ == "__main__":
    main()

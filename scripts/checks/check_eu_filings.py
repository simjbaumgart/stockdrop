import sys
import os
import json

# Add the parent directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.services.finnhub_service import finnhub_service

def main():
    print("--- Testing EU Company Filings ---")

    # Case 1: EU Company listed in US (ADR)
    # SAP SE (SAP)
    symbol_us = "SAP"
    print(f"\n1. Testing US-listed EU Company: {symbol_us}")
    filings = finnhub_service.get_filings(symbol_us, from_date="2024-01-01")
    if filings:
        print(f"Found {len(filings)} filings for {symbol_us}.")
        # Check for 20-F (Foreign Private Issuer Annual Report)
        foreign_forms = [f for f in filings if f.get('form') in ['20-F', '6-K']]
        if foreign_forms:
             print(f"Found {len(foreign_forms)} Foreign Issuer forms (20-F/6-K).")
             print(f"Sample URL: {foreign_forms[0].get('reportUrl')}")
        else:
             print(f"No 20-F/6-K found. Forms: {list(set(f.get('form') for f in filings))}")
    else:
        print(f"No filings found for {symbol_us}.")

    # Case 2: EU Company on local exchange
    # SAP SE on XETRA (SAP.DE) - using international_filings
    symbol_eu = "SAP"
    country = "DE"
    print(f"\n2. Testing Local Exchange EU Company: {symbol_eu} ({country})")
    try:
        # Note: 'international_filings' uses (symbol, country)
        intl_filings = finnhub_service.client.international_filings(symbol=symbol_eu, country=country)
        if intl_filings:
            print(f"Found {len(intl_filings)} international filings.")
        else:
             print("No international filings found (empty list).")
    except Exception as e:
        print(f"Error fetching international filings: {e}")

if __name__ == "__main__":
    main()

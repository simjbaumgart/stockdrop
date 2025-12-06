import sys
import os
import time

# Add the parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.finnhub_service import finnhub_service

def main():
    # DAX 40 Companies wit US Tickers (ADR or Direct)
    dax_companies = {
        "Adidas": "ADDYY",
        "Airbus": "EADSY",
        "Allianz": "ALIZY",
        "BASF": "BASFY",
        "Bayer": "BAYRY",
        "Beiersdorf": "BDRFY",
        "BMW": "BMWYY",
        "Brenntag": "BNTGY",
        "Continental": "CTTAY",
        "Covestro": "COVTY",
        "Daimler Truck": "DTRUY",
        "Deutsche Bank": "DB",
        "Deutsche Boerse": "DBOEY",
        "Deutsche Post (DHL)": "DHLGY",
        "Deutsche Telekom": "DTEGY",
        "E.ON": "EONGY",
        "Fresenius": "FSNUY",
        "Hannover Re": "HVRRY",
        "Heidelberg Materials": "HDELY",
        "Henkel": "HENKY",
        "Infineon": "IFNNY",
        "Linde": "LIN",
        "Mercedes-Benz": "MBGYY",
        "Munich Re": "MURGY",
        "MTU Aero Engines": "MTUAY",
        "Porsche SE": "POAHY",
        "Puma": "PUMSY",
        "Qiagen": "QGEN",
        "RWE": "RWEOY",
        "SAP": "SAP",
        "Sartorius": "SOAGY",
        "Siemens": "SIEGY",
        "Siemens Healthineers": "SMMNY",
        "Symrise": "SYIEY",
        "Volkswagen": "VWAGY",
        "Vonovia": "VONOY",
        "Zalando": "ZLNDY"
    }

    print(f"Checking {len(dax_companies)} DAX companies for US filings reachability...")
    print("-" * 60)
    print(f"{'Company':<25} | {'Ticker':<8} | {'Status':<15} | {'Latest Form'}")
    print("-" * 60)

    reachable_count = 0
    target_forms = ['10-K', '10-Q', '20-F', '40-F', '6-K', '8-K']

    for name, ticker in dax_companies.items():
        try:
            # We specifically want Foreign Issuer reports (20-F, 6-K) or standard US ones if direct listed
            filings = finnhub_service.get_filings(ticker, from_date="2024-01-01")
            
            status = "No Filings"
            latest_form = "-"
            
            if filings:
                # Check if any relevant form exists
                relevant_filings = [f for f in filings if f.get('form') in target_forms]
                
                if relevant_filings:
                    status = "Reachable"
                    latest_form = relevant_filings[0].get('form')
                    reachable_count += 1
                else:
                    # Check what forms ARE there
                    forms = list(set(f.get('form') for f in filings)) # up to 5
                    status = "Irrelevant Forms"
                    latest_form = str(forms[:3])
            
            print(f"{name:<25} | {ticker:<8} | {status:<15} | {latest_form}")
            
            # Rate limit respect (30 calls/sec is generous, but let's be safe)
            time.sleep(0.1) 
            
        except Exception as e:
             print(f"{name:<25} | {ticker:<8} | Error: {str(e)}")

    print("-" * 60)
    print(f"Total Reachable (with significant filings): {reachable_count} / {len(dax_companies)}")
    print(f"Percentage: {reachable_count / len(dax_companies) * 100:.1f}%")

if __name__ == "__main__":
    main()

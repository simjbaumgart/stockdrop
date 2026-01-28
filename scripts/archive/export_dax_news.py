
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.benzinga_service import benzinga_service

def export_dax_news():
    print("Fetching DAX and EWG news...")
    
    results = {}
    
    # 1. DAX (Global X DAX Germany ETF)
    try:
        dax_news = benzinga_service.get_company_news("DAX")
        dax_news.sort(key=lambda x: x.get('datetime', 0), reverse=True)
        results["DAX"] = dax_news[:10]
        print(f"  -> Got {len(results['DAX'])} DAX items.")
    except Exception as e:
        print(f"  -> Error fetching DAX: {e}")
        
    # 2. EWG (iShares MSCI Germany ETF)
    try:
        ewg_news = benzinga_service.get_company_news("EWG")
        ewg_news.sort(key=lambda x: x.get('datetime', 0), reverse=True)
        results["EWG"] = ewg_news[:10]
        print(f"  -> Got {len(results['EWG'])} EWG items.")
    except Exception as e:
        print(f"  -> Error fetching EWG: {e}")
        
    output_dir = "experiment_data"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "dax_news.json")
    
    with open(output_file, "w") as f:
        json.dump(results, f, indent=4)
        
    print(f"Exported DAX news to {output_file}")

if __name__ == "__main__":
    export_dax_news()

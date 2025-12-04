import sys
import os
import json
from unittest.mock import MagicMock, patch
from dotenv import load_dotenv

# Load env vars
load_dotenv()

# Add app to path
sys.path.append(os.getcwd())

# Reset usage stats to allow simulation
if os.path.exists("usage_stats.json"):
    os.remove("usage_stats.json")

from app.services.stock_service import stock_service
from app.services.research_service import research_service

def simulate_research():
    print("--- Starting Simulation ---")
    
    # Target stocks
    test_data = [
        {"symbol": "NVDA", "name": "NVIDIA Corporation"},
        {"symbol": "AIR.PA", "name": "Airbus SE"},
        {"symbol": "BAYN.DE", "name": "Bayer AG"}
    ]
    
    # Mock get_large_cap_movers to return our test data
    # We need to return a list of dicts as expected by check_large_cap_drops
    mock_movers = []
    for item in test_data:
        mock_movers.append({
            "symbol": item["symbol"],
            "name": item["name"], # This is what we added to TradingViewService
            "price": 100.0,
            "change_percent": -7.5,
            "market_cap": 100_000_000_000,
            "volume": 1_000_000,
            "pe_ratio": 20.0,
            "sector": "Technology",
            "region": "US"
        })

    # Patch stock_service.get_large_cap_movers AND research_service limit
    with patch.object(stock_service, 'get_large_cap_movers', return_value=mock_movers), \
         patch.object(research_service, '_check_and_increment_usage', return_value=True):
        
        # Force cache clear
        stock_service.cache = {}
        
        # Run the check
        stock_service.check_large_cap_drops()
        
    # Check results
    print("\n--- Simulation Results ---")
    for item in test_data:
        sym = item["symbol"]
        if sym in stock_service.research_reports:
            print(f"\n[ {sym} ]")
            report = stock_service.research_reports[sym]
            print(f"Report generated. Length: {len(str(report))}")
            # Check if company name is in the report (simple check)
            if item["name"] in str(report) or item["name"].split()[0] in str(report):
                 print(f"SUCCESS: Company name '{item['name']}' found in report.")
            else:
                 print(f"WARNING: Company name '{item['name']}' NOT found in report.")
        else:
            print(f"\n[ {sym} ] - No Report Generated")

if __name__ == "__main__":
    simulate_research()

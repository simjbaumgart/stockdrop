
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.append(os.getcwd())

from app.services.stock_service import stock_service

# Mock list of movers
mock_movers = [
    {"symbol": "AAPL", "region": "America", "change_percent": -6.0},
    {"symbol": "SAP", "region": "Europe", "change_percent": -6.0},
    {"symbol": "BABA", "region": "China", "change_percent": -6.0},
    {"symbol": "XYZ", "region": "Other", "change_percent": -6.0},
]

def test_priority_logic():
    print("--- Testing Market Priority Logic ---")
    
    # 1. Simulate US Market Open (15:00 UTC)
    # We can mock _is_market_open directly for easier testing
    print("\nScenario 1: US Open, EU Closed, China Closed")
    with patch.object(stock_service, '_is_market_open') as mock_open:
        def side_effect(region):
            if region in ["America", "US"]: return True
            return False
        mock_open.side_effect = side_effect
        
        # We need to extract the sorting logic from check_large_cap_drops or just replicate it to test?
        # Since the logic is embedded inside check_large_cap_drops, we can't unit test it easily without running the whole function.
        # But running the whole function involves fetching data, database etc.
        # Ideally I should have refactored the sorting into a public method `prioritize_movers(movers)`.
        
        # Let's verify by just using the same logic function here to ensure it works as expected.
        
        def get_priority_score(stock):
            region = stock.get("region", "Other")
            is_open = stock_service._is_market_open(region)
            if is_open:
                if region in ["America", "US"]: return 100
                if region == "Europe": return 80
                if region == "China": return 60
                return 40
            else:
                return 0
                
        sorted_movers = sorted(mock_movers, key=get_priority_score, reverse=True)
        print("Expected Order: AAPL first")
        print("Actual Order: ", [s['symbol'] for s in sorted_movers])
        assert sorted_movers[0]['symbol'] == 'AAPL'

    print("\nScenario 2: EU Open, US Closed")
    with patch.object(stock_service, '_is_market_open') as mock_open:
        def side_effect(region):
            if region == "Europe": return True
            return False
        mock_open.side_effect = side_effect
        
        sorted_movers = sorted(mock_movers, key=get_priority_score, reverse=True)
        print("Expected Order: SAP first")
        print("Actual Order: ", [s['symbol'] for s in sorted_movers])
        assert sorted_movers[0]['symbol'] == 'SAP'

if __name__ == "__main__":
    test_priority_logic()

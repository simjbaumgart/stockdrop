import sys
import os
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import pandas as pd

# Add app to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.stock_service import stock_service

def test_earnings_logic():
    print("Testing Earnings Drop Logic...")
    
    symbol = "TEST_EARNINGS"
    today = datetime.now().date()
    
    # Mock yfinance Ticker
    with patch("yfinance.Ticker") as MockTicker:
        mock_instance = MockTicker.return_value
        
        # Case 1: Earnings yesterday (Drop today) -> Should be TRUE
        earnings_date = today - timedelta(days=1)
        mock_instance.earnings_dates = pd.DataFrame({'EPS Estimate': [1.0]}, index=[pd.Timestamp(earnings_date)])
        
        is_earnings, date_str = stock_service._check_earnings_proximity(symbol)
        print(f"Case 1 (Earnings Yesterday): {is_earnings} (Date: {date_str})")
        assert is_earnings == True
        assert date_str == earnings_date.strftime("%Y-%m-%d")
        
        # Case 2: Earnings tomorrow (Anticipation?) -> Should be TRUE
        earnings_date = today + timedelta(days=1)
        mock_instance.earnings_dates = pd.DataFrame({'EPS Estimate': [1.0]}, index=[pd.Timestamp(earnings_date)])
        
        is_earnings, date_str = stock_service._check_earnings_proximity(symbol)
        print(f"Case 2 (Earnings Tomorrow): {is_earnings} (Date: {date_str})")
        assert is_earnings == True
        
        # Case 3: Earnings 10 days ago -> Should be FALSE
        earnings_date = today - timedelta(days=10)
        mock_instance.earnings_dates = pd.DataFrame({'EPS Estimate': [1.0]}, index=[pd.Timestamp(earnings_date)])
        
        is_earnings, date_str = stock_service._check_earnings_proximity(symbol)
        print(f"Case 3 (Earnings 10 days ago): {is_earnings}")
        assert is_earnings == False
        
    print("Verification Passed!")

if __name__ == "__main__":
    test_earnings_logic()

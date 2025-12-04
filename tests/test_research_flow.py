import sys
import os
from unittest.mock import MagicMock, patch

# Add app to path
sys.path.append(os.getcwd())

from app.services.stock_service import stock_service
from app.services.email_service import email_service
from app.services.research_service import research_service

def test_research_flow():
    print("Testing Research Flow...")
    
    # Mock Alpaca snapshot
    mock_snapshot = MagicMock()
    mock_snapshot.latest_trade.price = 100.0
    mock_snapshot.daily_bar.close = 100.0
    mock_snapshot.prev_daily_bar.close = 110.0 # ~9% drop
    
    with patch('app.services.stock_service.alpaca_service') as mock_alpaca_service:
        mock_alpaca_service.get_snapshots.return_value = {"AAPL": mock_snapshot}
        
        # Mock stock_service.stock_tickers to only include AAPL for test speed
        original_tickers = stock_service.stock_tickers
        stock_service.stock_tickers = ["AAPL"]
        
        # Mock email service to avoid sending real emails
        email_service.send_notification = MagicMock()
        
        # Run the check
        stock_service.check_large_cap_drops()
        
        # Verify research report generated
        if "AAPL" in stock_service.research_reports:
            print("SUCCESS: Research report generated for AAPL")
            print("Report content:", stock_service.research_reports["AAPL"][:50] + "...")
        else:
            print("FAILURE: No research report generated")
            
        # Verify email sent with report
        if email_service.send_notification.called:
            args = email_service.send_notification.call_args[0]
            if args[3] is not None: # 4th argument is research_report
                print("SUCCESS: Email notification triggered with report")
            else:
                print("FAILURE: Email notification triggered but report is missing")
        else:
            print("FAILURE: Email notification not triggered")
            
        # Restore tickers
        stock_service.stock_tickers = original_tickers

if __name__ == "__main__":
    test_research_flow()

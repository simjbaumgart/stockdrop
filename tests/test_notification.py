import sys
import os
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.stock_service import stock_service
from app.services.email_service import email_service

def test_notification_logic():
    print("Testing notification logic...")
    
    # Mock get_large_cap_movers to return a stock with > 6% drop
    mock_stock = {
        "symbol": "TEST_DROP",
        "name": "Test Drop Corp",
        "price": 100.0,
        "change": -7.0,
        "change_percent": -7.0 # > 6% drop
    }
    
    # Mock a stock with small drop
    mock_stock_small = {
        "symbol": "TEST_SMALL",
        "name": "Test Small Corp",
        "price": 100.0,
        "change": -2.0,
        "change_percent": -2.0
    }

    with patch.object(stock_service, 'get_large_cap_movers', return_value=[mock_stock, mock_stock_small]):
        with patch.object(email_service, 'send_notification') as mock_send:
            
            # Run check
            stock_service.check_large_cap_drops()
            
            # Verify email sent for TEST_DROP
            mock_send.assert_called_with("TEST_DROP", -7.0, 100.0)
            print("SUCCESS: Email triggered for TEST_DROP")
            
            # Verify NOT sent for TEST_SMALL
            # We can check call args list to ensure only one call
            assert mock_send.call_count == 1
            print("SUCCESS: Email NOT triggered for TEST_SMALL")
            
            # Run check again to verify caching (should not send again)
            stock_service.check_large_cap_drops()
            assert mock_send.call_count == 1
            print("SUCCESS: Email NOT triggered again (caching works)")

if __name__ == "__main__":
    test_notification_logic()

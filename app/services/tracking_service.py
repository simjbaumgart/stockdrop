import logging
from typing import List, Dict, Any
from app.database import get_decision_points, add_tracking_point
from app.services.tradingview_service import tradingview_service

logger = logging.getLogger(__name__)

class TrackingService:
    def update_tracked_stocks(self):
        """
        Fetches all recorded decisions and adds a new price point to the tracking history.
        """
        decisions = get_decision_points()
        if not decisions:
            logger.info("No decisions found to track.")
            return

        print(f"Tracking {len(decisions)} stocks...")
        
        for decision in decisions:
            symbol = decision['symbol']
            decision_id = decision['id']
            
            # Skip test symbols
            if symbol in ["MOCK_TEST", "TEST", "EXAMPLE"]:
                continue
                
            # Determine region (logic copied from performance_service.py)
            region = "US"
            if "." in symbol:
                suffix = symbol.split(".")[-1]
                if suffix in ["DE", "PA", "SW", "L", "AS", "BR", "LS"]:
                    region = "EU"
                elif suffix in ["SS", "SZ", "HK"]:
                    region = "CN"
            
            try:
                current_price = tradingview_service.get_latest_price(symbol, region)
                
                if current_price > 0.0:
                    add_tracking_point(decision_id, current_price)
                    print(f"Tracked {symbol}: ${current_price:.2f}")
                else:
                    print(f"Failed to get price for {symbol}")
                    
            except Exception as e:
                print(f"Error tracking {symbol}: {e}")

tracking_service = TrackingService()

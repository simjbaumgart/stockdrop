import logging
from typing import List, Dict, Any
from app.database import get_decision_points
from app.services.tradingview_service import tradingview_service

logger = logging.getLogger(__name__)

class PerformanceService:
    def evaluate_decisions(self) -> List[Dict[str, Any]]:
        """
        Fetches all recorded decisions and compares the decision price
        with the current market price to evaluate performance.
        """
        decisions = get_decision_points()
        if not decisions:
            logger.info("No decisions found to evaluate.")
            return []

        results = []
        
        for decision in decisions:
            symbol = decision['symbol']
            
            # Skip test symbols
            if symbol in ["MOCK_TEST", "TEST", "EXAMPLE"]:
                continue
                
            start_price = decision['price_at_decision']
            
            # The recommendation comes from the 'decision_points' table in the database.
            # This table is populated by StockService.check_large_cap_drops(), which calls 
            # ResearchService.analyze_stock() (using Gemini) to generate the BUY/HOLD/SELL recommendation.
            recommendation = decision['recommendation']
            
            timestamp = decision['timestamp']
            
            # Try to determine region from decision data if available, or default to US
            # The decision table doesn't have region, but we might have saved it in the JSON blob in 'reasoning' 
            # or we can try to guess. 
            # Actually, stock_service saves 'region' in the separate CSV/JSON files, but the DB table 'decision_points'
            # currently only has: symbol, price, drop_percent, recommendation, reasoning, status.
            # We will default to "US" for now, or we could try to look it up in stock_service metadata if we imported it.
            # For simplicity, let's try US first, or maybe we can update the DB to store region later.
            # For now, we'll just pass "US" as default, but we can try to infer from symbol format (e.g. .DE, .PA)
            
            region = "US"
            if "." in symbol:
                suffix = symbol.split(".")[-1]
                if suffix in ["DE", "PA", "SW", "L", "AS", "BR", "LS"]:
                    region = "EU"
                elif suffix in ["SS", "SZ", "HK"]:
                    region = "CN"
            
            current_price = tradingview_service.get_latest_price(symbol, region)
            
            if current_price == 0.0:
                performance_percent = 0.0
            else:
                performance_percent = ((current_price - start_price) / start_price) * 100
            
            # Determine if the decision was "good"
            # BUY -> Positive return (> 2%) is good
            # SELL -> Negative return (price dropped > 2% after sell) is good (saved money)
            # HOLD -> Neutral
            
            outcome = "NEUTRAL"
            if recommendation == "BUY":
                if performance_percent > 2.0:
                    outcome = "PROFIT"
                elif performance_percent < -2.0:
                    outcome = "LOSS"
            elif recommendation == "SELL":
                if performance_percent < -2.0:
                    outcome = "SAVED" # Price went down significantly after sell
                elif performance_percent > 2.0:
                    outcome = "MISSED" # Price went up significantly after sell
            
            results.append({
                "id": decision['id'],
                "symbol": symbol,
                "timestamp": timestamp,
                "recommendation": recommendation,
                "start_price": start_price,
                "current_price": current_price,
                "performance_percent": performance_percent,
                "outcome": outcome,
                "reasoning": decision['reasoning']
            })
            
        return results

    def record_daily_performance(self):
        """
        Evaluates current performance and records it in the history table.
        """
        logger.info("Recording daily performance metrics...")
        results = self.evaluate_decisions()
        
        from app.database import add_tracking_point
        
        count = 0
        for result in results:
            # Only record if we have a valid price
            if result['current_price'] > 0:
                add_tracking_point(result['id'], result['current_price'])
                count += 1
                
        logger.info(f"Recorded performance for {count} decisions.")
        return count

performance_service = PerformanceService()


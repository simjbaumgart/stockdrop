import logging
from typing import List, Dict, Any
from datetime import datetime, timedelta
import yfinance as yf
import pandas as pd
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

    def analyze_historical_trade(self, symbol: str, buy_date: str, investment_amount: float = 1000.0) -> Dict[str, Any]:
        """
        Simulates a trade from a past date and calculates performance metrics.
        
        Args:
            symbol: Stock symbol (e.g., AAPL)
            buy_date: Date string in YYYY-MM-DD format
            investment_amount: Initial investment amount (default 1000)
            
        Returns:
            Dictionary containing performance metrics, history data for graph, and hold point metrics.
        """
        try:
            # 1. Validation
            start_date = datetime.strptime(buy_date, "%Y-%m-%d")
            if start_date > datetime.now():
                return {"error": "Buy date cannot be in the future."}
                
            # 2. Fetch History using yfinance
            # Fetch from buy_date to now
            ticker = yf.Ticker(symbol)
            history = ticker.history(start=buy_date, interval="1d")
            
            if history.empty:
                 return {"error": f"No data found for {symbol} starting from {buy_date}."}
                 
            # 3. Calculate Daily Values
            initial_price = history.iloc[0]['Close']
            shares_bought = investment_amount / initial_price
            
            daily_data = []
            for date, row in history.iterrows():
                close_price = row['Close']
                current_value = shares_bought * close_price
                roi_percent = ((current_value - investment_amount) / investment_amount) * 100
                
                daily_data.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "price": round(close_price, 2),
                    "value": round(current_value, 2),
                    "roi": round(roi_percent, 2)
                })
                
            # 4. Calculate Specific Hold Point Metrics
            hold_periods = [7, 14, 31, 365]
            metrics = []
            
            # Helper to find closest available date if exact date is non-trading
            def get_data_at_offset(days_offset):
                # Target date
                target_date = start_date + timedelta(days=days_offset)
                
                # Check if target date is in the future
                if target_date > datetime.now():
                    return None
                    
                # Find the row in history closely matching this date
                # We can filter history index
                # Converting history index to dates for comparison
                # Note: history.index is usually DatetimeIndex
                
                # Simple approach: look for date >= target_date in our processed daily_data
                # equivalent to "next trading day"
                target_str = target_date.strftime("%Y-%m-%d")
                
                for entry in daily_data:
                    if entry["date"] >= target_str:
                        return entry
                
                # If we are here, maybe the data ends before the target (shouldn't happen if we check future)
                return daily_data[-1]

            for days in hold_periods:
                data_point = get_data_at_offset(days)
                if data_point:
                    metrics.append({
                        "days": days,
                        "date": data_point["date"],
                        "value": data_point["value"],
                        "roi": data_point["roi"],
                        "status": "Completed"
                    })
                else:
                    metrics.append({
                        "days": days,
                        "date": (start_date + timedelta(days=days)).strftime("%Y-%m-%d"),
                        "value": "-",
                        "roi": "-",
                        "status": "Pending"
                    })
            
            # Add "Current/Today" metric
            current = daily_data[-1]
            days_held = (datetime.strptime(current["date"], "%Y-%m-%d") - start_date).days
            metrics.append({
                "days": f"Today ({days_held}d)",
                "date": current["date"],
                "value": current["value"],
                "roi": current["roi"],
                "status": "Current"
            })
            
            return {
                "symbol": symbol.upper(),
                "buy_date": buy_date,
                "initial_price": round(initial_price, 2),
                "shares": round(shares_bought, 4),
                "investment": investment_amount,
                "daily_data": daily_data,
                "metrics": metrics
            }
            
        except Exception as e:
            logger.error(f"Error evaluating historical trade: {e}")
            return {"error": str(e)}

performance_service = PerformanceService()


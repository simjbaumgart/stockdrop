import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional
from app.services.tradingview_service import tradingview_service

class GatekeeperService:
    def __init__(self):
        self.benchmark_symbol = "SPY" # Can be switched to QQQ
        self.regime_cache = None
        self.regime_cache_time = None
        self.cache_duration = timedelta(hours=1)



    def check_market_regime(self) -> Dict[str, str]:
        """
        Checks the global market regime (Rising Tide Rule).
        Returns a dict with 'regime' ('BULL' or 'BEAR') and 'details'.
        """
        # Simple cache to avoid fetching SPY every time
        if self.regime_cache and self.regime_cache_time and \
           datetime.now() - self.regime_cache_time < self.cache_duration:
            return self.regime_cache

        try:
            # Use TradingView TA to get SPY SMA200
            # Assuming US market for SPY
            indicators = tradingview_service.get_technical_indicators(self.benchmark_symbol, region="US")
            
            if not indicators:
                print(f"Error: Could not fetch indicators for {self.benchmark_symbol}")
                return {"regime": "UNKNOWN", "details": "No data"}

            current_close = indicators.get("close", 0.0)
            current_sma = indicators.get("sma200", 0.0)
            
            if current_close == 0.0 or current_sma == 0.0:
                 return {"regime": "UNKNOWN", "details": "Missing Price or SMA data"}

            # Rising Tide Rule: Close > SMA 200 = BULL
            regime = "BULL" if current_close > current_sma else "BEAR"
            
            result = {
                "regime": regime,
                "details": f"{self.benchmark_symbol} Close ({current_close:.2f}) {'above' if regime == 'BULL' else 'below'} 200 SMA ({current_sma:.2f})"
            }
            
            self.regime_cache = result
            self.regime_cache_time = datetime.now()
            return result

        except Exception as e:
            print(f"Error checking market regime: {e}")
            return {"regime": "UNKNOWN", "details": str(e)}

    def check_technical_filters(self, symbol: str, region: str = "US", exchange: str = None, screener: str = None, cached_indicators: Dict = None) -> Tuple[bool, Dict]:
        """
        Applies 'Deep Dip' Guardrails: BB %B < 0.30 (Bottom 30% of range).
        Returns (is_valid, reasons_dict).
        """
        try:
            if cached_indicators:
                indicators = cached_indicators
            else:
                indicators = tradingview_service.get_technical_indicators(symbol, region=region, exchange=exchange, screener=screener)
            
            if not indicators:
                return False, {"error": "Insufficient data"}

            reasons = {}
            
            # Extract
            price = indicators.get('close', 0.0)
            bb_lower = indicators.get('bb_lower', 0.0)
            bb_upper = indicators.get('bb_upper', 0.0)
            volume = indicators.get('volume', 0)
            
            # --- Filter: Bollinger Band %B (Dip) ---
            # %B = (Price - Lower) / (Upper - Lower)
            if bb_upper != bb_lower:
                curr_pct_b = (price - bb_lower) / (bb_upper - bb_lower)
            else:
                curr_pct_b = 0.5 # Default if squeezed/error
            
            is_valid = False
            
            # Logic: IF %B < 0.50: VALID
            if curr_pct_b < 0.50:
                is_valid = True
                reasons['bb_status'] = f"%B ({curr_pct_b:.2f}) < 0.50 (Dip)"
            else:
                reasons['bb_status'] = f"%B ({curr_pct_b:.2f}) >= 0.50 (Not Dip Enough)"

            # --- Filter: Volume Anomaly (Optional) ---
            # We don't have Avg Volume easily from TA.
            # We'll skip reliable volume anomaly check for now or just log volume.
            # reasons['volume'] = volume

            # Add raw values for debugging/logging
            reasons['price'] = price
            reasons['lower_bb'] = bb_lower
            reasons['bb_pct_b'] = curr_pct_b
            
            return is_valid, reasons

        except Exception as e:
            print(f"Error in technical filters for {symbol}: {e}")
            return False, {"error": str(e)}

gatekeeper_service = GatekeeperService()

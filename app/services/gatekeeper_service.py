import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional
from app.services.tradingview_service import tradingview_service

# Minimum share price (USD) for a ticker to be considered tradeable.
# Filters out OTC penny stocks (e.g. PBMRF $0.003, BKRKF $0.17, PPERF $0.26,
# CEBCF $0.38, PINXF $0.75) whose Bollinger math still flags them as "dipped"
# but which have no realistic liquidity, wide spreads, and poor LLM coverage.
MIN_PRICE_USD = 5.0

# Tier names for the graduated Bollinger gate.
TIER_DEEP_DIP = "DEEP_DIP"           # %B < 0.30 — high-conviction oversold
TIER_STANDARD_DIP = "STANDARD_DIP"   # 0.30 ≤ %B < 0.50 — current default
TIER_SHALLOW_DIP = "SHALLOW_DIP"     # 0.50 ≤ %B < 0.70 with ≥ 8% drop
TIER_REJECT = "REJECT"

# Tier boundaries
PCT_B_DEEP = 0.30
PCT_B_STANDARD = 0.50
PCT_B_SHALLOW = 0.70
SHALLOW_MIN_DROP_PCT = 8.0

class GatekeeperService:
    def __init__(self):
        self.benchmark_symbol = "SPY" # Can be switched to QQQ
        self.regime_cache = None
        self.regime_cache_time = None
        self.cache_duration = timedelta(hours=1)

    def classify_tier(self, pct_b: float, drop_pct: float) -> str:
        """
        Classify a candidate into a Bollinger gate tier.

        Args:
            pct_b: Bollinger %B value, (price - lower) / (upper - lower).
            drop_pct: Today's percentage drop. Sign-agnostic (uses abs()).

        Returns one of TIER_DEEP_DIP, TIER_STANDARD_DIP, TIER_SHALLOW_DIP, TIER_REJECT.
        """
        drop_magnitude = abs(drop_pct)
        if pct_b < PCT_B_DEEP:
            return TIER_DEEP_DIP
        if pct_b < PCT_B_STANDARD:
            return TIER_STANDARD_DIP
        if pct_b < PCT_B_SHALLOW and drop_magnitude >= SHALLOW_MIN_DROP_PCT:
            return TIER_SHALLOW_DIP
        return TIER_REJECT

    def check_liquidity_filter(self, price: float) -> Tuple[bool, str]:
        """
        Pre-filter: reject sub-$5 tickers before any expensive analysis.
        Returns (is_valid, reason_string).
        """
        if price is None or price <= 0:
            return False, f"Price missing or non-positive ({price})"
        if price < MIN_PRICE_USD:
            return False, f"Price ${price:.2f} < ${MIN_PRICE_USD:.2f} minimum (penny-stock filter)"
        return True, f"Price ${price:.2f} >= ${MIN_PRICE_USD:.2f} minimum"

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

    def check_technical_filters(
        self,
        symbol: str,
        region: str = "US",
        exchange: str = None,
        screener: str = None,
        cached_indicators: Dict = None,
        drop_pct: float = 0.0,
    ) -> Tuple[bool, Dict]:
        """
        Applies the tiered Bollinger gate plus liquidity pre-filter.
        Returns (is_valid, reasons_dict). reasons['tier'] is always set.
        """
        try:
            if cached_indicators:
                indicators = cached_indicators
            else:
                indicators = tradingview_service.get_technical_indicators(
                    symbol, region=region, exchange=exchange, screener=screener
                )

            if not indicators:
                return False, {"error": "Insufficient data", "tier": TIER_REJECT}

            reasons = {}
            price = indicators.get("close", 0.0)
            bb_lower = indicators.get("bb_lower", 0.0)
            bb_upper = indicators.get("bb_upper", 0.0)

            # --- Pre-filter: liquidity ---
            liquidity_ok, liquidity_reason = self.check_liquidity_filter(price)
            reasons["liquidity_status"] = liquidity_reason
            reasons["price"] = price
            if not liquidity_ok:
                reasons["lower_bb"] = bb_lower
                reasons["tier"] = TIER_REJECT
                return False, reasons

            # --- Bollinger %B ---
            if bb_upper != bb_lower:
                curr_pct_b = (price - bb_lower) / (bb_upper - bb_lower)
            else:
                curr_pct_b = 0.5

            tier = self.classify_tier(pct_b=curr_pct_b, drop_pct=drop_pct)
            is_valid = tier != TIER_REJECT

            if tier == TIER_DEEP_DIP:
                reasons["bb_status"] = f"%B ({curr_pct_b:.2f}) < {PCT_B_DEEP:.2f} (Deep Dip)"
            elif tier == TIER_STANDARD_DIP:
                reasons["bb_status"] = f"%B ({curr_pct_b:.2f}) < {PCT_B_STANDARD:.2f} (Standard Dip)"
            elif tier == TIER_SHALLOW_DIP:
                reasons["bb_status"] = (
                    f"%B ({curr_pct_b:.2f}) in [{PCT_B_STANDARD:.2f}, {PCT_B_SHALLOW:.2f}) "
                    f"with drop {abs(drop_pct):.1f}% >= {SHALLOW_MIN_DROP_PCT:.1f}% (Shallow Dip)"
                )
            else:
                if curr_pct_b >= PCT_B_SHALLOW:
                    reasons["bb_status"] = f"%B ({curr_pct_b:.2f}) >= {PCT_B_SHALLOW:.2f} (Not Dip Enough)"
                else:
                    reasons["bb_status"] = (
                        f"%B ({curr_pct_b:.2f}) in shallow zone but drop "
                        f"{abs(drop_pct):.1f}% < {SHALLOW_MIN_DROP_PCT:.1f}% (Insufficient Drop)"
                    )

            reasons["lower_bb"] = bb_lower
            reasons["bb_pct_b"] = curr_pct_b
            reasons["tier"] = tier
            return is_valid, reasons

        except Exception as e:
            print(f"Error in technical filters for {symbol}: {e}")
            return False, {"error": str(e), "tier": TIER_REJECT}

gatekeeper_service = GatekeeperService()

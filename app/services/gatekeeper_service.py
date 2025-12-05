import yfinance as yf
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional

class GatekeeperService:
    def __init__(self):
        self.benchmark_symbol = "SPY" # Can be switched to QQQ
        self.regime_cache = None
        self.regime_cache_time = None
        self.cache_duration = timedelta(hours=1)

    def _calculate_sma(self, series: pd.Series, length: int) -> pd.Series:
        return series.rolling(window=length).mean()

    def _calculate_rsi(self, series: pd.Series, length: int = 14) -> pd.Series:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).fillna(0)
        loss = (-delta.where(delta < 0, 0)).fillna(0)
        
        # Use Wilder's Smoothing (RMA)
        avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _calculate_bbands(self, series: pd.Series, length: int = 20, std: float = 1.96) -> pd.DataFrame:
        sma = series.rolling(window=length).mean()
        std_dev = series.rolling(window=length).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        # %B
        percent_b = (series - lower) / (upper - lower)
        
        return pd.DataFrame({
            'BBL': lower,
            'BBM': sma,
            'BBU': upper,
            'BBB': percent_b
        })

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
            # Fetch enough data for 200 SMA
            ticker = yf.Ticker(self.benchmark_symbol)
            hist = ticker.history(period="1y")
            
            if hist.empty:
                print(f"Error: Could not fetch history for {self.benchmark_symbol}")
                return {"regime": "UNKNOWN", "details": "No data"}

            if len(hist) < 200:
                print(f"Warning: Not enough data for 200 SMA (only {len(hist)} days)")
                return {"regime": "BULL", "details": "Insufficient data for 200 SMA, defaulting to BULL"}

            hist['SMA_200'] = self._calculate_sma(hist['Close'], 200)
            
            current_close = hist['Close'].iloc[-1]
            current_sma = hist['SMA_200'].iloc[-1]
            
            if pd.isna(current_sma):
                 return {"regime": "UNKNOWN", "details": "SMA calculation failed"}

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

    def check_technical_filters(self, symbol: str) -> Tuple[bool, Dict]:
        """
        Applies 'Deep Dip' Guardrails: BB %B < 0.30 (Bottom 30% of range).
        Returns (is_valid, reasons_dict).
        """
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period="6mo")
            
            if hist.empty or len(hist) < 30:
                return False, {"error": "Insufficient data"}

            # Bollinger Bands (1.96 SD)
            bb = self._calculate_bbands(hist['Close'], length=20, std=1.96)
            hist = pd.concat([hist, bb], axis=1)
            
            # Volume Average
            hist['Vol_Avg_20'] = self._calculate_sma(hist['Volume'], length=20)

            # Get latest data points
            current = hist.iloc[-1]
            
            reasons = {}
            
            # --- Filter: Bollinger Band %B (Dip) ---
            # Logic: IF %B < 0.50: VALID (Price is in the bottom 50% of the band).
            
            curr_pct_b = current['BBB']
            
            is_valid = False
            
            if curr_pct_b < 0.50:
                is_valid = True
                reasons['bb_status'] = f"%B ({curr_pct_b:.2f}) < 0.50 (Dip)"
            else:
                reasons['bb_status'] = f"%B ({curr_pct_b:.2f}) >= 0.50 (Not Dip Enough)"

            # --- Filter: Volume Anomaly (Optional/Supporting) ---
            if current['Volume'] > 1.35 * current['Vol_Avg_20']:
                reasons['volume_anomaly'] = f"Volume Spike: {current['Volume']} > 1.35x Avg ({current['Vol_Avg_20']:.0f})"

            # Add raw values for debugging/logging
            reasons['price'] = current['Close']
            reasons['lower_bb'] = current['BBL']
            reasons['bb_pct_b'] = current['BBB']
            
            return is_valid, reasons

        except Exception as e:
            print(f"Error in technical filters for {symbol}: {e}")
            return False, {"error": str(e)}

gatekeeper_service = GatekeeperService()

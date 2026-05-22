import datetime
import logging
from typing import Any, Dict, List, Optional

import requests
import yfinance as yf

from app.services.fred_service import fred_service

logger = logging.getLogger(__name__)

# VIX level bands (CBOE convention).
VIX_COMPLACENT = 15.0
VIX_ELEVATED = 20.0
VIX_PANIC = 30.0


def classify_vix(level: float) -> str:
    """Map a VIX level to a regime band."""
    if level < VIX_COMPLACENT:
        return "COMPLACENT"
    if level < VIX_ELEVATED:
        return "NORMAL"
    if level < VIX_PANIC:
        return "ELEVATED"
    return "PANIC"


def _percentile_rank(window: List[float], value: float) -> float:
    """Percent of `window` values strictly below `value` (0-100)."""
    if not window:
        return 0.0
    below = sum(1 for v in window if v < value)
    return 100.0 * below / len(window)


class VolatilityService:
    def get_vix_context(self) -> Dict[str, Any]:
        """Latest VIX level + 5/20-day percentile from FRED VIXCLS."""
        try:
            history = fred_service.fetch_series_history("VIXCLS", limit=30)
        except Exception as e:
            logger.warning(f"VIX history fetch failed: {e}")
            return {"vix": None, "error": str(e)}

        series: List[float] = []
        latest_date: Optional[str] = None
        for value, date in history:  # newest first
            try:
                series.append(float(value))
            except (TypeError, ValueError):
                continue  # FRED uses "." for holidays / missing days
            if latest_date is None:
                latest_date = date

        if not series:
            return {"vix": None, "error": "no numeric VIXCLS observations"}

        latest = series[0]
        return {
            "vix": round(latest, 2),
            "vix_date": latest_date,
            "vix_class": classify_vix(latest),
            "vix_pctile_5d": round(_percentile_rank(series[:5], latest), 1),
            "vix_pctile_20d": round(_percentile_rank(series[:20], latest), 1),
        }


volatility_service = VolatilityService()

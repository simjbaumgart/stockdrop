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

# CNN Fear & Greed — unofficial endpoint, must fail gracefully.
_CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_CNN_HEADERS = {"User-Agent": "Mozilla/5.0 (StockDrop volatility probe)"}

# Favorability of each VIX class for dip-buy mean reversion (0-1).
# Low VIX = slow grind, drops continue. Elevated VIX = real panic, real reversion.
_VIX_FAVORABILITY = {
    "COMPLACENT": 0.30,
    "NORMAL": 0.50,
    "ELEVATED": 0.85,
    "PANIC": 0.70,  # extreme panic: still favorable but outcome variance rises
}


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


def _format_summary(r: Dict[str, Any]) -> str:
    vix = r.get("vix")
    vix_txt = f"VIX {vix} ({r.get('vix_class')})" if vix is not None else "VIX unavailable"
    term_txt = r.get("term_structure") or "term structure unavailable"
    fg = r.get("fear_greed")
    fg_txt = (f", Fear&Greed {fg} ({r.get('fear_greed_rating')})"
              if fg is not None else "")
    return (f"{vix_txt}, {term_txt}, trend {r.get('trend')}{fg_txt} — "
            f"regime {r.get('regime_label')} ({r.get('regime_score')}) for dip-buying.")


class VolatilityService:
    CACHE_TTL = datetime.timedelta(hours=1)

    def __init__(self):
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_time: Optional[datetime.datetime] = None
        self._cache_trend: Optional[str] = None

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

    def get_term_structure(self) -> Dict[str, Any]:
        """VIX vs 3-month VIX (^VIX, ^VIX3M) spread from yfinance.

        spread > 0 (backwardation) is historically a strong mean-reversion
        signal — directly relevant to the dip-recovery thesis.
        """
        try:
            data = yf.download(["^VIX", "^VIX3M"], period="5d", progress=False)
            closes = data["Close"].dropna()
            vix = float(closes["^VIX"].iloc[-1])
            vix3m = float(closes["^VIX3M"].iloc[-1])
        except Exception as e:
            logger.warning(f"VIX term structure fetch failed: {e}")
            return {"term_spread": None, "error": str(e)}

        spread = vix - vix3m
        return {
            "vix_spot": round(vix, 2),
            "vix3m": round(vix3m, 2),
            "term_spread": round(spread, 2),
            "term_structure": "BACKWARDATION" if spread > 0 else "CONTANGO",
        }

    def get_fear_greed(self) -> Dict[str, Any]:
        """CNN Fear & Greed composite (0-100). Unofficial endpoint —
        every failure path returns None rather than raising.
        """
        try:
            r = requests.get(_CNN_URL, headers=_CNN_HEADERS, timeout=10)
            r.raise_for_status()
            fg = r.json().get("fear_and_greed", {})
            score = fg.get("score")
            return {
                "fear_greed": round(float(score)) if score is not None else None,
                "fear_greed_rating": fg.get("rating"),
            }
        except Exception as e:
            logger.warning(f"CNN Fear & Greed fetch failed (non-fatal): {e}")
            return {"fear_greed": None, "fear_greed_rating": None}

    @staticmethod
    def score_regime(trend: str, vix_class: str,
                     term_spread: Optional[float]) -> float:
        """Combine trend, VIX class, and term structure into a 0-1 score.
        Higher = more favorable for dip-buying.
        """
        trend_component = {"BULL": 0.65, "BEAR": 0.35}.get(trend, 0.50)
        vix_component = _VIX_FAVORABILITY.get(vix_class, 0.50)
        if term_spread is None:
            term_component = 0.50
        else:
            # spread 0 -> 0.5, +2 -> 1.0, -2 -> 0.0
            term_component = min(1.0, max(0.0, 0.5 + term_spread / 4.0))
        score = (0.40 * trend_component
                 + 0.35 * vix_component
                 + 0.25 * term_component)
        return round(score, 3)

    def get_regime(self, trend: str = "UNKNOWN") -> Dict[str, Any]:
        """Assemble the unified volatility-regime dict. Cached for CACHE_TTL
        per trend value.
        """
        now = datetime.datetime.utcnow()
        if (self._cache is not None and self._cache_time is not None
                and self._cache_trend == trend
                and now - self._cache_time < self.CACHE_TTL):
            return self._cache

        errors: List[str] = []
        vix_ctx = self.get_vix_context()
        if vix_ctx.get("error"):
            errors.append(f"vix: {vix_ctx['error']}")
        term_ctx = self.get_term_structure()
        if term_ctx.get("error"):
            errors.append(f"term: {term_ctx['error']}")
        fg_ctx = self.get_fear_greed()

        vix_class = vix_ctx.get("vix_class") or "NORMAL"
        term_spread = term_ctx.get("term_spread")
        score = self.score_regime(trend, vix_class, term_spread)
        if score >= 0.60:
            label = "FAVORABLE"
        elif score >= 0.40:
            label = "NEUTRAL"
        else:
            label = "UNFAVORABLE"

        regime = {
            "trend": trend,
            "vix": vix_ctx.get("vix"),
            "vix_date": vix_ctx.get("vix_date"),
            "vix_class": vix_ctx.get("vix_class"),
            "vix_pctile_5d": vix_ctx.get("vix_pctile_5d"),
            "vix_pctile_20d": vix_ctx.get("vix_pctile_20d"),
            "vix3m": term_ctx.get("vix3m"),
            "term_spread": term_spread,
            "term_structure": term_ctx.get("term_structure"),
            "fear_greed": fg_ctx.get("fear_greed"),
            "fear_greed_rating": fg_ctx.get("fear_greed_rating"),
            "regime_score": score,
            "regime_label": label,
            "errors": errors,
        }
        regime["summary"] = _format_summary(regime)

        self._cache = regime
        self._cache_time = now
        self._cache_trend = trend
        return regime


volatility_service = VolatilityService()

"""
Single source of truth for TradingView exchange/screener resolution.

Both the gatekeeper path (get_technical_indicators) and the analyst path
(get_technical_analysis) must agree on where a ticker trades. Having one
function here means they cannot diverge silently — which was the MBGYY bug
where the gatekeeper correctly used OTC but the analyst hardcoded NASDAQ
and returned {}.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple
from tradingview_ta import TA_Handler, Interval

_EXCHANGE_CACHE: Dict[str, Tuple[str, str]] = {}

TA_UNAVAILABLE_SENTINEL = {"ta_unavailable": True}

_PROBE_ORDER = ("NASDAQ", "NYSE", "AMEX", "OTC")
_US_SCREENER = "america"


def clear_cache() -> None:
    _EXCHANGE_CACHE.clear()


def _tv_symbol_exists(symbol: str, exchange: str) -> bool:
    try:
        handler = TA_Handler(
            symbol=symbol,
            screener=_US_SCREENER,
            exchange=exchange,
            interval=Interval.INTERVAL_1_DAY,
        )
        handler.get_analysis()
        return True
    except Exception:
        return False


def resolve_tv_exchange(
    symbol: str,
    known_exchange: Optional[str] = None,
    known_screener: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """
    Map a ticker to a (exchange, screener) pair TradingView will accept.

    - If the caller already knows both, return them verbatim.
    - Otherwise consult the cache, then probe NASDAQ -> NYSE -> AMEX -> OTC.
    - Returns None if unresolvable; callers should treat None as
      "TA unavailable" rather than substituting a default exchange.
    """
    if known_exchange and known_screener:
        return (known_exchange.upper(), known_screener.lower())

    if symbol in _EXCHANGE_CACHE:
        return _EXCHANGE_CACHE[symbol]

    for candidate in _PROBE_ORDER:
        if _tv_symbol_exists(symbol, candidate):
            resolved = (candidate, _US_SCREENER)
            _EXCHANGE_CACHE[symbol] = resolved
            return resolved

    return None

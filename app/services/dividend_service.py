"""Fetch canonical dividend facts (ex-date, pay date, amount) from yfinance.

The PM uses these as ground truth so it cannot build a dividend-capture thesis
around a payout whose ex-date has already passed. Returns None on any failure,
mirroring finnhub_service.get_earnings_facts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import yfinance as yf


def _to_iso(val) -> Optional[str]:
    """Normalize a date-like value to an ISO 'YYYY-MM-DD' string, or None."""
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        try:
            return val.isoformat()[:10]
        except Exception:
            return None
    return str(val)[:10] or None


class DividendService:
    def get_dividend_facts(self, symbol: str) -> Optional[dict]:
        """Return the upcoming/most-recent dividend facts as a structured dict,
        or None if no ex-dividend date is available.

        Returned shape:
            {
                "ex_dividend_date": "YYYY-MM-DD",
                "pay_date": "YYYY-MM-DD" | None,
                "amount": float | None,        # per-share, last known
                "source": "yfinance",
                "fetched_at": "<ISO 8601 UTC>",
            }
        """
        try:
            ticker = yf.Ticker(symbol)
            calendar = getattr(ticker, "calendar", None)
        except Exception as e:
            print(f"[DividendService] yf.Ticker failed for {symbol}: {e}")
            return None

        if not isinstance(calendar, dict):
            return None

        ex_iso = _to_iso(calendar.get("Ex-Dividend Date"))
        if not ex_iso:
            return None
        pay_iso = _to_iso(calendar.get("Dividend Date"))

        amount = None
        try:
            info = getattr(ticker, "info", None) or {}
            raw_amount = info.get("lastDividendValue")
            if raw_amount is not None:
                amount = float(raw_amount)
        except Exception:
            amount = None

        return {
            "ex_dividend_date": ex_iso,
            "pay_date": pay_iso,
            "amount": amount,
            "source": "yfinance",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


dividend_service = DividendService()

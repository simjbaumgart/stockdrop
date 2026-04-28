"""Load {ticker: sector} from the user's Portfolio_Total_Weights.xlsx.

Tolerant to missing / malformed files. The xlsx has a 3-row preamble before
the actual header row (Account, Holding, ..., Sector/Theme). Some ticker
rows are account-marker rows (e.g. "ETORO") with no sector — those are skipped.
"""

from __future__ import annotations

import logging
from typing import Dict

import pandas as pd

from app.services.news_digest_schema import news_archive_root

logger = logging.getLogger(__name__)

TICKER_COL = "Account"
SECTOR_COL = "Sector/Theme"
HEADER_ROW = 3

# Known account-marker rows (not real tickers)
_ACCOUNT_MARKERS = {"ETORO", "IBKR", "SAXO", "NORDNET", "DKBANK"}


def load_portfolio_tickers() -> Dict[str, str]:
    path = news_archive_root() / "Portfolio_Total_Weights.xlsx"
    if not path.exists():
        return {}
    try:
        df = pd.read_excel(path, header=HEADER_ROW)
    except Exception as e:
        logger.warning("Could not read %s: %s", path, e)
        return {}
    if TICKER_COL not in df.columns:
        logger.warning("Portfolio xlsx missing %r column; got %s", TICKER_COL, df.columns.tolist())
        return {}
    sector_series = df[SECTOR_COL] if SECTOR_COL in df.columns else pd.Series([""] * len(df))
    out: Dict[str, str] = {}
    for ticker, sector in zip(df[TICKER_COL], sector_series):
        t = ("" if pd.isna(ticker) else str(ticker)).strip().upper()
        s = ("" if pd.isna(sector) else str(sector)).strip()
        if not t or t == "NAN" or t in _ACCOUNT_MARKERS:
            continue
        if not s or s.lower() == "nan":
            # Row exists but no sector — still include with "Unknown"
            s = "Unknown"
        out[t] = s
    return out

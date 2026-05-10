"""Cache yfinance daily OHLC bars to disk so re-runs don't re-hit the API."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("PRICE_CACHE_DIR", "data/price_cache"))


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.upper()}.parquet"


def _read_cache(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        csv_path = path.with_suffix(".csv")
        if csv_path.exists():
            return pd.read_csv(csv_path, index_col=0, parse_dates=True)
        return None
    try:
        return pd.read_parquet(path)
    except Exception as e:
        logger.warning("Failed reading parquet %s, falling back to CSV: %s", path, e)
        csv_path = path.with_suffix(".csv")
        if csv_path.exists():
            return pd.read_csv(csv_path, index_col=0, parse_dates=True)
        return None


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path)
    except Exception as e:
        logger.warning("Parquet write failed (%s); writing CSV", e)
        df.to_csv(path.with_suffix(".csv"))


def get_bars(
    ticker: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    refresh: bool = False,
) -> pd.DataFrame:
    """
    Return daily OHLC bars for ticker between start and end (inclusive).
    Cached on disk; appends new bars to the cache when end exceeds cached range.
    """
    ticker = ticker.upper()
    path = _cache_path(ticker)
    cached = None if refresh else _read_cache(path)

    need_fetch_start = start
    need_fetch_end = end

    if cached is not None and not cached.empty:
        cached.index = pd.to_datetime(cached.index)
        cached_min, cached_max = cached.index.min(), cached.index.max()
        if cached_min <= start and cached_max >= end:
            return cached.loc[(cached.index >= start) & (cached.index <= end)]
        need_fetch_start = min(start, cached_min)
        need_fetch_end = max(end, cached_max)

    fetch_end_inclusive = need_fetch_end + pd.Timedelta(days=1)
    try:
        downloaded = yf.download(
            ticker,
            start=need_fetch_start.strftime("%Y-%m-%d"),
            end=fetch_end_inclusive.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=False,
            threads=False,
        )
    except Exception as e:
        logger.warning("yfinance download failed for %s: %s", ticker, e)
        return cached if cached is not None else pd.DataFrame()

    if downloaded is None or downloaded.empty:
        return cached if cached is not None else pd.DataFrame()

    if isinstance(downloaded.columns, pd.MultiIndex):
        downloaded.columns = downloaded.columns.get_level_values(0)

    downloaded.index = pd.to_datetime(downloaded.index).normalize()

    if cached is not None and not cached.empty:
        merged = pd.concat([cached, downloaded])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = downloaded

    _write_cache(path, merged)
    return merged.loc[(merged.index >= start) & (merged.index <= end)]


def prefetch(
    tickers: list,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict:
    """Bulk-fetch bars for many tickers; returns dict of ticker -> DataFrame."""
    out = {}
    for t in sorted({str(s).upper() for s in tickers if s}):
        try:
            out[t] = get_bars(t, start, end)
        except Exception as e:
            logger.warning("prefetch failed for %s: %s", t, e)
            out[t] = pd.DataFrame()
    return out

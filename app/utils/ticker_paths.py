"""Helpers for turning ticker symbols into filesystem-safe identifiers.

Some tickers contain characters (notably '/') that are interpreted as
directory separators when interpolated into file paths. This module
provides a single sanitizer used everywhere the pipeline writes or reads
ticker-keyed files, so we never silently drop a report again.
"""


def safe_ticker_path(ticker: str) -> str:
    """Return a filesystem-safe version of *ticker*.

    Replaces any path-separator characters with underscores. Other
    characters (letters, digits, '.', '-') are preserved, which covers
    every ticker format we currently see (AAPL, BRK.B, BRK-B, QXO/PB).

    Raises ValueError on empty or None input — ticker must always be set.
    """
    if not ticker or not isinstance(ticker, str):
        raise ValueError(f"safe_ticker_path requires a non-empty ticker, got {ticker!r}")
    return ticker.replace("/", "_").replace("\\", "_")

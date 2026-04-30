import os
import sys
import pytest
from app.services.stock_service import StockService
from app.database import init_db


@pytest.fixture(autouse=True)
def _ensure_db_schema():
    """Ensure the DB schema (including transcript_cache) is present.

    The _isolate_db fixture in other test modules reloads app.database and
    redirects DB_NAME to a tmp path.  Running init_db() here (inside the
    fixture, not at module-import time) guarantees the schema exists against
    whatever DB_NAME is current when the test actually executes.
    """
    init_db()


# Initialize Service
service = StockService()

def test_fetch_and_save(symbol="AAPL"):
    """Verify get_latest_transcript returns a well-formed dict without raising.
    DefeatBeta 0.0.29 is installed; real transcript data may be returned."""
    result = service.get_latest_transcript(symbol)
    # Must always return a dict with the expected keys
    assert isinstance(result, dict), f"expected dict, got {type(result).__name__}: {result!r}"
    assert set(result.keys()) >= {"text", "date", "warning"}, (
        f"missing expected keys, got: {list(result.keys())}"
    )
    text = result.get("text", "")
    # text may be non-empty (real transcript) or empty (no data available) — both valid
    assert isinstance(text, str), f"text must be str, got {type(text).__name__}"
    print(f"[{symbol}] get_latest_transcript returned dict, text length={len(text)}, date={result.get('date')}.")

if __name__ == "__main__":
    # Test a few tickers
    tickers = ["MSFT", "TSLA", "NVDA", "AAPL"]
    
    for t in tickers:
        test_fetch_and_save(t)

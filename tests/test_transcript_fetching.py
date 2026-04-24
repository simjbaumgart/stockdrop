import os
import sys
from app.services.stock_service import StockService

# Initialize Service
service = StockService()

def test_fetch_and_save(symbol="AAPL"):
    """Transcript sources were removed (2026-04-24); verify get_latest_transcript
    returns an empty result without raising."""
    result = service.get_latest_transcript(symbol)
    # Result must be empty str or a dict with no text content
    assert result == "" or (
        isinstance(result, dict) and not result.get("text")
    ), f"expected empty transcript, got: {result!r}"
    print(f"[{symbol}] get_latest_transcript returned empty result as expected.")

if __name__ == "__main__":
    # Test a few tickers
    tickers = ["MSFT", "TSLA", "NVDA", "AAPL"]
    
    for t in tickers:
        test_fetch_and_save(t)

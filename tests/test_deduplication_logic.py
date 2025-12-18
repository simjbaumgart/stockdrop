import os
import sqlite3
import sys
from unittest.mock import MagicMock
from datetime import datetime, timedelta, date

# Mock dependencies before import
sys.modules["yfinance"] = MagicMock()
sys.modules["google"] = MagicMock()
sys.modules["google.cloud"] = MagicMock()
sys.modules["app.services.alpaca_service"] = MagicMock()
sys.modules["app.services.tradingview_service"] = MagicMock()
sys.modules["app.services.finnhub_service"] = MagicMock()
sys.modules["app.services.alpha_vantage_service"] = MagicMock()
sys.modules["app.services.gatekeeper_service"] = MagicMock()
sys.modules["app.services.research_service"] = MagicMock()
sys.modules["app.services.email_service"] = MagicMock()
sys.modules["app.services.drive_service"] = MagicMock()
sys.modules["app.services.benzinga_service"] = MagicMock()
sys.modules["app.services.deep_research_service"] = MagicMock()
sys.modules["app.services.storage_service"] = MagicMock()
sys.modules["app.services.yahoo_ticker_resolver"] = MagicMock()

from app.services.stock_service import stock_service
from app.database import init_db, add_decision_point, get_analyzed_companies_since

# Override DB Path for testing
os.environ["DB_PATH"] = "test_deduplication.db"
import app.database
app.database.DB_NAME = "test_deduplication.db"

def test_deduplication():
    print("=== Testing Deduplication Logic ===")
    
    if os.path.exists("test_deduplication.db"):
        os.remove("test_deduplication.db")
        
    init_db()
    
    # 1. Test Previous Trading Day Calculation
    print("\n[Logic Check] Previous Trading Day:")
    monday = date(2025, 12, 15) # Dec 15 2025 is Monday
    friday = stock_service._get_previous_trading_day(monday)
    print(f"  Monday {monday} -> {friday} (Expected: 2025-12-12)")
    
    if friday == date(2025, 12, 12):
        print("PASS: Monday Logic")
    else:
        print("FAIL: Monday Logic")
        
    wednesday = date(2025, 12, 17)
    tuesday = stock_service._get_previous_trading_day(wednesday)
    print(f"  Wednesday {wednesday} -> {tuesday} (Expected: 2025-12-16)")
    
    if tuesday == date(2025, 12, 16):
        print("PASS: Normal Day Logic")
    else:
        print("FAIL: Normal Day Logic")

    # 2. Test DB Filter
    print("\n[DB Check] Analyzed Companies:")
    
    # Simulate an entry from LAST FRIDAY (relative to our fake Monday)
    # We cheat by inserting with specific timestamp
    fake_friday_str = "2025-12-12 10:00:00"
    
    add_decision_point(
        symbol="NVDA", 
        price=100, 
        drop_percent=-5, 
        recommendation="HOLD", 
        reasoning="Test",
        company_name="NVIDIA Corporation"
    )
    
    # Manually backdate it
    conn = sqlite3.connect("test_deduplication.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE decision_points SET timestamp = ? WHERE symbol = 'NVDA'", (fake_friday_str,))
    conn.commit()
    conn.close()
    
    # Now check if get_analyzed_companies_since(Friday) catches it
    companies = get_analyzed_companies_since("2025-12-12")
    print(f"  Companies since 2025-12-12: {companies}")
    
    if "NVIDIA CORPORATION" in companies:
        print("PASS: Found historical company.")
    else:
        print("FAIL: Did not find company.")
        
    # Check if a date after misses it
    date_after = "2025-12-13"
    companies_after = get_analyzed_companies_since(date_after)
    if "NVIDIA CORPORATION" not in companies_after:
        print("PASS: Correctly filtered out by date.")
    else:
        print("FAIL: Found company when it should be excluded.")

    # Clean up
    if os.path.exists("test_deduplication.db"):
        os.remove("test_deduplication.db")

if __name__ == "__main__":
    test_deduplication()

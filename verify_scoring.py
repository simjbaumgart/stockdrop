import asyncio
import time
from app.services.stock_service import stock_service
from app.services.research_service import research_service
from app.database import get_decision_points
import sqlite3

# Mock data
MOCK_STOCKS = [
    {"symbol": "TEST_SCORE_1", "price": 100.0, "change_percent": -8.0, "description": "Test Score 1"},
]

# Mock methods
def mock_get_large_cap_movers(processed_symbols=None):
    print("Mocking get_large_cap_movers returning 1 stock")
    return MOCK_STOCKS

def mock_analyze_stock(symbol, company_name, price, change_percent):
    print(f"Mock analyzing {symbol}...")
    return {
        "recommendation": "7.5", # Return a score!
        "executive_summary": f"Mock analysis for {symbol} with score 7.5",
        "detailed_report": "Detailed report...",
        "full_text": "Full text..."
    }

def mock_get_indices_data(config):
    return {}

class MockTicker:
    def __init__(self, symbol):
        self.earnings_dates = None

def mock_ticker(symbol):
    return MockTicker(symbol)

# Patch services
stock_service.get_large_cap_movers = mock_get_large_cap_movers
research_service.analyze_stock = mock_analyze_stock
from app.services.tradingview_service import tradingview_service
tradingview_service.get_indices_data = mock_get_indices_data
import yfinance as yf
yf.Ticker = mock_ticker
stock_service._fetch_market_context = lambda: {} 

# Clear previous test data
conn = sqlite3.connect("subscribers.db")
cursor = conn.cursor()
cursor.execute("DELETE FROM decision_points WHERE symbol LIKE 'TEST_SCORE%'")
conn.commit()
conn.close()

async def run_test():
    print("Triggering check_large_cap_drops...")
    await asyncio.to_thread(stock_service.check_large_cap_drops)
    
    # Verify DB
    points = get_decision_points()
    score_stock = next((p for p in points if p['symbol'] == 'TEST_SCORE_1'), None)
    
    if score_stock:
        print(f"Stock found: {score_stock['symbol']}")
        print(f"Recommendation: {score_stock['recommendation']}")
        if score_stock['recommendation'] == "7.5":
            print("SUCCESS: Score 7.5 correctly saved!")
        else:
            print(f"FAILURE: Expected 7.5, got {score_stock['recommendation']}")
    else:
        print("FAILURE: Stock not found in DB")

if __name__ == "__main__":
    asyncio.run(run_test())

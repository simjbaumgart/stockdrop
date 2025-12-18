
import os
import sys
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_rhm")

# Ensure app is in path
sys.path.append(os.getcwd())

from app.services.stock_service import stock_service
from app.services.tradingview_service import tradingview_service
from app.utils import get_git_version

def test_rhm_full():
    symbol = "RHM" # Rheinmetall usually
    # If using yfinance, RHM might be US OTC. 
    # But usually RHM in Europe context is RHM.DE.
    # Let's try to assume the system handles it or we pass a hint.
    # In check_large_cap_drops, it gets region from the screener.
    # We will fake that.
    
    print(f"--- STARTING FULL RHM TEST ---")
    
    # 1. Fake Stock Object (Simulate what check_large_cap_drops gets)
    stock_obj = {
        "symbol": symbol,
        "region": "EU", # Hinting it's European
        "exchange": "XETR", # Frankfurt/Xetra
        "price": 550.0, # Approximate
        "change_percent": -5.5, # Simulate a drop
        "pe_ratio": 25.0,
        "market_cap": 25_000_000_000,
        "sector": "Industrials",
        "description": "Rheinmetall AG"
    }
    
    # 2. Market Context
    market_context = {
        "DAX": -1.2,
        "S&P 500": -0.8
    }
    
    # 3. Fetch Technicals
    print("Fetching Technicals...")
    # technical_analysis = tradingview_service.get_technical_analysis(symbol, region="EU")
    # To save time/API issues, we can mock or just try. Let's try real first.
    # If it fails, I'll mock it.
    try:
        technical_analysis = tradingview_service.get_technical_analysis(symbol, region="EU")
    except Exception as e:
        print(f"Technical fetch failed: {e}. Using mock.")
        technical_analysis = {"indicators": {"RSI": 35, "MACD": "Bearish"}}
        
    technical_analysis["gatekeeper_findings"] = {"bb_status": "Lower Band Touch"}
    
    # 4. Fetch News
    print("Fetching News...")
    news_data = stock_service.get_aggregated_news(symbol, region="EU", exchange="XETR", company_name="Rheinmetall")
    
    # 5. Earnings Check
    is_earnings = False
    earnings_date_str = None
    
    # 6. Run Analysis
    print("Running Deep Analysis (Parallel Agents)...")
    stock_service._run_deep_analysis(
        symbol=symbol,
        price=stock_obj["price"],
        change_percent=stock_obj["change_percent"],
        stock=stock_obj,
        company_name=stock_obj["description"],
        exchange=stock_obj["exchange"],
        reasons=technical_analysis["gatekeeper_findings"],
        market_context=market_context,
        news_data=news_data,
        is_earnings=is_earnings,
        earnings_date_str=earnings_date_str,
        current_version="TEST_MODE",
        technical_analysis=technical_analysis
    )
    
    print("--- TEST COMPLETE ---")

if __name__ == "__main__":
    test_rhm_full()

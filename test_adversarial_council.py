import os
import sys
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

from app.services.research_service import research_service
from app.services.tradingview_service import tradingview_service

def test_adversarial_council():
    print("Testing Adversarial Council...")
    
    symbol = "NVDA"
    company_name = "NVIDIA Corporation"
    price = 100.0
    change_percent = -6.5
    
    # Mock Market Context
    market_context = {
        "S&P 500": -1.2,
        "Technology": -2.5,
        "Semiconductors": -3.0
    }
    
    # Mock Technical Analysis (or fetch real if possible, but let's mock for speed/reliability in test)
    # Actually let's try to fetch real to test integration
    try:
        print(f"Fetching TA for {symbol}...")
        technical_analysis = tradingview_service.get_technical_analysis(symbol)
        print("TA Fetched.")
    except Exception as e:
        print(f"Failed to fetch TA: {e}")
        technical_analysis = {}

    print("Running Analysis...")
    result = research_service.analyze_stock(
        symbol, 
        company_name, 
        price, 
        change_percent, 
        technical_analysis=technical_analysis, 
        market_context=market_context
    )
    
    print("\n--- RESULT ---")
    print(f"Recommendation: {result.get('recommendation')}")
    print(f"Executive Summary: {result.get('executive_summary')}")
    print("\n--- DEBATE TRANSCRIPT ---")
    print(f"Technician: {result.get('technician_report')[:100]}...")
    print(f"Bear: {result.get('bear_report')[:100]}...")
    print(f"Macro: {result.get('macro_report')[:100]}...")
    print("\n--- FULL TEXT ---")
    print(result.get('full_text')[:200] + "...")

if __name__ == "__main__":
    test_adversarial_council()

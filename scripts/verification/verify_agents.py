
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.services.research_service import research_service

def verify_flow():
    print("--- Verifying 5-Agent Research Flow ---")
    
    # Mock Data
    symbol = "TEST"
    company_name = "Test Corp"
    price = 100.0
    change_percent = -15.0
    
    cached_indicators = {
        "rsi": 25.0,
        "sma200": 140.0,
        "volume": 5000000,
        "close": 100.0,
        "pe_ratio": 12.5,
        "debt_to_equity": 2.5
    }
    
    technical_sheet = json.dumps(cached_indicators, indent=2)
    
    news_headlines = """
    - 2024-10-01: Test Corp CEO resigns amid accounting probe.
    - 2024-10-02: Analysts downgrade Test Corp to Sell.
    """
    
    market_context = {
        "S&P 500": -0.5,
        "Sector (Tech)": -1.2
    }
    
    print("\n[Input Data]")
    print(f"Symbol: {symbol}")
    print(f"Technical Sheet:\n{technical_sheet}")
    print(f"Headlines:\n{news_headlines}")
    
    print("\n[Executing Analysis...]")
    try:
        result = research_service.analyze_stock(
            symbol,
            company_name,
            price,
            change_percent,
            technical_sheet=technical_sheet,
            news_headlines=news_headlines,
            market_context=market_context
        )
        
        print("\n[Result]")
        print(f"Recommendation: {result.get('recommendation')}")
        print(f"Confidence Score: {result.get('score', 'N/A')}")
        print("-" * 20)
        print("Executive Summary:")
        print(result.get('executive_summary'))
        print("-" * 20)
        print("Detailed Report Preview (First 500 chars):")
        print(result.get('detailed_report')[:500])
        print("-" * 20)
        print("Transcript Keys Present:", [k for k in result.keys() if 'report' in k])
        
    except Exception as e:
        print(f"FAILED: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    verify_flow()


import sys
import os
import json
from unittest.mock import MagicMock

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.services.stock_service import stock_service
from app.services.research_service import research_service
from app.services.tradingview_service import tradingview_service

def verify_agent_data_flow(symbol):
    print(f"\n--- Verifying Agent Data Flow for {symbol} ---")
    
    # 1. Fetch Filings
    print("1. Fetching Filings...")
    filings_text = stock_service.get_latest_filing_text(symbol)
    print(f"   > Filings Text Length: {len(filings_text)}")
    
    # 2. Fetch Transcript
    print("2. Fetching Transcript...")
    transcript_text = stock_service.get_latest_transcript(symbol)
    print(f"   > Transcript Text Length: {len(transcript_text)}")
    
    # 3. Simulate Technicals & Market Context
    tech_sheet = json.dumps({
        "ticker": symbol,
        "close": 150.0,
        "sma200": 140.0,
        "rsi": 45,
        "volume": 5000000,
        "avg_volume": 4000000,
        "market_cap": 2500000000000,
        "pe_ratio": 30,
        "forward_pe": 28,
        "debt_to_equity": 1.5,
        "earnings_date": None
    })
    market_context = {"S&P 500": 0.005, "Sector": -0.01}
    news_headlines = "- News 1: Good earnings\n- News 2: New product"
    
    # 4. Call Research Service
    print("3. Calling Research Service (analyze_stock)...")
    # We will mock the actual agent calls if we don't want to burn API credits, 
    # OR we can let it run to verifying prompts are constructed correctly.
    # To save money/time, let's just inspect the prompts constructed, IF we could.
    # But analyze_stock is a black box.
    # Let's run it. Mocking 'model.generate_content' to print the prompt would be cool.
    
    # If model is None (no API key), we mock it entirely to force the logic flow
    if research_service.model is None:
        print("   > ResearchService model is None, injecting Mock object.")
        research_service.model = MagicMock()
    
    original_generate = research_service.model.generate_content
    
    def mock_generate(prompt):
        print(f"\n[MOCK GENERATE] Prompt length: {len(prompt)}")
        if "FILINGS SNIPPETS" in prompt:
            print("   > ✅ PROMPT CONTAINS FILINGS DATA")
            # print snippet
            idx = prompt.find("FILINGS SNIPPETS")
            print(f"   > Snippet: {prompt[idx:idx+100]}...")
        else:
            print("   > ❌ PROMPT MISSING FILINGS DATA (Might be Sentinel/Context agent)")
            
        if "TRANSCRIPT SNIPPETS" in prompt:
             print("   > ✅ PROMPT CONTAINS TRANSCRIPT DATA")
        
        # Return a dummy response object
        class MockResponse:
            text = "Mock Agent Response"
        return MockResponse()
        
    # Inject Mock
    research_service.model.generate_content = mock_generate
    
    try:
        result = research_service.analyze_stock(
            symbol=symbol,
            company_name="Apple Inc.",
            price=150.0,
            change_percent=-2.5,
            technical_sheet=tech_sheet,
            news_headlines=news_headlines,
            market_context=market_context,
            filings_text=filings_text,
            transcript_text=transcript_text
        )
        print("\nAnalysis Result Keys:", result.keys())
        
    except Exception as e:
        print(f"Error during analysis: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Restore (not strictly necessary for script but good practice)
        research_service.model.generate_content = original_generate

if __name__ == "__main__":
    verify_agent_data_flow("AAPL")

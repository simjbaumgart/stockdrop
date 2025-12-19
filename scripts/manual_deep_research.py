import sys
import os
import time
import json
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.stock_service import StockService
from app.services.deep_research_service import deep_research_service

def run_manual_deep_research(symbol: str):
    print(f"Starting Manual Deep Research for {symbol}...")
    stock_service = StockService()
    
    # 1. Fetch Basic Stock Details
    print("Fetching Stock Details...")
    details = stock_service.get_stock_details(symbol)
    if not details:
        print(f"Error: Could not fetch details for {symbol}")
        return

    price = details.get('price', 100.0)
    # Mocking change percent if not available, or forcing a drop for test
    # But usually we want real data.
    # Let's see if we can get real change
    # Alpaca details has prev close, we can calc change
    prev_close = details.get('previous_close', price)
    if prev_close:
        change_percent = ((price - prev_close) / prev_close) * 100
    else:
        change_percent = -5.0 # Fallback mock drop
    
    print(f"Price: {price}, Change: {change_percent:.2f}%")
    
    # 2. Fetch News
    print("Fetching News...")
    # Using 'US' as default region for manual run
    news_data = stock_service.get_aggregated_news(symbol, region="US", company_name=symbol)
    print(f"Fetched {len(news_data)} news items.")
    
    # 3. Fetch Technicals
    print("Fetching Technical Analysis...")
    from app.services.tradingview_service import tradingview_service
    technical_data = tradingview_service.get_technical_analysis(symbol, region="US")
    # Add dummy gatekeeper findings
    technical_data["gatekeeper_findings"] = {"status": "Manual Run"}
    
    # 4. Check Earnings
    print("Checking Earnings...")
    # Mocking transcript for now unless we have a method to fetch it easily without full flow 
    # Actually StockService has _check_earnings_proximity but getting transcript text is harder
    # usually done in _run_deep_analysis by calling other services or passed in.
    # We will pass empty transcript or try to fetch from DefeatBeta if available
    transcript_text = ""
    transcript_date = None
    
    # Try to load local mock/cache transcript if exists for testing
    defeatbeta_path = f"data/DefeatBeta_data/{symbol}/transcript.txt"
    if os.path.exists(defeatbeta_path):
        with open(defeatbeta_path, 'r') as f:
            transcript_text = f.read()
            print("Loaded transcript from DefeatBeta cache.")

    # 5. Execute Deep Research
    print("\n>>> Triggering Deep Research Agent (Standalone)...")
    result = deep_research_service.execute_deep_research(
        symbol=symbol,
        raw_news=news_data,
        technical_data=technical_data,
        drop_percent=change_percent,
        transcript_text=transcript_text,
        transcript_date=transcript_date,
        transcript_warning="Manual Run"
    )
    
    if result:
        print("\n=== Deep Research Result ===")
        # print(json.dumps(result, indent=2)) # Reduce noise in console
        print(f"Verdict: {result.get('verdict')}")
        
        # Explicitly save to file since we bypassed the queue wrapper
        try:
            deep_research_service._save_result_to_file(symbol, result)
            print(f"Successfully saved JSON and PDF for {symbol}.")
        except Exception as e:
            print(f"Error saving files: {e}")
        
        print("\nCheck 'data/deep_research_reports/' for JSON and PDF output.")
    else:
        print("\nDeep Research Failed or Timed Out.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        ticker = sys.argv[1]
    else:
        ticker = "TSLA"
    
    run_manual_deep_research(ticker)

import sys
import os
import json
import logging
from datetime import datetime

# Add app to path
sys.path.append(os.getcwd())

from app.services.research_service import research_service

# Configure logging to see output
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_positive_scenario():
    print("\n" + "="*60)
    print("RUNNING POSITIVE SCENARIO: 'Golden Techs + Record Earnings'")
    print("="*60)
    
    ticker = "GOOD_CO"
    
    # 1. Good Technicals (Dip in Uptrend)
    indicators = {
        "close": 150.0,
        "change": -8.0, 
        "change_percent": -5.06, # ~5% drop
        "sma200": 130.0, # Price > SMA200 (Long Term Uptrend)
        "sma50": 145.0, # Price > SMA50 (Medium Term Support)
        "rsi": 32.5, # Oversold (<35) - Buy Signal
        "macd_hist": -0.5, # Mild negative momentum, possibly turning
        "bb_lower": 148.0, # Price near lower band (Bounce zone)
        "volume": 5000000,
        "recommend_all": "BUY"
    }
    
    # 2. Very Good News
    news_items = [
        {
            "datetime": datetime.now().timestamp(),
            "datetime_str": "Today 08:30 AM",
            "provider": "Benzinga/Massive",
            "source": "Benzinga",
            "headline": "GOOD_CO Reports Record Q4 Revenue, Beats Expectations by 20%",
            "summary": "GOOD_CO just released Q4 earnings showing record growth. Revenue up 40% YoY. CEO announced a new $5B buyback program.",
            "content": "GOOD_CO smashed analyst estimates today... Revenue $10B vs $8B exp... AI division grew 200%... Stock dropped 5% initially on profit taking but analysts call it a 'Golden Opportunity'..."
        },
        {
            "datetime": datetime.now().timestamp() - 3600,
            "datetime_str": "Today 07:45 AM",
            "provider": "Alpha Vantage",
            "source": "Reuters",
            "headline": "GOOD_CO Raises Full Year Guidance",
            "summary": "The company sees no slowdown in demand. Raised FY2025 outlook significantly.",
            "content": "Full year guidance raised to $50 EPS..."
        }
    ]
    
    # 3. Optimistic Transcript
    transcript_text = """
    Operator: Welcome to the Q4 Earnings Call.
    CEO: Thank you. We are thrilled to report the best quarter in our history. Demand for our new product is unprecedented. 
    Analyst: Why is the stock down today?
    CFO: Likely just noise or profit taking after our 50% run up this year. Fundamentals are stronger than ever. We are buying back shares aggressively at these levels.
    """
    
    raw_data = {
        "change_percent": -5.06,
        "indicators": indicators,
        "news_items": news_items,
        "transcript_text": transcript_text
    }
    
    # Run Analysis
    result = research_service.analyze_stock(ticker, raw_data)
    
    _print_result(result)

def run_negative_scenario():
    print("\n" + "="*60)
    print("RUNNING NEGATIVE SCENARIO: 'Falling Knife + Accounting Scandal'")
    print("="*60)
    
    ticker = "BAD_CO"
    
    # 1. Bad Technicals (Breakdown)
    indicators = {
        "close": 45.0,
        "change": -8.0, 
        "change_percent": -15.1, # Massive drop
        "sma200": 80.0, # Price << SMA200 (Long Term Downtrend)
        "sma50": 60.0, # well below 50
        "rsi": 25.0, # Oversold, but possibly "locked" low
        "macd_hist": -5.0, # Accelerating negative momentum
        "bb_lower": 50.0, # Price broke below lower band (Volatility expansion downwards)
        "volume": 25000000, # Huge selling volume
        "recommend_all": "STRONG_SELL"
    }
    
    # 2. Negative News
    news_items = [
        {
            "datetime": datetime.now().timestamp(),
            "datetime_str": "Today 09:00 AM",
            "provider": "Benzinga/Massive",
            "source": "Benzinga",
            "headline": "BAD_CO CFO Resigns Amid Accounting Irregularities Probe",
            "summary": "BAD_CO shares are plummeting after the CFO resigned unexpectedly. The SEC has launched a probe into revenue recognition.",
            "content": "Panic selling in BAD_CO... Auditors refused to sign off on Q4 results... Potential delisting warning..."
        },
        {
            "datetime": datetime.now().timestamp() - 7200,
            "datetime_str": "Today 07:00 AM",
            "provider": "Finnhub",
            "source": "CNBC",
            "headline": "Analysts Double downgrade BAD_CO to SELL",
            "summary": "Goldman and Morgan Stanley both cut price targets to $20.",
            "content": "Uninvestable at this stage... massive uncertainty..."
        }
    ]
    
    # 3. Defensive Transcript
    transcript_text = """
    Operator: Welcome.
    CEO: We have some challenges. Our CFO has decided to pursue other opportunities. We are cooperating with the SEC.
    Analyst: Is the revenue number real?
    CEO: I cannot comment on ongoing investigations. Next question.
    Analyst: Are you facing liquidity issues?
    CEO: We are exploring all options to strengthen our balance sheet.
    """
    
    raw_data = {
        "change_percent": -15.1,
        "indicators": indicators,
        "news_items": news_items,
        "transcript_text": transcript_text
    }
    
    # Run Analysis
    result = research_service.analyze_stock(ticker, raw_data)
    
    _print_result(result)

def _print_result(result):
    print("\n--- FINAL SCENARIO RESULT ---")
    print(f"RECOMMENDATION: {result.get('recommendation')}")
    print(f"SCORE: {result.get('score')}")
    print(f"SUMMARY: {result.get('executive_summary')}")
    
    print("\n[DEEP REASONING REPORT]:")
    print(result.get('deep_reasoning_report', 'N/A'))
    
    print("\n[Technical Agent Report]:")
    print(result.get('technician_report', 'N/A')[:300] + "...") # Snippet
    
    print("\n[News Agent Report]:")
    print(result.get('macro_report', 'N/A')[:300] + "...") # Snippet

if __name__ == "__main__":
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
        if "pos" in mode:
            run_positive_scenario()
        elif "neg" in mode:
            run_negative_scenario()
        else:
            print("Usage: python tests/test_scenarios.py [positive|negative]")
    else:
        # Default run both
        run_positive_scenario()
        run_negative_scenario()

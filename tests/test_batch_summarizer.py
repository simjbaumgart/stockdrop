
import os
import sys
import json
import logging

# Ensure app imports work
sys.path.append(os.getcwd())

from app.services.deep_research_service import deep_research_service

# Mock Council Report with MASSIVE fluff
MOCK_REPORT = {
    "final_verdict": "STRONG_BUY",
    "bull_bear_debate": {
        "bull_case": "The company is undervalued by 50% due to overreaction.",
        "bear_case": "However, interest rates might hurt them."
    },
    "technical_analysis": {
        "verdict": "BULLISH",
        "details": "RSI is 30. MACD is crossing over. " + ("BLAH " * 1000) # Fluff
    },
    "seeking_alpha_data": {
        "html_blob": "<div>" + ("<p>Massive HTML Content</p>" * 5000) + "</div>" # 100KB+ fluff
    },
    "fundamental_analysis": {
        "conclusion": "Financials are solid.",
        "raw_metrics": {"pe": 15, "eps": 1.2}
    },
    "transcripts": "CEO: We are doing great... " + ("Talk " * 5000)
}

def test_summarizer():
    print("Running Batch Summarizer Test...")
    
    report_json = json.dumps(MOCK_REPORT)
    original_len = len(report_json)
    
    summary = deep_research_service._summarize_report_context(report_json)
    summary_len = len(summary)
    
    print("-" * 40)
    print(f"ORIGINAL JSON SIZE: {original_len} bytes")
    print(f"SUMMARY SIZE:       {summary_len} bytes")
    print(f"REDUCTION:          {100 - (summary_len/original_len*100):.2f}%")
    print("-" * 40)
    print("SUMMARY CONTENT:")
    print(summary)
    print("-" * 40)
    
    # Assertions
    if "STRONG_BUY" in summary:
        print("[PASS] Verdict preserved.")
    else:
        print("[FAIL] Verdict missing.")
        
    if "undervalued by 50%" in summary:
        print("[PASS] Bull case preserved.")
    else:
        print("[FAIL] Bull case missing.")
        
    if "Massive HTML Content" not in summary:
        print("[PASS] Seeking Alpha fluff excluded.")
    else:
        print("[FAIL] Fluff still present.")

    if summary_len < 5000:
        print("[PASS] Summary is concise.")
    else:
        print("[FAIL] Summary is still too large.")

if __name__ == "__main__":
    test_summarizer()

import sys
import os
import json
import logging
import concurrent.futures

# Add project root to path
sys.path.append(os.getcwd())

from app.services.research_service import research_service
from app.models.market_state import MarketState

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def verify_new_council_2(ticker="TSLA"):
    print(f"\n--- Verifying New Council 2 Logic for {ticker} ---\n")
    
    # 1. Mock Market State
    state = MarketState(ticker=ticker, date="2025-01-27")
    
    # 2. Mock Council 1 Reports
    state.reports = {
        "technical": "RSI is 30 (Oversold).",
        "news": "- Earnings Missed by 5% due to supply chain.",
        "competitive": "Competitors are gaining market share.",
        "market_sentiment": "Fear is high."
    }
    
    drop_str = "-5.0%"

    # 3. Run Phase 2
    print("Running _run_bull_bear_perspectives...")
    research_service._run_bull_bear_perspectives(state, drop_str)
    
    # 4. Inspect Results
    bull_report = state.reports.get('bull', '')
    bear_report = state.reports.get('bear', '')
    quant_report = state.reports.get('quantitative_impact')

    print("\n--- Verification Results ---")
    
    # Check for Quant Agent removal
    if quant_report is None:
        print("[SUCCESS] Quantitative Impact Agent report IS None (Correctly removed).")
    else:
        print(f"[FAIL] Quantitative Impact Agent report EXISTS (Should be removed): {quant_report[:50]}...")

    # Check Bull Report for Quant Estimation
    if "QUANTITATIVE ESTIMATION" in bull_report or "Valuation Impact" in bull_report:
        print(f"[SUCCESS] Bull Report contains Quantitative Estimation sections.")
        # print(bull_report[-500:]) # Print tail to see the new section
    else:
        print("[FAIL] Bull Report MISSING Quantitative Estimation sections.")
        print(f"DEBUG Bull Report: {bull_report[:200]}...")

    # Check Bear Report for Quant Estimation
    if "QUANTITATIVE ESTIMATION" in bear_report or "Valuation Impact" in bear_report:
        print(f"[SUCCESS] Bear Report contains Quantitative Estimation sections.")
    else:
        print("[FAIL] Bear Report MISSING Quantitative Estimation sections.")

if __name__ == "__main__":
    verify_new_council_2()

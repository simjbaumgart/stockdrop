import sys
import os
import json
import logging

# Add project root to path
sys.path.append(os.getcwd())

from app.services.research_service import research_service
from app.models.market_state import MarketState

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_earnings_impact(ticker="TSLA"):
    print(f"\n--- Testing Earnings Impact Agent for {ticker} ---\n")
    
    # 1. Mock Market State
    state = MarketState(ticker=ticker, date="2025-01-03")
    
    # 2. Mock Council 1 Reports (Input Data)
    # We provide enough context for the Quant agent to work on
    state.reports = {
        "technical": "RSI is 25 (Oversold). Heavy volume selling. Support at $180 broke.",
        "news": """
        - TESLA MISSES DELIVERY ESTIMATES BY 5% (Source: Reuters)
        - MARGINS CONTRACT TO 15% DUE TO PRICE CUTS (Source: CNBC)
        - ANALYSTS CUT PRICE TARGETS ACROSS THE BOARD
        """,
        "competitive": """
        COMPETITIVE LANDSCAPE:
        1. BYD: Growing 20% YoY, margins stable at 20%.
        2. RIVIAN: Still loss making but production ramping up.
        3. FORD: EV division losing money.
        
        Industry Average PE is approx 25x.
        """,
        "market_sentiment": "Bearish on EV sector due to demand concerns."
    }
    
    # 3. Define Drop Percent
    drop_str = "-8.5%"

    # 4. Run Phase 2 (Bull/Bear/Quant)
    # This calls _run_bull_bear_perspectives which calls the Quant Agent
    print("Running Agents (Simulated Council 2)...")
    research_service._run_bull_bear_perspectives(state, drop_str)
    
    print("\n--- Test Complete ---")
    print("Check console output above for [Quantitative Impact Agent] details.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, default="TSLA")
    args = parser.parse_args()
    
    test_earnings_impact(args.ticker)

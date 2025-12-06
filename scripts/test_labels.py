import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.research_service import research_service
from app.models.market_state import MarketState

# Mock Data (Minimal)
raw_data = {
    "metrics": {"pe_ratio": 10.0},
    "indicators": {"RSI": 25.0}, # Oversold -> Likely Buy/Strong Buy
    "news_items": [],
    "transcript_text": "Growth is accelerating.",
    "market_context": {}
}

print("Running Refined Fund Manager Test...")
try:
    result = research_service.analyze_stock("TEST", raw_data)
    print("\n--- ANALYSIS COMPLETE ---")
    print(f"Recommendation: {result['recommendation']}")
    print(f"Score: {result['score']}")
    
    valid_labels = ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]
    if result['recommendation'].upper() in valid_labels:
        print(f"✅ Label '{result['recommendation']}' is valid.")
    else:
        print(f"⚠️ Label '{result['recommendation']}' might be invalid or legacy.")

except Exception as e:
    print(f"FAILED: {e}")

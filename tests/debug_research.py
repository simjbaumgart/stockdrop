import sys
import os
from dotenv import load_dotenv

# Load .env before importing services that initialize on import
load_dotenv()

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.research_service import research_service
import logging

# Configure logging to see the error
logging.basicConfig(level=logging.INFO)

def test_research():
    print("Testing Research Service with Gemini 3 Pro...")
    
    # Check for API Key
    if not os.getenv("GEMINI_API_KEY"):
        print("\n[WARNING] GEMINI_API_KEY not found in environment variables.")
        print("The service will return MOCK DATA. To test the real API, export the key first:")
        print("export GEMINI_API_KEY='your_key_here'")
        print("-" * 50)

    # Mock data
    symbol = "NVDA"
    price = 120.0
    change_percent = -8.5
    
    print(f"Simulating drop for {symbol}: {change_percent}% at ${price}")
    
    # Force usage check to pass
    research_service.MAX_DAILY_REPORTS = 100
    
    result = research_service.analyze_stock(symbol, price, change_percent)
    
    print("\n" + "="*20 + " RESULT " + "="*20)
    print(f"Recommendation: {result.get('recommendation')}")
    print(f"Executive Summary: {result.get('executive_summary')}")
    print("-" * 50)
    print(f"Detailed Report Preview: {result.get('detailed_report')[:200]}...")
    print("="*50)

if __name__ == "__main__":
    test_research()

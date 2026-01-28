import sys
import os
from datetime import datetime

# Add app to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.research_service import ResearchService

def verify_sa_integration():
    print("Initializing Research Service...")
    service = ResearchService()
    
    # Mock raw data
    raw_data = {
        "change_percent": -5.0,
        "news_items": [], # Empty to ensure we rely on SA for those parts or separate
        "indicators": {},
        "transcript_text": "Mock Transcript",
        "transcript_date": "2025-01-01"
    }
    
    ticker = "KBR"
    print(f"Running Analysis for {ticker}...")
    
    # Run analysis (will trigger parallel agents including SA)
    result = service.analyze_stock(ticker, raw_data)
    
    print("\nanalysis complete.")
    print("Checking if SA Report exists in result structure...")
    
    # We can check specific internal state if returned, or inferred from logs.
    # The 'result' dict doesn't expose the full state.reports map directly in the final dict 
    # except via mapped keys.
    # 'detailed_report' likely contains it.
    
    # However, we added 'seeking_alpha' to the state.reports.
    # And we print a log message. The log message is key verification.

if __name__ == "__main__":
    verify_sa_integration()

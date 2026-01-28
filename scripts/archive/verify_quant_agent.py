import sys
import os
from datetime import datetime

# Add app to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.research_service import ResearchService

def verify_quant_agent():
    print("Initializing Research Service...")
    service = ResearchService()
    
    # Mock raw data
    raw_data = {
        "change_percent": -10.0,
        "news_items": [
            {
                "datetime_str": "2025-12-29 10:00",
                "headline": "KBR Misses Earnings by 20%",
                "provider": "Benzinga",
                "content": "KBR reported EPS of $0.50 vs expected $0.65. Revenue also missed. Guidance lowered for 2026."
            }
        ],
        "indicators": {"RSI": 30},
        "transcript_text": "We faced significant headwinds in our government services division. We expect this to impact margins for the next two quarters.",
        "transcript_date": "2025-12-29"
    }
    
    ticker = "KBR"
    print(f"Running Analysis for {ticker}...")
    
    # Run analysis (will trigger parallel agents including Quant Agent)
    result = service.analyze_stock(ticker, raw_data)
    
    print("\nanalysis complete.")
    
    # Check if Quant Report is present
    quant_report = result.get('quantitative_impact_report')
    
    if quant_report and isinstance(quant_report, str) and len(quant_report) > 50:
        print("[SUCCESS] Quantitative Impact Report found.")
        print("-" * 50)
        print(quant_report)
        print("-" * 50)
    else:
        print(f"[FAILURE] Quantitative Impact Report missing or empty. Got: {quant_report}")

if __name__ == "__main__":
    verify_quant_agent()

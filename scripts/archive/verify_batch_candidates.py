
import sys
import os
sys.path.append(os.getcwd())
from app.database import get_unbatched_candidates_by_date
from datetime import datetime

def test_fetch():
    # Test for today and yesterday as per user table dates
    dates = ["2025-12-30", "2025-12-31"]
    
    print("Checking for unbatched candidates...")
    
    for date_str in dates:
        candidates = get_unbatched_candidates_by_date(date_str)
        print(f"\nDate: {date_str}")
        print(f"Found {len(candidates)} candidates:")
        for c in candidates:
            print(f" - {c['symbol']} (Rec: {c['recommendation']}, Verdict: {c['deep_research_verdict']}, Score: {c['ai_score']})")

if __name__ == "__main__":
    test_fetch()

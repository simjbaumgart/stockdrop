import sys
import os

# Ensure app module is found
sys.path.append(os.getcwd())

from app.services.benzinga_service import benzinga_service
import json

def verify_update():
    print("Fetching Benzinga news (using mock or real API depending on env)...")
    
    # Use a popular ticker likely to have news
    results = benzinga_service.get_company_news("AAPL")
    
    if not results:
        print("No results returned. Check API key or ticker.")
        return

    print(f"Got {len(results)} articles.")
    
    if results:
        first = results[0]
        print("\n--- FIRST PROCESSED ARTICLE ---")
        print(f"Headline: {first['headline']}")
        print(f"Content Field (Should contain Insights/Keywords):\n{'-'*40}")
        print(first['content'])
        print(f"{'-'*40}")
        
        if "Insight Analysis:" in first['content']:
            print("\nSUCCESS: Insights found in content.")
        else:
            print("\nWARNING: No insights text found in content (might be absent for this article).")

if __name__ == "__main__":
    verify_update()

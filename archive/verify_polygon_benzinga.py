
import requests
import json
from datetime import datetime

# API Key from app/services/polygon_service.py
API_KEY = "MX8dLTzDgcUHHLh6GNE12iOzitcS_HCH"
BASE_URL = "https://api.polygon.io/v2/reference/news"

def verify_benzinga_news(symbol="GOOG"):
    print(f"Fetching news for {symbol} to check for Benzinga access...")
    
    params = {
        "apiKey": API_KEY,
        "ticker": symbol,
        "limit": 50, # Get a good chunk to increase odds of finding Benzinga
        "order": "desc",
        "sort": "published_utc"
    }
    
    try:
        response = requests.get(BASE_URL, params=params, timeout=15)
        
        if response.status_code != 200:
            print(f"Error: Status {response.status_code}")
            print(response.text)
            return

        data = response.json()
        results = data.get("results", [])
        
        print(f"Found {len(results)} articles for {symbol}.")
        
        benzinga_count = 0
        publishers = {}
        
        print("\n--- Recent News Sources ---")
        for item in results:
            publisher = item.get("publisher", {}).get("name", "Unknown")
            publishers[publisher] = publishers.get(publisher, 0) + 1
            
            if publisher.lower() == "benzinga":
                benzinga_count += 1
                print(f"[BENZINGA] {item.get('published_utc')} - {item.get('title')}")
                print(f"   URL: {item.get('article_url')}")
                
                # Check for content depth
                description = item.get("description", "")
                print(f"   Description Length: {len(description)}")
                print(f"   Available Keys: {list(item.keys())}")
                if benzinga_count == 1:
                     print(f"   Sample Description: {description[:200]}...")

        
        print("\n--- Publisher Summary ---")
        for pub, count in publishers.items():
            print(f"{pub}: {count}")
            
        if benzinga_count > 0:
            print(f"\nSUCCESS: Found {benzinga_count} Benzinga articles! You have access.")
        else:
            print(f"\nNo Benzinga articles found in the last {len(results)} items. You might not have access, or they haven't published recently for this ticker.")
            
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    verify_benzinga_news()

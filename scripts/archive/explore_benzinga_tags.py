
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.benzinga_service import benzinga_service

def explore_tags():
    symbol = "NVDA" 
    print(f"Fetching news for {symbol} to explore tags/metadata...")
    
    # We need the RAW response, but get_company_news returns processed dicts.
    # However, BenzingaService._process_news might drop fields.
    # To really EXPLORE, we should use the private method or replicate the request.
    # Or we can see what fields are available in processed item if we preserved them.
    # Let's inspect the keys of the PROCESSED items first, as that's what we have easy access to.
    # But wait, the user wants "available over the API", implying raw fields I might not be using.
    
    # So I should use the verify_massive_api approach but print all keys.
    # Or just subclass/use BenzingaService but print raw results.
    
    # Let's do a direct request using the same logic as BenzingaService to get raw JSON.
    # But I can't easily import the private attributes.
    # I will copy the minimal logic to fetch raw data.
    
    import requests
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())
    
    api_key = os.getenv("BENZINGA_API_KEY")
    if not api_key:
        # Fallback to hardcoded key found in verify script if env missing
        api_key = "MX8dLTzDgcUHHLh6GNE12iOzitcS_HCH"
        
    url = "https://api.polygon.io/v2/reference/news"
    params = {
        "ticker": symbol,
        "limit": 5,
        "sort": "published_utc",
        "order": "desc"
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    
    print(f"Requesting {url}...")
    resp = requests.get(url, params=params, headers=headers)
    
    if resp.status_code == 200:
        data = resp.json()
        results = data.get("results", [])
        print(f"Got {len(results)} raw items.")
        
        if results:
            first = results[0]
            print("\n--- AVAILABLE KEYS IN RAW API RESPONSE ---")
            for k in first.keys():
                print(f"- {k}")
                
            print("\n--- TAG-LIKE FIELDS CONTENT ---")
            # Check for common tag fields
            potential_fields = ["keywords", "tags", "insights", "tickers", "topics", "channels", "publisher"]
            
            for field in potential_fields:
                if field in first:
                    print(f"\n[{field}]:")
                    print(json.dumps(first[field], indent=2))
        else:
            print("No results found.")
            
    else:
        print(f"Error: {resp.status_code} - {resp.text}")

if __name__ == "__main__":
    explore_tags()

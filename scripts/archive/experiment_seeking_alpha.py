import requests
import json

API_KEY = "5477117586msh5d353b0362a0a36p119d3fjsn29a2acf5e2d8"
HOST = "seeking-alpha.p.rapidapi.com"

headers = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": HOST
}

def call_endpoint(endpoint, params=None):
    url = f"https://{HOST}/{endpoint}"
    print(f"Calling {url} with params {params}...")
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error calling {endpoint}: {e}")
        if 'response' in locals():
            print(f"Response text: {response.text}")
        return None

def main():
    print("--- Experiment: Seeking Alpha API    # 1. Get ID for Fraport")
    
    symbol = "Fraport"
    print(f"\n1. Searching for symbol '{symbol}'...")
    autocomplete = call_endpoint("auto-complete", {"term": symbol})
    
    goog_ticker = None
    goog_id = None
    if autocomplete and 'symbols' in autocomplete:
        print(f"Found {len(autocomplete['symbols'])} matches.")
        for item in autocomplete['symbols']:
            print(f"   Match: {item.get('name')} (ID: {item.get('id')}, Ticker: {item.get('name')})")
            if not goog_id:
                goog_id = item.get('id')
                goog_ticker = item.get('name') # 'name' seems to be the ticker in this API
    
    if not goog_id:
        print(f"Could not find ID for {symbol}")
        return
    
    print(f"\nTargeting: Ticker={goog_ticker}, ID={goog_id}")
    
    # Try different endpoints for News
    print(f"\n2. Fetching News...")
    
    # Attempt A: news/v2/list-by-symbol with Ticker
    print(f"   Attempting 'news/v2/list-by-symbol' with id='{goog_ticker}'...")
    news_list = call_endpoint("news/v2/list-by-symbol", {"id": goog_ticker, "size": 3})
    
    # Attempt B: news/v2/list with numeric ID
    if not news_list or 'errors' in news_list:
        print(f"   Failed. Attempting 'news/v2/list' with id={goog_id}...")
        news_list = call_endpoint("news/v2/list", {"id": goog_id, "size": 3})

    if news_list and 'data' in news_list:
        print(f"   Got {len(news_list['data'])} news items.")
        for item in news_list['data'][:3]:
            attrs = item.get('attributes', {})
            print(f"   - [{item.get('id')}] {attrs.get('title')} ({attrs.get('publishOn')})")
    else:
        print("   Failed to get news list.")
        if news_list: print(json.dumps(news_list, indent=2))
    
    # Try different endpoints for Analysis
    print(f"\n3. Fetching Analysis...")
    
    # Attempt A: analysis/v2/list with numeric ID (Worked for GOOG)
    print(f"   Attempting 'analysis/v2/list' with id='{goog_ticker}'...")
    analysis_list = call_endpoint("analysis/v2/list", {"id": goog_ticker, "size": 3})
    
    if not analysis_list or 'errors' in analysis_list:
         print(f"   Failed. Attempting 'analysis/v2/list' with id={goog_id}...")
         analysis_list = call_endpoint("analysis/v2/list", {"id": goog_id, "size": 3})
    
    if analysis_list and 'data' in analysis_list:
        print(f"   Got {len(analysis_list['data'])} analysis items.")
        for item in analysis_list['data'][:3]:
            attrs = item.get('attributes', {})
            print(f"   - [{item.get('id')}] {attrs.get('title')} ({attrs.get('publishOn')})")
    else:
        print("   Failed to get analysis list with tested params.")
        if analysis_list: print(json.dumps(analysis_list, indent=2))


if __name__ == "__main__":
    main()

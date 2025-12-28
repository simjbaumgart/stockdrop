import requests
import json
import os

API_KEY = "5477117586msh5d353b0362a0a36p119d3fjsn29a2acf5e2d8"
HOST = "seeking-alpha.p.rapidapi.com"
OUTPUT_DIR = "experiment_data/refinement"

headers = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": HOST
}

def call_endpoint(endpoint, params=None):
    url = f"https://{HOST}/{endpoint}"
    print(f"Calling {url} with params {params}...")
    try:
        response = requests.get(url, headers=headers, params=params)
        return response.json()
    except Exception as e:
        print(f"Error calling {endpoint}: {e}")
        return None

def save_json(data, filename):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, filename), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def main():
    # 1. Test Press Release Details
    # Known PR ID for KBR: 20343861
    pr_id = "20343861"
    print(f"\n--- Testing Press Release Details for {pr_id} ---")
    
    # Try 1: press-releases/get-details
    res1 = call_endpoint("press-releases/get-details", {"id": pr_id})
    save_json(res1, "pr_details_attempt1.json")
    
    # Try 2: press-releases/v2/get-details
    res2 = call_endpoint("press-releases/v2/get-details", {"id": pr_id})
    save_json(res2, "pr_details_attempt2.json")
    
    # Try 3: analysis/v2/get-details (we know this failed with 404, but double checking)
    res3 = call_endpoint("analysis/v2/get-details", {"id": pr_id})
    save_json(res3, "pr_details_attempt3.json")


    # 2. Test News with Numeric ID vs String
    kbr_id = "2787"
    print(f"\n--- Testing News for KBR (ID: {kbr_id}) ---")
    
    news_numeric = call_endpoint("news/v2/list-by-symbol", {"id": kbr_id, "size": 3})
    save_json(news_numeric, "news_numeric_id.json")
    
    if news_numeric and 'data' in news_numeric:
        print("News with Numeric ID:")
        for item in news_numeric['data']:
            print(f"  - {item['attributes'].get('title')} (ID: {item['id']})")

if __name__ == "__main__":
    main()

import requests
import json
import os

API_KEY = "5477117586msh5d353b0362a0a36p119d3fjsn29a2acf5e2d8"
HOST = "seeking-alpha.p.rapidapi.com"
OUTPUT_DIR = "experiment_data/refinement_v2"

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
    # 1. Get a valid News ID for KBR (using string ticker as numeric failed)
    print("\n--- Fetching News List for KBR (string ID) ---")
    news_list = call_endpoint("news/v2/list-by-symbol", {"id": "KBR", "size": 3})
    
    if news_list and 'data' in news_list:
        first_item = news_list['data'][0]
        news_id = first_item['id']
        news_title = first_item['attributes'].get('title')
        print(f"Found News item: {news_title} (ID: {news_id})")
        
        # 2. Test news/get-details
        print(f"\n--- Testing news/get-details for {news_id} ---")
        news_details = call_endpoint("news/get-details", {"id": news_id})
        save_json(news_details, f"news_details_{news_id}.json")
    else:
        print("Failed to get news list for KBR.")

    # 3. Test Wall Street Breakfast
    print("\n--- Testing Wall Street Breakfast ---")
    # Try the likely endpoints
    wsb_1 = call_endpoint("articles/list-wall-street-breakfast", {"size": 1})
    save_json(wsb_1, "wsb_attempt_articles.json")
    
    if wsb_1 and 'errors' in wsb_1:
         wsb_2 = call_endpoint("news/list-wall-street-breakfast", {"size": 1})
         save_json(wsb_2, "wsb_attempt_news.json")

if __name__ == "__main__":
    main()

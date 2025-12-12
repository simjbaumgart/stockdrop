import os
import requests
import json
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

API_KEY = os.getenv("BENZINGA_API_KEY")
URL = "https://api.polygon.io/v2/reference/news"

def inspect_response():
    if not API_KEY:
        print("Error: BENZINGA_API_KEY (Polygon Key) not found.")
        return

    print(f"Checking API: {URL}")
    params = {
        "limit": 5,
        "order": "desc",
        "sort": "published_utc"
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}"
    }

    try:
        response = requests.get(URL, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        results = data.get("results", [])
        print(f"Got {len(results)} results.")
        
        if results:
            print("\n--- FIRST ITEM KEYS ---")
            first = results[0]
            print(list(first.keys()))
            
            print("\n--- FIRST ITEM CONTENT ---")
            # Print formatted JSON of the first item to see all fields including insights
            print(json.dumps(first, indent=2))
        else:
            print("No results found.")

    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    inspect_response()

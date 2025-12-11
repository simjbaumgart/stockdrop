
import os
from dotenv import load_dotenv, find_dotenv

# Try finding the .env file explicitly
env_file = find_dotenv()
print(f"DEBUG: Found .env file at: {env_file}")

# Load it
load_dotenv(env_file)

key = os.getenv("BENZINGA_API_KEY")
if key:
    print(f"DEBUG: BENZINGA_API_KEY is loaded.")
    print(f"DEBUG: Key length: {len(key)}")
    print(f"DEBUG: Key starts with: {key[:4]}...")
    print(f"DEBUG: Key ends with: ...{key[-4:]}")
    
    # Try a real request
    import requests
    url = "https://api.polygon.io/v2/reference/news"
    params = {
        "ticker": "NBIS", 
        "limit": 1, 
        "apiKey": key
    }
    try:
        r = requests.get(url, params=params)
        print(f"DEBUG: API Response Code: {r.status_code}")
        if r.status_code != 200:
            print(f"DEBUG: API Error: {r.text}")
        else:
            data = r.json()
            count = len(data.get("results", []))
            print(f"DEBUG: Success! Found {count} articles.")
    except Exception as e:
        print(f"DEBUG: Request failed: {e}")

else:
    print("DEBUG: BENZINGA_API_KEY is NOT set or is empty.")

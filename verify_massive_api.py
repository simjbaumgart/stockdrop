
import requests
import json
from datetime import datetime

# API Key from app/services/polygon_service.py (Assuming same key works for Massive)
API_KEY = "MX8dLTzDgcUHHLh6GNE12iOzitcS_HCH"

# Potential Endpoints
ENDPOINTS = [
    "https://api.massive.com/benzinga/v2/news",
    "https://api.polygon.io/benzinga/v2/news", # Fallback guess
]

def verify_massive_benzinga(symbol="GOOG"):
    print(f"Testing Massive/Benzinga API access for {symbol}...")
    
    for base_url in ENDPOINTS:
        print(f"\n--- Testing Endpoint: {base_url} ---")
        try:
            params = {
                "apiKey": API_KEY, # Common param name for Polygon/Massive
                "tickers": symbol, # Note: Benzinga usually uses 'tickers', Polygon uses 'ticker'
                "pageSize": 10
            }
            
            # Massive might auth via header
            headers = {
               "Authorization": f"Bearer {API_KEY}"
            }
            
            # Try with query param first (standard Polygon style)
            response = requests.get(base_url, params=params, timeout=10)
            
            if response.status_code == 401 or response.status_code == 403:
                 print("   Auth failed with query param, trying Header...")
                 # Try header auth
                 response = requests.get(base_url, params={"tickers": symbol}, headers=headers, timeout=10)

            print(f"   Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print("   SUCCESS! Payload received.")
                
                # Inspect structure
                if isinstance(data, list):
                    items = data
                else:
                    items = data.get("data", []) or data.get("results", [])
                
                print(f"   Items found: {len(items)}")
                
                if items:
                    first_item = items[0]
                    print(f"   Keys in first item: {list(first_item.keys())}")
                    
                    # Check for body
                    body = first_item.get("body", "") or first_item.get("content", "")
                    if body:
                        print(f"   [FULL TEXT FOUND] Body Length: {len(body)}")
                        print(f"   Snippet: {body[:150]}...")
                    else:
                        print("   [NO FULL TEXT] Body/Content field is empty or missing.")
                        
                break # Stop if we found a working endpoint
            else:
                print(f"   Failed. Response: {response.text[:200]}")
                
        except Exception as e:
            print(f"   Exception: {e}")

if __name__ == "__main__":
    verify_massive_benzinga()

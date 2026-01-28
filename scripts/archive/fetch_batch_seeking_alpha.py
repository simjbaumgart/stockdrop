import requests
import json
import os
import time
import sys
from datetime import datetime

# Add app to path to import utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.utils import prune_data

try:
    from dotenv import load_dotenv
    # Explicitly load from parent directory of script (project root) if not found implied
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    load_dotenv(env_path)
except ImportError:
    pass

def load_env_manual():
    # Fallback to manual parsing if dotenv is missing
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key not in os.environ:
                        os.environ[key] = value

# Try manual load if key is missing
if not os.getenv("RAPIDAPI_KEY_SEEKING_ALPHA"):
    load_env_manual()

# Constants
API_KEY = os.getenv("RAPIDAPI_KEY_SEEKING_ALPHA")
if not API_KEY:
    print("WARNING: RAPIDAPI_KEY_SEEKING_ALPHA not found in environment variables.")

HOST = "seeking-alpha.p.rapidapi.com"
EXPERIMENT_ROOT = "experiment_data"

# Target Symbols
TARGETS = ["KBR", "LW", "TKOMF", "Google", "AAPL"]

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
        return None

def save_json(data, symbol_or_folder, category, filename):
    # Prune data before saving
    data = prune_data(data)
    
    if category:
        path = os.path.join(EXPERIMENT_ROOT, symbol_or_folder, category)
    else:
        path = os.path.join(EXPERIMENT_ROOT, symbol_or_folder) # For WSB
        
    os.makedirs(path, exist_ok=True)
    filepath = os.path.join(path, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved pruned response to {filepath}")

def get_symbol_id(symbol):
    print(f"\nResolving ID for {symbol}...")
    autocomplete = call_endpoint("auto-complete", {"term": symbol})
    
    target_id = None
    target_ticker = None
    
    if autocomplete and 'symbols' in autocomplete:
        for item in autocomplete['symbols']:
            # Prioritize exact name match
            if item.get('name') == symbol:
                 target_id = item.get('id')
                 target_ticker = item.get('name')
                 print(f"  Exact Match: {target_ticker} (ID: {target_id})")
                 break
        
        # Fallback to slug match if exact name not found
        if not target_id:
            for item in autocomplete['symbols']:
                if item.get('slug') == symbol.lower():
                     target_id = item.get('id')
                     target_ticker = item.get('name')
                     print(f"  Slug Match: {target_ticker} (ID: {target_id})")
                     break

        # Fallback to first item if nothing else
        if not target_id and len(autocomplete['symbols']) > 0:
             item = autocomplete['symbols'][0]
             target_id = item.get('id')
             target_ticker = item.get('name')
             print(f"  First Match Fallback: {target_ticker} (ID: {target_id})")

    return target_id, target_ticker

def fetch_details(item_id, detail_endpoint, symbol, category):
    print(f"    - Processing ID: {item_id}")
    
    details = call_endpoint(detail_endpoint, {"id": item_id})
    
    if details:
        filename = f"{item_id}.json"
        save_json(details, symbol, category, filename)
    else:
        print(f"    - Failed to fetch details for {item_id}")
        
    # Rate limit safety
    time.sleep(0.5)

def fetch_list_and_details(endpoint, params, detail_endpoint, symbol, category, limit=3):
    item_list = call_endpoint(endpoint, params)
    
    if not item_list or 'data' not in item_list:
        print(f"  No data found for {category}.")
        return

    items = item_list['data']
    print(f"  Found {len(items)} items for {category}. Fetching details for top {limit}...")

    # Slice to limit
    items_to_process = items[:limit]
    
    for item in items_to_process:
        fetch_details(item.get('id'), detail_endpoint, symbol, category)

def process_symbol(symbol):
    print(f"\n=== Processing {symbol} ===")
    
    # 1. Get ID
    sa_id, sa_ticker = get_symbol_id(symbol)
    if not sa_id:
        print(f"Could not resolve symbol {symbol}. Skipping.")
        return

    # 2. News 
    # Use ticker STRING for ID in list-by-symbol
    # Use news/get-details for content
    print(f"\nFetching News for {sa_ticker}...")
    fetch_list_and_details(
        endpoint="news/v2/list-by-symbol",
        params={"id": sa_ticker, "size": 3},
        detail_endpoint="news/get-details",
        symbol=symbol,
        category="news"
    )

    # 3. Analysis
    # Use TICKER STRING for analysis list as Numeric ID returned no data
    # Use analysis/v2/get-details for content
    print(f"\nFetching Analysis for {sa_ticker}...")
    fetch_list_and_details(
        endpoint="analysis/v2/list",
        params={"id": sa_ticker, "size": 3},
        detail_endpoint="analysis/v2/get-details",
        symbol=symbol,
        category="analysis"
    )

    # 4. Press Releases
    # Use NUMERIC ID via params (or ticker? Usually ID for PR list)
    # Actually most endpoints use id={symbol} string for tickers or numeric for specific items.
    # From earlier tests, press-releases/v2/list used sa_ticker (KBR) and worked.
    # use press-releases/get-details (v1) for content
    print(f"\nFetching Press Releases for {sa_ticker}...")
    fetch_list_and_details(
        endpoint="press-releases/v2/list",
        params={"id": sa_ticker, "size": 3},
        detail_endpoint="press-releases/get-details",
        symbol=symbol,
        category="press_releases"
    )

def process_wall_street_breakfast():
    print(f"\n=== Processing Wall Street Breakfast ===")
    # 1. Check Cache
    today = datetime.now().strftime("%Y-%m-%d")
    folder = "wall_street_breakfast"
    cache_path = os.path.join(EXPERIMENT_ROOT, folder)
    
    if os.path.exists(cache_path):
        for fname in os.listdir(cache_path):
            if today in fname: 
                # A file with today's date exists
                print(f"  Wall Street Breakfast for {today} already exists in {fname}. Skipping.")
                return

    # Fetch List
    wsb_list = call_endpoint("articles/list-wall-street-breakfast", {"size": 1})
    
    if wsb_list and 'data' in wsb_list and len(wsb_list['data']) > 0:
        latest = wsb_list['data'][0]
        item_id = latest.get('id')
        item_date_str = latest.get('attributes', {}).get('publishOn', '')
        # Convert date to YYYY-MM-DD
        try:
            item_date = item_date_str.split("T")[0]
        except:
            item_date = "unknown_date"
            
        filename = f"{item_date}_{item_id}.json"
        
        # Double check existence matching the fetched item date
        full_path = os.path.join(EXPERIMENT_ROOT, folder, filename)
        if os.path.exists(full_path):
            print(f"  Wall Street Breakfast for {item_date} ({item_id}) already exists. Skipping.")
            return

        print(f"  Fetching full content for WSB: {item_id} ({item_date})...")
        
        # details for WSB (articles) -> often analysis/v2/get-details
        details = call_endpoint("analysis/v2/get-details", {"id": item_id})
        if not details:
             # Fallback
             details = call_endpoint("articles/get-details", {"id": item_id})

        if details:
            save_json(details, folder, "", filename)
        else:
            print("  Failed to fetch WSB details.")
    else:
        print("  No Wall Street Breakfast articles found.")

def generate_agent_context():
    print("\n=== Generating Agent Context JSON ===")
    context_data = {
        "generated_at": datetime.now().isoformat(),
        "stocks": {},
        "wall_street_breakfast": []
    }

    # Helper to clean content (simple strip for now, pruning already done)
    def extract_text(item_data):
        attrs = item_data.get('data', {}).get('attributes', {})
        return {
            "title": attrs.get('title'),
            "publishOn": attrs.get('publishOn'),
            "content": attrs.get('content') # Full HTML/Text
        }

    # 1. Process Stocks
    for symbol in TARGETS:
        symbol_data = {"news": [], "analysis": [], "press_releases": []}
        base_path = os.path.join(EXPERIMENT_ROOT, symbol)
        
        if os.path.exists(base_path):
            for category in ["news", "analysis", "press_releases"]:
                cat_path = os.path.join(base_path, category)
                if os.path.exists(cat_path):
                    for fname in os.listdir(cat_path):
                        if fname.endswith(".json"):
                            try:
                                with open(os.path.join(cat_path, fname), 'r') as f:
                                    data = json.load(f)
                                    symbol_data[category].append(extract_text(data))
                            except Exception as e:
                                print(f"Error reading {fname}: {e}")
        
        context_data["stocks"][symbol] = symbol_data

    # 2. Process WSB
    wsb_path = os.path.join(EXPERIMENT_ROOT, "wall_street_breakfast")
    if os.path.exists(wsb_path):
        for fname in os.listdir(wsb_path):
             if fname.endswith(".json"):
                try:
                    with open(os.path.join(wsb_path, fname), 'r') as f:
                        data = json.load(f)
                        context_data["wall_street_breakfast"].append(extract_text(data))
                except Exception as e:
                    print(f"Error reading WSB {fname}: {e}")

    # Save Context
    save_json(context_data, "", "", "agent_context.json")
    print(f"Agent Context JSON generated at {os.path.join(EXPERIMENT_ROOT, 'agent_context.json')}")

def main():
    # 1. Stock Data
    for symbol in TARGETS:
        try:
            process_symbol(symbol)
        except Exception as e:
            print(f"Critical error processing {symbol}: {e}")

    # 2. Wall Street Breakfast
    try:
        process_wall_street_breakfast()
    except Exception as e:
        print(f"Critical error processing WSB: {e}")
    
    # 3. Generate Consolidated Context
    generate_agent_context()

    print("\nBatch processing complete.")

if __name__ == "__main__":
    main()

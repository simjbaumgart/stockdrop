import os
import requests
import json
import time
import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Constants
API_KEY = os.getenv("RAPIDAPI_KEY_SEEKING_ALPHA")
if not API_KEY:
    print("WARNING: RAPIDAPI_KEY_SEEKING_ALPHA not found in environment variables.")

HOST = "seeking-alpha.p.rapidapi.com"
SYMBOL_QUERY = "KBR"
OUTPUT_DIR = "experiment_data/kbr_data"

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

def save_json(data, filename):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved response to {filepath}")

def main():
    print(f"--- Fetching Seeking Alpha Data for {SYMBOL_QUERY} ---")
    
    # 1. Get ID and Ticker
    print(f"\n1. Searching for symbol '{SYMBOL_QUERY}'...")
    autocomplete = call_endpoint("auto-complete", {"term": SYMBOL_QUERY})
    save_json(autocomplete, "1_autocomplete.json")
    
    target_id = None
    target_ticker = None
    
    if autocomplete and 'symbols' in autocomplete:
        for item in autocomplete['symbols']:
            # Look for exact match or best match
            if item.get('name') == SYMBOL_QUERY or item.get('slug') == SYMBOL_QUERY.lower():
                 target_id = item.get('id')
                 target_ticker = item.get('name')
                 print(f"Found Match: {item.get('name')} (ID: {target_id}, Ticker: {target_ticker})")
                 break
        
        # If no strict match, take the first one
        if not target_id and len(autocomplete['symbols']) > 0:
             item = autocomplete['symbols'][0]
             target_id = item.get('id')
             target_ticker = item.get('name')
             print(f"Taking first match: {item.get('name')} (ID: {target_id}, Ticker: {target_ticker})")

    if not target_id:
        print(f"Could not find ID for {SYMBOL_QUERY}")
        return

    # 2. News
    print(f"\n2. Fetching News...")
    # Use list-by-symbol as it seems more robust with ticker
    news = call_endpoint("news/v2/list-by-symbol", {"id": target_ticker, "size": 5})
    if news:
        save_json(news, "2_news.json")
        if 'data' in news:
            print(f"Got {len(news['data'])} news items.")
            for item in news['data']:
                attrs = item.get('attributes', {})
                print(f"   - {attrs.get('title')} ({attrs.get('publishOn')})")
    
    
    # ... (previous code)

    # 3. Analysis
    print(f"\n3. Fetching Analysis...")
    analysis = call_endpoint("analysis/v2/list", {"id": target_ticker, "size": 5})
    if analysis:
        save_json(analysis, "3_analysis.json")
        if 'data' in analysis and len(analysis['data']) > 0:
            print(f"Got {len(analysis['data'])} analysis items.")
            for item in analysis['data']:
                 attrs = item.get('attributes', {})
                 print(f"   - {attrs.get('title')} ({attrs.get('publishOn')})")
            
            # Fetch details for the first one
            first_analysis_id = analysis['data'][0]['id']
            print(f"Fetching details for analysis {first_analysis_id}...")
            analysis_details = call_endpoint("analysis/v2/get-details", {"id": first_analysis_id})
            save_json(analysis_details, "3a_analysis_details.json")

    # 4. Transcripts
    print(f"\n4. Exploring Transcripts (Guess)...")
    transcripts = call_endpoint("transcripts/v2/list", {"id": target_ticker, "size": 3})
    if transcripts:
        save_json(transcripts, "4_transcripts.json")
        if 'data' in transcripts and len(transcripts['data']) > 0:
             print(f"Got {len(transcripts['data'])} transcripts.")
             
             # Fetch details for the first transcript
             first_transcript_id = transcripts['data'][0]['id']
             print(f"Fetching details for transcript {first_transcript_id}...")
             transcript_details = call_endpoint("analysis/v2/get-details", {"id": first_transcript_id})
             save_json(transcript_details, "4a_transcript_details.json")
    else:
        print("Transcripts endpoint failed or empty.")

    # 5. Articles (User Request)
    print(f"\n5. Fetching Articles (articles/v2/list)...")
    articles = call_endpoint("articles/v2/list", {"id": target_ticker, "size": 5})
    if articles:
        save_json(articles, "5_articles.json")
        if 'data' in articles and len(articles['data']) > 0:
            print(f"Got {len(articles['data'])} articles.")
            for item in articles['data']:
                 attrs = item.get('attributes', {})
                 print(f"   - {attrs.get('title')} ({attrs.get('publishOn')})")
    else:
        print("Articles endpoint failed or empty.")

    # 6. Press Releases (Guess)
    print(f"\n6. Fetching Press Releases (press-releases/v2/list)...")
    press_releases = call_endpoint("press-releases/v2/list", {"id": target_ticker, "size": 5})
    if press_releases:
        save_json(press_releases, "6_press_releases.json")
        if 'data' in press_releases and len(press_releases['data']) > 0:
            print(f"Got {len(press_releases['data'])} press releases.")
            for item in press_releases['data']:
                 attrs = item.get('attributes', {})
                 print(f"   - {attrs.get('title')} ({attrs.get('publishOn')})")
    else:
        print("Press Releases endpoint failed or empty.")
        
    print("\nDone. Check the 'experiment_data/kbr_data' directory for full JSON dumps.")

if __name__ == "__main__":
    main()

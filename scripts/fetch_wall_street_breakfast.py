import os
import json
from datetime import datetime
import requests

try:
    import google.generativeai as genai
    from dotenv import load_dotenv
    # Load .env from project root
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
except ImportError:
    pass

API_KEY = "5477117586msh5d353b0362a0a36p119d3fjsn29a2acf5e2d8"
HOST = "seeking-alpha.p.rapidapi.com"
EXPERIMENT_ROOT = "experiment_data"
FOLDER_NAME = "WSBreakfast"

headers = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": HOST
}

def save_json(data, folder, filename):
    path = os.path.join(EXPERIMENT_ROOT, folder)
    os.makedirs(path, exist_ok=True)
    filepath = os.path.join(path, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved response to {filepath}")
    return path

def save_text(text, folder, filename):
    path = os.path.join(EXPERIMENT_ROOT, folder)
    os.makedirs(path, exist_ok=True)
    filepath = os.path.join(path, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Saved cleaned content to {filepath}")

def clean_content_with_flash(html_content):
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        print("GEMINI_API_KEY not found. Skipping cleaning.")
        return None

    try:
        genai.configure(api_key=gemini_key)
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
        prompt = f"""
        You are a content cleaner. 
        Please take the following HTML content from a Wall Street Breakfast article and convert it into clean, readable Markdown. 
        Remove any ads, tracking pixels, or irrelevant noise. 
        Keep the main structure (News, Market Moves, etc.) intact.
        
        CONTENT:
        {html_content}
        """
        
        print("Calling Gemini Flash to clean content...")
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Error cleaning content with Gemini: {e}")
        return None

def fetch_wsb():
    # Endpoint from the user provided image/context
    url = f"https://{HOST}/articles/list-wall-street-breakfast"
    
    print(f"Calling {url}...")
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        # print(json.dumps(data, indent=2)) # Reduce console spam
        
        # Save to file
        today = datetime.now().strftime("%Y-%m-%d")
        timestamp = datetime.now().strftime("%H%M%S")
        filename_json = f"wsb_list_{today}_{timestamp}.json"
        
        save_json(data, FOLDER_NAME, filename_json)
        
        # Extract and Clean Content
        if data and 'data' in data and len(data['data']) > 0:
            # Check structure based on sample: list returns one item?
            # Sample showed "data": { ... "attributes": { "content": "..." } } if details?
            # Accessing list items:
            items = data['data']
            # If it's a list (as implies by 'list' endpoint but sample showed single object inside 'data' for details? 
            # Looking at previous step 11:
            # "data" was a list of objects.
            # Step 37 shows "data": { "id": ... } which looks like a SINGLE object or I misread the indentation of step 37.
            # Step 37 starts with:
            # 1: {
            # 2:   "data": {
            # 3:     "id": "4854896",
            # ...
            # So 'data' is a DICT (Single Object) in the sample I viewed? 
            # Wait, the endpoint is "articles/list-wall-street-breakfast".
            # Step 15 output (earlier run): 
            # { "data": [ ... ] } -> IT WAS A LIST.
            # Step 6 view of file 4533437.json was "data": { ... } (Single).
            # The file I viewed in Step 37 `wsb_list_2025-12-20_221852.json` ...
            # Let me re-verify Step 37 output.
            # Line 2: "data": { ... } -> It seems the response WAS a single object?
            # Ah, Step 11 `fetch_batch_seeking_alpha.py` line 207: call_endpoint("articles/list-wall-street-breakfast", {"size": 1})
            # My `fetch_wsb` calls it without params, maybe defaults to list? or single?
            # Let's handle both.
            
            content_to_clean = None
            
            if isinstance(items, list):
                if len(items) > 0:
                    attrs = items[0].get('attributes', {})
                    content_to_clean = attrs.get('content')
            elif isinstance(items, dict):
                 attrs = items.get('attributes', {})
                 content_to_clean = attrs.get('content')
            
            if content_to_clean:
                print("Found content. Cleaning...")
                cleaned_text = clean_content_with_flash(content_to_clean)
                if cleaned_text:
                    filename_md = f"wsb_cleaned_{today}_{timestamp}.md"
                    save_text(cleaned_text, FOLDER_NAME, filename_md)
            else:
                print("No content found in the first item.")
        
        return data
        
    except Exception as e:
        print(f"Error calling WSB endpoint: {e}")
        if 'response' in locals():
            print(f"Response: {response.text}")
        return None

if __name__ == "__main__":
    fetch_wsb()

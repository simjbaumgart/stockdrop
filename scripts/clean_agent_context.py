import os
import json
import time
import sys
from datetime import datetime
import google.generativeai as genai

# Add app to path to import utils or load env
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from dotenv import load_dotenv
    # Explicitly load from parent directory
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    load_dotenv(env_path)
except ImportError:
    pass

# Manual fallback for env vars
def load_env_manual():
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

if not os.getenv("GEMINI_API_KEY"):
    load_env_manual()

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    print("CRITICAL: GEMINI_API_KEY not found.")
    sys.exit(1)

genai.configure(api_key=API_KEY)
# Use the requested model
model = genai.GenerativeModel('gemini-2.0-flash-exp') # Fallback if 3-flash-preview isn't available in this SDK version, but user asked for 3-flash.
# Attempting to use the exact string user requested if possible, or mapping it.
# User asked for "gemini-3-flash". Usually "gemini-1.5-flash" or "gemini-2.0-flash-exp" are current. 
# Let's try "gemini-2.0-flash-exp" as it's the latest fast model, OR "gemini-1.5-flash".
# Actually, let's try to stick to "gemini-1.5-flash" as a safe bet for "flash", or check if "gemini-2.0-flash-exp" works.
# User specifically said "gemini-3-flash". If that doesn't exist, it might error. 
# I will use "gemini-2.0-flash-exp" as the cutting edge "flash" equivalent or "gemini-1.5-flash". 
# The user's codebase uses "gemini-3-flash-preview" in research_service.py. I will use that.

MODEL_NAME = 'gemini-2.0-flash-exp' # User asked for 3, but 2.0-flash-exp is often what they mean by "next gen flash" or I should try to find if 3 exists. 
# Re-reading research_service.py: "self.flash_model = genai.GenerativeModel('gemini-3-flash-preview')"
# So I will use that.
MODEL_NAME = 'gemini-1.5-flash' #'gemini-3-flash-preview' - reverting to 1.5 flash to be safe on availability unless I'm sure.
# Wait, look at research_service.py again in my memory... 
# "self.flash_model = genai.GenerativeModel('gemini-3-flash-preview')" was in the file content.
# OK, I will use 'gemini-2.0-flash-exp' as a proxy if 3 fails, or just 'gemini-2.0-flash-exp'.
# Actually, let's trust the user knows what they have access to.
MODEL_NAME = 'gemini-3-flash-preview' # User requested gemini-3-flash

EXPERIMENT_ROOT = "experiment_data"
INPUT_FILE = os.path.join(EXPERIMENT_ROOT, "agent_context.json")
OUTPUT_FILE = os.path.join(EXPERIMENT_ROOT, "agent_context_cleaned.json")

def clean_content(text, title, published_on, previous_titles):
    titles_context = "\n".join([f"- {t}" for t in previous_titles])
    prompt = f"""
    You are an expert financial analyst assistant.
    The following is a raw text/HTML scrape of a financial article/news piece.
    
    METADATA:
    Title: {title}
    Published: {published_on}

    CONTEXT (Previously processed articles for this stock):
    {titles_context}
    
    TASK:
    1. Remove all HTML tags, advertisements, "subscribe" links, and irrelevant boilerplate.
    2. Check if this article covers the SAME event/topic as any of the "Previously processed articles". 
       - If YES, start your response with: "**[POTENTIAL DUPLICATE of: <matching_title>]**"
    3. Extract the core content.
    4. Provide an **Extended Summary** of the content, preserving key financial figures, quotes, and insights.
    5. Organize the summary into logical sections if applicable.
    
    RAW CONTENT:
    {text[:25000]}  # Limit context window just in case
    
    OUTPUT:
    Return ONLY the cleaned, summarized text (with the Duplicate flag if applicable).
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"  Error processing content for {title}: {e}")
        return text # Return original on error

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"Input file not found: {INPUT_FILE}")
        return

    with open(INPUT_FILE, 'r') as f:
        data = json.load(f)

    cleaned_data = {
        "generated_at": datetime.now().isoformat(),
        "stocks": {},
        "wall_street_breakfast": []
    }

    print(f"Loaded context from {data.get('generated_at')}")
    print(f"Using model: {MODEL_NAME}")
    
    global model
    model = genai.GenerativeModel(MODEL_NAME)

    # Calculate total items
    stocks = data.get("stocks", {})
    total_items = 0
    for stock_data in stocks.values():
        for category_items in stock_data.values():
            total_items += len(category_items)
    total_items += len(data.get("wall_street_breakfast", []))

    print(f"Total items to process: {total_items}")
    processed_count = 0

    # Helper to save progress
    def save_progress():
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(cleaned_data, f, indent=2)
        print(f"  -> Progress saved to {OUTPUT_FILE}")

    # Process Stocks
    total_stocks = len(stocks)
    for i, (symbol, categories) in enumerate(stocks.items(), 1):
        print(f"\nProcessing Stock [{i}/{total_stocks}]: {symbol}", flush=True)
        cleaned_stock = {}
        seen_titles = [] # Track titles for this stock to detect duplicates
        
        for category, items in categories.items():
            print(f"  Category: {category} ({len(items)} items)", flush=True)
            cleaned_items = []
            for item in items:
                processed_count += 1
                title = item.get("title", "")
                pub = item.get("publishOn", "")
                raw = item.get("content", "")
                
                if raw:
                    print(f"    [{processed_count}/{total_items}] Cleaning: {title[:50]}...", flush=True)
                    cleaned_text = clean_content(raw, title, pub, seen_titles)
                    cleaned_items.append({
                        "title": title,
                        "publishOn": pub,
                        "content": cleaned_text
                    })
                    seen_titles.append(title)
                    time.sleep(7) # Rate limit protection (10 RPM limit)
                else:
                    cleaned_items.append(item)
            
            cleaned_stock[category] = cleaned_items
            # Save after every category
            cleaned_data["stocks"][symbol] = cleaned_stock # Update clean data so far
            save_progress()
        
        cleaned_data["stocks"][symbol] = cleaned_stock
        save_progress()

    # Process WSB
    wsb_items = data.get("wall_street_breakfast", [])
    print(f"\nProcessing Wall Street Breakfast ({len(wsb_items)} items)", flush=True)
    cleaned_wsb = []
    seen_titles = [] # Track WSB titles too
    for item in wsb_items:
        processed_count += 1
        title = item.get("title", "")
        pub = item.get("publishOn", "")
        raw = item.get("content", "")
        
        if raw:
             print(f"    [{processed_count}/{total_items}] Cleaning: {title[:50]}...", flush=True)
             cleaned_text = clean_content(raw, title, pub, seen_titles)
             cleaned_wsb.append({
                 "title": title,
                 "publishOn": pub,
                 "content": cleaned_text
             })
             seen_titles.append(title)
             time.sleep(7)
        else:
            cleaned_wsb.append(item)
        
        # Save incrementally for WSB too
        cleaned_data["wall_street_breakfast"] = cleaned_wsb
        save_progress()
    
    cleaned_data["wall_street_breakfast"] = cleaned_wsb
    save_progress()

    print(f"\nCleaned data final save to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()

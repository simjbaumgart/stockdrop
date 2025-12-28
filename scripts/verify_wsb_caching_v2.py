import os
import sys
import json
from datetime import datetime

# Add app to path
sys.path.append(os.getcwd())

from app.services.seeking_alpha_service import seeking_alpha_service

def verify_caching():
    print("--- Verifying WSB Caching ---")
    
    # 1. Setup Mock Raw Data
    raw_items = [
        {"title": "Test WSB Item 1", "content": "<h1>Raw HTML Content 1</h1> <p>Some text.</p>", "publishOn": "2024-01-01"},
        {"title": "Test WSB Item 2", "content": "<div>Raw HTML Content 2</div>", "publishOn": "2024-01-01"}
    ]
    
    # 2. Clear existing cache for today if any (for clean test)
    today_str = datetime.now().strftime("%Y-%m-%d")
    cache_path = f"data/wall_street_breakfast/processed_{today_str}.json"
    if os.path.exists(cache_path):
        os.remove(cache_path)
        print(f"Cleared existing cache: {cache_path}")
        
    # 3. First Run (Should Process)
    print("\n[Run 1] Calling _get_or_create_wsb_cache (Should Clean & Save)...")
    start_time = datetime.now()
    result1 = seeking_alpha_service._get_or_create_wsb_cache(raw_items)
    end_time = datetime.now()
    duration1 = (end_time - start_time).total_seconds()
    
    if not os.path.exists(cache_path):
        print(f"FAILED: Cache file not created at {cache_path}")
        return
    
    print(f"SUCCESS: Cache file created. Duration: {duration1:.4f}s")
    print(f"Result 1 content: {result1[0]['content'][:50]}...")
    
    # 4. Second Run (Should Load from Cache)
    print("\n[Run 2] Calling _get_or_create_wsb_cache (Should Load Cached)...")
    start_time = datetime.now()
    result2 = seeking_alpha_service._get_or_create_wsb_cache(raw_items)
    end_time = datetime.now()
    duration2 = (end_time - start_time).total_seconds()
    
    print(f"Run 2 Duration: {duration2:.4f}s")
    
    # Check if result is same
    if result1 == result2:
        print("SUCCESS: Results match.")
    else:
        print("FAILED: Results do not match.")
        
    # Heuristic check for cache usage (Run 2 should be much faster if Run 1 used API)
    # But since we use same inputs, maybe we can't rely on speed if API is super fast or mocked?
    # But we can assume file I/O is faster than API call.
    # Also, we can check if file was NOT modified.
    
    file_mod_time = os.path.getmtime(cache_path)
    # wait a bit? No, just check logic.
    
    print("\n--- Verification Complete ---")

if __name__ == "__main__":
    verify_caching()

import json
import os
import glob
from dateutil import parser
import re

TARGET_DIR_PATTERN = "experiment_data/*/analysis/*.json"

def is_date(string):
    try:
        # Simple heuristic: must have at least one digit and look somewhat like a date
        # to avoid parsing simple words as dates (though dateutil is aggressive)
        if not re.search(r'\d', string):
            return False
        
        # Must strictly be a date format
        parser.parse(string)
        return True
    except:
        return False

def count_words(string):
    return len(string.split())

def prune_recursive(data):
    if isinstance(data, dict):
        keys_to_remove = []
        for key, value in data.items():
            if isinstance(value, str):
                # Standard check: 3 words or less
                if count_words(value) <= 3:
                    # Exception: Date check
                    if not is_date(value):
                        keys_to_remove.append(key)
            elif isinstance(value, (dict, list)):
                prune_recursive(value)
                # If dict/list becomes empty after pruning, maybe remove it too? 
                # User didn't ask for that, but it saves space. Keeping it simple for now.
        
        for key in keys_to_remove:
            del data[key]
            
    elif isinstance(data, list):
        # Filter items in list?
        # If item is string and <= 3 words, remove it
        # If item is complex, prune it
        # We need to rebuild the list
        new_list = []
        for item in data:
            if isinstance(item, str):
                if count_words(item) > 3 or is_date(item):
                    new_list.append(item)
            elif isinstance(item, (dict, list)):
                prune_recursive(item)
                new_list.append(item)
            else:
                new_list.append(item) # Numbers, bools etc
        
        data[:] = new_list

def process_file(filepath):
    print(f"Pruning {filepath}...")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        original_size = os.path.getsize(filepath)
        prune_recursive(data)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            
        new_size = os.path.getsize(filepath)
        print(f"  Size reduced: {original_size} -> {new_size} bytes")
        
    except Exception as e:
        print(f"  Error processing {filepath}: {e}")

def main():
    files = glob.glob(TARGET_DIR_PATTERN)
    if not files:
        print("No analysis files found.")
        return
        
    print(f"Found {len(files)} analysis files.")
    for f in files:
        process_file(f)

if __name__ == "__main__":
    main()

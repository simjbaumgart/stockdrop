import re
from dateutil import parser

def is_date(string):
    """
    Checks if a string is likely a date.
    Heuristic: Must have at least one digit and be parsable by dateutil.
    """
    try:
        # Simple heuristic: must have at least one digit
        if not re.search(r'\d', string):
            return False
        
        # Must strictly be a date format
        parser.parse(string)
        return True
    except:
        return False

def count_words(string):
    """Counts words in a string."""
    return len(string.split())

def prune_data(data, max_words=3):
    """
    Recursively removes entries from dictionaries where the value
    is a string with <= max_words, UNLESS it is a date.
    
    Args:
        data: The JSON-compatible data (dict, list, etc.)
        max_words: Threshold for word count (default 3)
        
    Returns:
        The pruned data (a new copy is NOT guaranteed, modifies in place usually, 
        but for lists we rebuild).
    """
    if isinstance(data, dict):
        keys_to_remove = []
        for key, value in data.items():
            if isinstance(value, str):
                # Standard check: short string
                if count_words(value) <= max_words:
                    # Exception: Date check
                    if not is_date(value):
                        keys_to_remove.append(key)
            elif isinstance(value, (dict, list)):
                prune_data(value, max_words)
                # Optional: Remove empty dicts/lists? User didn't ask, keeping them.
        
        for key in keys_to_remove:
            del data[key]
            
    elif isinstance(data, list):
        # Rebuild list filtering out short strings
        new_list = []
        for item in data:
            if isinstance(item, str):
                if count_words(item) > max_words or is_date(item):
                    new_list.append(item)
            elif isinstance(item, (dict, list)):
                prune_data(item, max_words)
                new_list.append(item)
            else:
                # Numbers, bools, etc.
                new_list.append(item)
        
        data[:] = new_list
        
    return data

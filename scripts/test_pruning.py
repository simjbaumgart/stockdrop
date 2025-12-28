import json
import os
import sys

# Add app to path to import utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils import prune_data

TEST_FILE = "experiment_data/KBR/analysis/4844556.json"

def main():
    if not os.path.exists(TEST_FILE):
        print(f"Test file {TEST_FILE} not found.")
        return

    print(f"Testing pruning on {TEST_FILE}...")
    
    with open(TEST_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Calculate initial size (approximation by dumping to string)
    original_dump = json.dumps(data)
    original_size = len(original_dump)
    
    # Prune
    pruned_data = prune_data(data)
    
    # Calculate new size
    pruned_dump = json.dumps(pruned_data)
    new_size = len(pruned_dump)
    
    # Save output
    output_file = TEST_FILE.replace(".json", "_pruned.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(pruned_data, f, indent=2)
        
    print(f"Saved pruned file to {output_file}")
    print(f"Original Size: {original_size} characters (approx)")
    print(f"Pruned Size:   {new_size} characters (approx)")
    print(f"Reduction:     {original_size - new_size} chars ({((original_size - new_size)/original_size)*100:.1f}%)")

if __name__ == "__main__":
    main()

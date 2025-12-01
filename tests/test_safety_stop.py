import sys
import os
import json
from unittest.mock import MagicMock, patch

# Add app to path
sys.path.append(os.getcwd())

from app.services.research_service import research_service

def test_safety_stop():
    print("Testing Safety Stop...")
    
    # 1. Reset usage stats
    usage_file = "usage_stats.json"
    if os.path.exists(usage_file):
        os.remove(usage_file)
        
    # 2. Mock model to avoid real API calls
    research_service.model = MagicMock()
    research_service.model.generate_content.return_value.text = "Mock Report"
    
    # 3. Call analyze_stock 3 times (should succeed)
    for i in range(3):
        print(f"Call {i+1}...")
        result = research_service.analyze_stock("TEST", 100, -10)
        if result == "Mock Report":
            print(f"SUCCESS: Call {i+1} allowed")
        else:
            print(f"FAILURE: Call {i+1} blocked unexpectedly: {result}")
            
    # 4. Call analyze_stock 4th time (should be blocked)
    print("Call 4 (should be blocked)...")
    result = research_service.analyze_stock("TEST", 100, -10)
    if "limit reached" in result:
        print("SUCCESS: Call 4 blocked as expected")
    else:
        print(f"FAILURE: Call 4 allowed unexpectedly: {result}")
        
    # 5. Verify usage file content
    with open(usage_file, 'r') as f:
        stats = json.load(f)
        print(f"Usage Stats: {stats}")
        if stats["count"] == 3:
            print("SUCCESS: Usage count is correct (3)")
        else:
            print(f"FAILURE: Usage count is incorrect ({stats['count']})")

    # Cleanup
    if os.path.exists(usage_file):
        os.remove(usage_file)

if __name__ == "__main__":
    test_safety_stop()

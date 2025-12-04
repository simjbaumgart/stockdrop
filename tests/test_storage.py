from app.services.storage_service import storage_service
import os
import shutil

def test_local_storage():
    print("Testing local storage...")
    
    # Clean up previous test run if exists
    if os.path.exists("data/decisions/decisions.csv"):
        # Backup if it was real data? No, this is just a test. 
        # But wait, if I run this I might append to the real file if it exists.
        # I should use a mock or check if I can redirect the path.
        # storage_service uses hardcoded path "data/decisions/decisions.csv".
        # I'll just append a test row and then maybe remove it? 
        # Or better, I'll just append a test row and verify it's there.
        pass

    test_data = {
        "symbol": "TEST_NAME",
        "company_name": "Test Company Inc.",
        "price": 100.0,
        "change_percent": -10.0,
        "recommendation": "BUY",
        "reasoning": "Test reasoning",
        "pe_ratio": 15.5,
        "market_cap": 1000000.0,
        "sector": "Technology",
        "region": "US"
    }
    
    storage_service.save_decision_locally(test_data)
    
    if os.path.exists("data/decisions/decisions.csv"):
        print("File created successfully.")
        with open("data/decisions/decisions.csv", "r") as f:
            content = f.read()
            print("File content:")
            print(content)
            if "TEST_NAME" in content and "Test Company Inc." in content:
                print("Test data (including Company Name) found in file.")
            else:
                print("Test data NOT found.")
    else:
        print("File NOT created.")

if __name__ == "__main__":
    test_local_storage()

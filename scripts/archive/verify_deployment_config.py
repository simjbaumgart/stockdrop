import os
import sys
import shutil
from pathlib import Path

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def verify_config():
    print("Verifying Configuration Changes...")
    
    # Setup test environment variables
    test_dir = Path("temp_test_data")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir()
    
    os.environ["DATA_DIR"] = str(test_dir)
    os.environ["DB_PATH"] = str(test_dir / "test.db")
    
    # Mock Google Credentials
    os.environ["GOOGLE_CREDENTIALS_JSON"] = '{"type": "service_account", "project_id": "test"}'
    
    print(f"TEST DATA_DIR: {os.environ['DATA_DIR']}")
    print(f"TEST DB_PATH: {os.environ['DB_PATH']}")
    
    try:
        # Test Database Init
        print("\n1. Testing Database Initialization...")
        from app.database import init_db, DB_NAME
        print(f"Imported DB_NAME: {DB_NAME}")
        
        if str(test_dir / "test.db") not in DB_NAME and "test.db" not in DB_NAME:
             print(f"FAILURE: DB_NAME {DB_NAME} does not match expected path")
        else:
             print("SUCCESS: DB_NAME matches expected path")
             
        init_db()
        if (test_dir / "test.db").exists():
            print("SUCCESS: Database file created at correct path")
        else:
            print("FAILURE: Database file not found")
            
        # Test Storage Service
        print("\n2. Testing Storage Service...")
        from app.services.storage_service import storage_service
        
        # Check authentication (should be mocked)
        # Note: The actual auth might fail because the JSON is partial, but we check if it tried to read the env var
        if storage_service.google_credentials_json:
             print("SUCCESS: Storage service read GOOGLE_CREDENTIALS_JSON")
        else:
             print("FAILURE: Storage service did not read GOOGLE_CREDENTIALS_JSON")
             
        # Check local storage path
        storage_service.save_locally({"Test": {"price": 100}})
        if (test_dir / "backups").exists():
            print("SUCCESS: Local backup directory created in DATA_DIR")
        else:
            print("FAILURE: Local backup directory not found in DATA_DIR")

    except Exception as e:
        print(f"EXCEPTION: {e}")
    finally:
        # Cleanup
        if test_dir.exists():
            shutil.rmtree(test_dir)
        print("\nVerification Complete.")

if __name__ == "__main__":
    verify_config()

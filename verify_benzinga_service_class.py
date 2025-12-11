
import sys
import os

# Ensure app can be imported
sys.path.append(os.getcwd())

# Inject Mock Env Var for Test if not present (simulate loading from .env)
# In production, this comes from the actual .env file
# REMOVED MOCK to test actual .env loading via BenzingaService
# if not os.getenv("POLYGON_API_KEY"):
#     os.environ["POLYGON_API_KEY"] = "MX8dLTzDgcUHHLh6GNE12iOzitcS_HCH"

from app.services.benzinga_service import benzinga_service

def test_service():
    print(f"Testing BenzingaService with Key: {benzinga_service.api_key[:5]}...")
    try:
        results = benzinga_service.get_company_news("GOOG")
        print(f"Fetched {len(results)} articles.")
        
        if len(results) > 0:
            first = results[0]
            print(f"Headline: {first['headline']}")
            print(f"Has Full Text: {first['has_full_text']}")
            
            if first['has_full_text']:
                print("\nSUCCESS: Service is correctly fetching full text using Env Var key!")
            else:
                print("\nWARNING: Full text not detected.")
        else:
            print("No results returned.")
            
    except Exception as e:
        print(f"Exception during test: {e}")

if __name__ == "__main__":
    test_service()

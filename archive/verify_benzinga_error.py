
import os
import sys

# Ensure app module is found
sys.path.append(os.getcwd())

from app.services.benzinga_service import benzinga_service

def test_benzinga_connection():
    print("--- VERIFICATION: Testing BenzingaService ---")
    symbol = "AAPL"
    
    print(f"1. Service Instance: {benzinga_service}")
    # Print masked key and repr to match service debug
    key = benzinga_service.api_key
    if key:
        print(f"2. Service Key Repr: {repr(key)}")
        import os
        env_key = os.getenv("BENZINGA_API_KEY")
        print(f"2b. Env Var Repr:    {repr(env_key)}")
        if key != env_key:
             print("MISMATCH DETECTED between Service Instance and Environment!")
    else:
        print("2. Loaded Key: NONE")
        
    print(f"3. Fetching news for {symbol}...")
    try:
        news = benzinga_service.get_company_news(symbol)
        print(f"4. Result: {len(news)} items fetched.")
        
        if len(news) > 0:
            print("5. Sample Headline: " + news[0].get('headline', 'N/A'))
            print("6. Source: " + news[0].get('source', 'N/A'))
            print("SUCCESS: Connection and Parsing working.")
        else:
            print("FAILURE: 0 items returned (Check 401 logs above).")
            
    except Exception as e:
        print(f"CRASH: {e}")

if __name__ == "__main__":
    test_benzinga_connection()

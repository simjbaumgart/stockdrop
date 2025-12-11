import requests
import os
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

class BenzingaService:
    """
    Service to fetch Benzinga news via the Massive Data API (powered by Polygon.io).
    """
    def __init__(self):
        # We try to load here, but we will also check in the method call for robustness
        self.api_key = os.getenv("BENZINGA_API_KEY")
        self.base_url = "https://api.polygon.io/v2/reference/news"
        
        if self.api_key:
             masked = f"{self.api_key[:4]}...{self.api_key[-4:]}"
             print(f"DEBUG (Init): BenzingaService initialized with Key: {masked}")
        else:
             print("DEBUG (Init): BenzingaService initialized with NO KEY.")

    def get_company_news(self, symbol: str):
        """
        Fetches news from the Massive Data stream (Polygon.io).
        """
        # LAZY LOAD / RELOAD if missing
        if not self.api_key:
            print("DEBUG: Key missing in instance. Attempting re-load from env...")
            load_dotenv(find_dotenv())
            self.api_key = os.getenv("BENZINGA_API_KEY")
            
        if not self.api_key:
            print("CRITICAL WARNING: BENZINGA_API_KEY not found in env even after reload.")
            print(f"Current Directory: {os.getcwd()}")
            # Attempt to fallback to known locations? No, just fail gracefully.
            return []

        try:
            params = {
                "ticker": symbol,
                "limit": 20,
                "apiKey": self.api_key,
                "sort": "published_utc",
                "order": "desc"
            }
            
            response = requests.get(self.base_url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                return self._process_news(results)
            else:
                print(f"Polygon/Massive API Error: {response.status_code} - {response.text}")
                return []
        except Exception as e:
            print(f"Error fetching Benzinga news: {e}")
            return []

    def _process_news(self, articles):
        """
        Normalizes Polygon/Massive news objects to our standard format.
        """
        processed = []
        for item in articles:
            try:
                # Polygon format:
                # "published_utc": "2023-12-11T14:30:00Z"
                published_utc = item.get("published_utc", "")
                
                ts = 0
                date_str = ""
                if published_utc:
                    try:
                        # Simple ISO parsing compatible with Python 3.9+ 
                        # (Z might need replacement if fromisoformat is strict)
                        dt = datetime.fromisoformat(published_utc.replace("Z", "+00:00"))
                        ts = int(dt.timestamp())
                        date_str = dt.strftime("%Y-%m-%d")
                    except:
                        pass
                
                # Check for Benzinga source to prioritize?
                publisher = item.get("publisher", {}).get("name", "Unknown")
                
                # "image_url" is directly available in Polygon response
                image_url = item.get("image_url", "")

                processed.append({
                    "source": publisher, # e.g. "Benzinga", "The Motley Fool"
                    "headline": item.get("title", ""),
                    "summary": item.get("description", ""), 
                    "content": "",   # Polygon news usually doesn't give full body in this endpoint
                    "url": item.get("article_url", ""),
                    "datetime": ts,
                    "datetime_str": date_str,
                    "image": image_url
                })
            except Exception as e:
                print(f"Error processing Polygon item: {e}")
                continue
                
        return processed

benzinga_service = BenzingaService()

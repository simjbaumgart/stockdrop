import requests
import os
from datetime import datetime

class BenzingaService:
    """
    Service to fetch Benzinga news via the Massive Data API (powered by Polygon.io).
    
    NOTE: Massive.com functionality is provided via Polygon.io endpoints.
    This service connects to Polygon's standardized news endpoint to retrieve
    content originating from Benzinga and other premium publishers provided by Massive.
    """
    def __init__(self):
        self.api_key = os.getenv("BENZINGA_API_KEY")
        # Massive / Polygon endpoint
        self.base_url = "https://api.polygon.io/v2/reference/news"

    def get_company_news(self, symbol: str):
        """
        Fetches news from the Massive Data stream (Polygon.io).
        
        This call routes through Polygon's infrastructure but targets the data 
        streams that include Massive's content partners (like Benzinga).
        """
        # Use BENZINGA_API_KEY env var, which should now hold the Polygon/Massive Key.
        if not self.api_key:
            print("Warning: BENZINGA_API_KEY not found.")
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

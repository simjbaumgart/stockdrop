
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import os

class PolygonService:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("POLYGON_API_KEY")
        if not self.api_key:
            print("WARNING: POLYGON_API_KEY not found in environment variables.")
        self.base_url = "https://api.polygon.io/v2/reference/news"

    def get_company_news(self, symbol: str, limit: int = 50) -> List[Dict]:
        """
        Fetches news for a company from Polygon.io.
        """
        try:
            params = {
                "apiKey": self.api_key,
                "ticker": symbol,
                "limit": limit,
                "order": "desc",
                "sort": "published_utc"
            }
            
            response = requests.get(self.base_url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                return self._parse_news(results)
            else:
                print(f"Error fetching Polygon news: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            print(f"Error in PolygonService: {e}")
            return []

    def _parse_news(self, items: List[Dict]) -> List[Dict]:
        news_list = []
        for item in items:
            try:
                title = item.get('title', 'No Title')
                url = item.get('article_url', '')
                
                # published_utc is "2024-12-10T..."
                pub_utc = item.get('published_utc', '')
                try:
                    dt = datetime.fromisoformat(pub_utc.replace('Z', '+00:00'))
                    ts = int(dt.timestamp())
                    dt_str = dt.strftime("%Y-%m-%d")
                except:
                    ts = 0
                    dt_str = pub_utc[:10]
                
                description = item.get('description', '') or ''
                
                # Polygon often just gives a description, not full body.
                # We check description length as proxy or look for 'keywords' etc.
                # Real 'content' not available on this endpoint usually.
                
                news_list.append({
                    "source": "Polygon.io",
                    "headline": title,
                    "summary": description,
                    "url": url,
                    "datetime": ts,
                    "datetime_str": dt_str,
                    "image": item.get('image_url', ''),
                    "has_full_text": len(description) > 500 # Unlikely for Polygon Free/Starter
                })
            except Exception:
                continue
                
        return news_list

polygon_service = PolygonService()

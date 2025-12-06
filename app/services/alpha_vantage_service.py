import os
import requests
import time
from datetime import datetime
from typing import List, Dict, Optional

class AlphaVantageService:
    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self):
        self.api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
        if not self.api_key:
            print("WARNING: ALPHA_VANTAGE_API_KEY not found. AlphaVantageService will use mock/empty data.")

    def get_company_news(self, symbol: str, start_date: str = None, end_date: str = None) -> List[Dict]:
        """
        Fetches news sentiment data from Alpha Vantage.
        
        Args:
            symbol: Stock ticker.
            start_date: YYYY-MM-DD format.
            end_date: YYYY-MM-DD format.
            
        Returns:
            List of news dictionaries formatted for internal use.
        """
        if not self.api_key:
            return []

        # Convert dates to Alpha Vantage format (YYYYMMDDTHHMM)
        # Default to last 7 days if not provided
        if not start_date or not end_date:
             # Basic fallback logic handled by caller usually, but good to have safety
             pass

        time_from = self._format_date(start_date) if start_date else ""
        time_to = self._format_date(end_date) if end_date else ""

        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "apikey": self.api_key,
            "sort": "LATEST",
            "limit": 50 # Max limit
        }
        
        if time_from:
            params["time_from"] = time_from
        if time_to:
            params["time_to"] = time_to

        try:
            response = requests.get(self.BASE_URL, params=params)
            
            # Rate Limit Handling (Simple wait)
            if response.status_code == 429 or "rate limit" in response.text.lower():
                print("Alpha Vantage Rate Limit Hit. Waiting 60 seconds...")
                time.sleep(60)
                response = requests.get(self.BASE_URL, params=params)
                
            data = response.json()
            
            if "feed" not in data:
                # print(f"Alpha Vantage: No feed found for {symbol}. Response: {data.keys()}")
                return []
                
            news_items = []
            for item in data.get("feed", []):
                # Parse standard fields
                title = item.get("title", "No Title")
                summary = item.get("summary", "")
                url = item.get("url", "")
                source = item.get("source", "Alpha Vantage")
                
                # Parse Date (20230101T123000)
                time_str = item.get("time_published", "")
                ts = 0
                dt_str = ""
                try:
                    dt_obj = datetime.strptime(time_str, "%Y%m%dT%H%M%S")
                    ts = dt_obj.timestamp()
                    dt_str = dt_obj.strftime("%Y-%m-%d")
                except ValueError:
                    pass

                news_items.append({
                    "source": source,
                    "headline": title,
                    "summary": summary,
                    "url": url,
                    "datetime": ts,
                    "datetime_str": dt_str,
                    "image": item.get("banner_image", "") 
                })
                
            return news_items

        except Exception as e:
            print(f"Error fetching Alpha Vantage news for {symbol}: {e}")
            return []

    def _format_date(self, date_str: str) -> str:
        """Converts YYYY-MM-DD to YYYYMMDDTHHMM"""
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.strftime("%Y%m%dT0000")
        except:
            return ""

alpha_vantage_service = AlphaVantageService()

import requests
import os
from dotenv import load_dotenv, find_dotenv
from datetime import datetime

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
            load_dotenv(find_dotenv(), override=True)
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
                # "apiKey": self.api_key, # Moved to Header
                "sort": "published_utc",
                "order": "desc"
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}"
            }
            
            response = requests.get(self.base_url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                return self._process_news(results)
            else:
                print(f"Polygon/Massive API Error: {response.status_code} - {response.text}")
                if response.status_code == 401:
                    print("Benzinga API: 401 Unauthorized. Check API key configuration.")
                    try:
                        import urllib.request
                        import json
                        
                        req = urllib.request.Request(response.url)
                        req.add_header("Authorization", f"Bearer {self.api_key}")
                        with urllib.request.urlopen(req) as f:
                            resp_body = f.read().decode('utf-8')
                            data = json.loads(resp_body)
                            # urllib fallback succeeded
                            results = data.get("results", [])
                            return self._process_news(results)
                    except Exception as fallback_err:
                        print(f"Fallback Failed: {fallback_err}")
                        
                    # Fallback: Subprocess CURL (Bypasses Python SSL/Network Stack)
                    try:
                        import subprocess
                        import json
                        
                        cmd = [
                            "curl", "-s",
                            "-H", f"Authorization: Bearer {self.api_key}",
                            response.url
                        ]
                        
                        result = subprocess.run(cmd, capture_output=True, text=True)
                        
                        if result.returncode == 0:
                            data = json.loads(result.stdout)
                            if "results" in data:
                                # CURL fallback succeeded
                                results = data.get("results", [])
                                return self._process_news(results)
                            else:
                                print(f"CURL JSON Error: {data}")
                        else:
                            print(f"CURL Execution Failed: {result.stderr}")
                            
                    except Exception as curl_err:
                        print(f"Nuclear Fallback Failed: {curl_err}")
                        
                return []
        except Exception as e:
            print(f"Error fetching Benzinga news: {e}")
            return []

        return processed

    def get_market_news(self, limit: int = 10) -> list:
        """
        Fetches broad market news using ETF proxies (SPY, DIA, QQQ).
        Returns a deduplicated list of the most recent news items.
        """
        market_tickers = ["SPY", "DIA", "QQQ"]
        all_news = []
        
        print(f"DEBUG: Fetching Market News for {market_tickers}...")
        
        for ticker in market_tickers:
            try:
                # Fetch recent news for each ticker
                news = self.get_company_news(ticker)
                if news:
                    all_news.extend(news)
            except Exception as e:
                print(f"Error fetching market news for {ticker}: {e}")
                
        # Deduplicate by headline
        unique_news = {}
        for item in all_news:
            headline = item.get('headline')
            if headline and headline not in unique_news:
                unique_news[headline] = item
                
        # Convert back to list
        final_list = list(unique_news.values())
        
        # Sort by date descending
        final_list.sort(key=lambda x: x.get('datetime', 0), reverse=True)
        
        # Limit result
        return final_list[:limit]

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

                # Extract Insights and Keywords to augment content
                insights = item.get("insights", [])
                keywords = item.get("keywords", [])
                
                content_parts = []
                
                # Add Description/Summary if available as intro
                description = item.get("description", "")
                if description:
                    content_parts.append(f"Summary: {description}")

                if insights:
                    content_parts.append("\nInsight Analysis:")
                    for insight in insights:
                        ticker = insight.get("ticker", "N/A")
                        sentiment = insight.get("sentiment", "unknown")
                        reasoning = insight.get("sentiment_reasoning", "")
                        content_parts.append(f"- [{ticker}] {sentiment.upper()}: {reasoning}")
                
                if keywords:
                    content_parts.append(f"\nKeywords: {', '.join(keywords)}")

                full_content = "\n".join(content_parts)

                processed.append({
                    "source": publisher, # e.g. "Benzinga", "The Motley Fool"
                    "headline": item.get("title", ""),
                    "summary": description, 
                    "content": full_content,   # Augmented with insights and keywords
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

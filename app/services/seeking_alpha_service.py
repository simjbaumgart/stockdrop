import os
import json
import logging
import google.generativeai as genai
import requests
import time
from typing import Optional, Dict, List, Any
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SeekingAlphaService:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.rapidapi_key = os.getenv("RAPIDAPI_KEY_SEEKING_ALPHA")
        self.rapidapi_host = "seeking-alpha.p.rapidapi.com"
        
        if self.api_key:
            genai.configure(api_key=self.api_key)
            # Using valid Flash model (matching ResearchService)
            self.flash_model = genai.GenerativeModel('gemini-3-flash-preview') 
        else:
            logger.warning("GEMINI_API_KEY not found. Seeking Alpha Service will lack AI cleaning.")
            self.flash_model = None

        if not self.rapidapi_key:
            logger.warning("RAPIDAPI_KEY_SEEKING_ALPHA not found. Dynamic fetching disabled.")

    def _call_endpoint(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """Helper to call RapidAPI endpoint."""
        if not self.rapidapi_key:
            return None
            
        url = f"https://{self.rapidapi_host}/{endpoint}"
        headers = {
            "x-rapidapi-key": self.rapidapi_key,
            "x-rapidapi-host": self.rapidapi_host
        }
        try:
            # logger.info(f"Calling Seeking Alpha API: {endpoint}")
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error calling {endpoint}: {e}")
            return None

    def _get_symbol_id(self, ticker: str) -> Optional[str]:
        """Resolves ticker to Seeking Alpha ID (though many endpoints accept ticker string)."""
        data = self._call_endpoint("auto-complete", {"term": ticker})
        if not data or 'symbols' not in data:
            return None
            
        # Try exact match first
        for item in data['symbols']:
            if item.get('name') == ticker:
                return item.get('id')
                
        # Fallback to slugs
        for item in data['symbols']:
            if item.get('slug') == ticker.lower():
                 return item.get('id')
                 
        # Fallback to first
        if data['symbols']:
             return data['symbols'][0].get('id')
             
        return None

    def fetch_data_for_ticker(self, ticker: str) -> Dict[str, List[Dict]]:
        """
        Dynamically fetches News, Analysis, and PRs for a ticker.
        Returns a dict with keys: 'news', 'analysis', 'press_releases' (cleaned items).
        """
        print(f"  > [Seeking Alpha Service] Fetching FRESH data for {ticker}...")
        
        # We use the ticker string directly for lists as per previous script findings
        # Limits: 3 items per category (standardizing on "Recent 3")
        
        fetched_data = {
            "news": [],
            "analysis": [],
            "press_releases": []
        }
        
        # Helper to fetch list -> fetch details
        def fetch_category(list_endpoint, detail_endpoint, category):
            params = {"id": ticker, "size": 4}
            # PR list uses 'id' but sometimes we used numeric ID. Lets stick to ticker first.
            if category == "press_releases":
                 # PR endpoint might be sensitive, try ticker first
                 pass
            
            list_resp = self._call_endpoint(list_endpoint, params)
            if not list_resp or 'data' not in list_resp:
                return
            
            items = list_resp['data'][:4]
            for item in items:
                item_id = item.get('id')
                # Detail fetch
                details = self._call_endpoint(detail_endpoint, {"id": item_id})
                if details:
                    # Extract useful fields immediately
                    attrs = details.get('data', {}).get('attributes', {})
                    fetched_data[category].append({
                        "title": attrs.get('title'),
                        "publishOn": attrs.get('publishOn'),
                        "content": attrs.get('content')
                    })
                time.sleep(0.2) # Soft rate limit protection

        # 1. Analysis
        fetch_category("analysis/v2/list", "analysis/v2/get-details", "analysis")
        
        # 2. News
        fetch_category("news/v2/list-by-symbol", "news/get-details", "news")
        
        # 3. Press Releases
        fetch_category("press-releases/v2/list", "press-releases/get-details", "press_releases")
        
        return fetched_data

    def fetch_wall_street_breakfast(self) -> List[Dict]:
        """
        Dynamically fetches the latest Wall Street Breakfast article.
        """
        print(f"  > [Seeking Alpha Service] Fetching FRESH Wall Street Breakfast...")
        
        # 1. List
        list_resp = self._call_endpoint("articles/list-wall-street-breakfast", {"size": 1})
        if not list_resp or 'data' not in list_resp:
            return []
            
        raw_data = list_resp['data']
        # API might return a single dict or a list
        if isinstance(raw_data, dict):
             items = [raw_data]
        else:
             items = raw_data
             
        fetched_items = []
        
        for item in items:
            item_id = item.get('id')
            # Detail fetch (try analysis endpoint first as per script findings)
            details = self._call_endpoint("analysis/v2/get-details", {"id": item_id})
            if not details:
                 details = self._call_endpoint("articles/get-details", {"id": item_id})
                 
            if details:
                attrs = details.get('data', {}).get('attributes', {})
                fetched_items.append({
                    "title": attrs.get('title'),
                    "publishOn": attrs.get('publishOn'),
                    "content": attrs.get('content')
                })
        
        return fetched_items

    def _save_fetched_data(self, ticker: Optional[str], data: Any, type: str = "stock"):
        """Updates the local JSON cache with new data."""
        try:
            path = "experiment_data/agent_context.json"
            
            if os.path.exists(path):
                with open(path, "r") as f:
                    context = json.load(f)
            else:
                context = {"stocks": {}, "wall_street_breakfast": []}
                
            if type == "stock" and ticker:
                context["stocks"][ticker] = data
            elif type == "wsb":
                context["wall_street_breakfast"] = data
            
            with open(path, "w") as f:
                json.dump(context, f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to save fetched data: {e}")

    def get_evidence(self, ticker: str) -> str:
        """
        Loads cleaned Seeking Alpha data (News, Analysis, PR) for the ticker.
        If data is missing/stale, it fetches it dynamically.
        """
        try:
            # 1. Attempt to load existing data
            sa_path = "experiment_data/agent_context.json"
            
            # Helper to load context
            def load_context():
                if os.path.exists(sa_path):
                    with open(sa_path, "r") as f:
                        return json.load(f)
                return {"stocks": {}, "wall_street_breakfast": []}

            data = load_context()
            stock_data = data.get("stocks", {}).get(ticker)

            # 2. If missing, FETCH dynamically (Stock Data)
            if not stock_data:
                if self.rapidapi_key:
                    logger.info(f"Data missing for {ticker}. Fetching from Seeking Alpha...")
                    stock_data = self.fetch_data_for_ticker(ticker)
                    if stock_data: 
                        self._save_fetched_data(ticker, stock_data, type="stock")
                        data = load_context() 
                else:
                    msg = "Seeking Alpha Data: Not Available (Missing API Key)"
                    # We continue to check WSB even if stock data failed, though unlikely if key is missing.
                    # But returning early is safer for clarity.
                    if not data.get("wall_street_breakfast"): # If both missing
                         return msg

            # 3. Check WSB (Fetcher)
            wsb_items = data.get("wall_street_breakfast", [])
            if not wsb_items and self.rapidapi_key:
                 logger.info("WSB data missing. Fetching...")
                 wsb_items = self.fetch_wall_street_breakfast()
                 if wsb_items:
                     self._save_fetched_data(None, wsb_items, type="wsb")
                     # No need to reload full context, just use local var
            
            if not stock_data:
                 stock_msg = f"Seeking Alpha Data: No specific data found for ticker {ticker}"
            else:
                 stock_msg = "" # Will build evidence below

            # Format Output
            evidence = f"# SEEKING ALPHA EVIDENCE LAYER: {ticker}\n\n"
            
            # Calculate Stats
            analysis_items = stock_data.get("analysis", [])
            analysis_count = len(analysis_items)
            analysis_len = sum(len(item.get('content', '')) for item in analysis_items)
            
            news_items = stock_data.get("news", [])
            news_count = len(news_items)
            news_len = sum(len(item.get('content', '')) for item in news_items)
            
            pr_items = stock_data.get("press_releases", [])
            pr_count = len(pr_items)
            pr_len = sum(len(item.get('content', '')) for item in pr_items)
            
            wsb_len = sum(len(item.get('content', '')) for item in wsb_items) if wsb_items else 0
            
            # Console Output (Stats)
            print(f"\n  > [Seeking Alpha Service] Data Statistics for {ticker}:")
            print(f"    [SPECIFIC] Analysis:       {analysis_count} articles (Total Len: {analysis_len})")
            print(f"    [SPECIFIC] Breaking News:  {news_count} items    (Total Len: {news_len})")
            print(f"    [SPECIFIC] Press Releases: {pr_count} items    (Total Len: {pr_len})")
            print(f"    [BROAD]    Sentimen (WSB): {len(wsb_items)} items    (Total Len: {wsb_len})")
            
            if analysis_count == 0 and news_count == 0 and pr_count == 0:
                print(f"    [WARNING] No company-specific Seeking Alpha data found for {ticker}.")

            # Deduplication sets
            seen_titles = set()
            
            def is_duplicate(item):
                title = item.get('title', '').strip()
                if not title: return False
                # Simple normalization
                norm_title = title.lower()
                if norm_title in seen_titles:
                    return True
                seen_titles.add(norm_title)
                return False

            # 1. Analysis (High Value)
            evidence += "## ANALYST SENTIMENT (Seeking Alpha)\n"
            if analysis_items:
                for item in analysis_items:
                    if is_duplicate(item): continue
                    
                    title = item.get('title')
                    date = item.get("publishOn")
                    raw_content = item.get('content', '')
                    
                    # AI Cleaning
                    cleaned_content = self._clean_content_with_ai(raw_content, context=f"Analysis: {title}")
                    
                    evidence += f"### {title}\n"
                    evidence += f"Published: {date}\n"
                    evidence += f"{cleaned_content}\n\n"
            else:
                evidence += "No specific Analysis articles found.\n\n"
                
            # 2. Breaking News
            evidence += "## BREAKING NEWS (Seeking Alpha)\n"
            if news_items:
                for item in news_items:
                    if is_duplicate(item): continue
                    
                    title = item.get('title')
                    date = item.get('publishOn')
                    raw_content = item.get('content', '')
                    
                    # AI Cleaning (even for news, often contains HTML)
                    cleaned_content = self._clean_content_with_ai(raw_content, context=f"News: {title}")
                    
                    evidence += f"- **{title}** ({date})\n"
                    evidence += f"  {cleaned_content}\n\n"
            else:
                 evidence += "No specific Breaking News found.\n\n"

            # 3. Press Releases
            evidence += "## PRESS RELEASES (Official)\n"
            if pr_items:
                for item in pr_items:
                    if is_duplicate(item): continue
                    
                    title = item.get('title')
                    raw_content = item.get('content', '')
                    # PRs often cleaner but safe to clean
                    cleaned_content = self._clean_content_with_ai(raw_content, context=f"PR: {title}")
                    
                    evidence += f"- **{title}** ({item.get('publishOn')})\n"
                    evidence += f"  {cleaned_content}\n\n"
            else:
                evidence += "No Press Releases found.\n\n"

            # 4. Wall Street Breakfast (Daily Context)
            evidence += "## WALL STREET BREAKFAST (Market Context)\n"
            
            # Use Caching Logic
            # Note: wsb_items from 'agent_context_cleaned' might be already cleaned if that flow executes?
            # But here we are assuming 'agent_context.json' might be the source if _cleaned is missing.
            # However, if 'agent_context_cleaned.json' exists, it means consolidated_reports ran?
            # Actually, fetch_batch saves to 'agent_context.json'. The 'consolidate_reports.py' was just a test/demo script.
            # So 'agent_context.json' usually has RAW content.
            # So we should ALWAYS use our cache logic to safe-guard.
            
            cleaned_wsb_items = self._get_or_create_wsb_cache(wsb_items)
            
            if cleaned_wsb_items:
                for item in cleaned_wsb_items:
                     title = item.get('title')
                     content = item.get('content')
                     
                     evidence += f"### {title}\n"
                     evidence += f"{content}\n\n"
            else:
                evidence += "No Wall Street Breakfast data.\n"

            print(f"  > [Seeking Alpha Service] Evidence compilation complete.")

            return evidence

        except Exception as e:
            logger.error(f"Error loading Seeking Alpha data: {e}")
            return f"Seeking Alpha Data: Error loading data ({e})"

    def get_counts(self, ticker: str) -> Dict[str, int]:
        """
        Returns a dictionary of article counts for the ticker.
        Used for console logging in StockService.
        Triggers fetch if data is missing.
        """
        try:
            # Same path logic
            sa_path = "experiment_data/agent_context.json"
            
            # Helper to load
            def load_context():
                if os.path.exists(sa_path):
                    with open(sa_path, "r") as f:
                        return json.load(f)
                return {"stocks": {}, "wall_street_breakfast": []}

            data = load_context()
            stock_data = data.get("stocks", {}).get(ticker, {})
            
            # Dynamic Fetch Trigger
            if not stock_data:
                if self.rapidapi_key:
                     # We can just rely on get_evidence doing it, OR do it here. 
                     # Doing it here ensures stats are ready immediately for the console log.
                     stock_data = self.fetch_data_for_ticker(ticker)
                     if stock_data:
                         self._save_fetched_data(ticker, stock_data)
                         data = load_context() # Reload
                         stock_data = data.get("stocks", {}).get(ticker, {})
            
            analysis_count = len(stock_data.get("analysis", []))
            news_count = len(stock_data.get("news", []))
            pr_count = len(stock_data.get("press_releases", []))
            
            # WSB is global, so it technically exists for all, but let's count it
            wsb_items = data.get("wall_street_breakfast", [])
            wsb_count = len(wsb_items)
            wsb_date = "N/A"
            if wsb_items:
                # Try to get date from first item
                raw_date = wsb_items[0].get("publishOn", "")
                if raw_date:
                    # Keep it simple or format it. Raw is usually ISO-like or date string.
                    # Just taking the date part if possible (YYYY-MM-DD)
                    wsb_date = raw_date.split("T")[0]
            
            # Total specific to the company
            total_specific = analysis_count + news_count + pr_count
            
            return {
                "analysis": analysis_count,
                "news": news_count,
                "pr": pr_count,
                "wsb": wsb_count,
                "wsb_date": wsb_date,
                "total": total_specific
            }
        except Exception as e:
            logger.error(f"Error getting SA counts: {e}")
            return {"analysis": 0, "news": 0, "pr": 0, "wsb": 0, "total": 0}

    def _get_or_create_wsb_cache(self, raw_items: List[Dict]) -> List[Dict]:
        """
        Manages daily caching for Wall Street Breakfast cleaning.
        Returns the cleaned items list.
        """
        if not raw_items:
            return []

        # Cache file path: data/wall_street_breakfast/processed_YYYY-MM-DD.json
        today_str = datetime.now().strftime("%Y-%m-%d")
        cache_dir = "data/wall_street_breakfast"
        cache_file = os.path.join(cache_dir, f"processed_{today_str}.json")

        # 1. Try to load from cache
        if os.path.exists(cache_file):
            try:
                # logger.info(f"Loading cached WSB data from {cache_file}")
                with open(cache_file, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error reading WSB cache: {e}. Will re-process.")

        # 2. Process (Clean) if cache missing or invalid
        logger.info(f"Processing and Cleaning WSB data for {today_str}...")
        cleaned_items = []
        
        # Take up to 2 items (matching original logic) or all?
        # The prompt says "Just take the latest one or two".
        # Let's clean up to 2 items for the cache to match usage.
        
        for item in raw_items[:2]:
            title = item.get('title')
            # Handle different structures if coming from different sources, but here we expect 'content' key
            # In raw 'agent_context.json' it might be 'content'.
            raw_content = item.get('content', '')
            
            # AI Cleaning (Strict No Summarization)
            cleaned_content = self._clean_content_with_ai(raw_content, context=f"WSB: {title}")
            
            cleaned_items.append({
                "title": title,
                "content": cleaned_content,
                "publishOn": item.get("publishOn")
            })

        # 3. Save to cache
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_file, "w") as f:
                json.dump(cleaned_items, f, indent=2)
            print(f"  > [Seeking Alpha Service] Cached cleaned WSB data to {cache_file}")
        except Exception as e:
            logger.error(f"Error saving WSB cache: {e}")

        return cleaned_items

    def _clean_html(self, content: str) -> str:
        """
        Cleans HTML/Ads from text using standard parsing (No AI).
        Preserves content length/integrity but removes tags and noise.
        """
        if not content:
            return ""
            
        try:
            # 1. Regex Clean for common junk
            import re
            
            # Remove scripts and styles
            content = re.sub(r'<(script|style).*?</\1>', '', content, flags=re.DOTALL)
            
            # Remove attributes but keep some tags? No, easier to just strip all tags for raw text
            # Or use a simple heuristic to preserve structure.
            
            # 2. BeautifulSoup Text Extraction (Smart Structure)
            from bs4 import BeautifulSoup
            # Use 'lxml' if available, else 'html.parser'
            soup = BeautifulSoup(content, 'html.parser')
            
            # Remove script/style/ads
            for tag in soup(["script", "style", "noscript", "iframe", "header", "footer", "nav", "aside"]):
                tag.decompose()
                
            # Remove ad containers
            for div in soup.find_all("div", class_=lambda x: x and ('ad-container' in x or 'advertisement' in x)):
                div.decompose()

            # Strategy: Inject newlines after block elements, then use get_text with space separator
            block_tags = ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'div', 'li', 'article', 'section', 'blockquote']
            for tag in soup.find_all(block_tags):
                tag.insert_after('\n')
                
            # Handle <br> explicitly
            for br in soup.find_all('br'):
                br.replace_with('\n')

            # Get text with space separator (handles inline tags like <span>, <a>, <b>)
            text = soup.get_text(separator=' ')
            
            # Post-processing cleanup
            import re
            # 1. Collapse multiple spaces into one
            text = re.sub(r'[ \t]+', ' ', text)
            # 2. Fix spaces before punctuation (artifact of tag stripping)
            # text = re.sub(r'\s+([.,])', r'\1', text) # Risky if code points exist
            
            # 3. Collapse multiple newlines (created by block injection) into max 2
            text = re.sub(r'\n\s*\n', '\n\n', text)
            
            return text.strip()
            
        except Exception as e:
            logger.error(f"Error cleaning content: {e}")
            return content

    def _clean_content_with_ai(self, content: str, context: str = "") -> str:
        """
        Legacy name adapter to _clean_html to avoid breaking callsites.
        """
        return self._clean_html(content)

seeking_alpha_service = SeekingAlphaService()

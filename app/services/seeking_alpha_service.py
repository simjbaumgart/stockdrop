import os
import json
import logging
import google.generativeai as genai
from typing import Optional, Dict, List
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SeekingAlphaService:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            genai.configure(api_key=self.api_key)
            # Using the Flash model as requested for cleaning/digest
            self.flash_model = genai.GenerativeModel('gemini-3.5-flash-preview') 
            # Note: User requested 'gemini-3.5-flash'.
        else:
            logger.warning("GEMINI_API_KEY not found. Seeking Alpha Service will lack AI cleaning.")
            self.flash_model = None

    def get_evidence(self, ticker: str) -> str:
        """
        Loads cleaned Seeking Alpha data (News, Analysis, PR) for the ticker.
        """
        try:
            # Path to the CLEANED context file
            sa_path = "experiment_data/agent_context_cleaned.json"
            
            # Fallback to agent_context.json if cleaned one doesn't exist (simulating the consolidation flow)
            if not os.path.exists(sa_path):
                sa_path = "experiment_data/agent_context.json"
                
            if not os.path.exists(sa_path):
                return "Seeking Alpha Data: Not Available (File missing)"
            
            with open(sa_path, "r") as f:
                data = json.load(f)
            
            # Extract Stock Data
            stock_data = data.get("stocks", {}).get(ticker, {})
            if not stock_data:
                return f"Seeking Alpha Data: No data found for ticker {ticker}"
                
            # Extract WSB Data (General Market Context) - Fixed logic
            # WSB is usually top-level list
            wsb_items = data.get("wall_street_breakfast", [])
            
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

    def _clean_content_with_ai(self, content: str, context: str = "") -> str:
        """
        Uses Gemini Flash to clean HTML/Ads/Scraping artifacts from text.
        CRITICAL: Does NOT summarize. Preserves extensive content.
        """
        if not self.flash_model or not content:
            return content
        
        # Heuristic: If content is already short or clean-ish, skip to save latency
        if len(content) < 300:
             return content

        prompt = f"""
        You are a **Content Cleaning Expert**.
        Your task is to convert the following RAW HTML/Text into clean, readable Markdown.
        
        CONTEXT: {context}
        
        RULES:
        1. **REMOVE NOISE**: Strip out HTML tags, JavaScript blobs, "Click to enlarge", "Join now", "Subscribe", advertisements, and footer links.
        2. **PRESERVE TEXT**: Do NOT summarize. Do NOT shorten. Keep the original paragraphs, bullet points, and structure.
        3. **PRESERVE LENGTH**: The output length should be roughly equal to the input text length (minus the garbage).
        4. **FORMAT**: Use standard Markdown (## Headers, **Bold**, - Bullets).
        
        RAW CONTENT:
        {content[:40000]} 
        """
        # Cap input at 40k chars to avoid token limits, though Flash handles 1M. 
        # But for speed/cost, reasonable limit.
        
        try:
             # Using rate-limit safe generation if needed, but here simple call
             response = self.flash_model.generate_content(prompt)
             cleaned = response.text.strip()
             
             # Metric Check (Console only)
             if len(content) > 0:
                ratio = len(cleaned) / len(content)
                # logger.info(f"AI Cleaning ({context}): {len(content)} -> {len(cleaned)} (Ratio: {ratio:.2f})")
                if ratio < 0.5:
                    logger.warning(f"  > [Cleaning Warning] Significant compression detected for {context} (Ratio: {ratio:.2f}). Original preserved if needed.")
                 
             return cleaned
        except Exception as e:
             logger.error(f"Error cleaning content ({context}): {e}")
             return content # Fallback to raw

seeking_alpha_service = SeekingAlphaService()

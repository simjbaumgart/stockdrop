from typing import Dict, List, Optional
from datetime import datetime

class EvidenceService:
    @staticmethod
    def collect_barometer(raw_data: Dict, agent_reports: Dict) -> Dict:
        """
        Constructs a comprehensive Evidence Barometer dictionary from raw inputs and agent outputs.
        
        Args:
            raw_data (Dict): The input data passed to the Research Council.
            agent_reports (Dict): The output reports from the agents.
            
        Returns:
            Dict: A dictionary containing granular evidence metrics.
        """
        # 1. Analyze News
        news_barometer = EvidenceService._analyze_news(raw_data.get('news_items', []))
        
        # 2. Analyze Fundamentals (Transcript)
        fundamentals_barometer = EvidenceService._analyze_transcript(
            raw_data.get('transcript_text', ""),
            raw_data.get('transcript_date')
        )
        
        # 3. Analyze Agent Outputs
        agent_barometer = EvidenceService._measure_reports(agent_reports)
        
        # 4. Metadata
        metadata = {
            "analyzed_at": datetime.now().isoformat(),
            "stock_ticker": raw_data.get('ticker', 'UNKNOWN') # raw_data might not have ticker directly if not passed specific way, but usually is available in context or we can pass it.
            # actually research_service calls analyze_stock(ticker, raw_data). 
            # We might want to pass ticker explicitly to this function or rely on it being in raw_data if we put it there.
            # Looking at stock_service.py, ticker is not in raw_data by default, it's passed as arg to analyze_stock.
            # Let's handle 'unknown' gracefully.
        }

        return {
            "news": news_barometer,
            "fundamentals": fundamentals_barometer,
            "agents": agent_barometer,
            "metadata": metadata
        }

    @staticmethod
    def _analyze_news(news_items: List[Dict]) -> Dict:
        """
        Analyzes the news items to build a provider histogram and time range.
        """
        if not news_items:
            return {
                "total_count": 0,
                "total_length_chars": 0,
                "providers": {},
                "time_range": {"newest": None, "oldest": None}
            }
            
        total_count = len(news_items)
        total_length = 0
        providers = {}
        dates = []
        
        for item in news_items:
            # Length
            content = item.get('content', '') or ''
            summary = item.get('summary', '') or ''
            headline = item.get('headline', '') or ''
            total_length += len(headline) + len(content) + len(summary)
            
            # Provider
            # Check for 'provider' key, or infer from 'source'
            prov = item.get('provider') or item.get('source') or 'Unknown'
            # Normalize common names if needed, but raw is fine for now
            providers[prov] = providers.get(prov, 0) + 1
            
            # Date
            d_str = item.get('datetime_str')
            if d_str:
                dates.append(d_str)
                
        # Sort dates to find range
        # Assuming ISO format or comparable strings, naive sort works for "YYYY-MM-DD"
        # If dates are messy, we might just store nulls.
        newest = max(dates) if dates else None
        oldest = min(dates) if dates else None
        
        return {
            "total_count": total_count,
            "total_length_chars": total_length,
            "providers": providers,
            "time_range": {
                "newest": newest,
                "oldest": oldest
            }
        }

    @staticmethod
    def _analyze_transcript(text: str, date_str: Optional[str]) -> Dict:
        """
        Analyzes the transcript availability and length.
        """
        text = text or ""
        is_available = len(text) > 100 # Arbitrary threshold for "real" content
        
        return {
            "transcript_available": is_available,
            "transcript_date": date_str,
            "transcript_length": len(text)
        }

    @staticmethod
    def _measure_reports(reports: Dict) -> Dict:
        """
        Measures the length of the generated agent reports.
        """
        return {
            f"{key}_length": len(val) if val else 0
            for key, val in reports.items()
        }

# Optional: Singleton instance if needed, but static methods work fine here.
evidence_service = EvidenceService()

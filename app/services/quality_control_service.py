from typing import Dict, Optional
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class QualityControlService:
    @staticmethod
    def validate_reports(reports: Dict[str, Optional[str]], ticker: str, keys_to_check: list) -> Dict[str, Optional[str]]:
        """
        Validates the length and presence of reports for the given keys.
        Flags short or missing reports.
        """
        for key in keys_to_check:
            content = reports.get(key)
            
            if isinstance(content, str):
                if len(content) < 100:
                    msg = f"[WARNING] Report section '{key}' is suspiciously short ({len(content)} chars) for {ticker}. Flagging input."
                    print(msg) 
                    reports[key] = f"[SHORT INPUT DETECTED: Length {len(content)} < 100] {content}"
            elif content is None:
                if key != "seeking_alpha":
                     print(f"[WARNING] Report section '{key}' is MISSING (None) for {ticker}.")
        
        return reports

    @staticmethod
    def validate_council_reports(reports: Dict[str, Optional[str]], ticker: str) -> Dict[str, Optional[str]]:
        """Legacy wrapper for Council 1 reports."""
        keys = ["technical", "news", "market_sentiment", "economics", "competitive", "seeking_alpha"]
        return QualityControlService.validate_reports(reports, ticker, keys)

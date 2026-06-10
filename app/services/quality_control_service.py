from typing import Dict, Optional
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class QualityControlService:
    # Agents that run conditionally and legitimately produce empty output when
    # they don't fire (e.g. economics is skipped on NEEDS_ECONOMICS: FALSE).
    # An empty string from these is expected, not a defect — don't flag it and
    # don't rewrite the field with the [SHORT INPUT DETECTED] sentinel (which
    # would inject noise into a field the pipeline intentionally left blank).
    CONDITIONAL_KEYS = {"economics"}

    @staticmethod
    def validate_reports(reports: Dict[str, Optional[str]], ticker: str, keys_to_check: list) -> Dict[str, Optional[str]]:
        """
        Validates the length and presence of reports for the given keys.
        Flags short or missing reports.
        """
        for key in keys_to_check:
            content = reports.get(key)

            if isinstance(content, str):
                # Skip the length check for conditional agents that are
                # legitimately empty (they didn't run this time).
                if key in QualityControlService.CONDITIONAL_KEYS and content == "":
                    continue
                if len(content) < 200:
                    msg = f"[WARNING] Report section '{key}' is suspiciously short ({len(content)} chars) for {ticker}. Flagging input."
                    print(msg)
                    reports[key] = f"[SHORT INPUT DETECTED: Length {len(content)} < 200] {content}"
            elif content is None:
                if key not in ("seeking_alpha", *QualityControlService.CONDITIONAL_KEYS):
                     print(f"[WARNING] Report section '{key}' is MISSING (None) for {ticker}.")

        return reports

    @staticmethod
    def validate_council_reports(reports: Dict[str, Optional[str]], ticker: str) -> Dict[str, Optional[str]]:
        """Legacy wrapper for Council 1 reports."""
        keys = ["technical", "news", "market_sentiment", "economics", "competitive", "seeking_alpha"]
        return QualityControlService.validate_reports(reports, ticker, keys)

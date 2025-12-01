import google.generativeai as genai
import os
import logging
import json
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ResearchService:
    MAX_DAILY_REPORTS = 3
    USAGE_FILE = "usage_stats.json"

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel('gemini-pro')
        else:
            logger.warning("GEMINI_API_KEY not found. Research service will use mock data.")
            self.model = None

    def analyze_stock(self, symbol: str, price: float, change_percent: float) -> str:
        """
        Analyzes a stock using Gemini API to determine if it's a buy opportunity.
        Enforces a daily limit on the number of reports.
        """
        if not self._check_and_increment_usage():
            logger.warning(f"Daily research limit reached. Skipping analysis for {symbol}.")
            return "Daily research limit reached. Please try again tomorrow."

        if not self.model:
            return self._get_mock_analysis(symbol, price, change_percent)

        try:
            prompt = self._create_prompt(symbol, price, change_percent)
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Error generating research for {symbol}: {e}")
            return f"Error generating research report: {str(e)}"

    def _check_and_increment_usage(self) -> bool:
        """
        Checks if the daily limit has been reached. If not, increments the counter.
        Returns True if allowed, False if limit reached.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        stats = self._load_usage_stats()
        
        if stats.get("date") != today_str:
            # Reset for new day
            stats = {"date": today_str, "count": 0}
        
        if stats["count"] >= self.MAX_DAILY_REPORTS:
            return False
        
        stats["count"] += 1
        self._save_usage_stats(stats)
        return True

    def _load_usage_stats(self) -> dict:
        try:
            if os.path.exists(self.USAGE_FILE):
                with open(self.USAGE_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading usage stats: {e}")
        return {"date": "", "count": 0}

    def _save_usage_stats(self, stats: dict):
        try:
            with open(self.USAGE_FILE, 'w') as f:
                json.dump(stats, f)
        except Exception as e:
            logger.error(f"Error saving usage stats: {e}")

    def _create_prompt(self, symbol: str, price: float, change_percent: float) -> str:
        return (
            f"You are a financial analyst. The stock {symbol} has dropped by {change_percent:.2f}% today "
            f"and is currently trading at ${price:.2f}. "
            "Analyze if this is a good buying opportunity and if further drops can be expected. "
            "Provide a clear 'YES' or 'NO' recommendation for buying now at the start, "
            "followed by a brief reasoning. "
            "Format the output as:\n"
            "Decision: [YES/NO]\n"
            "Reasoning: [Your reasoning]"
        )

    def _get_mock_analysis(self, symbol: str, price: float, change_percent: float) -> str:
        return (
            f"Decision: YES (MOCK)\n"
            f"Reasoning: This is a mock analysis for {symbol}. The drop of {change_percent:.2f}% "
            "appears to be an overreaction to market news. Fundamentals remain strong."
        )

research_service = ResearchService()

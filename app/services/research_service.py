import google.generativeai as genai
import os
import logging
import json
from datetime import datetime
import concurrent.futures

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ResearchService:
    MAX_DAILY_REPORTS = 1000
    USAGE_FILE = "usage_stats.json"

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if self.api_key:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel('gemini-3-pro-preview')
        else:
            logger.warning("GEMINI_API_KEY not found. Research service will use mock data.")
            self.model = None

    def analyze_stock(self, symbol: str, company_name: str, price: float, change_percent: float) -> dict:
        """
        Analyzes a stock using a multi-agent approach (Analyst, Bull, Bear, Synthesizer).
        """
        if not self._check_and_increment_usage():
            logger.warning(f"Daily research limit reached. Skipping analysis for {symbol}.")
            return {
                "recommendation": "SKIP",
                "executive_summary": "Daily research limit reached.",
                "detailed_report": "Please try again tomorrow.",
                "reasoning": "Daily research limit reached. Please try again tomorrow."
            }

        if not self.model:
            return self._get_mock_analysis(symbol, price, change_percent)

        try:
            # 1. The Analyst (Agent A)
            analyst_prompt = self._create_analyst_prompt(symbol, company_name, price, change_percent)
            analyst_report = self._call_agent(analyst_prompt, "Analyst")
            
            # 2. Bull & Bear (Parallel)
            bull_prompt = self._create_bull_prompt(symbol, company_name, analyst_report)
            bear_prompt = self._create_bear_prompt(symbol, company_name, analyst_report)
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                future_bull = executor.submit(self._call_agent, bull_prompt, "Bull")
                future_bear = executor.submit(self._call_agent, bear_prompt, "Bear")
                
                bull_case = future_bull.result()
                bear_case = future_bear.result()
            
            # 3. The Synthesizer (Agent D)
            synthesizer_prompt = self._create_synthesizer_prompt(symbol, company_name, analyst_report, bull_case, bear_case)
            final_response_text = self._call_agent(synthesizer_prompt, "Synthesizer")
            
            # Parse the final response
            result = self._parse_synthesizer_response(final_response_text)
            
            # Add intermediate reports to the result
            result["analyst_report"] = analyst_report
            result["bull_case"] = bull_case
            result["bear_case"] = bear_case
            
            return result

        except Exception as e:
            logger.error(f"Error generating research for {symbol}: {e}")
            return {
                "recommendation": "ERROR",
                "executive_summary": "An error occurred while generating the report.",
                "detailed_report": f"Error details: {str(e)}",
                "full_text": str(e)
            }

    def _call_agent(self, prompt: str, agent_name: str) -> str:
        """Helper to call Gemini with error handling and logging."""
        try:
            logger.info(f"Calling Agent: {agent_name}")
            # Standard generation
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Error in {agent_name} agent: {e}")
            return f"[Error generating {agent_name} report: {e}]"

    def _parse_synthesizer_response(self, text: str) -> dict:
        """Parses the final output from the Synthesizer agent."""
        recommendation = "HOLD"
        executive_summary = "No summary provided."
        detailed_report = text

        lines = text.split('\n')
        
        # Extract Score
        for line in lines:
            if line.upper().startswith("SCORE:"):
                try:
                    score_val = line.split(":", 1)[1].strip()
                    # Handle potential "/10" suffix
                    if "/" in score_val:
                        score_val = score_val.split("/")[0]
                    recommendation = str(float(score_val))
                except Exception:
                    recommendation = "5.0" # Default to neutral if parsing fails
                break
        
        # Extract Executive Summary and Detailed Report
        try:
            if "EXECUTIVE SUMMARY:" in text and "DETAILED REPORT:" in text:
                parts = text.split("DETAILED REPORT:")
                summary_part = parts[0].split("EXECUTIVE SUMMARY:")[1]
                detailed_report = parts[1].strip()
                executive_summary = summary_part.strip()
        except Exception as parse_err:
            logger.warning(f"Failed to parse structured output: {parse_err}")

        return {
            "recommendation": recommendation,
            "executive_summary": executive_summary,
            "detailed_report": detailed_report,
            "full_text": text
        }

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

    def _create_analyst_prompt(self, symbol: str, company_name: str, price: float, change_percent: float) -> str:
        return (
            f"You are a Senior Financial Analyst known for your skepticism. Your task is to provide a neutral, data-driven assessment of {company_name} ({symbol}).\n"
            f"The stock has dropped {change_percent:.2f}% recently. Current Price: {price}\n\n"
            "Analyze the following:\n"
            "1. What triggered this drop? (News, Earnings, Macro)\n"
            "2. Key Fundamentals (P/E, Revenue Growth, Margins)\n"
            "3. Technical Status (Support levels, RSI, Volume)\n\n"
            "Provide a factual report. Do not sugarcoat anything. Focus on the risks."
        )

    def _create_bull_prompt(self, symbol: str, company_name: str, analyst_report: str) -> str:
        return (
            f"You are a Value Investor. Your goal is to find the hidden value in {company_name} ({symbol}), but ONLY if it is truly there.\n"
            "Review the Analyst Report below and construct a BULL CASE.\n\n"
            f"--- ANALYST REPORT ---\n{analyst_report}\n----------------------\n\n"
            "Focus on:\n"
            "- Is this an overreaction?\n"
            "- What are the tangible long-term growth catalysts?\n"
            "- Is the valuation actually compelling?\n\n"
            "Be persuasive but realistic. Do not invent positives."
        )

    def _create_bear_prompt(self, symbol: str, company_name: str, analyst_report: str) -> str:
        return (
            f"You are a Forensic Accountant and Short Seller. Your goal is to expose the flaws in {company_name} ({symbol}).\n"
            "Review the Analyst Report below and construct the strongest possible BEAR CASE.\n\n"
            f"--- ANALYST REPORT ---\n{analyst_report}\n----------------------\n\n"
            "Focus on:\n"
            "- Why is the drop justified?\n"
            "- What are the structural problems or accounting red flags?\n"
            "- Why is this a value trap?\n\n"
            "Be ruthless. Highlight every danger."
        )

    def _create_synthesizer_prompt(self, symbol: str, company_name: str, analyst_report: str, bull_case: str, bear_case: str) -> str:
        return (
            f"You are the Chief Investment Officer. You have received reports from your team regarding {company_name} ({symbol}).\n"
            "Your task is to make the final decision and write the investment memo.\n\n"
            f"--- ANALYST REPORT ---\n{analyst_report}\n\n"
            f"--- BULL CASE ---\n{bull_case}\n\n"
            f"--- BEAR CASE ---\n{bear_case}\n\n"
            "----------------------\n"
            "Decide: Is this stock a conviction buy? Be extremely conservative. Prioritize capital preservation.\n"
            f"IMPORTANT: Explicitly state the company name '{company_name}' in your report.\n"
            "IMPORTANT: Format your response EXACTLY as follows:\n"
            "SCORE: [0-10]\n"
            "(0 = Do not invest, 5 = Neutral/Hold, 10 = High Conviction Buy)\n"
            "EXECUTIVE SUMMARY:\n"
            "[Provide a concise 3-5 sentence summary of the situation and your decision for the email body]\n"
            "DETAILED REPORT:\n"
            "[Provide a comprehensive deep-dive analysis. Incorporate insights from all reports. Structure it logically.]"
        )

    def _get_mock_analysis(self, symbol: str, price: float, change_percent: float) -> dict:
        return {
            "recommendation": "BUY",
            "executive_summary": f"This is a mock summary for {symbol}. The drop of {change_percent:.2f}% seems excessive.",
            "detailed_report": f"This is a mock detailed report for {symbol}. Fundamentals are strong...",
            "full_text": "Mock full text"
        }

research_service = ResearchService()

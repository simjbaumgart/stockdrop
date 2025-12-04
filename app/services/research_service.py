import google.generativeai as genai
import os
import logging
import json
from datetime import datetime
import concurrent.futures

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from typing import Dict, List, Optional

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

    def analyze_stock(self, symbol: str, company_name: str, price: float, change_percent: float, technical_analysis: Dict = {}, market_context: Dict = {}) -> dict:
        """
        Analyzes a stock using the Adversarial Council (Technician, Bear, Macro, Judge).
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
            # --- The Adversarial Council Debate Protocol ---
            
            # 1. Agent Beta (The Technician)
            # Proposes the trade based on technicals.
            technician_prompt = self._create_technician_prompt(symbol, price, change_percent, technical_analysis)
            technician_report = self._call_agent(technician_prompt, "Technician")
            
            # 2. Agent Alpha (The Bear)
            # Attacks the proposal with a "Pre-Mortem".
            bear_prompt = self._create_bear_prompt(symbol, company_name, change_percent, technician_report)
            bear_report = self._call_agent(bear_prompt, "Bear")
            
            # 3. Agent Gamma (The Macro)
            # Contextualizes the drop with sector/factor data.
            macro_prompt = self._create_macro_prompt(symbol, company_name, change_percent, market_context)
            macro_report = self._call_agent(macro_prompt, "Macro")
            
            # 4. Agent Omega (The Judge)
            # Synthesizes the debate and issues a verdict.
            judge_prompt = self._create_judge_prompt(symbol, company_name, technician_report, bear_report, macro_report)
            final_response_text = self._call_agent(judge_prompt, "Judge")
            
            # Parse the final response
            result = self._parse_synthesizer_response(final_response_text)
            
            # Add debate transcript to the result
            result["technician_report"] = technician_report
            result["bear_report"] = bear_report
            result["macro_report"] = macro_report
            
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
        """Parses the final output from the Judge agent."""
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

    def _create_technician_prompt(self, symbol: str, price: float, change_percent: float, technical_analysis: Dict) -> str:
        ta_summary = technical_analysis.get("summary", {})
        ta_indicators = technical_analysis.get("indicators", {})
        
        rsi = ta_indicators.get("RSI", "Unknown")
        recommendation = ta_summary.get("RECOMMENDATION", "Unknown")
        
        return (
            f"You are Agent Beta (The Technician), a disciplined momentum trader. You do not care about fundamentals. You only care about price action and support zones.\n"
            f"Stock: {symbol}\n"
            f"Price: {price}\n"
            f"Change: {change_percent:.2f}%\n"
            f"Technical Summary: {recommendation}\n"
            f"RSI: {rsi}\n\n"
            "Analyze the chart (based on the data provided). If the price breaks the current level, where is the floor?\n"
            "Identify 'air pockets' where price could freefall.\n"
            "Define precise Entry and Stop-Loss levels.\n"
            "Output your analysis clearly."
        )

    def _create_bear_prompt(self, symbol: str, company_name: str, change_percent: float, technician_report: str) -> str:
        return (
            f"You are Agent Alpha (The Bear), a forensic short-seller. Your goal is to find ONE fatal flaw in {company_name} ({symbol}).\n"
            "You must counteract the natural optimism of the market.\n\n"
            f"--- TECHNICIAN'S REPORT ---\n{technician_report}\n---------------------------\n\n"
            "PERFORM A PRE-MORTEM:\n"
            f"Assume it is one month from now, and this stock has dropped another 50% (total drop > {abs(change_percent) + 50}%).\n"
            "Write a retrospective news article explaining exactly what went wrong.\n"
            "- Did an investigation expand?\n"
            "- Did competitors steal market share?\n"
            "- Is there accounting fraud?\n"
            "- Is there a liquidity crisis?\n\n"
            "Be specific, pessimistic, and ruthless. Ignore all growth narratives."
        )

    def _create_macro_prompt(self, symbol: str, company_name: str, change_percent: float, market_context: Dict) -> str:
        context_str = "\n".join([f"{k}: {v:.2f}%" for k, v in market_context.items()])
        return (
            f"You are Agent Gamma (The Macro), a global macro strategist. You analyze SECTOR and FACTOR exposure.\n"
            f"Stock: {company_name} ({symbol})\n"
            f"Drop: {change_percent:.2f}%\n\n"
            f"--- MARKET CONTEXT ---\n{context_str}\n----------------------\n\n"
            "Is this stock dropping because of a sector rotation (e.g., Tech to Value) or systematic risk?\n"
            "If the entire sector is down, the drop might be justified and not an opportunity.\n"
            "Analyze the correlation. Is this idiosyncratic risk (company specific) or systematic risk?"
        )

    def _create_judge_prompt(self, symbol: str, company_name: str, technician_report: str, bear_report: str, macro_report: str) -> str:
        return (
            f"You are Agent Omega (The Judge), a skeptical Chief Investment Officer. You only approve a trade if The Bear fails to find a fatal flaw AND The Technician identifies clear support.\n"
            "Prefer inaction over loss.\n\n"
            f"--- DEBATE TRANSCRIPT ---\n\n"
            f"1. TECHNICIAN:\n{technician_report}\n\n"
            f"2. BEAR (Pre-Mortem):\n{bear_report}\n\n"
            f"3. MACRO:\n{macro_report}\n\n"
            "-------------------------\n"
            "Synthesize the debate. Requires 'Clear and Convincing Evidence' to Buy.\n"
            "If the Bear's argument contains 'Existential Threats' (Fraud, Bankruptcy, Delisting), the trade is VETOED immediately.\n\n"
            f"IMPORTANT: Explicitly state the company name '{company_name}' in your report.\n"
            "IMPORTANT: Format your response EXACTLY as follows:\n"
            "SCORE: [0-10]\n"
            "(0 = Do not invest, 5 = Neutral/Hold, 10 = High Conviction Buy)\n"
            "EXECUTIVE SUMMARY:\n"
            "[Provide a concise 3-5 sentence summary of the verdict and the key reasons]\n"
            "DETAILED REPORT:\n"
            "[Provide a comprehensive synthesis of the debate. Address the Bear's points directly. Explain your final score.]"
        )

    def _get_mock_analysis(self, symbol: str, price: float, change_percent: float) -> dict:
        return {
            "recommendation": "BUY",
            "executive_summary": f"This is a mock summary for {symbol}. The drop of {change_percent:.2f}% seems excessive.",
            "detailed_report": f"This is a mock detailed report for {symbol}. Fundamentals are strong...",
            "full_text": "Mock full text",
            "technician_report": "Mock Technician Report: Support at $95.",
            "bear_report": "Mock Bear Report: Pre-mortem shows no fatal flaws.",
            "macro_report": "Mock Macro Report: Sector rotation is the main driver."
        }

research_service = ResearchService()

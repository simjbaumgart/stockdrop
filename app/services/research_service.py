import google.generativeai as genai
import os
import logging
import json
from datetime import datetime
from typing import Dict, List, Optional
from app.models.market_state import MarketState
from app.services.analyst_service import analyst_service

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

    def analyze_stock(self, ticker: str, raw_data: Dict) -> dict:
        """
        Orchestrates the new 3-Phase Agent Flow:
        1. Analysts (Sensors) -> MarketState.reports
        2. Researchers (Brain) -> MarketState.debate_transcript
        3. Risk/FundManager (Safety) -> Final Decision
        """
        if not self._check_and_increment_usage():
            return {"recommendation": "SKIP", "reasoning": "Daily limit reached."}

        print(f"\n[ResearchService] Starting Deep Analysis for {ticker}...")
        
        # Initialize State
        state = MarketState(
            ticker=ticker,
            date=datetime.now().strftime("%Y-%m-%d")
        )

        # --- Phase 1: The Analysts (Sensors) ---
        print("  > Phase 1: Running Analysts...")
        state.reports = analyst_service.run_all_analysis(ticker, raw_data)
        
        # --- Phase 2: The Researcher Debate (Brain) ---
        print("  > Phase 2: Starting Bull/Bear Debate...")
        self._run_debate(state)
        
        # --- Phase 3: Risk Compliance & Final Decision ---
        print("  > Phase 3: Risk Council & Fund Manager...")
        final_decision = self._run_risk_council_and_decision(state)
        state.final_decision = final_decision
        
        # Construct Final Output compatible with existing app expectations
        recommendation = final_decision.get("action", "HOLD").upper()
        # Map "BUY" with size to "STRONG BUY" if needed, or keep generic
        
        return {
            "recommendation": recommendation,
            "score": final_decision.get("score", 50),
            "executive_summary": final_decision.get("reason", "No reason provided."),
            "detailed_report": self._format_full_report(state),
            # Legacy compatibility fields
            "technician_report": state.reports.get('technical', ''),
            "bull_report": self._extract_debate_side(state, "Bull"),
            "bear_report": self._extract_debate_side(state, "Bear"),
            "macro_report": state.reports.get('news', ''),
            "reasoning": final_decision.get("reason", "")
        }

    def _run_debate(self, state: MarketState):
        """
        Executes the adversarial debate loop.
        """
        # Round 1: Bull Thesis
        bull_prompt = self._create_bull_prompt(state)
        bull_thesis = self._call_agent(bull_prompt, "Bull Researcher")
        state.debate_transcript.append(f"**BULL ARGUMENT:**\n{bull_thesis}")
        
        # Round 2: Bear Rebuttal
        bear_prompt = self._create_bear_prompt(state, bull_thesis)
        bear_rebuttal = self._call_agent(bear_prompt, "Bear Researcher")
        state.debate_transcript.append(f"**BEAR REBUTTAL:**\n{bear_rebuttal}")
        
        # Round 3: Bull Defense (Optional, let's keep it 2 rounds for speed/cost or 3 as planned? Plan said 3)
        # Plan: Bull reads Rebuttal, generates Defense.
        bull_defense_prompt = self._create_bull_defense_prompt(state, bear_rebuttal)
        bull_defense = self._call_agent(bull_defense_prompt, "Bull Defense")
        state.debate_transcript.append(f"**BULL DEFENSE:**\n{bull_defense}")

    def _run_risk_council_and_decision(self, state: MarketState) -> Dict:
        """
        Runs Risk Agents (Deterministic + LLM) and then Fund Manager.
        """
        # 1. SafeGuardian (Deterministic Checks) - NO LONGER VETOING, Just Flagging
        safe_concerns = []
        tech_report = state.reports.get("technical", "")
        
        if "OVERBOUGHT" in tech_report:
            safe_concerns.append("Technicals are Overbought (RSI > 70). Proceed with caution.")
        if "Bearish Divergence" in tech_report:
            safe_concerns.append("MACD shows Bearish Divergence.")
        if "Weak" in tech_report or "weak" in tech_report.lower():
            safe_concerns.append("Trend detected as Weak.")
            
        # 2. RiskyGuardian (Contextual/News Checks - Simplified)
        # We'll pass the News report to the Fund Manager who acts as the arbiter.
        risky_support = []
        news_report = state.reports.get("news", "")
        if "Corporate Drivers" in news_report:
            risky_support.append("Significant corporate news identified. Volatility expected.")
            
        # 3. Fund Manager (Final Decision)
        manager_prompt = self._create_fund_manager_prompt(state, safe_concerns, risky_support)
        decision_json_str = self._call_agent(manager_prompt, "Fund Manager")
        decision = self._extract_json(decision_json_str)
        
        # Fallback if JSON extraction fails
        if not decision:
            decision = {"action": "HOLD", "size": "0%", "reason": "Failed to generate decision JSON.", "score": 50}
            
        return decision

    # --- Prompts ---

    def _create_bull_prompt(self, state: MarketState) -> str:
        return f"""
You are the **Bullish Researcher**. Your goal is to maximize the firm's exposure to this asset.
Review the Analyst Reports below. Ignore minor risks. Highlight growth vectors, positive momentum, and favorable macro-trends.
Construct a persuasive argument for a LONG position.

ANALYST REPORTS:
{json.dumps(state.reports, indent=2)}

OUTPUT:
A concise, high-conviction thesis (max 200 words).
"""

    def _create_bear_prompt(self, state: MarketState, bull_thesis: str) -> str:
        return f"""
You are the **Bearish Researcher**. Your goal is to protect the firm's capital from risk.
Review the Analyst Reports and the Bull's argument.
Ignore hype. Highlight valuation concerns, overbought technicals, and geopolitical risks.
Deconstruct the Bull's argument ruthlessly.

ANALYST REPORTS:
{json.dumps(state.reports, indent=2)}

BULL'S ARGUMENT:
{bull_thesis}

OUTPUT:
A brutal rebuttal (max 200 words).
"""

    def _create_bull_defense_prompt(self, state: MarketState, bear_rebuttal: str) -> str:
        return f"""
You are the **Bullish Researcher**. The Bear has attacked your thesis.
Defend your position. Acknowledge valid risks but explain why the upside outweighs them.

BEAR'S REBUTTAL:
{bear_rebuttal}

OUTPUT:
A final defense closing statement (max 100 words).
"""

    def _create_fund_manager_prompt(self, state: MarketState, safe_concerns: List[str], risky_support: List[str]) -> str:
        debate_text = "\\n\\n".join(state.debate_transcript)
        return f"""
You are the **Fund Manager**. You have the final vote.
You must weigh the Analyst Reports and the Debate Transcript to make a decision.

RISK FACTORS (For Consideration Only - No Hard Vetos):
- SafeGuardian Flags: {safe_concerns}
- RiskyGuardian Notes: {risky_support}

DEBATE TRANSCRIPT:
{debate_text}

OBJECTIVE:
Decide whether to BUY, HOLD, or SELL.
You are free to ignore standard technical signals if the Fundamental/News case is compelling.
Take calculated risks.

OUTPUT:
A strictly formatted JSON object:
{{
  "action": "STRONG BUY" | "BUY" | "HOLD" | "SELL" | "STRONG SELL",
  "size": "String (e.g. 'Standard', 'Half', 'Double')",
  "score": Number (0-100),
  "reason": "String (One sentence summary of your decision logic)"
}}
"""

    # --- Helpers ---

    def _call_agent(self, prompt: str, agent_name: str) -> str:
        if not self.model:
            return "Mock Output"
        try:
            # logger.info(f"Calling Agent: {agent_name}")
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Error in {agent_name}: {e}")
            return f"[Error: {e}]"

    def _extract_json(self, text: str) -> dict:
        try:
            start = text.find('{')
            end = text.rfind('}') + 1
            if start != -1 and end != -1:
                return json.loads(text[start:end])
        except Exception:
            pass
        return {}

    def _format_full_report(self, state: MarketState) -> str:
        debate_section = ''.join([f"\n{entry}\n" for entry in state.debate_transcript])
        
        return f"""
# STOCKDROP INVESTMENT MEMO: {state.ticker}
Date: {state.date}

## 1. Risk Council & Decision
**Action:** {state.final_decision.get('action')}
**Score:** {state.final_decision.get('score')}/100
**Reasoning:** {state.final_decision.get('reason')}

## 2. The Debate
{debate_section}

## 3. Analyst Reports
### Technical
{state.reports.get('technical')}

### Fundamental
{state.reports.get('fundamental')}

### Sentiment
{state.reports.get('sentiment')}

### News
{state.reports.get('news')}
"""

    def _extract_debate_side(self, state: MarketState, side: str) -> str:
        for entry in state.debate_transcript:
            if side.upper() in entry[:20].upper():
                return entry
        return ""

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

research_service = ResearchService()

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
        1. Agents (Technical + News) -> MarketState.reports
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
        
        # Extract drop percent for context (default to generic if missing)
        drop_percent = raw_data.get('change_percent', -5.0)
        # Ensure it's formatted as a string with sign if needed, or absolute
        drop_str = f"{drop_percent:.2f}%"

        # --- Phase 1: The Agents (Sensors) ---
        print("  > Phase 1: Running Agent Council (Technical & News)...")
        
        # Technical Agent
        tech_prompt = self._create_technical_agent_prompt(state, raw_data, drop_str)
        tech_report = self._call_agent(tech_prompt, "Technical Agent", state)
        
        # News Agent
        news_prompt = self._create_news_agent_prompt(state, raw_data, drop_str)
        news_report = self._call_agent(news_prompt, "News Agent", state)
        
        state.reports = {
            "technical": tech_report,
            "news": news_report
        }
        
        # --- Phase 2: The Researcher Debate (Brain) ---
        print("  > Phase 2: Starting Bull/Bear Debate...")
        self._run_debate(state, drop_str)
        
        # --- Phase 3: Risk Compliance & Final Decision ---
        print("  > Phase 3: Risk Council & Fund Manager...")
        final_decision = self._run_risk_council_and_decision(state, drop_str)
        state.final_decision = final_decision
        
        # --- Print Final Decision to Console ---
        print("\n" + "="*50)
        print(f"  [FUND MANAGER DECISION]: {final_decision.get('action')}")
        print(f"  Score: {final_decision.get('score')}/100")
        print(f"  Reason: {final_decision.get('reason')}")
        print("  Key Decision Points:")
        for point in final_decision.get('key_decision_points', []):
            print(f"   - {point}")
        print(f"  Total Agent Calls: {state.agent_calls}")
        print("="*50 + "\n")
        
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
            "reasoning": final_decision.get("reason", ""),
            "agent_calls": state.agent_calls
        }

    def _run_debate(self, state: MarketState, drop_str: str):
        """
        Executes the adversarial debate loop.
        """
        # Round 1: Bull Thesis
        bull_prompt = self._create_bull_prompt(state, drop_str)
        bull_thesis = self._call_agent(bull_prompt, "Bull Researcher", state)
        print(f"\n  [BULL THESIS]:\n{bull_thesis}")
        state.debate_transcript.append(f"**BULL ARGUMENT:**\n{bull_thesis}")
        
        # Round 2: Bear Rebuttal
        # Round 2: Bear Rebuttal
        bear_prompt = self._create_bear_prompt(state, bull_thesis, drop_str)
        bear_rebuttal = self._call_agent(bear_prompt, "Bear Researcher", state)
        print(f"\n  [BEAR REBUTTAL]:\n{bear_rebuttal}")
        state.debate_transcript.append(f"**BEAR REBUTTAL:**\n{bear_rebuttal}")
        
        # Round 3: Bull Defense
        bull_defense_prompt = self._create_bull_defense_prompt(state, bear_rebuttal)
        bull_defense = self._call_agent(bull_defense_prompt, "Bull Defense", state)
        print(f"\n  [BULL DEFENSE]:\n{bull_defense}")
        state.debate_transcript.append(f"**BULL DEFENSE:**\n{bull_defense}")

    def _run_risk_council_and_decision(self, state: MarketState, drop_str: str) -> Dict:
        """
        Runs Risk Agents (Deterministic + LLM) and then Fund Manager.
        """
        # 1. SafeGuardian (Deterministic Checks)
        safe_concerns = []
        tech_report = state.reports.get("technical", "")
        
        # Simple string matching on the new Tech report might be less reliable if LLM output varies,
        # but the prompt instructs specific analysis.
        if "OVERBOUGHT" in tech_report.upper():
            safe_concerns.append("Technicals are Overbought.")
        if "DIVERGENCE" in tech_report.upper():
            safe_concerns.append("Bearish Divergence detected.")
        if "WEAK" in tech_report.upper() and "TREND" in tech_report.upper():
            safe_concerns.append("Trend detected as Weak.")
        
        if safe_concerns:
            print(f"\n  [RISK FLAGS DETECTED]:")
            for risk in safe_concerns:
                print(f"   ! {risk}")
            
        # 2. RiskyGuardian (Contextual/News Checks)
        risky_support = []
        news_report = state.reports.get("news", "")
        if "CORPORATE" in news_report.upper():
            risky_support.append("Corporate events identified.")
            
        # 3. Fund Manager (Final Decision)
        # 3. Fund Manager (Final Decision)
        manager_prompt = self._create_fund_manager_prompt(state, safe_concerns, risky_support, drop_str)
        decision_json_str = self._call_agent(manager_prompt, "Fund Manager", state)
        decision = self._extract_json(decision_json_str)
        
        if not decision:
            decision = {"action": "HOLD", "size": "0%", "reason": "Failed to generate decision JSON.", "score": 50}
            
        return decision

    # --- Prompts ---

    def _create_technical_agent_prompt(self, state: MarketState, raw_data: Dict, drop_str: str) -> str:
        # Extract inputs
        indicators = raw_data.get('indicators', {})
        transcript = raw_data.get('transcript_text', "No transcript available.")
        
        # We assume 'indicators' contains what currently comes from TradingViewService:
        # RSI, Moving Averages, MACD, etc.
        
        return f"""
You are the **Technical Analyst Agent**.
Your goal is to analyze the price action and technical health of {state.ticker}.
Crucially, you must correlate technical signals with the **Fundamental Context** provided in the Quarterly Report snippet.

CONTEXT: The stock has specifically dropped {drop_str} recently.

INPUT DATA:
1. TECHNICAL INDICATORS:
{json.dumps(indicators, indent=2)}

2. QUARTERLY REPORT SNIPPET (Transcript/Filing):
{transcript}

TASK:
- Analyze if this drop has pushed the stock into oversold territory (RSI, Bollinger Bands, %B, etc.) or into a key support zone favorable for a short-term bounce.
- Analyze the Trend (SMA, MACD) - is this a breakdown or a pullback?
- Analyze Momentum (RSI, Stochastic).
- CROSS-REFERENCE with the Report: Does the CEO/CFO mention reasons for the current price action? (e.g. "We expected a slow Q3", "Supply chain issues").
- Is the technical drop or rally justified by the report?

OUTPUT:
A professional technical assessment (max 300 words).
Use headers: "Technical Signal", "Oversold Status", "Context from Report", "Verdict".
"""

    def _create_news_agent_prompt(self, state: MarketState, raw_data: Dict, drop_str: str) -> str:
        # Extract inputs
        news_items = raw_data.get('news_items', [])
        transcript = raw_data.get('transcript_text', "No transcript available.")
        
        # Format news - Sort by new to old
        # Assuming news_items have 'datetime' (timestamp) or 'datetime_str'. 
        # Safest to sort by datetime if available, else trust order but reverse or check.
        # Our get_aggregated_news returns dict with 'datetime' (int timestamp).
        news_items.sort(key=lambda x: x.get('datetime', 0), reverse=True)
        
        news_summary = ""
        for n in news_items[:10]:
            news_summary += f"- {n.get('datetime_str', 'N/A')}: {n.get('headline')}\n"

        return f"""
You are the **News & Sentiment Agent**.
Your goal is to gauge the market sentiment and identifying key narrative drivers for {state.ticker}.
You have access to recent News Headlines and the latest Quarterly Report.

CONTEXT: The stock has dropped {drop_str}. We need to know WHY.

INPUT DATA:
1. RECENT NEWS HEADLINES:
{news_summary}

2. QUARTERLY REPORT SNIPPET (Transcript/Filing):
{transcript}

TASK:
- Determine if the drop is due to temporary panic/overreaction or a fundamental structural change. Is this a short-term negative event?
- Identify the dominant narrative (Fear vs Greed? Growth vs Stagnation?).
- Highlight specific events from news or the report that are driving sentiment.
- Check for consistency: Do the headlines match the company's internal tone in the report?

OUTPUT:
A sentiment analysis report (max 300 words).
Use headers: "Sentiment Overview", "Reason for Drop", "Key Drivers", "Narrative Check", "Top 5 Sources".

CITATION REQUIREMENT:
You MUST explicitly list the Top 5 News Headlines/Sources that most influenced your analysis in the "Top 5 Sources" section.
"""

    def _create_bull_prompt(self, state: MarketState, drop_str: str) -> str:
        return f"""
You are the **Bullish Researcher**. Your goal is to maximize the firm's exposure to this asset.
CONTEXT: We are looking for a swing trade / short-term recovery opportunity on this {drop_str} drop.
Review the Agent Reports below.
Construct a persuasive argument for a LONG position.

AGENT REPORTS:
{json.dumps(state.reports, indent=2)}

OUTPUT:
A concise, high-conviction thesis (max 200 words).
Argue why this specific drop is an overreaction and a buying opportunity for a bounce. Focus on short-term recovery mechanics.
"""

    def _create_bear_prompt(self, state: MarketState, bull_thesis: str, drop_str: str) -> str:
        return f"""
You are the **Bearish Researcher**. Your goal is to protect the firm's capital from risk.
CONTEXT: The stock dropped {drop_str}.
Review the Agent Reports and the Bull's argument.
Deconstruct the Bull's argument ruthlessly.

AGENT REPORTS:
{json.dumps(state.reports, indent=2)}

BULL'S ARGUMENT:
{bull_thesis}

OUTPUT:
A brutal rebuttal (max 200 words).
Argue why this is a 'falling knife'. Why should we NOT catch this bounce?
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

    def _create_fund_manager_prompt(self, state: MarketState, safe_concerns: List[str], risky_support: List[str], drop_str: str) -> str:
        debate_text = "\\n\\n".join(state.debate_transcript)
        return f"""
You are the **Fund Manager**. You have the final vote.
You must weigh the Agent Reports and the Debate Transcript to make a decision.

DECISION CONTEXT: This is a Swing Trade / Short-term Recovery play on a stock that dropped {drop_str}.

RISK FACTORS (For Consideration):
- Technical Flags: {safe_concerns}
- News Flags: {risky_support}

DEBATE TRANSCRIPT:
{debate_text}

AGENT REPORTS (Technical & News):
{json.dumps(state.reports, indent=2)}

OBJECTIVE:
Decide whether to BUY, HOLD, or SELL.
Evaluate if the risk/reward favors a short-term bounce. We are not marrying this stock for years; will it recover in the near term?
Take calculated risks. If the Fundamental/News context (Agent Reports) explains a drop and it seems temporary, it might be a buying opportunity.

OUTPUT:
A strictly formatted JSON object:
{{
  "action": "STRONG BUY" | "BUY" | "HOLD" | "SELL" | "STRONG SELL",
  "size": "String (e.g. 'Standard', 'Half', 'Double')",
  "score": Number (0-100),
  "reason": "String (One sentence summary of your decision logic)",
  "key_decision_points": [
      "String (Point 1 - why you made this decision)",
      "String (Point 2 - specific data point)",
      "String (Point 3 - risk mitigation)"
  ]
}}
"""

    # --- Helpers ---

    def _call_agent(self, prompt: str, agent_name: str, state: Optional[MarketState] = None) -> str:
        if not self.model:
            return "Mock Output"
        try:
            # logger.info(f"Calling Agent: {agent_name}")
            if state:
                state.agent_calls += 1
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

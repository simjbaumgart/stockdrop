import google.generativeai as genai
from google.generativeai.types import RequestOptions
import os
import logging
import json
from datetime import datetime
from typing import Dict, List, Optional
from app.models.market_state import MarketState
from app.services.analyst_service import analyst_service
from app.services.fred_service import fred_service
import time
import requests

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
            
        # OpenAI API Key for Deep Reasoning
        self.openai_key = os.getenv("OPENAI_API_KEY")

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
        
        # Check for Economics Trigger
        economics_report = ""
        if "NEEDS_ECONOMICS: TRUE" in news_report:
            print("  > [Economics Agent] Triggered by News Agent (US Exposure detected).")
            print("  > Fetching US Macro Data from FRED...")
            macro_data = fred_service.get_macro_data()
            if macro_data:
                econ_prompt = self._create_economics_agent_prompt(state, macro_data)
                economics_report = self._call_agent(econ_prompt, "Economics Agent", state)
            else:
                economics_report = "Economics Agent triggered but failed to fetch FRED data."
        
        state.reports = {
            "technical": tech_report,
            "news": news_report,
            "economics": economics_report
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
        
        # --- Phase 4: deep reasoning check for STRONG BUY ---
        deep_reasoning_report = ""
        action = final_decision.get('action', 'HOLD').upper()
        
        # Trigger if Strong Buy, or plain Buy with high confidence (e.g. > 80)
        # PAUSED by User Request
        is_strong_buy = False # action == "STRONG BUY" or (action == "BUY" and final_decision.get('score', 0) >= 80)
        
        if is_strong_buy:
             print("  > [Deep Reasoning] 'Strong Buy' signal detected. Validating with OpenAI (o3-mini)...")
             deep_reasoning_report = self._run_deep_reasoning_check(state, drop_str)
             
             # If the Deep Reasoning model explicitly downgrades, we should reflect that in the final output
             # Simple heuristic: if it says "DOWNGRADE" in the first line or verdict.
             if "DOWNGRADE TO" in deep_reasoning_report.upper():
                 print("  > [Deep Reasoning] VERDICT: Recommendation Downgraded.")
                 # We won't overwrite the Fund Manager's decision object to preserve history,
                 # but we will append a major warning to the executive summary.
                 final_decision['reason'] += " [WARNING: Deep Reasoning Model suggests caution/downgrade - see report]"
        
        # Construct Final Output compatible with existing app expectations
        recommendation = final_decision.get("action", "HOLD").upper()
        # Map "BUY" with size to "STRONG BUY" if needed, or keep generic
        
        # Extract checklist metadata
        economics_run = "NEEDS_ECONOMICS: TRUE" in news_report and economics_report != "" and "failed to fetch" not in economics_report
        drop_reason_identified = "REASON_FOR_DROP_IDENTIFIED: YES" in news_report

        return {
            "recommendation": recommendation,
            "score": final_decision.get("score", 50),
            "executive_summary": final_decision.get("reason", "No reason provided."),
            "deep_reasoning_report": deep_reasoning_report,
            "detailed_report": self._format_full_report(state, deep_reasoning_report),
            # Legacy compatibility fields
            "technician_report": state.reports.get('technical', ''),
            "bull_report": self._extract_debate_side(state, "Bull"),
            "bear_report": self._extract_debate_side(state, "Bear"),
            "macro_report": state.reports.get('news', ''),
            "reasoning": final_decision.get("reason", ""),
            "agent_calls": state.agent_calls,
            "checklist": {
                "economics_run": economics_run,
                "drop_reason_identified": drop_reason_identified
            }
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

    def _run_deep_reasoning_check(self, state: MarketState, drop_str: str) -> str:
        """
        Uses OpenAI o3-mini (Reasoning Model) as a 'Stock Investor' validation step.
        """
        if not self.openai_key:
            return "Skipped: No OpenAI API Key."
            
        reports_text = json.dumps(state.reports, indent=2)
        debate_text = "\\n\\n".join(state.debate_transcript)
        fund_decision = json.dumps(state.final_decision, indent=2)
        
        prompt = f"""
You are a **Senior Stock Investor** at a hedge fund.
Your specialty is evaluating "Buying the Dip" opportunities.
A proposal has landed on your desk from your team (Fund Manager + Analysts).

THE PROPOSAL:
They want to BUY {state.ticker} after a {drop_str} drop.
Current Decision:
{fund_decision}

THE RESEARCH:
--- Analyst Reports ---
{reports_text}

--- Internal Debate ---
{debate_text}

YOUR TASK:
Act as the FINAL DECISION MAKER.
Scrutinize their reasoning. Are they catching a falling knife? Are they ignoring a fatal flaw?
You have the power to veto or confirm.

OUTPUT:
Write a memo to the team (max 200 words).
Start with one of the following Verdicts:
- "CONFIRM STRONG BUY"
- "DOWNGRADE TO BUY" (If good but risky)
- "DOWNGRADE TO HOLD" (If too dangerous)

Then explain your reasoning.
"""
        
        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_key}"
            }
            data = {
                "model": "o3-mini", # Fallback from o3-deep-research
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "reasoning_effort": "medium"
            }
            
            response = requests.post(url, headers=headers, json=data)
            
            if response.status_code == 200:
                content = response.json()['choices'][0]['message']['content']
                print(f"\n  [DEEP REASONING VERDICT]:\n{content}\n")
                return content
            else:
                logger.error(f"OpenAI API Error: {response.text}")
                return f"Error calling Deep Reasoning model: {response.status_code}"
                
        except Exception as e:
            logger.error(f"Deep Reasoning Check Failed: {e}")
            return f"Exception during Deep Reasoning check: {e}"

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
        news_items = raw_data.get('news_items', [])
        transcript = raw_data.get('transcript_text', "No transcript available.")
        
        # Split into Massive and Others to enforce user priority in the PROMPT display
        massive_news = [n for n in news_items if "Massive" in n.get('source', '')]
        other_news = [n for n in news_items if "Massive" not in n.get('source', '')]
        
        # Sort each group by date desc
        massive_news.sort(key=lambda x: x.get('datetime', 0), reverse=True)
        other_news.sort(key=lambda x: x.get('datetime', 0), reverse=True)
        
        news_summary = "--- PRIMARY SOURCE (Massive/Benzinga) ---\n"
        for n in massive_news:
            date_str = n.get('datetime_str', 'N/A')
            headline = n.get('headline', 'No Headline')
            source = n.get('source', 'Unknown')
            content = n.get('content', '')  # Full body text if available
            
            news_summary += f"- {date_str}: {headline} ({source})\n"
            if content:
                 # Truncate slightly if massive to prevent context window explosion
                 truncated_content = content[:5000] + "..." if len(content) > 5000 else content
                 news_summary += f"  CONTENT: {truncated_content}\n  ---\n"
                 
        news_summary += "\n--- OTHER SOURCES ---\n"
        # Remaining slots? We already limited total to 30 in stock_service, but let's be safe.
        for n in other_news:
            date_str = n.get('datetime_str', 'N/A')
            headline = n.get('headline', 'No Headline')
            source = n.get('source', 'Unknown')
            
            news_summary += f"- {date_str}: {headline} ({source})\n"

        # --- LOGGING NEWS CONTEXT ---
        try:
            log_dir = "data/news"
            os.makedirs(log_dir, exist_ok=True)
            log_file = f"{log_dir}/{state.ticker}_{state.date}_news_context.txt"
            
            with open(log_file, "w") as f:
                f.write(f"NEWS CONTEXT FOR {state.ticker} ({state.date})\n")
                f.write("==================================================\n\n")
                f.write(news_summary)
                
            print(f"  > [News Agent] Logged news context to {log_file}")
        except Exception as e:
            print(f"  > [News Agent] Error logging news context: {e}")

        # Try to load DefeatBeta data
        defeatbeta_path = f"data/DefeatBeta_data/{state.ticker}"
        db_news = []
        db_transcript = ""
        
        try:
            if os.path.exists(defeatbeta_path):
                # Load Transcript
                t_path = os.path.join(defeatbeta_path, "transcript.txt")
                if os.path.exists(t_path):
                    with open(t_path, "r") as f:
                        db_transcript = f.read()
                        
                # Load News
                n_path = os.path.join(defeatbeta_path, "news.json")
                if os.path.exists(n_path):
                    with open(n_path, "r") as f:
                        db_json = json.load(f)
                        for item in db_json:
                             # Format roughly to match expected structure or just stringify
                             db_news.append(f"{item.get('report_date','')} - {item.get('publisher','')}: {item.get('title','')} ({item.get('link','')})")
        except Exception as e:
            print(f"Error loading DefeatBeta data: {e}")

        # Mix DefeatBeta transcript if available and original is empty or short
        if db_transcript and len(transcript) < 100:
             transcript = db_transcript
        elif db_transcript:
             transcript += f"\n\n--- ADDITIONAL TRANSCRIPT DATA (DefeatBeta) ---\n{db_transcript[:2000]}..." # Append snippet

        # Mix DefeatBeta news
        if db_news:
            news_summary += "\n--- ADDITIONAL NEWS SOURCES (DefeatBeta) ---\n"
            for line in db_news[:5]:
                news_summary += f"- {line}\n"

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

NOTE ON DATA:
You have been provided with additional data files (JSON/PDF-derived content) in the input.
Treat this as **additional information** which can be **considered or dropped if outdated**.
Verify dates where possible. If the DefeatBeta data seems older or less relevant than the primary source, prioritize the primary source.

TASK:
- Determine if the drop is due to temporary panic/overreaction or a fundamental structural change. Is this a short-term negative event?
- Identify the dominant narrative (Fear vs Greed? Growth vs Stagnation?).
- Highlight specific events from news or the report that are driving sentiment.
- Check for consistency: Do the headlines match the company's internal tone in the report?
- **CRITICAL ASSESSMENT**: Assess the validity of the news. Is it "Hype" or "Fluff"? Be skeptical of clickbait or promotional content. If a source looks unreliable or the headline is sensationalist, note it. Distinguish between hard facts (earnings miss, lawsuit) and opinion pieces.

OUTPUT:
A sentiment analysis report.
Use headers: "Sentiment Overview", "Reason for Drop", "Extended Transcript Summary", "Key Drivers", "Narrative Check", "Top 5 Sources".

SECTION: "Extended Transcript Summary":
If transcript data is provided, you MUST provide a detailed summary (bullet points).
Focus on:
- Guidance & Outlook (most important)
- Management Tone (Confident vs Defessive)
- Key Operational Updates or Strategic Shifts
If no transcript is available, state "No Transcript Available".

CITATION REQUIREMENT:
If valid news items are provided, you MUST list the Top 5 Sources that influenced your analysis.
If NO news items are provided in the input, explicitly state "No Sources Available" in this section.
DO NOT simulate or hallucinate sources if real data is missing.

MACRO CHECK:
At the very end of your response, on a new line, explicitly state "NEEDS_ECONOMICS: TRUE" if:
1. The company has significant business exposure to the US Economy.
2. The stock drop might be related to macro factors (Interest Rates, Inflation, Recession fears).
Otherwise, state "NEEDS_ECONOMICS: FALSE".

DROP REASON CHECK:
Also, explicitly state on a new line: "REASON_FOR_DROP_IDENTIFIED: YES" if you have found a specific news event or report detail explaining the drop (e.g. "missed earnings", "CEO resignation", "lawsuit").
If the drop is a mystery or just general market noise with no specific catalyst found, state "REASON_FOR_DROP_IDENTIFIED: NO".
"""

    def _create_economics_agent_prompt(self, state: MarketState, macro_data: Dict) -> str:
        return f"""
You are the **Macro Economics Agent**.
Your goal is to analyze the US Macroeconomic environment and its potential impact on {state.ticker}.

INPUT DATA (FRED API):
{json.dumps(macro_data, indent=2)}

TASK:
- Analyze the provided macro indicators (Unemployment, CPI, Rates, GDP, Yields).
- Determine if the current macro environment is a Headwind or Tailwind for this specific company/sector.
- specifically look for "Recession Signals" (Yield Curve Inversion, rising Unemployment) if relevant.

OUTPUT:
A concise Macro Assessment (max 200 words).
Headers: "Macro Environment", "Impact on {state.ticker}", "Risk Level".
"""

    def _create_bull_prompt(self, state: MarketState, drop_str: str) -> str:
        return f"""
You are the **Bullish Researcher**. Your goal is to maximize the firm's exposure to this asset.
CONTEXT: We are looking for a swing trade / short-term recovery opportunity on this {drop_str} drop.
Review the Agent Reports below, ESPECIALLY the "Extended Transcript Summary" in the News Report.

AGENT REPORTS:
{json.dumps(state.reports, indent=2)}

TASK:
Construct a persuasive argument for a LONG position.
1. If available, you SHOULD cite specific positive drivers from the **News Headlines** and **Earnings Transcript** to support your thesis.
2. Do not rely solely on technicals; explain the FUNDAMENTAL/NARRATIVE reason for a reversal.
3. If the transcript mentions a temporary issue (e.g., supply chain) that is resolving, highlight it.

SAFETY:
DO NOT HALLUCINATE: If no news/transcript is provided or relevant, rely on the technicals/fundamentals present. State "No specific news drivers found" if applicable. Do not invent events.

OUTPUT:
A concise, high-conviction thesis (max 200 words).
Argue why this specific drop is an overreaction and a buying opportunity.
"""

    def _create_bear_prompt(self, state: MarketState, bull_thesis: str, drop_str: str) -> str:
        return f"""
You are the **Bearish Researcher**. Your goal is to protect the firm's capital from risk.
CONTEXT: The stock dropped {drop_str}.
Review the Agent Reports (especially the "Extended Transcript Summary") and the Bull's argument.

AGENT REPORTS:
{json.dumps(state.reports, indent=2)}

BULL'S ARGUMENT:
{bull_thesis}

TASK:
Deconstruct the Bull's argument ruthlessly.
1. If available, you SHOULD cite specific negative risks from the **News and Transcript** (e.g., guidance cut, macro headwinds, management hesitation).
2. Use the fundamental data to show why the drop is justified.
3. Point out if the Bull is ignoring a "fatal flaw" mentioned in the News.

SAFETY:
DO NOT HALLUCINATE: If data is missing/limited, stick to what is known. Do not invent negative news.

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
            
            # Rate limit buffer
            time.sleep(2)
            
            response = self.model.generate_content(prompt, request_options=RequestOptions(timeout=600))
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

    def _format_full_report(self, state: MarketState, deep_report: str = "") -> str:
        debate_section = ''.join([f"\n{entry}\n" for entry in state.debate_transcript])
        
        deep_section = ""
        if deep_report:
            deep_section = f"\n## 0. DEEP REASONING VERDICT (Verification)\n{deep_report}\n"
        
        return f"""
# STOCKDROP INVESTMENT MEMO: {state.ticker}
Date: {state.date}

{deep_section}
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

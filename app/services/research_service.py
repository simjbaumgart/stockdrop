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

    def analyze_stock(self, symbol: str, company_name: str, price: float, change_percent: float, technical_sheet: str, news_headlines: str, market_context: Dict = {}, filings_text: str = "", transcript_text: str = "") -> dict:
        """
        Analyzes a stock using the new 5-Agent Council.
        """
        if not self._check_and_increment_usage():
            logger.warning(f"Daily research limit reached. Skipping analysis for {symbol}.")
            return {
                "recommendation": "SKIP",
                "executive_summary": "Daily research limit reached.",
                "detailed_report": "Please try again tomorrow.",
                "reasoning": "Daily research limit reached. Please try again tomorrow."
            }

        # --- Input Validation & Logging ---
        print(f"\n[ResearchService] Analyzing {symbol}...")
        
        # Validate technical sheet integrity
        try:
            tech_data = json.loads(technical_sheet)
            required_keys = ["rsi", "sma200", "volume", "close"] # Critical keys for Sentinel
            missing_keys = [k for k in required_keys if k not in tech_data]
            
            if missing_keys:
                logger.warning(f"⚠️ Technical Sheet for {symbol} is missing keys: {missing_keys}")
                print(f"⚠️ WARNING: Technical Sheet is missing critical data: {missing_keys}")
            else:
                print(f"✅ Technical Sheet validated. Contains: {list(tech_data.keys())}")
        except Exception as e:
            # json.JSONDecodeError or other
            logger.warning(f"⚠️ Technical Sheet for {symbol} is valid JSON string: {e}")
            print(f"⚠️ WARNING: Technical Sheet is not valid JSON.")

        if not self.model:
            return self._get_mock_analysis(symbol, price, change_percent)

        try:
            # --- 5-Agent Sequential Pipeline ---
            
            # Agent 1: The Technical Sentinel (The Logic Generator)
            sentinel_prompt = self._create_sentinel_prompt(technical_sheet)
            sentinel_output = self._call_agent(sentinel_prompt, "Sentinel")
            sentinel_json = self._extract_json(sentinel_output)
            sentinel_text = self._extract_text_part(sentinel_output) # For the report
            
            # Agent 2: The Contextual Analyst (The Environment Scanner)
            contextual_prompt = self._create_contextual_prompt(sentinel_json, news_headlines)
            contextual_output = self._call_agent(contextual_prompt, "Contextual Analyst")
            
            # Agent 3: The Rational Bull (Value Recognition)
            bull_prompt = self._create_bull_prompt(sentinel_json, contextual_output, filings_text, transcript_text)
            bull_output = self._call_agent(bull_prompt, "Rational Bull")
            
            # Agent 4: The Rational Bear (Risk Identification)
            bear_prompt = self._create_bear_prompt(sentinel_json, contextual_output, filings_text, transcript_text)
            bear_output = self._call_agent(bear_prompt, "Rational Bear")
            
            # Agent 5: The Judge (Weighted Probabilistic Synthesis)
            judge_prompt = self._create_judge_prompt(sentinel_json, contextual_output, bull_output, bear_output)
            judge_output = self._call_agent(judge_prompt, "Judge")
            
            # Parse the final judge response
            result = self._parse_judge_response(judge_output)
            
            # Attach transcript
            result["technician_report"] = f"ANALYSIS:\n{sentinel_text}\n\nSENTINEL DATA:\n{json.dumps(sentinel_json, indent=2)}"
            result["macro_report"] = contextual_output # Mapping Contextual to Macro slot for compatibility
            result["bear_report"] = bear_output
            result["bull_report"] = bull_output # New field
            
            # Add data source footer
            footer = "\n\n*Analysis includes data from recent SEC Filings & Earnings Transcripts.*"
            result["detailed_report"] += footer
            result["executive_summary"] += footer
            
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
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Error in {agent_name} agent: {e}")
            return f"[Error generating {agent_name} report: {e}]"
            
    def _extract_json(self, text: str) -> dict:
        """Extracts JSON object from text (handling markdown code blocks)."""
        try:
            # simplistic extraction: look for { and }
            start = text.find('{')
            end = text.rfind('}') + 1
            if start != -1 and end != -1:
                json_str = text[start:end]
                return json.loads(json_str)
        except Exception as e:
            logger.error(f"Error extracting JSON: {e}")
        return {}

    def _extract_text_part(self, text: str) -> str:
        """Attempts to extract the text part (Part 1) from Sentinel output."""
        # Assuming Part 1 is before the JSON or explicitly labeled
        # If unable to split cleanly, return the whole text (minus JSON if possible, but whole is fine for report)
        return text

    def _parse_judge_response(self, text: str) -> dict:
        """Parses the final output from the Judge agent (Agent 5)."""
        recommendation = "HOLD"
        score = 50.0 # Default neutral
        executive_summary = "No summary provided."
        detailed_report = text

        lines = text.split('\n')
        
        # 1. Final Verdict and Score
        for line in lines:
            # Verdict
            if "Final Verdict:" in line or "FINAL VERDICT:" in line:
                try:
                    parts = line.split(":", 1)[1].strip()
                    # Map to Buy/Hold/Avoid/Strong Buy/Speculative Buy
                    # clean up
                    clean_verdict = parts.upper().replace("[", "").replace("]", "").strip()
                    logger.info(f"Judge Verdict: {clean_verdict}")
                    recommendation = clean_verdict
                except Exception:
                    pass
            
            # Score
            if "Investment Score:" in line or "INVESTMENT SCORE:" in line or "Score:" in line:
                 try:
                    parts = line.split(":", 1)[1].strip()
                    # Extract number
                    import re
                    match = re.search(r"(\d+(\.\d+)?)", parts)
                    if match:
                        score = float(match.group(1))
                        logger.info(f"Judge Score: {score}")
                 except Exception:
                    pass
        
        # 2. Extract Synthesis (Paragraph)
        
        # 3. Create Executive Summary
        # Try to find "Primary Driver" or "Synthesis"
        summary_buf = []
        capture = False
        for line in lines:
            if "Primary Driver:" in line:
                summary_buf.append(line)
            if "Synthesis:" in line:
                capture = True
                summary_buf.append(line)
                continue
            if capture and line.strip() == "":
                # Stop at empty line after synthesis?
                pass
        
        if summary_buf:
            executive_summary = "\n".join(summary_buf)
        else:
            executive_summary = text[:500] + "..."

        # Append score to summary for visibility if needed, or just return it
        return {
            "recommendation": recommendation,
            "score": score,
            "executive_summary": executive_summary,
            "detailed_report": text,
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

    # --- AGENT PROMPTS ---

    def _create_sentinel_prompt(self, technical_sheet: str) -> str:
        return f"""
You are the **Technical Sentinel**, the logic anchor for a financial analysis system. 
You will receive a raw "Technical Data Sheet."

**YOUR OBJECTIVE:**
1. Analyze the provided data. While you must extract specific metrics for the JSON, you have the **freedom to interpret the chart's story creatively** in your text summary (e.g., identifying psychology, hidden patterns, or market traps).
2. Generate a "Technical Analysis" summary text.
3. Output a structured JSON "Fact Sheet."

**LOGIC FRAMEWORK (Use as a guide, but interpret strictly for the JSON):**
- **Trend Logic:** Compare Current Price vs 200-day SMA.
- **Drop Severity:** Check 5-day price drop.
- **Volume Logic:** Analyze Volume spikes relative to average. Label as "Capitulation" or "Weak."
- **RSI Logic:** Check RSI levels and potential divergences.
- **Valuation Logic:** Compare ForwardPE vs TrailingPE.
- **Solvency Check:** Check DebtToEquity levels.

**INPUT DATA:**
{technical_sheet}

**OUTPUT REQUIREMENTS:**
Part 1: A creative technical analysis paragraph (max 100 words) summarizing the chart structure and market psychology.
Part 2: A strictly formatted JSON object.

**JSON FORMAT:**
{{
  "ticker": "String",
  "current_price": Number,
  "technical_logic": {{
    "trend_status": "Uptrend" | "Downtrend",
    "rsi_status": "Oversold" | "Neutral" | "Overbought",
    "volume_status": "Capitulation" | "Normal" | "Weak",
    "support_level_breached": Boolean
  }},
  "fundamental_logic": {{
    "valuation_gap": "Undervalued" | "Fair" | "Overvalued",
    "solvency_risk": "High" | "Low",
    "earnings_quality": "Stable" | "Deteriorating"
  }},
  "news_logic": {{
    "catalyst_type": "Systemic" | "Operational" | "Existential"
  }}
}}
"""

    def _create_contextual_prompt(self, sentinel_json: dict, headlines: str) -> str:
        return f"""
You are the **Contextual Analyst**. Your role is to determine if the market movement is a "Market Error" or a "Company Failure."

**INPUTS:**
1. The "Fact Sheet" JSON generated by the Technical Sentinel:
{json.dumps(sentinel_json, indent=2)}

2. The recent News Headlines:
{headlines}

**YOUR TASKS:**
1. **News Classification:** Scan the headlines. Classify the primary driver.
2. **Creative Contextualization:** You might consider the reason for the drop of the stock in your analysis. You are free to **infer broader implications**. Connect the headlines to potential industry shifts, macro trends, or competitor reactions.

**OUTPUT:**
Provide a "Context Brief" (max 150 words). 
- **Reason for Drop:** One clear sentence showing specific cause (e.g. "Missed earnings by 5%", "CEO resigned", "Sector rotation").
- **Catalyst Type:** (Systemic | Operational | Existential).
- **Assessment:** Defend your classification using the headlines and your own creative deductions about the market environment.
"""

    def _create_bull_prompt(self, sentinel_json: dict, context_brief: str, filings_text: str = "", transcript_text: str = "") -> str:
        return f"""
You are the **Rational Bull**. Your objective is to construct the strongest case for *Asymmetric Upside*.

**GUIDELINES:**
- **Freedom of Thought:** Use the metrics from the "Fact Sheet" as your evidence base, but you are free to **propose new strategic ideas**. You can speculate on potential turnarounds, hidden assets, or market overreactions that the data hints at but doesn't explicitly prove.
- **Tone:** Convincing, visionary, yet grounded in the numbers provided.

**INPUTS:**
Fact Sheet: {json.dumps(sentinel_json, indent=2)}
Context: {context_brief}

**ADDITIONAL DATA (Recent Filings/Transcripts):**
Use these snippets to find hidden growth drivers, product announcements, or positive guidance.
---
FILINGS SNIPPETS:
{filings_text[:5000] if filings_text else "No recent filings data."}

TRANSCRIPT SNIPPETS:
{transcript_text[:5000] if transcript_text else "No recent transcript data."}
---

**REASONING FRAMEWORK:**
1. **Context:** You might consider the reason for the drop of the stock in your analysis. Why might this be temporary?
2. **Technical Opportunity:** How does the volume/RSI suggest a reversal?
3. **Valuation Dislocation:** Why is the market wrong about the current price?
4. **Fundamental Defense:** What is the "hidden gem" aspect of this company?
5. **Data Insights:** specifically cite something positive from the Filings/Transcript if available.

**OUTPUT:**
A thesis titled "**The Rational Case for Mean Reversion**."
- List 3 distinct arguments (Technical, Fundamental, Contextual).
- Cite specific numbers from the Fact Sheet to back up your creative theories.
"""

    def _create_bear_prompt(self, sentinel_json: dict, context_brief: str, filings_text: str = "", transcript_text: str = "") -> str:
        return f"""
You are the **Rational Bear**. Your objective is to uncover **Structural Risks** that could lead to capital loss.

**GUIDELINES:**
- **Freedom of Thought:** Use the metrics from the "Fact Sheet" as your evidence base, but you are free to **identify "Second-Order" risks**. Look beyond the immediate numbers—what could go wrong in the future? (e.g., brand damage, regulatory crackdowns, obsolescence).
- **Tone:** Forensic, skeptical, and prudent.

**INPUTS:**
Fact Sheet: {json.dumps(sentinel_json, indent=2)}
Context: {context_brief}

**ADDITIONAL DATA (Recent Filings/Transcripts):**
Use these snippets to find omissions, risks, litigation warnings, or cash burn concerns.
---
FILINGS SNIPPETS:
{filings_text[:5000] if filings_text else "No recent filings data."}

TRANSCRIPT SNIPPETS:
{transcript_text[:5000] if transcript_text else "No recent transcript data."}
---

**REASONING FRAMEWORK:**
1. **Context:** You might consider the reason for the drop of the stock in your analysis. Why does this confirm a structural issue?
2. **Trend Fragility:** Why might the support fail?
3. **Valuation Trap:** Why are the current earnings misleading?
4. **Liquidity & Solvency:** What is the worst-case scenario for their balance sheet?
5. **Catalyst Danger:** How could the current news spiral into something worse?
6. **Data Red Flags:** specifically cite a risk from the Filings/Transcript if available.

**OUTPUT:**
A thesis titled "**Structural Risk Assessment**."
- List 3 distinct failure modes.
- Cite specific numbers from the Fact Sheet to back up your risk warnings.
"""

    def _create_judge_prompt(self, sentinel_json: dict, context_brief: str, bull_thesis: str, bear_thesis: str) -> str:
        return f"""
You are the **Chief Investment Officer**. Your goal is to render a final decision based on the Probability of Recovery vs. Risk of Further Decline.

**INPUTS:**
- Fact Sheet: {json.dumps(sentinel_json, indent=2)}
- Context Brief: {context_brief}
- Bull Case: {bull_thesis}
- Bear Case: {bear_thesis}

**DECISION LOGIC:**
1. **The Kill Switch:** If Agent 2 (Context) identified "Existential Risk" (Fraud/Bankruptcy/Lawsuit), your verdict must be **AVOID**, regardless of how cheap the stock is. (Note: Solvency Risk alone does not trigger the kill switch).
2. **Evaluate the Evidence:** Weigh the arguments presented by the Bull and Bear. You are free to agree with the "new ideas" they proposed if they seem logical.
   - **Technicals:** Did the volume and price action confirm capitulation?
   - **Fundamentals:** Is the company historically strong?
   - **Context:** Is the news temporary or permanent?
3. **Event Analysis:** In your final verdict you should incorporate the recent event that led to the stock drop.

**OUTPUT:**
1. **Final Verdict:** Choose ONE [Strong Buy | Speculative Buy | Hold | Avoid | Short Sell].
2. **Investment Score:** A score between 0-100 reflecting your conviction in the long trade.
   - 0-20: Strong Sell / Bankruptcy Risk
   - 21-40: Sell / Avoid
   - 41-60: Hold / Neutral
   - 61-80: Speculative Buy / Accumulate
   - 81-100: Strong Buy / High Conviction
3. **Primary Driver:** One sentence explaining the single most important factor in this decision.
4. **Reason for Drop:** State the specific reason for the price drop clearly (citing the Context agent).
5. **Synthesis:** A paragraph reconciling the Bull and Bear arguments. Explain why one side is strictly more logical than the other based on the data and arguments provided.
"""

    def _get_mock_analysis(self, symbol: str, price: float, change_percent: float) -> dict:
        return {
            "recommendation": "BUY",
            "score": 85.0,
            "executive_summary": f"This is a mock summary for {symbol}. The drop of {change_percent:.2f}% seems excessive.",
            "detailed_report": f"This is a mock detailed report for {symbol}. Fundamentals are strong...",
            "full_text": "Mock full text",
            "technician_report": "Mock Technician Report: Support at $95.",
            "bear_report": "Mock Bear Report: Pre-mortem shows no fatal flaws.",
            "macro_report": "Mock Macro Report: Sector rotation is the main driver."
        }
# End of Class

research_service = ResearchService()

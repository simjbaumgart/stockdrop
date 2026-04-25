import google.generativeai as genai # Existing SDK
from google.generativeai.types import RequestOptions
from google import genai as new_genai # New SDK (Enabled)
from google.genai import types as new_types
import os
import logging
import json
import re
from datetime import datetime
from typing import Dict, List, Optional
from app.models.market_state import MarketState
from app.services.analyst_service import analyst_service
from app.services.fred_service import fred_service
import time
import requests
from app.services.deep_research_service import deep_research_service
from app.utils.ticker_paths import safe_ticker_path

# Citation strip — Gemini grounding injects [Source N] markers that corrupt JSON
_STANDALONE_CITATION_RE = re.compile(r"\s+\[Source\s*\d+\]\s+")
_EDGE_CITATION_RE = re.compile(r"\s*\[Source\s*\d+\]\s*")


def _strip_citations(raw: str) -> str:
    """Remove inline [Source N] markers that break JSON parsing."""
    if "[Source" not in raw:
        return raw
    cleaned = _STANDALONE_CITATION_RE.sub(" ", raw)
    cleaned = _EDGE_CITATION_RE.sub("", cleaned)
    return cleaned


# Grounding retry policy: 1 initial attempt + up to MAX_GROUNDING_RETRIES retries
MAX_GROUNDING_RETRIES = 2

# Substrings that indicate a transient, retryable error from the Gemini grounding API.
_RETRYABLE_ERROR_KEYWORDS = (
    "Connection reset",
    "[Errno 54]",
    "503",
    "504",
    "UNAVAILABLE",
    "RESOURCE_EXHAUSTED",
    "DEADLINE_EXCEEDED",
    "timed out",
    "Timeout",
)


def _is_retryable_grounding_error(e: Exception) -> bool:
    """Return True for transient network/API errors that are worth retrying."""
    if isinstance(e, (ConnectionResetError, TimeoutError, ConnectionError)):
        return True
    msg = str(e)
    return any(k in msg for k in _RETRYABLE_ERROR_KEYWORDS)


# Wall-clock budget per grounded agent call. Ten minutes is generous for a
# normal call but hard-caps the QXO/PB-style multi-hour stalls that happen
# when a transient 503 + exponential backoff combine to loop for hours.
AGENT_WALL_CLOCK_BUDGET_SEC = 600


# Phase 1 quality gate: abort if fewer than this many core agents return real reports.
# Four-of-five is deliberate: we tolerate a single flaky sensor (e.g. seeking_alpha
# on an OTC ticker with no coverage) but refuse to produce a decision when the
# majority of sensors are error stubs. The 04-22 BBY outage (5/5 truncated
# outputs producing a HIGH-conviction AVOID) is the canonical motivator.
MIN_REAL_PHASE1_REPORTS = 4

# Phase 1 core agents counted by the quality gate (economics is conditional and excluded).
PHASE1_CORE_AGENTS = ("technical", "news", "market_sentiment", "competitive", "seeking_alpha")

# Report-content markers that signal a failed agent output.
_FAILED_REPORT_MARKERS = (
    "[Error",
    "[SYSTEM ERROR",
    "[SHORT INPUT DETECTED:",
    "Market Sentiment Analysis Failed",
    "[Grounding Error",
)


def _is_real_report(report: Optional[str]) -> bool:
    """Return True if the report looks like real agent output, not an error stub."""
    if not report or not isinstance(report, str):
        return False
    if len(report) < 200:
        return False
    stripped = report.lstrip()
    return not any(stripped.startswith(marker) for marker in _FAILED_REPORT_MARKERS)


from app.services.seeking_alpha_service import seeking_alpha_service

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
            self.model = genai.GenerativeModel('gemini-3.1-pro-preview')
            self.flash_model = genai.GenerativeModel('gemini-3-flash-preview')
        else:
            logger.warning("GEMINI_API_KEY not found. Research service will use mock data.")
            self.model = None
            self.flash_model = None
            
        # OpenAI API Key for Deep Reasoning
        self.openai_key = os.getenv("OPENAI_API_KEY")

        # Initialize New SDK Client for Grounding (News Agent)
        self.grounding_client = None
        if self.api_key:
             try:
                 self.grounding_client = new_genai.Client(api_key=self.api_key)
                 logger.info("Initialized Google GenAI V2 Client for Grounding.")
             except Exception as e:
                 logger.error(f"Failed to initialize Google GenAI V2 Client: {e}")

        # Thread safety for shared state updates
        import threading
        self.lock = threading.Lock()

    # ... (skipping methods until _call_agent)


    def analyze_stock(self, ticker: str, raw_data: Dict) -> dict:
        """
        Orchestrates the new 3-Phase Agent Flow:
        1. Agents (Technical + News) -> MarketState.reports
        1. Agents (Technical + News) -> MarketState.reports
        2. Bull & Bear Perspectives (Parallel) -> MarketState.reports['bull'/'bear']
        3. Portfolio Manager (Internet Verification) -> Final Decision
        """
        if not self._check_and_increment_usage():
            return {"recommendation": "SKIP", "reasoning": "Daily limit reached."}

        print(f"\n[ResearchService] Starting Research Council for {ticker}...")
        
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
        print("  > Phase 1: Running Agent Council (Technical, News, Sentiment, Competitive) in Parallel...")
        
        # Prepare Prompts
        tech_prompt = self._create_technical_agent_prompt(state, raw_data, drop_str)
        news_prompt = self._create_news_agent_prompt(state, raw_data, drop_str)
        comp_prompt = self._create_competitive_agent_prompt(state, drop_str)
        sentiment_prompt = self._create_market_sentiment_prompt(state, raw_data)
        
        # Define wrapper for safe execution and result collection
        def run_agent(name, func, *args):
            try:
                return name, func(*args)
            except Exception as e:
                logger.error(f"Error in {name}: {e}")
                return name, f"[Error in {name}: {e}]"

        # Execute in Parallel
        import concurrent.futures
        
        # Initialize results
        tech_report = ""
        news_report = ""
        sentiment_report = ""
        comp_report = ""
        sa_report = ""

        # Agent short names for compact progress display
        agent_short_names = {
            "Technical Agent": "Tech",
            "News Agent": "News",
            "Market Sentiment Agent": "Sentiment",
            "Competitive Landscape Agent": "Competitive",
            "Seeking Alpha Agent": "SA"
        }
        # Track (short_name, success_bool) tuples so the progress line reflects
        # real outcomes, not just "future completed without raising".
        completed_agents: List[tuple] = []

        # Increase max_workers to prevent starvation when agents hit 503 and retry
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(run_agent, "Technical Agent", self._call_agent, tech_prompt, "Technical Agent", state): "technical",
                executor.submit(run_agent, "News Agent", self._call_agent, news_prompt, "News Agent", state): "news",
                executor.submit(run_agent, "Market Sentiment Agent", self._call_agent, sentiment_prompt, "Market Sentiment Agent", state): "sentiment",
                executor.submit(run_agent, "Competitive Landscape Agent", self._call_agent, comp_prompt, "Competitive Landscape Agent", state): "competitive",
                executor.submit(run_agent, "Seeking Alpha Agent", seeking_alpha_service.get_evidence, state.ticker): "seeking_alpha"
            }

            for future in concurrent.futures.as_completed(futures):
                agent_name, result = future.result()
                short = agent_short_names.get(agent_name, agent_name)
                completed_agents.append((short, _is_real_report(result)))

                if agent_name == "Technical Agent":
                    tech_report = result
                elif agent_name == "News Agent":
                    news_report = result
                elif agent_name == "Market Sentiment Agent":
                    sentiment_report = result
                elif agent_name == "Competitive Landscape Agent":
                    comp_report = result
                elif agent_name == "Seeking Alpha Agent":
                    sa_report = result

        # Print compact agent progress summary — ✓ for real reports, ✗ for error stubs
        agent_status = " ".join([f"[{name}{'✓' if ok else '✗'}]" for name, ok in completed_agents])
        fail_count = sum(1 for _, ok in completed_agents if not ok)
        suffix = f"  ({fail_count} failed)" if fail_count else ""
        print(f"  > Agents: {agent_status}{suffix}")

        # Print data depth summary
        try:
            news_items = raw_data.get("news_items", [])
            transcript_text = raw_data.get("transcript_text", "")
            sa_counts = seeking_alpha_service.get_counts(state.ticker)
            sa_total = sa_counts.get('total', 0)
            sa_analysis = sa_counts.get('analysis', 0)
            sa_news = sa_counts.get('news', 0)
            sa_pr = sa_counts.get('pr', 0)
            has_transcript = "Yes" if transcript_text and len(transcript_text) > 100 else "No"
            has_econ = "Yes" if "NEEDS_ECONOMICS: TRUE" in news_report else "No"
            
            print(f"  > Data Depth: {len(news_items)} news articles | SA: {sa_total} items (Analysis:{sa_analysis} News:{sa_news} PR:{sa_pr}) | Transcript: {has_transcript} | Econ: {has_econ}")
        except Exception as e:
            logger.debug(f"Error printing data depth: {e}")

        # Print Competitive Summary to Console (Post-Execution)
        try:
            # Flexible matching for the header (handling potential markdown formatting like ##)
            if "Summary & Key Points" in comp_report:
                # Take everything after the header
                summary_part = comp_report.split("Summary & Key Points")[-1]
                
                # Scan the lines for bullet points
                lines = summary_part.strip().split('\n')
                print("    Key Takeaways:")
                count = 0
                found_bullets = False
                for line in lines:
                    s = line.strip()
                    # Check for standard bullet markers (including numbered lists like "1.")
                    if len(s) > 2 and (s.startswith('-') or s.startswith('*') or (s[0].isdigit() and s[1] in ['.', ')'])):
                        print(f"    {s}")
                        count += 1
                        found_bullets = True
                        if count >= 3: break
                
                if not found_bullets:
                     print("    (Summary found but no bullet points detected)")

            else:
                print("    (Detailed report generated, see full output)")
        except Exception as e:
            print(f"    Error printing summary: {e}")

        
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
            "market_sentiment": sentiment_report,
            "economics": economics_report,
            "competitive": comp_report,
            "seeking_alpha": sa_report
        }

        # --- Phase 1 one-shot retry for failed agents ---
        # Each agent that produced an error stub (or nothing at all) gets
        # a second attempt SEQUENTIALLY. We deliberately avoid parallel
        # retries because the most common cause of Phase-1 failure is
        # Gemini instability, and hammering the API in parallel during
        # an outage just wastes retries.
        retry_prompt_map = {
            "technical": (tech_prompt, "Technical Agent"),
            "news": (news_prompt, "News Agent"),
            "market_sentiment": (sentiment_prompt, "Market Sentiment Agent"),
            "competitive": (comp_prompt, "Competitive Landscape Agent"),
        }
        for key, (prompt, agent_label) in retry_prompt_map.items():
            current = state.reports.get(key)
            if _is_real_report(current):
                continue
            print(f"  > [Phase 1 Retry] {agent_label} failed first pass; retrying once...")
            try:
                retry_result = self._call_agent(prompt, agent_label, state)
                if _is_real_report(retry_result):
                    state.reports[key] = retry_result
                    print(f"  > [Phase 1 Retry] {agent_label} succeeded on retry.")
                else:
                    print(f"  > [Phase 1 Retry] {agent_label} still failing after retry.")
            except Exception as e:
                logger.warning(f"[Phase 1 Retry] {agent_label} retry raised: {e}")

        # Validate Reports (Quality Control)
        # Check for short or missing inputs before passing to Bull/Bear/Storage
        from app.services.quality_control_service import QualityControlService
        state.reports = QualityControlService.validate_council_reports(state.reports, state.ticker)


        # --- Save Council 1 Output to JSON ---
        try:
            council_dir = "data/council_reports"
            os.makedirs(council_dir, exist_ok=True)
            council_file = f"{council_dir}/{safe_ticker_path(state.ticker)}_{state.date}_council1.json"

            with open(council_file, "w") as f:
                json.dump(state.reports, f, indent=4)

            print(f"  > [System] AI Council 1 Reports saved to {council_file}")
        except Exception as e:
            logger.error(f"Failed to save Council 1 reports: {e}")

        # --- Phase 1 Quality Gate ---
        # Abort before Phase 2 if too few core agents produced real reports.
        # This prevents the PM from making decisions on a pile of error stubs
        # (which happened during the BBY outage: 5/5 agents error-stubbed, PM
        # still produced an AVOID verdict from grounding search alone).
        real_count, failed_agents = self._count_real_phase1_reports(state.reports)
        if real_count < MIN_REAL_PHASE1_REPORTS:
            msg = (
                f"[ABORT] Phase 1 quality gate failed for {state.ticker}: "
                f"only {real_count}/{len(PHASE1_CORE_AGENTS)} core agents returned real reports. "
                f"Failed: {failed_agents}. Skipping Phase 2/3/4."
            )
            print(f"\n{'=' * 50}\n  {msg}\n{'=' * 50}\n")
            logger.error(msg)
            return self._build_insufficient_data_response(state, failed_agents, real_count)

        # --- Phase 2: Bull & Bear Perspectives (Brain) ---
        self._run_bull_bear_perspectives(state, drop_str)
        
        # Validate Bull & Bear Reports
        state.reports = QualityControlService.validate_reports(state.reports, state.ticker, ["bull", "bear"])

        # --- Save Council 2 Output to JSON (Phase 1 + Phase 2: bull/bear/risk) ---
        try:
            council_dir = "data/council_reports"
            os.makedirs(council_dir, exist_ok=True)
            council2_file = f"{council_dir}/{safe_ticker_path(state.ticker)}_{state.date}_council2.json"

            with open(council2_file, "w") as f:
                json.dump(state.reports, f, indent=4)

            print(f"  > [System] AI Council 2 Reports (Phase 1+2) saved to {council2_file}")
        except Exception as e:
            logger.error(f"Failed to save Council 2 reports: {e}")

        # --- Phase 3: Portfolio Manager & Decision ---
        print("  > Phase 3: Portfolio Manager Decision...")
        final_decision = self._run_risk_council_and_decision(state, drop_str)
        state.final_decision = final_decision
        
        # --- Print Final Decision to Console ---
        print("\n" + "="*50)
        print(f"  [PORTFOLIO MANAGER DECISION]: {final_decision.get('action')} (Conviction: {final_decision.get('conviction', 'N/A')})")
        print(f"  Drop Type: {final_decision.get('drop_type', 'N/A')}")
        print(f"  Entry Zone: ${final_decision.get('entry_price_low', 'N/A')} - ${final_decision.get('entry_price_high', 'N/A')}")
        print(f"  Stop Loss: ${final_decision.get('stop_loss', 'N/A')} | TP1: ${final_decision.get('take_profit_1', 'N/A')} | TP2: ${final_decision.get('take_profit_2', 'N/A')}")
        print(f"  Upside: {final_decision.get('upside_percent', 'N/A')}% | Downside: {final_decision.get('downside_risk_percent', 'N/A')}% | R/R: {final_decision.get('risk_reward_ratio', 'N/A')}")
        print(f"  Sell Zone: ${final_decision.get('sell_price_low', 'N/A')} - ${final_decision.get('sell_price_high', 'N/A')} | Ceiling: ${final_decision.get('ceiling_exit', 'N/A')}")
        print(f"  Entry Trigger: {final_decision.get('entry_trigger', 'N/A')}")
        print(f"  Exit Trigger: {final_decision.get('exit_trigger', 'N/A')}")
        print(f"  Reassess In: {final_decision.get('reassess_in_days', 'N/A')} trading days")
        print(f"  Reason: {final_decision.get('reason')}")
        print("  Key Factors:")
        for factor in final_decision.get('key_factors', []):
            print(f"   - {factor}")
        print(f"  Total Agent Calls: {state.agent_calls}")
        print("="*50 + "\n")
        
        # --- Phase 4: deep reasoning check for BUY signals ---
        deep_reasoning_report = ""
        action = final_decision.get('action', 'AVOID').upper()
        
        # Gate on recommendation text only — ai_score has no predictive signal
        is_strong_buy = "BUY" in action.upper()
        
        # Override for testing if needed (User can request via flag, but for now we follow logic)
        # is_strong_buy = True 
        
        # [MODIFIED] Disabling synchronous Deep Reasoning to use Batched Async Deep Research in StockService
        # if is_strong_buy:
        #      print("  > [Deep Reasoning] 'Strong Buy' signal detected. Validating with Gemini Deep Research...")
        #      # Pass raw_data if we can, but we need to update the call signature on line 128 first.
        #      # For now, let's update the call here to pass what we have.
        #      deep_reasoning_report = self._run_deep_reasoning_check(state, drop_str, raw_data)
        #      
        #      # If the Deep Reasoning model explicitly downgrades, we should reflect that in the final output
        #      # Simple heuristic: if it says "DOWNGRADE" in the first line or verdict.
        #      if "DOWNGRADE TO" in deep_reasoning_report.upper():
        #          print("  > [Deep Reasoning] VERDICT: Recommendation Downgraded.")
        #          # We won't overwrite the Fund Manager's decision object to preserve history,
        #          # but we will append a major warning to the executive summary.
        #          final_decision['reason'] += " [WARNING: Deep Reasoning Model suggests caution/downgrade - see report]"
        
        # Construct Final Output compatible with existing app expectations
        recommendation = final_decision.get("action", "AVOID").upper()
        
        # Extract checklist metadata
        economics_run = "NEEDS_ECONOMICS: TRUE" in news_report and economics_report != "" and "failed to fetch" not in economics_report
        drop_reason_identified = "REASON_FOR_DROP_IDENTIFIED: YES" in news_report

        
        # --- Calculate Data Depth Metrics (Evidence Barometer) ---
        from app.services.evidence_service import evidence_service
        data_depth = evidence_service.collect_barometer(raw_data, state.reports)

        # Deterministic stop-loss guardrail: widen if PM placed it too tight.
        from app.utils.stop_loss_guard import widen_stop_if_too_tight
        _tv_inds = raw_data.get("indicators", {})
        _entry_low = final_decision.get("entry_price_low")
        # Fallback to current close if entry_price_low is missing or looks like a pct
        if _entry_low is None or (isinstance(_entry_low, (int, float)) and _entry_low < 0):
            _entry_low = _tv_inds.get("close")
        if _entry_low is not None:
            _guard = widen_stop_if_too_tight(
                stop_loss=final_decision.get("stop_loss"),
                entry_low=float(_entry_low),
                atr=float(_tv_inds.get("atr") or 0.0),
                sma_50=_tv_inds.get("sma50"),
                sma_200=_tv_inds.get("sma200"),
                bb_lower=_tv_inds.get("bb_lower"),
            )
            if _guard.adjusted:
                logger.info(
                    "[PM stop-guard] %s: widened stop %.2f -> %.2f (%s)",
                    state.ticker, final_decision["stop_loss"], _guard.stop_loss, _guard.reason,
                )
                final_decision["stop_loss"] = _guard.stop_loss
                final_decision["stop_loss_guard_reason"] = _guard.reason

        return {
            "recommendation": recommendation,
            "executive_summary": final_decision.get("reason", "No reason provided."),
            "deep_reasoning_report": deep_reasoning_report,
            "detailed_report": self._format_full_report(state, deep_reasoning_report, evidence_barometer=data_depth),
            # PM trading-level fields (v0.9)
            "conviction": final_decision.get("conviction", "LOW"),
            "drop_type": final_decision.get("drop_type", "UNKNOWN"),
            "entry_price_low": final_decision.get("entry_price_low"),
            "entry_price_high": final_decision.get("entry_price_high"),
            "stop_loss": final_decision.get("stop_loss"),
            "take_profit_1": final_decision.get("take_profit_1"),
            "take_profit_2": final_decision.get("take_profit_2"),
            "upside_percent": final_decision.get("upside_percent"),
            "downside_risk_percent": final_decision.get("downside_risk_percent"),
            "risk_reward_ratio": final_decision.get("risk_reward_ratio"),
            "pre_drop_price": final_decision.get("pre_drop_price"),
            "entry_trigger": final_decision.get("entry_trigger"),
            "reassess_in_days": final_decision.get("reassess_in_days"),
            # Sell range fields (v1.0)
            "sell_price_low": final_decision.get("sell_price_low"),
            "sell_price_high": final_decision.get("sell_price_high"),
            "ceiling_exit": final_decision.get("ceiling_exit"),
            "exit_trigger": final_decision.get("exit_trigger"),
            "key_factors": final_decision.get("key_factors", []),
            # Legacy compatibility fields
            "technician_report": state.reports.get('technical', ''),
            "bull_report": state.reports.get('bull', ''),
            "bear_report": state.reports.get('bear', ''),
            "macro_report": state.reports.get('news', ''),
            "reasoning": final_decision.get("reason", ""),
            "agent_calls": state.agent_calls,
            "checklist": {
                "economics_run": economics_run,
                "drop_reason_identified": drop_reason_identified
            },
            "key_decision_points": final_decision.get("key_factors", []),  # Mapped for backward compat
            "market_sentiment_report": state.reports.get('market_sentiment', ''),
            "competitive_report": state.reports.get('competitive', ''),
            "data_depth": data_depth
        }

    def _run_bull_bear_perspectives(self, state: MarketState, drop_str: str):
        """
        Executes Bull and Bear agents in parallel to generate independent playbooks.
        """
        import concurrent.futures

        print("  > Phase 2: Running Bull & Bear Agents in Parallel...")
        
        bull_prompt = self._create_bull_prompt(state, drop_str)
        bear_prompt = self._create_bear_prompt(state, drop_str)
        risk_prompt = self._create_risk_agent_prompt(state, drop_str)

        bull_report = ""
        bear_report = ""
        risk_report = ""

        def run_agent(name, func, *args):
            try:
                # print(f"    - Starting {name}...")
                return name, func(*args)
            except Exception as e:
                logger.error(f"Error in {name}: {e}")
                return name, f"[Error in {name}: {e}]"

        phase2_completed = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(run_agent, "Bull Researcher", self._call_agent, bull_prompt, "Bull Researcher", state): "bull",
                executor.submit(run_agent, "Bear Researcher", self._call_agent, bear_prompt, "Bear Researcher", state): "bear",
                executor.submit(run_agent, "Risk Management Agent", self._call_agent, risk_prompt, "Risk Management Agent", state): "risk"
            }
            
            agent_short = {"Bull Researcher": "Bull", "Bear Researcher": "Bear", "Risk Management Agent": "Risk"}
            for future in concurrent.futures.as_completed(futures):
                agent_name, result = future.result()
                phase2_completed.append(agent_short.get(agent_name, agent_name))
                if agent_name == "Bull Researcher":
                    bull_report = result
                elif agent_name == "Bear Researcher":
                    bear_report = result
                elif agent_name == "Risk Management Agent":
                    risk_report = result

        print(f"  > Phase 2: {' '.join([f'[{n}✓]' for n in phase2_completed])}")

        state.reports['bull'] = bull_report
        state.reports['bear'] = bear_report
        state.reports['risk'] = risk_report

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
            
        # 3. Portfolio Manager (Final Decision)
        manager_prompt = self._create_fund_manager_prompt(state, safe_concerns, risky_support, drop_str)
        decision_json_str = self._call_agent(manager_prompt, "Fund Manager", state)
        decision = self._extract_json(decision_json_str)
        
        if not decision:
            decision = {"action": "AVOID", "conviction": "LOW", "reason": "Failed to generate decision JSON.", "drop_type": "UNKNOWN"}
            
        return decision

    def _run_deep_reasoning_check(self, state: MarketState, drop_str: str, raw_data: Dict) -> str:
        """
        Uses Gemini Deep Research as a 'Stock Investor' validation step.
        """
        print("  > [Deep Research] Triggering Gemini Deep Research (Pro Preview)...")
        
        # Prepare inputs
        raw_news = raw_data.get('news_items', [])
        technical_data = raw_data.get('indicators', {})
        transcript_text = raw_data.get('transcript_text', "")
        transcript_date = raw_data.get('transcript_date')
        drop_percent = raw_data.get('change_percent', -5.0)

        # Call service synchronously
        result = deep_research_service.execute_deep_research(
            symbol=state.ticker,
            raw_news=raw_news,
            technical_data=technical_data,
            drop_percent=drop_percent,
            transcript_text=transcript_text,
            transcript_date=transcript_date
        )
        
        if not result:
            return "Deep Research Failed or Timed Out."
            
        # Format the result into a readable string for the report
        verdict = result.get('verdict', 'UNKNOWN')
        risk = result.get('risk_level', 'Unknown')
        reasoning = result.get('reasoning_bullet_points', [])
        
        report_str = f"VERDICT: {verdict}\nRISK LEVEL: {risk}\n\nREASONING:\n"
        for point in reasoning:
            report_str += f"- {point}\n"
            
        if verdict.upper() in ('BUY', 'BUY_LIMIT', 'STRONG_BUY', 'CONFIRMED', 'UPGRADED'):
            verdict_icon = '\U0001F7E2'  # 🟢
        elif verdict.upper() in ('WATCH', 'HOLD'):
            verdict_icon = '\U0001F7E1'  # 🟡
        elif verdict.upper() in ('AVOID', 'SELL', 'STRONG_SELL', 'DOWNGRADE', 'OVERRIDDEN'):
            verdict_icon = '\U0001F534'  # 🔴
        else:
            verdict_icon = '\u2753'  # ❓
            
        print(f"\n  {verdict_icon} [DEEP RESEARCH VERDICT]: {verdict}")
        return report_str

    # --- Prompts ---

    def _create_technical_agent_prompt(self, state: MarketState, raw_data: Dict, drop_str: str) -> str:
        # Extract inputs
        indicators = raw_data.get('indicators', {})
        transcript = raw_data.get('transcript_text', "No transcript available.")
        
        # We assume 'indicators' contains what currently comes from TradingViewService:
        # RSI, Moving Averages, MACD, etc.
        
        transcript_snippet = transcript

        return f"""
You are the **Technical Analyst Agent**.
Your goal is to analyze the price action and technical health of {state.ticker}.
Crucially, you must correlate technical signals with the **Fundamental Context** provided in the Quarterly Report snippet.

CONTEXT: The stock has specifically dropped {drop_str} recently.

INPUT DATA:
1. TECHNICAL INDICATORS:
{json.dumps(indicators, indent=2)}

2. QUARTERLY REPORT SNIPPET (Transcript/Filing - Truncated):
{transcript_snippet}

TASK:
- Analyze if this drop has pushed the stock into oversold territory (RSI, Bollinger Bands, %B, etc.) or into a key support zone favorable for a short-term bounce.
- Analyze the Trend (SMA, MACD) - is this a breakdown or a pullback?
- Analyze Momentum (RSI, Stochastic).
- CROSS-REFERENCE with the Report: Does the CEO/CFO mention reasons for the current price action? (e.g. "We expected a slow Q3", "Supply chain issues").
- Is the technical drop or rally justified by the report?

OUTPUT:
A detailed technical playbook.
We argue that contexts should function not as concise summaries, but as comprehensive, evolving playbooks—detailed, inclusive, and rich with domain insights.
Use headers: "Technical Signal", "Oversold Status", "Context from Report", "Verdict".
"""

    def _create_news_agent_prompt(self, state: MarketState, raw_data: Dict, drop_str: str) -> str:
        news_items = raw_data.get('news_items', [])
        transcript = raw_data.get('transcript_text', "No transcript available.")
        transcript_date = raw_data.get('transcript_date')
        if transcript_date:
            transcript = f"EARNINGS CALL DATE: {transcript_date}\n\n{transcript}"
        
        # Group items by Provider (e.g. Benzinga/Massive, Alpha Vantage, Finnhub)
        # Sort entire list by date desc first
        news_items.sort(key=lambda x: x.get('datetime', 0), reverse=True)
        
        # Organize by provider
        by_provider = {}
        for n in news_items:
            # Normalize provider name if needed or fallback
            prov = n.get('provider', 'Other Sources')
            if prov not in by_provider:
                by_provider[prov] = []
            by_provider[prov].append(n)
            
        # Build Summary String
        news_summary = ""
        
        # We might want a specific order (e.g. Benzinga first)
        preferred_order = ["Market News (Benzinga)", "Benzinga/Massive", "Alpha Vantage", "Finnhub", "Yahoo Finance", "TradingView"]
        
        # Process known providers first
        for prov in preferred_order:
            if prov in by_provider:
                items = by_provider[prov]
                
                # Determine dominant source_type for header annotation
                group_type = items[0].get('source_type', 'WIRE') if items else 'WIRE'
                
                # Custom Header for Market News
                if prov == "Market News (Benzinga)":
                    news_summary += f"--- BROAD MARKET CONTEXT (SPY/DIA/QQQ) [MARKET_CONTEXT] ---\n"
                else:
                    news_summary += f"--- SOURCE: {prov} [{group_type}] ---\n"
                    
                for n in items:
                    date_str = n.get('datetime_str', 'N/A')
                    headline = n.get('headline', 'No Headline')
                    source = n.get('source', 'Unknown')
                    source_type = n.get('source_type', 'WIRE')
                    content = n.get('content', '') # Full body/Insights
                    summary = n.get('summary', '') # Summary/Description
                    
                    news_summary += f"- {date_str}: [{source_type}] {headline} ({source})\n"
                    
                    # Display Content if available (Rich Data), else Summary
                    if content:
                        text_to_show = content
                        if len(text_to_show) > 8000:
                            text_to_show = text_to_show[:8000] + "..."
                        news_summary += f"  CONTENT:\n{text_to_show}\n\n"
                    elif summary:
                        news_summary += f"  SUMMARY: {summary}\n\n"
                    else:
                        news_summary += "\n"
                        
        # Process any remaining "Other" providers
        for prov, items in by_provider.items():
            if prov not in preferred_order:
                group_type = items[0].get('source_type', 'WIRE') if items else 'WIRE'
                news_summary += f"--- SOURCE: {prov} [{group_type}] ---\n"
                for n in items:
                    date_str = n.get('datetime_str', 'N/A')
                    headline = n.get('headline', 'No Headline')
                    source = n.get('source', 'Unknown')
                    source_type = n.get('source_type', 'WIRE')
                    summary = n.get('summary', '')
                    
                    news_summary += f"- {date_str}: [{source_type}] {headline} ({source})\n"
                    if summary:
                         news_summary += f"  SUMMARY: {summary}\n\n"

        # --- LOGGING NEWS CONTEXT ---
        try:
            log_dir = "data/news"
            os.makedirs(log_dir, exist_ok=True)
            log_file = f"{log_dir}/{safe_ticker_path(state.ticker)}_{state.date}_news_context.txt"
            
            with open(log_file, "w") as f:
                f.write(f"NEWS CONTEXT FOR {state.ticker} ({state.date})\n")
                f.write("==================================================\n\n")
                f.write(news_summary)
                
            print(f"  > [News Agent] Logged news context to {log_file}")
        except Exception as e:
            print(f"  > [News Agent] Error logging news context: {e}")

        return f"""
You are the **News Agent**.
Your goal is to gauge the stock's sentiment and identify key narrative drivers for {state.ticker}.
You have access to recent News Headlines and Summaries, and the latest Quarterly Report.

CONTEXT: The stock has dropped {drop_str}. We need to know WHY.

INPUT DATA:
1. RECENT NEWS HEADLINES AND SUMMARIES:
{news_summary}

2. QUARTERLY REPORT SNIPPET (Transcript/Filing):
{transcript}

NOTE ON DATA:
You have been provided with additional data files (JSON/PDF-derived content) in the input.
Treat this as **additional information** which can be **considered or dropped if outdated**.
Take good care about duplications; do not add them up, but rather treat them with caution. Be rational.
Verify dates where possible. If any source data seems older or less relevant than the primary source, prioritize the primary source.

TASK:
- Determine if the drop is due to temporary panic/overreaction or a fundamental structural change. Is this a short-term negative event?
- Identify the dominant narrative (Fear vs Greed? Growth vs Stagnation?).
- Highlight specific events from news or the report that are driving sentiment.
- Check for consistency: Do the headlines match the company's internal tone in the report?
- **CRITICAL ASSESSMENT**: Assess the validity of the news. Is it "Hype" or "Fluff"? Be skeptical of clickbait or promotional content. If a source looks unreliable or the headline is sensationalist, note it. Distinguish between hard facts (earnings miss, lawsuit) and opinion pieces.

SOURCE PRIORITY (when evidence conflicts):
Each news item is tagged with a source_type — use this to judge credibility:
1. OFFICIAL sources (press releases, SEC filings) — ground truth
2. WIRE sources (Benzinga, Reuters, Finnhub) — factual reporting
3. ANALYST sources (Seeking Alpha, Motley Fool) — informed opinion, check for bias
4. MARKET_CONTEXT — broad signals, not company-specific
When an ANALYST article contradicts a WIRE report, trust the WIRE source for facts
but consider the ANALYST perspective for sentiment and thesis construction.

OUTPUT:
A comprehensive sentiment playbook.
We argue that contexts should function not as concise summaries, but as comprehensive, evolving playbooks—detailed, inclusive, and rich with domain insights.
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
{self._news_block_for(state, "news")}"""

    def _create_economics_agent_prompt(self, state: MarketState, macro_data: Dict) -> str:
        return f"""
You are the **Macro Economics Agent**.
Your goal is to analyze the US and World Economic environment and its potential impact on {state.ticker}.

IMPORTANT: Before starting your analysis, verify the correct company name and sector for ticker {state.ticker} via Google Search. Do NOT guess — foreign tickers (e.g., OTC, ADRs) are easily confused with similarly-named companies. Base your entire analysis on the verified company.

Use the internet to find additional data if needed.

INPUT DATA (FRED API):
{json.dumps(macro_data, indent=2)}

TASK:
- Analyze the provided macro indicators (Unemployment, CPI, Rates, GDP, Yields).
- Determine if the current macro environment is a Headwind or Tailwind for this specific company/sector.
- specifically look for "Recession Signals" (Yield Curve Inversion, rising Unemployment) if relevant.

OUTPUT:
A detailed macro playbook.
We argue that contexts should function not as concise summaries, but as comprehensive, evolving playbooks—detailed, inclusive, and rich with domain insights.
Headers: "Macro Environment", "Impact on {state.ticker}", "Risk Level".
"""

    def _create_competitive_agent_prompt(self, state: MarketState, drop_str: str) -> str:
        return f"""
You are the **Competitive Landscape Agent**.
Your goal is to create a detailed competitive landscape analysis for {state.ticker} using Google Search.

IMPORTANT: Before starting your analysis, verify the correct company name and sector for ticker {state.ticker} via Google Search. Do NOT guess — foreign tickers (e.g., OTC, ADRs) are easily confused with similarly-named companies. Base your entire analysis on the verified company.

CONTEXT: The stock has dropped {drop_str}. We need to know if this is a company-specific issue or a sector-wide issue.

TASK:
1. Identify the top 3-5 direct competitors of {state.ticker}.
2. Compare their recent stock performance (last 1-3 months) vs {state.ticker}. Is {state.ticker} underperforming the peer group?
3. Identify any "Moat" or competitive advantage that is at risk.
4. Search for recent "Sector News" - are there regulatory headwinds, supply chain issues, or tech shifts affecting everyone in this industry?
5. Find if a competitor has recently launched a "Killer Product" or announced specific bad news that might drag peers down (sympathy drop).

OUTPUT FORMAT:
The output MUST be a long and detailed **Competitor Playbook**.
Structure it as follows:

## 1. Top Competitors & Performance
(List competitors and how they have fared recently compared to this stock)

## 2. Sector Headwinds/Tailwinds
(Industry-wide analysis)

## 3. Moat Analysis
(Is the competitive advantage intact?)

## 4. Specific Threats
(New products, regulatory changes, etc.)

## 5. Summary & Key Points
(Provide EXACTLY 3 bullet points summarizing the most critical competitive insights)
- Point 1
- Point 2
- Point 3
{self._news_block_for(state, "competitive")}"""

    def _create_bull_prompt(self, state: MarketState, drop_str: str) -> str:
        return f"""
You are the **Bullish Researcher**. Your goal is to look rationally at the given stock with searching for upsides.
CONTEXT: We are looking for a swing trade / short-term recovery opportunity on this {drop_str} drop. We want to know if the market reaction is too strong.
You received additional data from an analysis team below.

AGENT REPORTS:
{json.dumps(state.reports, indent=2)}

TASK:
Construct a realistic argument for a LONG position (The Bull Case).
1. Explain the "Narrative" for the bounce.
2. Cite specific positive drivers from the **News Headlines** and **Earnings Transcript**.
3. Do not rely solely on technicals; explain the FUNDAMENTAL and market reason for a reversal.
4. Remember that the Market often priced in information already but sometimes too much or too little.

SAFETY:
Answer primarily from the provided Agent Reports.
However, you have access to Google Search to fill in gaps if the reports are missing critical recent context or to check if a "Fear" narrative is overblown.
DO NOT HALLUCINATE.

OUTPUT:
A comprehensive bullish playbook.
Use headers: "Bullish Narrative", "Evidence from Report", "Catalysts", "Quantitative Estimation", "Conclusion".

QUANTITATIVE ESTIMATION (The Bull Case Numbers):
- **Revenue Stream**: Estimate the revenue impact in this Optimistic Scenario. Is the damage contained? (e.g. "0% impact", "-2% temporary hit").
- **Valuation Impact**:
    - **Current PE**: Identify the current PE.
    - **Bull Case Forward PE**: Estimate the forward PE assuming your benign narrative holds true.
    - **EPS Impact**: Provide a "% Impact on EPS" estimate (e.g. "-1% EPS impact").

SELL TARGET ESTIMATION (The Bull Case Exit):
- At what price level does the recovery top out? Consider pre-drop price, BB upper, SMA50, SMA200, and 52-week high as anchors.
- At what price would even a bull say "take profits"? (e.g. "RSI > 70 combined with price at $148-150 suggests overextension")
- Consider the drop type: earnings misses rarely fully recover in one quarter, while sector rotations can overshoot to the upside.
- Provide a TARGET_SELL_RANGE: {{"low": $X, "high": $Y}} in your Conclusion.
"""

    def _create_bear_prompt(self, state: MarketState, drop_str: str) -> str:
        return f"""
You are the **Bearish Researcher**. Your goal is to protect the firm's capital from risk.
CONTEXT: The stock dropped {drop_str}.
Review the Agent Reports.

AGENT REPORTS:
{json.dumps(state.reports, indent=2)}

TASK:
Construct a rational and logical argument for a NO TRADE or SHORT position. Explain if the drop is realistically priced in by the market. 
1. Explain why this drop is justified or could go deeper.
2. Cite specific negative risks from the **News and Transcript** (guidance cuts, macro headwinds).
3. Be skeptical of the "Dip Buy" mentality.

SAFETY:
Answer primarily from the provided Agent Reports.
However, you have access to Google Search to check for "Red Flags" that might be missing (e.g. lawsuits, fraud allegations, major analyst downgrades).
DO NOT HALLUCINATE.

A comprehensive bearish playbook.
Use headers: "Bearish Narrative", "Risks & Red Flags", "Fundamental Flaws", "Quantitative Estimation", "Conclusion".

QUANTITATIVE ESTIMATION (The Bear Case Numbers):
- **Revenue Stream**: Estimate the revenue impact in this Pessimistic Scenario. potential loss of contracts/customers? (e.g. "-15% permanent revenue loss").
- **Valuation Impact**:
    - **Current PE**: Identify the current PE.
    - **Bear Case Forward PE**: Estimate the forward PE assuming the worst-case structural decline.
    - **EPS Impact**: Provide a "% Impact on EPS" estimate (e.g. "-20% EPS impact").

REALISTIC EXIT CEILING (Bear's Upside Limit):
- Even if the bull case plays out, what is the maximum realistic recovery price? Where does the bear think recovery stalls?
- Identify the key resistance level that would cap any bounce (e.g. SMA50, BB upper, pre-drop price, volume resistance).
- Where would the "easy money" run out? (e.g. "Volume dries up above $142, institutional selling resumes at SMA200")
- Provide a BEAR_EXIT_CEILING: $X in your Conclusion.
{self._news_block_for(state, "bear")}"""



    def _create_fund_manager_prompt(self, state: MarketState, safe_concerns: List[str], risky_support: List[str], drop_str: str) -> str:
        bull_report = state.reports.get('bull', 'No Bull Report')
        bear_report = state.reports.get('bear', 'No Bear Report')
        risk_report = state.reports.get('risk', 'No Risk Report')

        # Extract available technical levels for the PM to reference
        indicators = state.reports.get('technical', '')

        return f"""
You are the **Portfolio Manager**. You have the final vote.
You must weigh the arguments from the Bull Agent and the Bear Agent, cross-reference with the original Agent Reports, and produce a concrete, actionable trading plan.

DECISION CONTEXT:
- Stock: {state.ticker}
- Drop: {drop_str} today
- This is a "Buy the Dip" evaluation. We are looking for oversold large-cap stocks with recovery potential.
- The investor holds positions until recovery (weeks to months), not day-trading.

RISK FACTORS (For Consideration):
- Technical Flags: {safe_concerns}
- News Flags: {risky_support}
- **RISK AGENT ASSESSMENT**:
{risk_report}

BULL CASE:
{bull_report}

BEAR CASE:
{bear_report}

AGENT REPORTS (Raw Data):
{json.dumps(state.reports, indent=2)}

CRITICAL TASK:
1. **TRUST BUT VERIFY**: You have access to Google Search. Use it to verify the key claims made by the Bull and Bear.
   - If the Bull claims "Earnings Beat", check if it was actually a beat or a mixed bag.
   - If the Bear claims "Lawsuit", verify the severity.
2. Weigh the evidence. Who has the stronger case based on FACTS, not just rhetoric?
3. **SOURCE QUALITY**: Agent reports reference source types — OFFICIAL (SEC/PR), WIRE (factual), ANALYST (opinion), MARKET_CONTEXT (broad). When claims conflict, OFFICIAL and WIRE sources carry more weight than ANALYST opinions.
4. **CLASSIFY THE DROP**: Determine WHY the stock dropped. This is critical for predicting recovery.
5. **CALCULATE TRADING LEVELS**: Using the technical data (ATR, Support, Resistance, Bollinger Bands) from the reports, determine concrete price levels.

AVAILABLE TECHNICAL DATA (use these exact fields from the reports):
- Current price: `close` field in indicators
- ATR: `atr` field (Average True Range — use for stop-loss distance)
- RSI: `rsi` field (oversold < 30, overbought > 70)
- Bollinger Band Lower: `bb_lower` (dynamic support proxy)
- Bollinger Band Upper: `bb_upper` (dynamic resistance proxy)
- SMA50: `sma50`, SMA200: `sma200` (trend context)
- 52-Week High: `high52`, 52-Week Low: `low52` (historical range)
- BB Middle (approximate): midpoint of bb_lower and bb_upper
Note: No explicit support/resistance levels are provided. Use Bollinger Bands, SMA levels, and 52-week extremes as proxies.

INSTRUCTIONS FOR TRADING LEVELS:
- **entry_price_low / entry_price_high**: The price zone where buying makes sense. Use bb_lower and the current close price as guides. If "BUY" (immediate), set this to the current close price +/- 1%.
- **stop_loss**: Set at 2x ATR below entry_price_low, or below the bb_lower if that is tighter. This is the "thesis is broken" level. Must be a concrete number.
- **take_profit_1**: Conservative target. Typically the pre-drop price (calculate from close and drop_percent) or the BB middle (average of bb_lower and bb_upper). This is the recovery target.
- **take_profit_2**: Optimistic target. bb_upper, SMA50, or SMA200 — whichever is above TP1 and realistic. Set to null if no clear upside beyond TP1.
- **upside_percent**: Calculate from current close to take_profit_1. Example: close is $100, TP1 is $112 → upside is 12.0.
- **downside_risk_percent**: Calculate from current close to stop_loss. Example: close is $100, SL is $90 → downside is 10.0.
- **pre_drop_price**: Calculate from close and drop_percent. Formula: close / (1 + drop_percent/100). Example: close=$93, drop=-7% → pre_drop = 93 / 0.93 = $100. Include this for reference.

INSTRUCTIONS FOR SELL RANGE:
These define where you recommend SELLING (taking profits) once the position is entered. Use the Bull's TARGET_SELL_RANGE and the Bear's BEAR_EXIT_CEILING as inputs alongside technicals.
- **sell_price_low**: Conservative exit target — where a cautious trader starts scaling out. Use the LESSER of: pre_drop_price, or bb_middle (midpoint of bb_lower and bb_upper). This is the "safe profits" level.
- **sell_price_high**: Optimistic exit target — where even bulls should exit. Use bb_upper, SMA50, or SMA200 — whichever represents realistic resistance above sell_price_low. Consider the Bear's exit ceiling as an upper bound.
- **ceiling_exit**: Absolute maximum target beyond which further gains are unlikely without a new catalyst. Calculate as: min(high52, bb_upper + 1×ATR). This is the "euphoria" level.
- **exit_trigger**: A specific condition that signals time to sell (not just a price level). Examples: 'RSI crosses above 70 and price enters $142-$148 zone', 'Price stalls at SMA200 on declining volume for 2 sessions', 'Earnings report in 3 days — de-risk'. For AVOID action, set to 'N/A — no position recommended.'

INSTRUCTIONS FOR DROP CLASSIFICATION:
Classify the `drop_type` as one of:
- "EARNINGS_MISS" — Drop triggered by disappointing earnings or guidance
- "ANALYST_DOWNGRADE" — Driven by analyst rating changes or price target cuts
- "SECTOR_ROTATION" — Sector-wide selling, not company-specific
- "MACRO_SELLOFF" — Broad market decline (rates, recession fears, geopolitics)
- "COMPANY_SPECIFIC" — Lawsuit, management change, product failure, fraud
- "TECHNICAL_BREAKDOWN" — No fundamental catalyst; purely technical selling
- "UNKNOWN" — No clear catalyst identified

INSTRUCTIONS FOR CONVICTION:
- "HIGH": Bull case is verified, risk/reward ratio > 2:1, multiple catalysts align, and the drop type is recoverable (EARNINGS_MISS with beat, SECTOR_ROTATION, MACRO_SELLOFF).
- "MODERATE": Mixed signals but favorable lean. Some unresolved risks. Risk/reward roughly 1.5:1.
- "LOW": Too many unknowns, bear case has strong points, or drop type is structural (fraud, permanent competitive loss). Skip this trade.

INSTRUCTIONS FOR ACTION:
- "BUY": Enter now at current price. Conviction is HIGH. The evidence strongly supports recovery.
- "BUY_LIMIT": Set a limit order at entry_price_low. Price needs to stabilize or dip slightly more before entry.
- "WATCH": Add to watchlist with specific entry_trigger condition. Do NOT buy yet.
- "AVOID": Do not trade. The bear case dominates or risk/reward is unfavorable.

OUTPUT:
A strictly formatted JSON object. All price fields must be numbers (not strings). All percentage fields must be numbers (e.g. 12.5 not "12.5%").
{{
  "action": "BUY" | "BUY_LIMIT" | "WATCH" | "AVOID",
  "conviction": "HIGH" | "MODERATE" | "LOW",
  "drop_type": "EARNINGS_MISS" | "ANALYST_DOWNGRADE" | "SECTOR_ROTATION" | "MACRO_SELLOFF" | "COMPANY_SPECIFIC" | "TECHNICAL_BREAKDOWN" | "UNKNOWN",
  "entry_price_low": <number>,
  "entry_price_high": <number>,
  "stop_loss": <number>,
  "take_profit_1": <number>,
  "take_profit_2": <number or null>,
  "upside_percent": <number>,
  "downside_risk_percent": <number>,
  "risk_reward_ratio": <number (upside_percent / downside_risk_percent, rounded to 1 decimal)>,
  "pre_drop_price": <number (calculated: close / (1 + drop_percent/100))>,
  "entry_trigger": "String describing specific condition to enter. Examples: 'RSI crosses above 30', 'Price holds above $142 for 2 sessions', 'Volume returns to 20-day average'. For BUY action, use 'Immediate — current levels are attractive.'",
  "reassess_in_days": <number (trading days before this analysis expires, typically 3-10)>,
  "sell_price_low": <number (conservative exit target — where to start taking profits)>,
  "sell_price_high": <number (optimistic exit target — where to fully exit)>,
  "ceiling_exit": <number (absolute max target — beyond this, gains unlikely without new catalyst)>,
  "exit_trigger": "String describing specific condition to sell. Examples: 'RSI > 70 and price in $142-$148 zone', 'Price stalls at SMA200 on declining volume'. For AVOID action, use 'N/A — no position recommended.'",
  "reason": "One sentence: why this is or isn't a good trade right now.",
  "key_factors": [
      "String (Factor 1 — most important evidence for/against)",
      "String (Factor 2 — verification result from Google Search)",
      "String (Factor 3 — technical or risk consideration)"
  ]
}}
{self._news_block_for(state, "pm")}"""

    # --- Helpers ---

    def _news_block_for(self, state: MarketState, agent_name: str) -> str:
        """Return a news-digest block ready to paste into a prompt, or empty string.

        Pulls the right slice per the agent-consumption map. Safe to call
        even when digests are disabled or missing — returns "".
        """
        try:
            from app.services.news_digest_service import format_for_agent
            block = format_for_agent(
                agent_name=agent_name,
                date=state.date,
                ticker=state.ticker,
                sector=getattr(state, "sector", None),
            )
            if not block.strip():
                return ""
            return f"\n\nRELEVANT NEWS DIGEST (FT / Finimize, auto-generated):\n{block.strip()}\n"
        except Exception as e:
            logger.warning(f"format_for_agent({agent_name}) failed: {e}")
            return ""

    def _count_real_phase1_reports(self, reports: Dict[str, Optional[str]]) -> tuple:
        """Count how many Phase 1 core agents returned a real report (not an error stub).

        Returns (real_count, failed_agent_names).
        """
        real = 0
        failed = []
        for key in PHASE1_CORE_AGENTS:
            if _is_real_report(reports.get(key)):
                real += 1
            else:
                failed.append(key)
        return real, failed

    def _build_insufficient_data_response(
        self,
        state: MarketState,
        failed_agents: List[str],
        real_count: int,
    ) -> Dict:
        """Build a short-circuit response when Phase 1 quality gate fails.

        Mirrors the shape of the normal analyze_stock return so downstream
        callers (stock_service, database writer, dashboard) don't need
        special-case handling. Uses recommendation='PASS_INSUFFICIENT_DATA'
        so it never matches 'BUY' downstream.
        """
        total = len(PHASE1_CORE_AGENTS)
        summary = (
            f"Phase 1 quality gate failed: only {real_count}/{total} core sensor "
            f"agents returned real reports (failed: {', '.join(failed_agents)}). "
            f"Analysis aborted before Phase 2 to avoid a PM decision from error stubs."
        )
        detailed = (
            f"*** INSUFFICIENT DATA — ANALYSIS ABORTED ***\n\n{summary}\n\n"
            f"This decision point was generated without running Bull/Bear/Risk/PM/Deep Research, "
            f"because the Phase 1 sensor agents did not produce enough usable data (likely a "
            f"Gemini API availability incident; check the preceding log lines for exception types).\n"
        )
        return {
            "recommendation": "PASS_INSUFFICIENT_DATA",
            "executive_summary": summary,
            "deep_reasoning_report": "",
            "detailed_report": detailed,
            # PM trading-level fields
            "conviction": "NONE",
            "drop_type": "UNKNOWN",
            "entry_price_low": None,
            "entry_price_high": None,
            "stop_loss": None,
            "take_profit_1": None,
            "take_profit_2": None,
            "upside_percent": None,
            "downside_risk_percent": None,
            "risk_reward_ratio": None,
            "pre_drop_price": None,
            "entry_trigger": None,
            "reassess_in_days": None,
            # Sell-range fields
            "sell_price_low": None,
            "sell_price_high": None,
            "ceiling_exit": None,
            "exit_trigger": None,
            "key_factors": [],
            # Legacy compatibility fields (pass through whatever we got, so the UI
            # can still show error stubs and operators can see what failed)
            "technician_report": state.reports.get("technical", ""),
            "bull_report": "",
            "bear_report": "",
            "macro_report": state.reports.get("news", ""),
            "reasoning": summary,
            "agent_calls": state.agent_calls,
            "checklist": {
                "economics_run": False,
                "drop_reason_identified": False,
            },
            "key_decision_points": [],
            "market_sentiment_report": state.reports.get("market_sentiment", ""),
            "competitive_report": state.reports.get("competitive", ""),
            "data_depth": {},
            # Sentinel field so dashboards/backfill can filter these out
            "aborted_reason": "insufficient_phase1_data",
            "failed_phase1_agents": failed_agents,
        }

    def _call_agent(self, prompt: str, agent_name: str, state: Optional[MarketState] = None) -> str:
        if not self.model:
            return "Mock Output"
        try:
            # logger.info(f"Calling Agent: {agent_name}")
            if state:
                with self.lock:
                    state.agent_calls += 1
            
            # List of agents that should use Grounding (Internet Search)
            # Technical and Economics added as per user request
            # Bull, Bear, and Fund Manager added as per user request
            grounded_agents = [
                "News Agent",
                "Competitive Landscape Agent",
                "Market Sentiment Agent",
                "Technical Agent",
                "Economics Agent",
                "Bull Researcher",
                "Bear Researcher",
                "Bull Researcher",
                "Bear Researcher",
                "Fund Manager",
                "Risk Management Agent"
            ]

            if agent_name in grounded_agents and self.grounding_client:
                 model_to_use = "gemini-3-flash-preview"
                 
                 # Bull, Bear, and Fund Manager should use Gemini 3 Pro
                 if agent_name in ["Bull Researcher", "Bear Researcher", "Fund Manager", "Risk Management Agent"]:
                     model_to_use = "gemini-3.1-pro-preview"
                 
                 logger.info(f"Calling {agent_name} with {model_to_use} + Grounding...")
                 return self._call_grounded_model(prompt, model_name=model_to_use, agent_context=agent_name)

            # Default (Bull, Bear, Manager) -> Main Model (Gemini 3 Pro) without grounding
            # Using standard generate_content (old SDK)
            # Rate limit buffer
            time.sleep(2)
            
            response = self.model.generate_content(prompt, request_options=RequestOptions(timeout=600))
            return response.text
        except Exception as e:
            # Check for 503 Unavailable inside generic exception (for GenAI v1 SDK)
            if "503" in str(e) and getattr(self, "model", None) and self.model.model_name == 'models/gemini-3.1-pro-preview':
                logger.warning(f"503 UNAVAILABLE for gemini-3.1-pro-preview. Falling back to gemini-3-pro-preview for {agent_name}...")
                try:
                    fallback_model = genai.GenerativeModel('gemini-3-pro-preview')
                    fallback_response = fallback_model.generate_content(prompt, request_options=RequestOptions(timeout=600))
                    return fallback_response.text
                except Exception as fallback_e:
                    logger.error(f"Fallback model also failed for {agent_name}: {fallback_e}")
                    return f"[Error: {fallback_e}]"
            
            logger.error(f"Error in {agent_name}: {e}")
            return f"[Error: {e}]"

    def _call_grounded_model(
        self,
        prompt: str,
        model_name: str,
        agent_context: str = "",
        retry_count: int = 0,
        budget_deadline: Optional[float] = None,
    ) -> str:
        """
        Generic helper to call a model with Google Search Grounding enabled.

        Retry policy:
        - FunctionCall (finish_reason 10, model failed to auto-ground): up to
          MAX_GROUNDING_RETRIES retries with no backoff.
        - Retryable exceptions (ConnectionReset, 503/504, UNAVAILABLE, timeouts):
          up to MAX_GROUNDING_RETRIES retries with exponential backoff (2s, 4s).
        - Non-retryable exceptions: fail fast, return error stub.
        - 3.1-pro 503 specifically falls back to 3-pro on the first attempt
          (model-availability issue, no point burning retries on a known-bad preview).

        Wall-clock budget:
        - First call stamps ``budget_deadline = time.time() + AGENT_WALL_CLOCK_BUDGET_SEC``.
        - Every recursive retry inherits that deadline; if it has already passed
          we abort immediately with an error stub, regardless of remaining
          retry_count. This prevents the QXO/PB 04-22 multi-hour stall where a
          transient 503 + exponential backoff looped for ~17 hours.
        """
        if budget_deadline is None:
            budget_deadline = time.time() + AGENT_WALL_CLOCK_BUDGET_SEC

        if time.time() >= budget_deadline:
            logger.warning(
                f"[{agent_context}] Wall-clock budget exhausted after {retry_count} retries; giving up."
            )
            return (
                f"[Error: {agent_context} exceeded {AGENT_WALL_CLOCK_BUDGET_SEC}s wall-clock "
                f"budget after {retry_count} retries]"
            )

        attempt_label = f"attempt {retry_count + 1}/{MAX_GROUNDING_RETRIES + 1}"
        try:
            config = new_types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.7,
                # Use GenAI's typed config for HTTP options (timeout is in millis if integer on some SDK versions, but 600 ensures a long enough wait in any unit)
                http_options=new_types.HttpOptions(timeout=600000)
            )

            response = self.grounding_client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )

            # Check for FunctionCall (finish_reason 10) which indicates failure to auto-ground
            candidate = response.candidates[0] if response.candidates else None
            finish_reason = candidate.finish_reason if candidate else None

            # 10 is STOP_REASON_FUNCTION_CALL
            if finish_reason == 10 or finish_reason == "STOP_REASON_FUNCTION_CALL":

                if retry_count < MAX_GROUNDING_RETRIES:
                    logger.warning(f"Model {model_name} returned FunctionCall ({attempt_label}) in {agent_context}. Retrying...")
                    return self._call_grounded_model(prompt, model_name, agent_context, retry_count=retry_count + 1, budget_deadline=budget_deadline)

                msg = f"""
################################################################################
[CRITICAL WARNING] GROUNDING FAILURE IN {agent_context.upper()}
Model: {model_name}
Reason: Model returned a Function Call (10) instead of grounded text despite {MAX_GROUNDING_RETRIES} retries.
Process continuing but this agent's output is compromised.
################################################################################
"""
                print(msg)
                logger.error(msg)
                return f"[SYSTEM ERROR: Grounding failed for {agent_context}. Model returned invalid Function Call.]"

            # Format and return with citations
            report_text = self._format_citations(response)
            report_text += f"\n\n(Context: {agent_context} | Model: {model_name} | Grounding: Enabled)"
            return report_text

        except Exception as e:
            err_type = type(e).__name__
            err_msg = str(e)
            is_503 = getattr(e, "code", None) == 503 or "503" in err_msg

            # 3.1-pro 503 → fall back to 3-pro on the first attempt only.
            # Both models being unavailable is a real outage; let the fallback run its own retry budget.
            if is_503 and "pro" in model_name and "3.1" in model_name and retry_count == 0:
                logger.warning(f"503 UNAVAILABLE for {model_name} in {agent_context} ({err_type}). Falling back to gemini-3-pro-preview...")
                if time.time() + 2 >= budget_deadline:
                    return (
                        f"[Error: {agent_context} exceeded {AGENT_WALL_CLOCK_BUDGET_SEC}s wall-clock "
                        f"budget before 503 fallback]"
                    )
                time.sleep(2)
                try:
                    return self._call_grounded_model(prompt, "gemini-3-pro-preview", agent_context, retry_count=0, budget_deadline=budget_deadline)
                except Exception as fallback_e:
                    logger.error(f"Fallback model also failed for {agent_context} ({type(fallback_e).__name__}): {fallback_e}")
                    return f"[Error in {agent_context} (Fallback): {type(fallback_e).__name__}: {fallback_e}]"

            retryable = _is_retryable_grounding_error(e)
            logger.error(f"Grounding call failed for {agent_context} (model={model_name}, type={err_type}, retryable={retryable}, {attempt_label}): {err_msg}")

            if retryable and retry_count < MAX_GROUNDING_RETRIES:
                wait = 2 ** (retry_count + 1)  # 2s, 4s
                # If the upcoming sleep would blow the wall-clock budget, bail now
                # rather than sleeping pointlessly before the recursive call aborts.
                if time.time() + wait >= budget_deadline:
                    logger.warning(
                        f"[{agent_context}] Skipping {wait}s backoff; wall-clock budget would expire."
                    )
                    return (
                        f"[Error: {agent_context} exceeded {AGENT_WALL_CLOCK_BUDGET_SEC}s wall-clock "
                        f"budget after {retry_count} retries]"
                    )
                logger.info(f"Retrying {agent_context} ({model_name}) in {wait}s ({err_type})...")
                time.sleep(wait)
                return self._call_grounded_model(prompt, model_name, agent_context, retry_count=retry_count + 1, budget_deadline=budget_deadline)

            if not retryable:
                logger.warning(f"Non-retryable exception for {agent_context} ({err_type}); failing fast.")

            print(f"\n!!! GROUNDING EXCEPTION ({agent_context}, {err_type}): {e} !!!\n")
            return f"[Error in {agent_context}: {err_type}: {e}]"

    def _create_market_sentiment_prompt(self, state: MarketState, raw_data: Dict) -> str:
        """Builds the prompt for the Market Sentiment Agent."""
        ticker = state.ticker

        market_news_str = ""
        news_items = raw_data.get('news_items', []) if raw_data else []
        found_market_news = False

        for n in news_items:
            if n.get('provider') == 'Market News (Benzinga)':
                headline = n.get('headline', 'No Headline')
                date = n.get('datetime_str', 'N/A')
                summary = n.get('summary', '')
                market_news_str += f"- {date}: {headline}\n  Summary: {summary}\n"
                found_market_news = True

        if found_market_news:
            market_news_str = f"\n        PROVIDED BROAD MARKET CONTEXT (Important):\n{market_news_str}\n"

        return f"""
        You are the **Market Sentiment Agent**.
        Your goal is to analyze the general market sentiment and specifically the markets relevant to {ticker}.

        IMPORTANT: Before starting your analysis, verify the correct company name and sector for ticker {ticker} via Google Search. Do NOT guess — foreign tickers (e.g., OTC, ADRs) are easily confused with similarly-named companies. Base your entire analysis on the verified company.

        CONTEXT:
        - Date: {state.date}
        - Focus: TODAY and YESTERDAY only.
        {market_news_str}

        TASK:
        1. **Identify Markets**:
           - **Listing Market**: Where is {ticker} listed? (e.g. Frankfurt -> DAX, London -> FTSE).
           - **Business Market**: Where does {ticker} generate most of its revenue? (e.g. US, China, Europe).

        2. **Analyze Sentiment (Live Search)**:
           - Use Google Search to find market summaries for **TODAY** and **YESTERDAY**.
           - **MANDATORY**: Always check the **US MARKET direction** (S&P 500, Nasdaq, Dow Jones) even if the stock is not US-listed.
           - Check the **Listing Market** sentiment (e.g. DAX if German).
           - Check the **Business Market** sentiment if different (e.g. if a German company sells mostly in US, US sentiment is double important).

        3. **Synthesize**:
           - Is the general market environment Risk-On or Risk-Off?
           - Are we in a broad sell-off or a rally?
           - How does this affect {ticker}?

        OUTPUT FORMAT:
        ## Market Identification
        - **Home Market**: [Exchange/Country]
        - **Primary Business Region**: [Region]

        ## Global/US Market Context (Today/Yesterday)
        - **US Indices (SPX/NDX)**: [Direction: Bullish/Bearish/Neutral]
        - **Commentary**: [Details on US market moves today/yesterday]

        ## Home/Local Market Context
        - **Index ([Name])**: [Direction]
        - **Commentary**: [Details on local market]

        ## Market Sentiment Summary
        [Concise summary of whether the market environment is a Headwind or Tailwind for {ticker} right now.]
        {self._news_block_for(state, "market_sentiment")}"""

    def _format_citations(self, response) -> str:
        """
        Adds inline citations to the response text based on grounding metadata.
        Adapted from Google GenAI docs.
        """
        try:
            if not response.candidates:
                return "No response generated."
            
            candidate = response.candidates[0]
            if not candidate.content or not candidate.content.parts:
                return "Empty response content."
                
            text = candidate.content.parts[0].text
            if not text:
                return ""

            # Check for grounding metadata
            if not candidate.grounding_metadata:
                return text

            metadata = candidate.grounding_metadata
            supports = metadata.grounding_supports
            chunks = metadata.grounding_chunks

            if not supports or not chunks:
                return text

            # Sort supports by end_index in descending order to avoid shifting issues
            sorted_supports = sorted(supports, key=lambda s: s.segment.end_index, reverse=True)

            for support in sorted_supports:
                end_index = support.segment.end_index
                if support.grounding_chunk_indices:
                    citation_refs = []
                    for i in support.grounding_chunk_indices:
                        if i < len(chunks):
                            citation_refs.append(f"[Source {i + 1}]")
                    
                    if citation_refs:
                        citation_string = " " + "".join(citation_refs)
                        text = text[:end_index] + citation_string + text[end_index:]
            
            # Add a clean Source List at the bottom (titles only, no raw URLs)
            text += "\n\n### Sources:\n"
            for i, chunk in enumerate(chunks):
                web = chunk.web
                title = web.title if web.title else "Source"
                text += f"{i + 1}. {title}\n"

            return text
            
        except Exception as e:
            logger.error(f"Error formatting citations: {e}")
            try:
                # Safe access to text if available, else string representation
                if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                     return response.candidates[0].content.parts[0].text or "Empty Text"
                return "Error: No Content"
            except:
                return f"[Error formatting response: {str(e)}]"

    def _extract_json(self, text: str) -> Optional[Dict]:
        """
        Robust JSON extractor that handles markdown code blocks.
        Strips Gemini [Source N] citation markers before parsing.
        """
        try:
            # Strip citation markers that corrupt JSON structure
            text = _strip_citations(text)

            # Find the start and end of the JSON object
            # Simple heuristic: Look for first { and last }
            start = text.find('{')
            end = text.rfind('}')

            if start != -1 and end != -1:
                json_str = text[start:end+1]
                return json.loads(json_str)
            return None
        except Exception as e:
            logger.error(f"Failed to extract JSON: {e}")
            logger.error(f"Raw text (first 500 chars): {text[:500]}")
            return None

    def _format_full_report(self, state: MarketState, deep_report: str = "", evidence_barometer: Dict = None) -> str:
        debate_section = ''.join([f"\n{entry}\n" for entry in state.debate_transcript])
        
        deep_section = ""
        if deep_report:
            deep_section = f"\n## 0. DEEP REASONING VERDICT (Verification)\n{deep_report}\n"
            
        # Format Evidence Barometer
        evidence_section = ""
        if evidence_barometer:
            news = evidence_barometer.get('news', {})
            fund = evidence_barometer.get('fundamentals', {})
            
            # Helper to format providers
            providers = news.get('providers', {})
            prov_str = ", ".join([f"{k}: {v}" for k, v in providers.items()])
            
            evidence_section = f"""
## 📊 EVIDENCE BAROMETER
**Data Depth & Quality Analysis**
- **News Coverage**: {news.get('total_count', 0)} Items ({news.get('total_length_chars', 0)} chars)
  - *Sources*: {prov_str or "None"}
  - *Range*: {news.get('time_range', {}).get('newest', 'N/A')} to {news.get('time_range', {}).get('oldest', 'N/A')}
- **Fundamentals**: Transcript {'Available' if fund.get('transcript_available') else 'Missing'} ({fund.get('transcript_length', 0)} chars)
  - *Date*: {fund.get('transcript_date', 'N/A')}
"""
        
        return f"""
# STOCKDROP INVESTMENT MEMO: {state.ticker}
Date: {state.date}
{evidence_section}
{deep_section}
## 1. Risk Council & Decision
**Action:** {state.final_decision.get('action')}
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

    def _create_risk_agent_prompt(self, state: MarketState, drop_str: str) -> str:
        return f"""
You are the **Risk Management Agent**.
Your goal is to be the "Devil's Advocate" and identify ALL potential risks associated with buying {state.ticker} on this dip.
You operate alongside the Bull and Bear agents but do not see their work. Your job is to ensure the Fund Manager is aware of the "Tail Risks" and "Hidden Dangers" in the data.

CONTEXT: The stock has dropped {drop_str}.
COUNCIL 1 REPORTS (Data from News, Technicals, Sentiment, etc.):
{json.dumps(state.reports, indent=2)}

TASK:
1. **Scrutinize the Data**: Look for inconsistencies between the News and the Financials (e.g. "Record Revenue" but "Lower Guidance").
2. **Identify "Trap" Signals**: Is the "Dip" actually a "Falling Knife"? (e.g. Broken technical trend + Fundamental deterioration).
3. **Anticipate the Bull Case**: A Bull might argue "It's oversold". Counter that argument: Why might it stay oversold?
4. **List Top 3 Specific Risks**: (e.g. "Regulatory Investigation pending", "Sector rotation out of Tech", "Margin compression").

OUTPUT:
A dedicated Risk Assessment.
We argue that contexts should function not as concise summaries, but as comprehensive, evolving playbooks—detailed, inclusive, and rich with domain insights.
Use Headers: "Risk Assessment", "Trap Check", "Counter-Thesis", "Key Risks".
{self._news_block_for(state, "risk")}"""


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

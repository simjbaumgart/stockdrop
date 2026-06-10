import google.generativeai as genai # Existing SDK
from google.generativeai.types import RequestOptions
from google import genai as new_genai # New SDK (Enabled)
from google.genai import types as new_types
import os
import logging
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from app.models.market_state import MarketState
from app.services.analyst_service import analyst_service
from app.services.fred_service import fred_service
from app.services import news_shadow_service
import time
import requests
from app.services.deep_research_service import deep_research_service
from app.services.gatekeeper_service import (
    TIER_DEEP_DIP,
    TIER_STANDARD_DIP,
    TIER_SHALLOW_DIP,
    gatekeeper_service,
)
from app.utils.ticker_paths import safe_ticker_path
from app.utils.agent_call_counter import counter as agent_call_counter
from app.utils.earnings_consistency import check_narrative_consistency, downgrade_action
from app.utils.json_repair import repair_json_via_flash

# Citation strip — Gemini grounding injects footnote markers that corrupt JSON
# AND mid-sentence text. Two known shapes:
#   1. [Source N]            — original grounding format
#   2. [N], [N.N], [N.N.N]   — bare-number footnotes (COR/ANET regression, May 2026)
# We also accept [cite N] / [cite:N]. We replace each marker with a single space,
# then collapse runs of whitespace, so word boundaries are preserved. Joined-vs-
# separated cases ('signaling' vs 'signa ling') are indistinguishable from the raw
# text alone; we deliberately favor word-boundary preservation. The CAR-style
# 'Massivestructuralunwind' production failure was the original trigger.
#
# The numeric branch is deliberately narrow: digits + optional dotted sub-sections
# only. This avoids eating real bracketed content like [BUY], [N/A], [YoY 5%],
# [low-high], or ISO dates [2026-05-06].
_CITATION_RE = re.compile(
    r"\[(?:Source\s*\d+|\d+(?:\.\d+)*|cite[:\s]?\s*\d+)\]",
    re.IGNORECASE,
)
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")


def _strip_citations(raw: str) -> str:
    """Remove inline citation markers, replacing each with a single space.

    'word [Source 1] word' → 'word word'   (collapses double space)
    'word [Source 1]word'  → 'word word'   (boundary preserved)
    '[Source 1][Source 2]' → ''            (leading/trailing trimmed)
    'word[Source 1]word'   → 'word word'   (always inserts a space)
    'growth[1.1]across'    → 'growth across' (numeric footnote)
    """
    # Cheap-rejection: skip work for the common case (no '[' at all).
    if "[" not in raw:
        return raw
    cleaned = _CITATION_RE.sub(" ", raw)
    if cleaned == raw:
        return raw
    cleaned = _MULTISPACE_RE.sub(" ", cleaned)
    return cleaned.strip(" ")


def _strip_trailing_commas(s: str) -> str:
    """Remove structural trailing commas (',' immediately before '}' or ']').

    String-aware: a ',]' or ',}' that lives INSIDE a string value is left
    untouched, so financial reason text is never corrupted. This handles the
    single most common LLM JSON defect losslessly, avoiding a Flash repair pass
    that silently drops key_factors list items.
    """
    out = []
    in_str = False
    esc = False
    n = len(s)
    for i, ch in enumerate(s):
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            continue
        if ch == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                continue  # drop the trailing comma
        out.append(ch)
    return "".join(out)


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

# When the wall clock advances much faster than the monotonic clock between
# two budget checks, we assume the machine slept. The budget should not be
# eaten by sleep time (laptop lid closed mid-cycle), so we re-stamp it.
SLEEP_DETECTION_THRESHOLD_SEC = 90


class BudgetClock:
    """Tracks wall-clock deadline with sleep-aware re-stamping.

    The grounded-call retry loop creates one BudgetClock per top-level call
    and ticks it on every (re)entry. If wall-clock time has advanced more
    than monotonic time by >= SLEEP_DETECTION_THRESHOLD_SEC, the deadline is
    pushed forward to (current wall time + AGENT_WALL_CLOCK_BUDGET_SEC).
    """

    __slots__ = ("deadline", "_last_now", "_last_monotonic")

    def __init__(self, now: Optional[float] = None, monotonic: Optional[float] = None):
        now = time.time() if now is None else now
        monotonic = time.monotonic() if monotonic is None else monotonic
        self.deadline = now + AGENT_WALL_CLOCK_BUDGET_SEC
        self._last_now = now
        self._last_monotonic = monotonic

    def tick(self, now: Optional[float] = None, monotonic: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        monotonic = time.monotonic() if monotonic is None else monotonic
        elapsed_wall = now - self._last_now
        elapsed_mono = monotonic - self._last_monotonic
        if (elapsed_wall - elapsed_mono) >= SLEEP_DETECTION_THRESHOLD_SEC:
            logger.warning(
                "[BudgetClock] wake-from-sleep detected (wall+%.1fs vs mono+%.1fs); "
                "resetting deadline.",
                elapsed_wall, elapsed_mono,
            )
            self.deadline = now + AGENT_WALL_CLOCK_BUDGET_SEC
        self._last_now = now
        self._last_monotonic = monotonic

    def expired(self) -> bool:
        return time.time() >= self.deadline


# Phase 1 quality gate: abort if fewer than this many core agents return real reports.
# Four-of-five is deliberate: we tolerate a single flaky sensor (e.g. seeking_alpha
# on an OTC ticker with no coverage) but refuse to produce a decision when the
# majority of sensors are error stubs. The 04-22 BBY outage (5/5 truncated
# outputs producing a HIGH-conviction AVOID) is the canonical motivator.
MIN_REAL_PHASE1_REPORTS = 4

# Phase 1 core agents counted by the quality gate (economics is conditional and excluded).
PHASE1_CORE_AGENTS = ("technical", "news", "market_sentiment", "competitive", "seeking_alpha")

# Source-depth gate: agents are happy to summarize a single headline, so the
# Phase 1 liveness check (MIN_REAL_PHASE1_REPORTS) doesn't catch tickers with
# essentially no specific coverage (FJIKY 2026-05-14: SA 0/0/0, news = 7
# total, 5/5 agents returned text). Abort when BOTH source signals are thin.
MIN_SA_ITEMS_FOR_DECISION = 1   # at least 1 SA article/news/PR specific to ticker
MIN_TICKER_NEWS_FOR_DECISION = 10  # OR at least 10 news items in raw_data

# Report-content markers that signal a failed agent output.
_FAILED_REPORT_MARKERS = (
    "[Error",
    "[SYSTEM ERROR",
    "[SHORT INPUT DETECTED:",
    "Market Sentiment Analysis Failed",
    "[Grounding Error",
)


# Schema for the Fund Manager JSON-repair pass. Mirrors the OUTPUT block of
# _create_fund_manager_prompt — kept in sync with that prompt's JSON spec.
_FM_OUTPUT_SCHEMA = """
{
  "action": "BUY | BUY_LIMIT | WATCH | AVOID",
  "conviction": "HIGH | MODERATE | LOW",
  "drop_type": "EARNINGS_MISS | ANALYST_DOWNGRADE | SECTOR_ROTATION | MACRO_SELLOFF | COMPANY_SPECIFIC | TECHNICAL_BREAKDOWN | UNKNOWN",
  "entry_price_low": 0.0,
  "entry_price_high": 0.0,
  "stop_loss": 0.0,
  "take_profit_1": 0.0,
  "take_profit_2": null,
  "upside_percent": 0.0,
  "downside_risk_percent": 0.0,
  "risk_reward_ratio": 0.0,
  "pre_drop_price": 0.0,
  "entry_trigger": "string",
  "reassess_in_days": 5,
  "sell_price_low": 0.0,
  "sell_price_high": 0.0,
  "ceiling_exit": 0.0,
  "exit_trigger": "string",
  "reason": "string",
  "key_factors": ["list", "of", "strings"]
}
"""


# Price fields covered by the post-repair semantic gate. Nullable fields
# (e.g. take_profit_2) may be None; a PRESENT value must be a positive number.
_FM_PRICE_KEYS = (
    "entry_price_low", "entry_price_high", "stop_loss",
    "take_profit_1", "take_profit_2", "pre_drop_price",
    "sell_price_low", "sell_price_high", "ceiling_exit",
)

# A key_factor must contain at least one real word to count as content.
_FM_FACTOR_WORD_RE = re.compile(r"[A-Za-z]{3}")


def _fm_semantic_check(decision: Dict) -> tuple:
    """Minimal content validation for a Fund Manager decision dict.

    The Flash JSON-repair pass validates structure, not content (NXT
    2026-06-10: repair "succeeded" but saved key_factors ["."], and GNRC/MOD
    got zeroed prices). Returns (ok, reason); callers re-prompt the PM once
    on failure rather than persisting junk.
    """
    for field in ("action", "conviction"):
        val = decision.get(field)
        if not isinstance(val, str) or not val.strip():
            return False, f"missing/empty {field}"

    # Missing/empty key_factors is tolerated: a payload truncated BEFORE the
    # key_factors field (NIO 2026-05-22) repairs to an empty list, and that is
    # honest — the factors never existed in the raw text. What must never pass
    # is degraded content: trivial items like "." (NXT 2026-06-10).
    factors = decision.get("key_factors")
    if factors is not None and not isinstance(factors, list):
        return False, "key_factors is not a list"
    for item in factors or []:
        if (
            not isinstance(item, str)
            or len(item.strip()) < 4
            or not _FM_FACTOR_WORD_RE.search(item)
        ):
            return False, f"key_factors contains trivial item {item!r}"

    for key in _FM_PRICE_KEYS:
        val = decision.get(key)
        if val is None:
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            return False, f"non-numeric price {key}={val!r}"
        if f <= 0:
            return False, f"non-positive price {key}={f}"

    return True, "ok"


def _is_real_report(report: Optional[str]) -> bool:
    """Return True if the report looks like real agent output, not an error stub."""
    if not report or not isinstance(report, str):
        return False
    if len(report) < 200:
        return False
    stripped = report.lstrip()
    return not any(stripped.startswith(marker) for marker in _FAILED_REPORT_MARKERS)


from app.services.seeking_alpha_service import seeking_alpha_service
from app.services.sa_grades_service import sa_grades_service
from app.services.pm_verdict_formatters import format_rr_block, format_ratings_block

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Maps the human-readable agent_name used in _call_agent to the stable
# (stage, tracker_agent_name) pair stored in agent_token_usage.
# These tracker names are IMMUTABLE once shipped — renaming silently
# breaks every historical per-agent trend query.
#
# Inclusion policy:
#   - Every grounded LLM call site whose first attempt belongs to the
#     decision pipeline goes in. That's the 5 sensors + 3 debate + PM.
#   - Phase 1 retry-loop calls reach _call_agent with the SAME agent_name
#     as their first attempt. They naturally re-record under the same row
#     vocabulary — the spec calls this out: only the final successful
#     attempt is recorded (retry-tax invisible).
#   - The Seeking Alpha agent is deterministic (no LLM call) — correctly absent.
TOKEN_TRACKER_AGENT_MAP = {
    "Technical Agent":             ("sensor", "sensor_technical"),
    "News Agent":                  ("sensor", "sensor_news"),
    "Market Sentiment Agent":      ("sensor", "sensor_market_sentiment"),
    "Competitive Landscape Agent": ("sensor", "sensor_competitive"),
    "Economics Agent":             ("sensor", "sensor_economics"),
    "Bull Researcher":             ("debate", "debate_bull"),
    "Bear Researcher":             ("debate", "debate_bear"),
    "Risk Management Agent":       ("debate", "debate_risk"),
    "Fund Manager":                ("pm",     "pm"),
}


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


    def analyze_stock(self, ticker: str, raw_data: Dict, decision_id: Optional[int] = None) -> dict:
        """
        Orchestrates the new 3-Phase Agent Flow:
        1. Agents (Technical + News) -> MarketState.reports
        1. Agents (Technical + News) -> MarketState.reports
        2. Bull & Bear Perspectives (Parallel) -> MarketState.reports['bull'/'bear']
        3. Portfolio Manager (Internet Verification) -> Final Decision

        `decision_id` (optional): FK into decision_points; threaded onto MarketState
        so downstream LLM calls can record per-call token usage.
        """
        if not self._check_and_increment_usage():
            return {"recommendation": "SKIP", "reasoning": "Daily limit reached."}

        print(f"\n[ResearchService] Starting Research Council for {ticker}...")
        
        # Initialize State
        state = MarketState(
            ticker=ticker,
            date=datetime.now().strftime("%Y-%m-%d"),
            gatekeeper_tier=raw_data.get("gatekeeper_tier"),
            earnings_facts=raw_data.get("earnings_facts"),
            volatility_regime=gatekeeper_service.check_market_regime(),
            decision_id=decision_id,
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
            agent_call_counter.record(f"phase1.{name.lower().replace(' agent', '').replace(' ', '_')}")
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

        # --- News Agent shadow comparison (non-blocking, isolated) ---
        news_metrics: Dict[str, Any] = {}
        news_shadow_data = None
        _shadow_executor = None
        _shadow_future = None
        if news_shadow_service.is_shadow_active():
            try:
                _shadow_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                _shadow_future = _shadow_executor.submit(
                    news_shadow_service.run_shadow_call,
                    self._call_grounded_model,
                    news_prompt,
                )
            except Exception as e:
                logger.warning(f"Could not start News Agent shadow call: {e}")
                _shadow_future = None

        # Increase max_workers to prevent starvation when agents hit 503 and retry
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(run_agent, "Technical Agent", self._call_agent, tech_prompt, "Technical Agent", state): "technical",
                executor.submit(run_agent, "News Agent", self._call_agent, news_prompt, "News Agent", state, news_metrics): "news",
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

        # Collect the shadow result. Any failure here is non-fatal — the live
        # News Agent output (news_report) is already final and unaffected.
        if _shadow_future is not None:
            _shadow_result = None
            try:
                _shadow_result = _shadow_future.result(timeout=120)
            except Exception as e:
                logger.warning(f"News Agent shadow call failed (non-fatal): {e}")
            finally:
                if _shadow_executor is not None:
                    # wait=False: on a timeout the shadow worker may keep running
                    # until its Gemini call's own request timeout — intended, so
                    # the live pipeline never blocks on the shadow.
                    _shadow_executor.shutdown(wait=False, cancel_futures=True)
            try:
                news_shadow_data = news_shadow_service.build_shadow_record(
                    ticker=state.ticker,
                    date=state.date,
                    production_report=news_report,
                    production_metrics=news_metrics,
                    shadow_result=_shadow_result,
                )
            except Exception as e:
                logger.warning(f"Could not build News shadow record: {e}")
                news_shadow_data = None

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

        # --- Phase 1 Source-Depth Gate ---
        # Catches thin-coverage tickers where all 5 agents passed the liveness
        # check by summarizing a Wall Street Breakfast headline (FJIKY 2026-05-14:
        # SA 0/0/0, total news = 7, yet 5/5 agents returned text).
        depth_aborted, depth_reason = self._source_depth_insufficient(raw_data)
        if depth_aborted:
            msg = f"[ABORT] Phase 1 source-depth gate failed for {state.ticker}: {depth_reason}"
            print(f"\n{'=' * 50}\n  {msg}\n{'=' * 50}\n")
            logger.error(msg)
            response = self._build_insufficient_data_response(
                state, failed_agents=["source_depth"], real_count=real_count
            )
            response["aborted_reason"] = "insufficient_source_depth"
            response["executive_summary"] = depth_reason
            return response

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

        if final_decision.get("aborted_reason") == "fund_manager_failed":
            logger.error(
                "[FM Abort] %s: Fund Manager failed; skipping Deep Research + "
                "downstream formatting and returning PASS_INSUFFICIENT_DATA.",
                state.ticker,
            )
            print(
                f"\n{'=' * 50}\n  [ABORT] Fund Manager failed for {state.ticker}; "
                f"recording PASS_INSUFFICIENT_DATA instead of AVOID/LOW.\n{'=' * 50}\n"
            )
            response = self._build_insufficient_data_response(
                state,
                failed_agents=["fund_manager"],
                real_count=len(PHASE1_CORE_AGENTS),
            )
            response["aborted_reason"] = "fund_manager_failed"
            return response

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
        
        # Extract checklist metadata
        economics_run = "NEEDS_ECONOMICS: TRUE" in news_report and economics_report != "" and "failed to fetch" not in economics_report
        drop_reason_identified = "REASON_FOR_DROP_IDENTIFIED: YES" in news_report

        
        # --- Calculate Data Depth Metrics (Evidence Barometer) ---
        from app.services.evidence_service import evidence_service
        data_depth = evidence_service.collect_barometer(raw_data, state.reports)

        # Deterministic stop-loss guardrail: widen if PM placed it too tight,
        # then recompute R/R so the print line + DB row + dashboard reflect
        # the new (wider) stop instead of the PM's stale numbers.
        from app.utils.stop_loss_guard import (
            widen_stop_if_too_tight,
            recompute_risk_metrics,
            evaluate_stop_acceptability,
        )
        _tv_inds = raw_data.get("indicators", {})
        _entry_low = final_decision.get("entry_price_low")
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

            # Recompute downside / R/R against (possibly-new) stop_loss so the
            # value the user sees matches the value the user takes risk on.
            _metrics = recompute_risk_metrics(
                entry_low=float(_entry_low),
                stop_loss=final_decision.get("stop_loss"),
                upside_percent=final_decision.get("upside_percent"),
            )
            if _metrics["downside_risk_percent"] is not None:
                final_decision["downside_risk_percent"] = _metrics["downside_risk_percent"]
            if _metrics["risk_reward_ratio"] is not None:
                final_decision["risk_reward_ratio"] = _metrics["risk_reward_ratio"]

            acceptability = evaluate_stop_acceptability(
                entry_low=float(_entry_low),
                stop_loss=final_decision.get("stop_loss"),
                risk_reward_ratio=final_decision.get("risk_reward_ratio"),
            )
            if not acceptability.acceptable and final_decision.get("action", "").upper().startswith("BUY"):
                logger.warning(
                    "[Stop-acceptability] %s: %s — overriding %s to AVOID/NONE.",
                    state.ticker, acceptability.reason, final_decision.get("action"),
                )
                print(
                    f"  > [Stop-acceptability] {state.ticker}: {acceptability.reason}. "
                    f"Overriding to AVOID/NONE (no tradable R/R panel)."
                )
                final_decision["action"] = "AVOID"
                final_decision["conviction"] = "NONE"
                final_decision["rejected_reason"] = "stop_too_wide"
                existing_reason = final_decision.get("reason") or ""
                final_decision["reason"] = (
                    f"[STOP-REJECTED] {acceptability.reason}. {existing_reason}"
                ).strip()

        # Deterministic earnings-narrative consistency check (see TOST 2026-05 incident).
        final_decision = self._apply_earnings_consistency(
            final_decision,
            ticker=state.ticker,
            earnings_facts=raw_data.get("earnings_facts"),
        )

        # Construct Final Output compatible with existing app expectations
        recommendation = final_decision.get("action", "AVOID").upper()

        # External ratings — looked up AFTER final_decision is finalized.
        # Never passed into any agent prompt; lives in dedicated dict keys + DB columns.
        external_ratings = sa_grades_service.lookup(state.ticker)

        # --- Print Final Decision to Console (post-guard, post-recompute) ---
        _company_name = raw_data.get("company_name") or state.ticker
        print("\n" + "="*50)
        print(f"  Stock: {_company_name} ({state.ticker})")
        print(f"  [PORTFOLIO MANAGER DECISION]: {final_decision.get('action')} (Conviction: {final_decision.get('conviction', 'N/A')})")
        print(f"  Drop Type: {final_decision.get('drop_type', 'N/A')}")
        print(f"  Entry Zone: ${final_decision.get('entry_price_low', 'N/A')} - ${final_decision.get('entry_price_high', 'N/A')}")
        print(f"  Stop Loss: ${final_decision.get('stop_loss', 'N/A')} | TP1: ${final_decision.get('take_profit_1', 'N/A')} | TP2: ${final_decision.get('take_profit_2', 'N/A')}")
        print()
        for line in format_rr_block(
            upside=final_decision.get("upside_percent"),
            downside=final_decision.get("downside_risk_percent"),
            rr=final_decision.get("risk_reward_ratio"),
        ).splitlines():
            print(f"  {line}")
        print()
        print(f"  Sell Zone: ${final_decision.get('sell_price_low', 'N/A')} - ${final_decision.get('sell_price_high', 'N/A')} | Ceiling: ${final_decision.get('ceiling_exit', 'N/A')}")
        print(f"  Entry Trigger: {final_decision.get('entry_trigger', 'N/A')}")
        print(f"  Exit Trigger: {final_decision.get('exit_trigger', 'N/A')}")
        print(f"  Reassess In: {final_decision.get('reassess_in_days', 'N/A')} trading days")
        print(f"  Reason: {final_decision.get('reason')}")
        print("  Key Factors:")
        for factor in final_decision.get('key_factors', []):
            print(f"   - {factor}")
        print()
        for line in format_ratings_block(external_ratings).splitlines():
            print(f"  {line}")
        print()
        print(f"  Total Agent Calls: {state.agent_calls}")
        print("="*50 + "\n")

        # Rollup token totals onto decision_points so per-run queries don't
        # need a GROUP BY. Safe to call multiple times — idempotent.
        if state.decision_id is not None:
            try:
                from app.services.token_tracker import rollup_decision_totals
                rollup_decision_totals(state.decision_id)
            except Exception as e:
                logger.warning("rollup_decision_totals failed in analyze_stock: %s", e)

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
            "stop_loss_guard_reason": final_decision.get("stop_loss_guard_reason"),
            # Sell range fields (v1.0)
            "sell_price_low": final_decision.get("sell_price_low"),
            "sell_price_high": final_decision.get("sell_price_high"),
            "ceiling_exit": final_decision.get("ceiling_exit"),
            "exit_trigger": final_decision.get("exit_trigger"),
            "key_factors": final_decision.get("key_factors", []),
            "earnings_narrative_flag": final_decision.get("earnings_narrative_flag"),
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
            "news_shadow_data": news_shadow_data,
            "key_decision_points": final_decision.get("key_factors", []),  # Mapped for backward compat
            "market_sentiment_report": state.reports.get('market_sentiment', ''),
            "competitive_report": state.reports.get('competitive', ''),
            "seeking_alpha_report": state.reports.get('seeking_alpha', ''),
            "data_depth": data_depth,
            # External ratings (informational; never shown to agents).
            # Sourced from data/SAgrades/SA_Quant_Ranked_Clean.csv after final_decision.
            "sa_quant_rating": external_ratings.get("sa_quant_rating"),
            "sa_authors_rating": external_ratings.get("sa_authors_rating"),
            "wall_street_rating": external_ratings.get("wall_street_rating"),
            "sa_rank": external_ratings.get("sa_rank"),
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
            agent_call_counter.record(f"phase2.{name.lower().replace(' researcher', '').replace(' agent', '').replace(' ', '_')}")
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

    _CONVICTION_LADDER = ["HIGH", "MEDIUM", "LOW", "NONE"]
    # The PM/DR prompts emit "MODERATE"; the ladder uses "MEDIUM". Normalize
    # before indexing so the downgrade doesn't silently no-op (TTWO 2026-05-22).
    _CONVICTION_ALIASES = {"MODERATE": "MEDIUM", "MED": "MEDIUM"}

    def _apply_earnings_consistency(self, final_decision: Dict, ticker: str, earnings_facts: Optional[Dict]) -> Dict:
        """Apply the earnings-narrative consistency check.

        Behavior:
        - If narrative is consistent: no-op.
        - If inconsistent:
            * Downgrade action if the ladder maps it (BUY -> BUY_LIMIT -> WATCH).
            * Always set `earnings_narrative_flag`.
            * Always prepend `[FLAGGED]` to the reason field.
            * Always drop conviction one tier (HIGH -> MEDIUM -> LOW -> NONE).
        """
        ef = earnings_facts or {}
        consistency = check_narrative_consistency(
            reasoning=final_decision.get("reason", ""),
            surprise_pct=ef.get("surprise_pct"),
        )
        if not consistency.inconsistent:
            return final_decision

        if final_decision.get("earnings_narrative_flag"):
            return final_decision  # already applied; idempotent

        original_action = final_decision.get("action", "")
        new_action = downgrade_action(original_action)

        # Drop conviction one notch (HIGH -> MEDIUM -> LOW -> NONE).
        old_conviction = (final_decision.get("conviction") or "LOW").upper()
        ladder_conviction = self._CONVICTION_ALIASES.get(old_conviction, old_conviction)
        try:
            idx = self._CONVICTION_LADDER.index(ladder_conviction)
            new_conviction = self._CONVICTION_LADDER[min(idx + 1, len(self._CONVICTION_LADDER) - 1)]
        except ValueError:
            logger.warning(
                "[Earnings Consistency] %s: unknown conviction %r — leaving unchanged.",
                ticker, old_conviction,
            )
            new_conviction = old_conviction

        logger.warning(
            "[Earnings Consistency] %s: %s. Action %s -> %s; Conviction %s -> %s",
            ticker, consistency.reason, original_action, new_action, old_conviction, new_conviction,
        )
        print(
            f"  > [Earnings Consistency Flag] {ticker}: {consistency.reason}. "
            f"{original_action} -> {new_action}, conviction {old_conviction} -> {new_conviction}"
        )

        final_decision["action"] = new_action
        final_decision["conviction"] = new_conviction
        final_decision["earnings_narrative_flag"] = consistency.flag

        existing_reason = final_decision.get("reason") or ""
        if not existing_reason.startswith("[FLAGGED]"):
            final_decision["reason"] = (
                f"[FLAGGED] {consistency.flag}: {consistency.reason}. {existing_reason}"
            ).strip()

        existing_factors = final_decision.get("key_factors") or []
        if isinstance(existing_factors, list):
            existing_factors.append(
                f"[FLAG] {consistency.flag}: {consistency.reason}. "
                f"Verdict {original_action} -> {new_action}, conviction {old_conviction} -> {new_conviction}."
            )
            final_decision["key_factors"] = existing_factors

        return final_decision

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
        agent_call_counter.record("pm")
        decision_json_str = self._call_agent(manager_prompt, "Fund Manager", state)

        # FM transport/timeout failures return error stubs starting with one of
        # the documented markers. Don't fall back to AVOID/LOW — that produces
        # a plausible-looking decision out of a network error (LSTR 2026-05-14).
        fm_failed = (
            not decision_json_str
            or any(
                decision_json_str.lstrip().startswith(m)
                for m in _FAILED_REPORT_MARKERS
            )
        )

        decision = None if fm_failed else self._extract_json(decision_json_str)

        # FM produced real output but it didn't parse — commonly truncated
        # mid-JSON (NIO 2026-05-22: clean through risk_reward_ratio, cut mid
        # sell_price_low). Attempt a Gemini Flash repair pass before giving
        # up, mirroring the Deep Research repair path. Never attempt this for
        # error stubs (fm_failed) — a transport failure has nothing to repair.
        if decision is None and not fm_failed and decision_json_str:
            logger.warning(
                "[Fund Manager] %s: JSON parse failed — attempting Gemini Flash repair.",
                state.ticker,
            )
            repaired = repair_json_via_flash(
                decision_json_str,
                _FM_OUTPUT_SCHEMA,
                self.api_key,
                log_prefix="[Fund Manager]",
                label="fund_manager",
            )
            if repaired and repaired.get("action") and repaired.get("conviction"):
                sem_ok, sem_reason = _fm_semantic_check(repaired)
                if sem_ok:
                    logger.info(
                        "[Fund Manager] %s: successfully repaired JSON output.",
                        state.ticker,
                    )
                    decision = repaired
                else:
                    # Repair validated structure but degraded content (NXT
                    # 2026-06-10: key_factors ["."]). Re-prompt the PM once
                    # rather than persisting junk.
                    logger.warning(
                        "[Fund Manager] %s: repaired JSON failed semantic check "
                        "(%s) — re-prompting PM once.",
                        state.ticker, sem_reason,
                    )
                    agent_call_counter.record("pm")
                    retry_str = self._call_agent(manager_prompt, "Fund Manager", state)
                    retry_failed = (
                        not retry_str
                        or any(
                            retry_str.lstrip().startswith(m)
                            for m in _FAILED_REPORT_MARKERS
                        )
                    )
                    retry_decision = None if retry_failed else self._extract_json(retry_str)
                    if retry_decision and _fm_semantic_check(retry_decision)[0]:
                        logger.info(
                            "[Fund Manager] %s: re-prompt produced a clean decision.",
                            state.ticker,
                        )
                        decision = retry_decision
                    else:
                        logger.warning(
                            "[Fund Manager] %s: re-prompt did not yield a usable "
                            "decision either.",
                            state.ticker,
                        )
            else:
                logger.warning(
                    "[Fund Manager] %s: repair did not yield a usable decision.",
                    state.ticker,
                )

        if not decision:
            logger.error(
                "[Fund Manager] %s: failed to produce a usable decision (%s). "
                "Marking PASS_INSUFFICIENT_DATA instead of defaulting to AVOID/LOW.",
                state.ticker,
                "error stub" if fm_failed else "JSON parse failure",
            )
            return {
                "action": "PASS_INSUFFICIENT_DATA",
                "conviction": "NONE",
                "reason": (
                    "Fund Manager call failed or returned unparseable JSON — "
                    "no decision rendered."
                ),
                "drop_type": "UNKNOWN",
                "aborted_reason": "fund_manager_failed",
                "key_factors": [],
            }

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
This section may ONLY be filled in from the transcript_text input field
provided in the input data. If transcript_text is empty, missing, or
shorter than ~500 characters, you MUST output exactly:
    "No Transcript Available."
and nothing else for this section.

You MUST NOT synthesize this section from news headlines, analyst notes,
press releases, or any other source. The section header is reserved for
verbatim earnings-call content. Fabricating a summary from non-transcript
sources is a hallucination and will be flagged.

If transcript_text IS substantive, provide bullet points covering:
- Guidance & Outlook (most important)
- Management Tone (Confident vs Defensive)
- Key Operational Updates or Strategic Shifts

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

        tier = getattr(state, "gatekeeper_tier", None)
        tier_line = {
            TIER_DEEP_DIP: "DEEP_DIP — %B < 0.30, statistically oversold. Default toward action if fundamentals support.",
            TIER_STANDARD_DIP: "STANDARD_DIP — %B in [0.30, 0.50). Standard dip-buying setup; weigh fundamentals normally.",
            TIER_SHALLOW_DIP: (
                "SHALLOW_DIP — %B in [0.50, 0.70). Stock is still extended above its 20-day midline; "
                "admitted only because today's drop was large. Apply tighter scrutiny: require a clear recovery "
                "catalyst and tighter stop-loss. Default toward WATCH or AVOID unless the bull case is strong."
            ),
        }.get(tier, "UNKNOWN — gatekeeper tier missing; treat as STANDARD_DIP.")

        vol = getattr(state, "volatility_regime", None) or {}
        if vol.get("regime_score") is not None:
            vol_block = (
                "\nVOLATILITY REGIME (numeric ground truth — dip-buys "
                "mean-revert better when volatility is elevated and the term "
                "structure is in backwardation):\n"
                f"- VIX: {vol.get('vix')} ({vol.get('vix_class')}), "
                f"20-day percentile {vol.get('vix_pctile_20d')}%\n"
                f"- Term structure: {vol.get('term_structure')} "
                f"(VIX - VIX3M spread {vol.get('term_spread')})\n"
                f"- CNN Fear & Greed: {vol.get('fear_greed')} "
                f"({vol.get('fear_greed_rating')})\n"
                f"- Regime: {vol.get('regime_label')} "
                f"(score {vol.get('regime_score')} of 1.0) — higher favors "
                "dip-buying. Weigh this against the bull/bear cases; a "
                "FAVORABLE regime is a tailwind, UNFAVORABLE a headwind.\n"
            )
        else:
            vol_block = ""

        ef = getattr(state, "earnings_facts", None) or {}
        if ef and ef.get("reported_eps") is not None:
            _sp = ef.get("surprise_pct")
            if _sp is None:
                _surprise_line = "- Surprise: N/A (estimate was 0 or missing)\n"
            else:
                _beat_miss = "BEAT" if _sp > 0 else "MISS" if _sp < 0 else "INLINE"
                _surprise_line = f"- Surprise: {_sp:+.1f}% ({_beat_miss})\n"
            earnings_block = (
                "\nEARNINGS_FACTS (canonical, from Finnhub — DO NOT infer EPS from news articles below):\n"
                f"- Reported EPS: ${ef['reported_eps']:.2f}\n"
                f"- Consensus EPS: ${ef['consensus_eps']:.2f}\n"
                + _surprise_line +
                f"- Fiscal quarter: {ef.get('fiscal_quarter')}\n"
                f"- Source: {ef.get('source')} (fetched {ef.get('fetched_at')})\n"
                "Whenever your reasoning describes whether the company beat or missed, "
                "use the surprise sign above. News articles may cite stale consensus numbers; "
                "the values above are the ground truth."
            )
        else:
            earnings_block = "\nEARNINGS_FACTS: (no recent reported quarter available — drop is not earnings-driven, or facts unavailable)"

        return f"""
You are the **Portfolio Manager**. You have the final vote.
You must weigh the arguments from the Bull Agent and the Bear Agent, cross-reference with the original Agent Reports, and produce a concrete, actionable trading plan.

DECISION CONTEXT:
- Stock: {state.ticker}
- Drop: {drop_str} today
- This is a "Buy the Dip" evaluation. We are looking for oversold large-cap stocks with recovery potential.
- The investor holds positions until recovery (weeks to months), not day-trading.
- Gatekeeper Tier: {tier_line}
{vol_block}
RISK FACTORS (For Consideration):
- Technical Flags: {safe_concerns}
- News Flags: {risky_support}
- **RISK AGENT ASSESSMENT**:
{risk_report}
{earnings_block}

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
- **stop_loss**: REQUIRED. Place at the *farther* (lower) of:
    (a) entry_price_low - 2.0 * ATR  (use TradingView ATR provided above)
    (b) nearest technical support below entry_price_low (prior swing low,
        SMA_50, or SMA_200 — whichever is below entry).
  Never place the stop closer than 1.5 * ATR below entry_price_low. Stops
  tighter than this floor will be programmatically widened.
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

    def _source_depth_insufficient(self, raw_data: Dict) -> Tuple[bool, str]:
        """Return (True, reason) when both SA coverage AND news count are
        below their floors. Either signal alone is enough to proceed."""
        sa_counts = raw_data.get("seeking_alpha_local_counts") or {}
        sa_items = (
            int(sa_counts.get("analysis", 0) or 0)
            + int(sa_counts.get("news", 0) or 0)
            + int(sa_counts.get("press_releases", 0) or 0)
        )
        news_count = len(raw_data.get("news_items") or [])

        if sa_items >= MIN_SA_ITEMS_FOR_DECISION:
            return False, ""
        if news_count >= MIN_TICKER_NEWS_FOR_DECISION:
            return False, ""

        return True, (
            f"source-depth insufficient: SA items={sa_items} "
            f"(min {MIN_SA_ITEMS_FOR_DECISION}), news_items={news_count} "
            f"(min {MIN_TICKER_NEWS_FOR_DECISION})"
        )

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
            # External ratings (informational; never shown to agents) — None on the abort path.
            "sa_quant_rating": None,
            "sa_authors_rating": None,
            "wall_street_rating": None,
            "sa_rank": None,
            # Sentinel field so dashboards/backfill can filter these out
            "aborted_reason": "insufficient_phase1_data",
            "failed_phase1_agents": failed_agents,
        }

    def _call_agent(self, prompt: str, agent_name: str, state: Optional[MarketState] = None, metrics_sink: Optional[Dict[str, Any]] = None) -> str:
        if not self.model:
            return "Mock Output"
        try:
            # logger.info(f"Calling Agent: {agent_name}")
            if state:
                with self.lock:
                    state.agent_calls += 1

            # Build tracker context only if we have everything we need.
            # If decision_id is missing (e.g. direct unit-test invocation),
            # skip tracking silently — the live pipeline always provides it.
            tracker_context = None
            mapping = TOKEN_TRACKER_AGENT_MAP.get(agent_name)
            if state and state.decision_id is not None and mapping is not None:
                stage, tracker_name = mapping
                tracker_context = {
                    "decision_id": state.decision_id,
                    "ticker": state.ticker,
                    "run_date": state.date,
                    "stage": stage,
                    "agent_name": tracker_name,
                }

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
                 elif agent_name == "News Agent":
                     # Production News Agent runs on the upgraded Gemini 3.5 Flash model.
                     model_to_use = news_shadow_service.PRODUCTION_NEWS_MODEL

                 logger.info(f"Calling {agent_name} with {model_to_use} + Grounding...")
                 _t0 = time.monotonic()
                 _result = self._call_grounded_model(
                     prompt, model_name=model_to_use, agent_context=agent_name,
                     metrics_sink=metrics_sink,
                     tracker_context=tracker_context,
                 )
                 if metrics_sink is not None:
                     metrics_sink["latency_ms"] = int((time.monotonic() - _t0) * 1000)
                 return _result

            # Default (Bull, Bear, Manager) -> Main Model (Gemini 3 Pro) without grounding
            # Using standard generate_content (old SDK)
            # Rate limit buffer
            time.sleep(2)
            
            response = self.model.generate_content(prompt, request_options=RequestOptions(timeout=600))

            # Record token usage if we have the context to attribute it.
            if tracker_context is not None:
                try:
                    um = getattr(response, "usage_metadata", None)
                    tokens_in  = (getattr(um, "prompt_token_count", 0) or 0) if um else 0
                    tokens_out = (getattr(um, "candidates_token_count", 0) or 0) if um else 0
                    model_used = getattr(self.model, "model_name", "unknown")
                    # Old-SDK model_name comes back as 'models/<name>' — strip prefix.
                    if model_used.startswith("models/"):
                        model_used = model_used[len("models/"):]
                    from app.services.token_tracker import record_llm_call
                    record_llm_call(
                        decision_id=tracker_context["decision_id"],
                        ticker=tracker_context["ticker"],
                        run_date=tracker_context["run_date"],
                        stage=tracker_context["stage"],
                        agent_name=tracker_context["agent_name"],
                        model=model_used,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                    )
                except Exception as e:
                    logger.warning("token tracker invocation failed (fallback path): %s", e)

            return response.text
        except Exception as e:
            # Check for 503 Unavailable inside generic exception (for GenAI v1 SDK)
            if "503" in str(e) and getattr(self, "model", None) and self.model.model_name == 'models/gemini-3.1-pro-preview':
                logger.warning(f"503 UNAVAILABLE for gemini-3.1-pro-preview. Falling back to gemini-3-pro-preview for {agent_name}...")
                try:
                    fallback_model = genai.GenerativeModel('gemini-3-pro-preview')
                    fallback_response = fallback_model.generate_content(prompt, request_options=RequestOptions(timeout=600))

                    if tracker_context is not None:
                        try:
                            um = getattr(fallback_response, "usage_metadata", None)
                            tokens_in  = (getattr(um, "prompt_token_count", 0) or 0) if um else 0
                            tokens_out = (getattr(um, "candidates_token_count", 0) or 0) if um else 0
                            from app.services.token_tracker import record_llm_call
                            record_llm_call(
                                decision_id=tracker_context["decision_id"],
                                ticker=tracker_context["ticker"],
                                run_date=tracker_context["run_date"],
                                stage=tracker_context["stage"],
                                agent_name=tracker_context["agent_name"],
                                model="gemini-3-pro-preview",
                                tokens_in=tokens_in,
                                tokens_out=tokens_out,
                            )
                        except Exception as e:
                            logger.warning("token tracker invocation failed (503-fallback path): %s", e)

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
        budget_clock: Optional["BudgetClock"] = None,
        metrics_sink: Optional[Dict[str, Any]] = None,
        tracker_context: Optional[Dict[str, Any]] = None,
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
        - The first call creates a BudgetClock stamped at time.time() +
          AGENT_WALL_CLOCK_BUDGET_SEC. Every recursive retry inherits that
          BudgetClock and calls tick() on entry so sleep-from-sleep gaps can be
          detected and the deadline re-stamped. If the clock has expired we abort
          immediately with an error stub, regardless of remaining retry_count.
          This prevents the QXO/PB 04-22 multi-hour stall where a transient 503 +
          exponential backoff looped for ~17 hours.
        """
        if budget_clock is None:
            budget_clock = BudgetClock()
        else:
            budget_clock.tick()

        if budget_clock.expired():
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

            if metrics_sink is not None:
                # Token counts reflect the final successful attempt only; on a retry
                # or 503 fallback earlier attempts' tokens are not summed.
                try:
                    um = getattr(response, "usage_metadata", None)
                    metrics_sink["model"] = model_name
                    metrics_sink["tokens_in"] = getattr(um, "prompt_token_count", 0) or 0
                    metrics_sink["tokens_out"] = getattr(um, "candidates_token_count", 0) or 0
                except Exception:
                    metrics_sink.setdefault("model", model_name)

            # Check for FunctionCall (finish_reason 10) which indicates failure to auto-ground
            candidate = response.candidates[0] if response.candidates else None
            finish_reason = candidate.finish_reason if candidate else None

            # 10 is STOP_REASON_FUNCTION_CALL
            if finish_reason == 10 or finish_reason == "STOP_REASON_FUNCTION_CALL":

                if retry_count < MAX_GROUNDING_RETRIES:
                    logger.warning(f"Model {model_name} returned FunctionCall ({attempt_label}) in {agent_context}. Retrying...")
                    return self._call_grounded_model(prompt, model_name, agent_context, retry_count=retry_count + 1, budget_clock=budget_clock, metrics_sink=metrics_sink, tracker_context=tracker_context)

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

            # --- token tracking: persist to agent_token_usage on every successful call ---
            # Placed AFTER the FunctionCall early-return retry so failed-grounding
            # attempts don't get recorded; only genuinely successful (non-FunctionCall)
            # responses reach this point.
            if tracker_context is not None:
                try:
                    um = getattr(response, "usage_metadata", None)
                    tokens_in  = (getattr(um, "prompt_token_count", 0) or 0) if um else 0
                    tokens_out = (getattr(um, "candidates_token_count", 0) or 0) if um else 0
                    from app.services.token_tracker import record_llm_call
                    record_llm_call(
                        decision_id=tracker_context["decision_id"],
                        ticker=tracker_context["ticker"],
                        run_date=tracker_context["run_date"],
                        stage=tracker_context["stage"],
                        agent_name=tracker_context["agent_name"],
                        model=model_name,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                    )
                except Exception as e:
                    logger.warning("token tracker invocation failed: %s", e)

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
                if time.time() + 2 >= budget_clock.deadline:
                    return (
                        f"[Error: {agent_context} exceeded {AGENT_WALL_CLOCK_BUDGET_SEC}s wall-clock "
                        f"budget before 503 fallback]"
                    )
                time.sleep(2)
                try:
                    return self._call_grounded_model(prompt, "gemini-3-pro-preview", agent_context, retry_count=0, budget_clock=budget_clock, metrics_sink=metrics_sink, tracker_context=tracker_context)
                except Exception as fallback_e:
                    logger.error(f"Fallback model also failed for {agent_context} ({type(fallback_e).__name__}): {fallback_e}")
                    return f"[Error in {agent_context} (Fallback): {type(fallback_e).__name__}: {fallback_e}]"

            retryable = _is_retryable_grounding_error(e)
            logger.error(f"Grounding call failed for {agent_context} (model={model_name}, type={err_type}, retryable={retryable}, {attempt_label}): {err_msg}")

            if retryable and retry_count < MAX_GROUNDING_RETRIES:
                wait = 2 ** (retry_count + 1)  # 2s, 4s
                # If the upcoming sleep would blow the wall-clock budget, bail now
                # rather than sleeping pointlessly before the recursive call aborts.
                if time.time() + wait >= budget_clock.deadline:
                    logger.warning(
                        f"[{agent_context}] Skipping {wait}s backoff; wall-clock budget would expire."
                    )
                    return (
                        f"[Error: {agent_context} exceeded {AGENT_WALL_CLOCK_BUDGET_SEC}s wall-clock "
                        f"budget after {retry_count} retries]"
                    )
                logger.info(f"Retrying {agent_context} ({model_name}) in {wait}s ({err_type})...")
                time.sleep(wait)
                return self._call_grounded_model(prompt, model_name, agent_context, retry_count=retry_count + 1, budget_clock=budget_clock, metrics_sink=metrics_sink, tracker_context=tracker_context)

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

        vol = getattr(state, "volatility_regime", None) or {}
        vol_block = ""
        if vol.get("regime_score") is not None:
            vol_block = (
                "\n        VOLATILITY REGIME (numeric ground truth — do NOT "
                "contradict this with news prose; cite these exact numbers):\n"
                f"        - VIX: {vol.get('vix')} ({vol.get('vix_class')}), "
                f"20-day percentile {vol.get('vix_pctile_20d')}%\n"
                f"        - VIX term structure: {vol.get('term_structure')} "
                f"(VIX - VIX3M spread {vol.get('term_spread')})\n"
                f"        - CNN Fear & Greed: {vol.get('fear_greed')} "
                f"({vol.get('fear_greed_rating')})\n"
                f"        - Regime: {vol.get('regime_label')} "
                f"(score {vol.get('regime_score')} of 1.0)\n"
                f"        {vol.get('summary')}\n"
            )

        return f"""
        You are the **Market Sentiment Agent**.
        Your goal is to analyze the general market sentiment and specifically the markets relevant to {ticker}.

        IMPORTANT: Before starting your analysis, verify the correct company name and sector for ticker {ticker} via Google Search. Do NOT guess — foreign tickers (e.g., OTC, ADRs) are easily confused with similarly-named companies. Base your entire analysis on the verified company.

        CONTEXT:
        - Date: {state.date}
        - Focus: TODAY and YESTERDAY only.
        {vol_block}{market_news_str}

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
            
            # Strip [Source N] markers from the inline body before appending
            # the clean Sources appendix. Markers in the prose corrupt downstream
            # consumers (DB, dashboard, PM prompt); the Sources list below is the
            # canonical reference.
            text = _strip_citations(text)

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

            if start == -1 or end == -1:
                return None
            json_str = text[start:end + 1]
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # Tolerant retry: strip structural trailing commas (string-aware,
                # value-preserving). Fixes the most common LLM defect without the
                # lossy Flash repair pass. If it still fails, fall through to the
                # full-payload dump + None so the caller can attempt Flash repair.
                return json.loads(_strip_trailing_commas(json_str))
        except Exception as e:
            logger.error(f"Failed to extract JSON: {e}")
            logger.error(f"Raw text (first 500 chars): {text[:500]}")
            # Persist the FULL unparseable payload so the recurring sell-price/
            # ceiling-block malformation can be root-caused (the 500-char log
            # tail rarely reaches the failing field).
            self._dump_unparseable_payload(text)
            return None

    def _dump_unparseable_payload(self, text: str) -> None:
        """Best-effort: write an unparseable agent payload to logs/ for diagnosis."""
        try:
            os.makedirs("logs", exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = os.path.join("logs", f"unparseable_json_{ts}.txt")
            with open(path, "w") as f:
                f.write(text)
            logger.error("Full unparseable payload written to %s", path)
        except Exception as dump_err:
            logger.warning("Could not dump unparseable payload: %s", dump_err)

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

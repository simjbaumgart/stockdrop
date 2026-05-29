"""Claude-native deep-research prompts.

Provides build_individual_prompt() and build_sell_prompt() — purpose-built for
Claude Opus (with web_search + web_fetch tools) rather than the Gemini Deep
Research API.  These replace the regex _deglooglify() shim that was reusing
Gemini's prompt verbatim.

Context dict keys (all optional except those noted):
  Individual:
    pm_decision         (dict)   — PM/fund-manager output
    bull_case           (str)    — Council bull researcher narrative
    bear_case           (str)    — Council bear researcher narrative
    technical_data      (dict)   — Raw technical indicators
    drop_percent        (float)  — Today's drop magnitude (positive = fell)
    raw_news            (list)   — Dicts with headline/content/source/source_type/datetime_str
    transcript_summary  (str)    — Condensed earnings-call transcript
    transcript_date     (str)
    data_depth          (dict)
    sensor_summaries    (dict)   — [OPTIONAL / Step 2b] agent_name → summary str
    disagreement_points (list)   — [OPTIONAL / Step 2b] list of str

  Sell:
    original_decision           (dict)
    current_price               (float)
    performance_since_entry     (str)
    technical_data              (dict)
    sensor_reports              (dict)
    raw_news                    (list)
"""
import json
from typing import Optional

# ── Individual (buy-the-dip review) ──────────────────────────────────────────

_INDIVIDUAL_SEARCH_DIRECTIVE = """\
═══════════════════════════════════════════════════════
TOOLS AVAILABLE TO YOU:
═══════════════════════════════════════════════════════
• web_search  — run keyword/news searches; use it liberally and repeatedly.
• web_fetch   — fetch a specific URL (including company IR pages, SEC EDGAR
                filing pages, and some paywalled sources).

BEGIN SEARCHING IMMEDIATELY. Do not stop after a single search round.
Follow leads: if one result points to a filing, fetch it; if a headline
mentions a CFO departure, search for confirmation; if the council's
claimed drop-cause differs from what you find, dig deeper.

PRIMARY SOURCES TO PULL (where relevant to this stock):
1. SEC EDGAR filings — 10-K (annual), 10-Q (quarterly), 8-K (material events)
   at https://www.sec.gov/cgi-bin/browse-edgar or direct EDGAR search.
2. The most recent earnings-call transcript — search "[SYMBOL] earnings call
   transcript Q[N] [YEAR]" and fetch the Seeking Alpha / The Motley Fool page.
3. Company IR page — press releases, investor presentations, guidance updates.
4. Form 4 filings — recent insider buys/sells (SEC EDGAR or OpenInsider.com).
5. 13F filings — recent institutional ownership changes.
6. Short interest data — search "[SYMBOL] short interest" for S3 or Fintel data.
7. Any analyst rating changes published today or in the past week.

The council news below is SUPPLEMENTARY EVIDENCE — provided articles may come
from premium/paywalled feeds and are not available to re-fetch via web_fetch.
They tell you what the council already saw; your job is to verify, dispute, and
extend that picture with fresh primary sources.
"""

_INDIVIDUAL_TASK_STEPS = """\
═══════════════════════════════════════════════════════
YOUR TASK (SENIOR INVESTMENT REVIEWER):
═══════════════════════════════════════════════════════

STEP 1: ESTABLISH THE TRUE DROP CAUSE
Independently determine what actually drove today's decline — do not just accept
the council's framing.  Build a SHORT DATED EVENT TIMELINE:
  • What event(s) occurred, on what date, and at what time?
  • How did the market react immediately vs. in subsequent hours?
  • Does the council's stated drop-cause match your primary-source findings?
Reconcile any discrepancy before proceeding.  Check as many factual claims as
you can find evidence for — do not limit yourself to three.

STEP 2: RESOLVE BULL-vs-BEAR DISAGREEMENTS (PRIORITY RESEARCH TARGET)
{disagreement_section}

STEP 3: CHALLENGE THE THESIS
Play devil's advocate against the council's recommendation:
  • Worst-case scenario they did not model?
  • Liquidity risk, delisting risk, regulatory action pending?
  • Is the council's "priced in" assessment correct?  (Elm Partners reminder
    below — even traders with tomorrow's news often lose by misreading what's
    already priced in.  If the bad news is obvious and widely covered, the dip
    may recover less than the council expects.)
  • Are there NEW developments since the council ran (breaking news, analyst
    notes, fresh insider trades, macro announcements)?

STEP 4: EXTERNAL DRIVER DOMINANCE CHECK
Is a sector trend, commodity price, interest-rate direction, or FX move currently
a BIGGER force on {symbol} than company-specific fundamentals?
  • If yes: your verdict MUST reflect where that external driver is heading —
    do not evaluate the stock in isolation.
  • Commodity-levered stocks (e.g. silver/gold miners, oil E&P, agricultural,
    lithium/uranium) are especially susceptible — check the underlying commodity.
  • Rate-sensitive names (REITs, utilities, high-duration growth): check the
    10-year yield trend.
  • FX-exposed names: check the relevant currency pair.

STEP 5: VALIDATE TRADING LEVELS
Review the council's entry zone, stop-loss, and take-profit:
  • Is the stop-loss realistic? Too tight = noise-stopped; too wide = too much risk.
  • Is the take-profit achievable? (Pre-drop price may be unrealistic if the
    fundamental story changed.)
  • Refine if necessary.

STEP 5b: CALCULATE SELL RANGE
Using your independent analysis, determine exit targets:
  • sell_price_low:  Conservative exit (pre-drop price recovery or BB middle).
  • sell_price_high: Optimistic exit (BB upper, SMA50, SMA200 as resistance).
  • ceiling_exit:    Absolute max = min(52-week high, BB upper + 1×ATR).
  • exit_trigger:    Specific condition combining price level + technical signal.

STEP 6: SWOT ANALYSIS
Construct a SWOT based on your independent research:
  • Strengths: competitive advantages protecting this company.
  • Weaknesses: structural problems.
  • Opportunities: catalysts for recovery, including sector momentum, commodity
    tailwinds, favorable rate/FX direction.
  • Threats: risks preventing recovery, including sector rotation headwinds,
    commodity price declines, adverse rate/FX moves.

STEP 7: FINAL VERDICT
  • CONFIRMED   — Council's recommendation stands.
  • UPGRADED    — You found additional positive evidence; even better than they thought.
  • ADJUSTED    — Thesis ok but trading levels need correction; provide corrected levels.
  • OVERRIDDEN  — You found critical issues the council missed; do NOT buy.

> **Elm Partners Humility Reminder:**
> Even traders with tomorrow's news often lose because they misjudge what's
> priced in.  If the news driving this drop is already widely understood, the
> recovery may be smaller or slower than the council projects.  Look at the
> MARKET'S REACTION to the news, not just the news itself.
> Humility: if you cannot independently verify the "why," the risk is higher
> than the council estimates.
"""

_INDIVIDUAL_OUTPUT_FORMAT = """\
═══════════════════════════════════════════════════════
OUTPUT FORMAT:
═══════════════════════════════════════════════════════
Your output will be parsed as structured JSON. All price fields must be numbers.
All percentage fields must be numbers. Do NOT include inline source markers like
[Source 1] or [1] inside any string value — citations are recorded separately.

Required fields:
  review_verdict       : "CONFIRMED" | "UPGRADED" | "ADJUSTED" | "OVERRIDDEN"
  action               : "BUY" | "BUY_LIMIT" | "WATCH" | "AVOID"
  conviction           : "HIGH" | "MODERATE" | "LOW"
  drop_type            : "EARNINGS_MISS" | "ANALYST_DOWNGRADE" | "SECTOR_ROTATION" |
                         "MACRO_SELLOFF" | "COMPANY_SPECIFIC" | "TECHNICAL_BREAKDOWN" | "UNKNOWN"
  risk_level           : "Low" | "Medium" | "High" | "Extreme"
  catalyst_type        : "Structural" | "Temporary" | "Noise"
  entry_price_low      : <number>
  entry_price_high     : <number>
  stop_loss            : <number>
  take_profit_1        : <number>
  take_profit_2        : <number or null>
  upside_percent       : <number>
  downside_risk_percent: <number>
  risk_reward_ratio    : <number>
  pre_drop_price       : <number>
  entry_trigger        : "Specific condition for entry"
  reassess_in_days     : <number>
  sell_price_low       : <number — conservative exit, where to start taking profits>
  sell_price_high      : <number — optimistic exit, fully exit here>
  ceiling_exit         : <number — absolute max beyond which further gains are unlikely>
  exit_trigger         : "Specific condition for selling, e.g. RSI > 70 and price in $X-$Y zone"
  global_market_analysis : "Macro drivers: broad trend, rate/yield direction (if rate-sensitive),
                            FX direction (if material). State whether any macro force dominates."
  local_market_analysis  : "Sector and commodity drivers: sector ETF / peer direction last 1-4 weeks,
                            commodity price trend if stock is a levered commodity play. State
                            whether sector or commodity currently dominates this stock's setup."
  swot_analysis        : {{ strengths: [...], weaknesses: [...], opportunities: [...], threats: [...] }}
  verification_results : array of {{ claim, verdict ("VERIFIED"|"DISPUTED"), source_url }}
                         — every entry MUST include a source_url from a page you actually fetched.
                           Claims without a verifiable URL will not count toward the score.
  council_blindspots   : ["Issue 1 the council missed", ...]
  knife_catch_warning  : true | false
  reason               : "One sentence: your final assessment as the senior reviewer."
  could_not_verify     : ["Load-bearing claim you searched for but could not confirm with a
                           primary source", ...]
                         — if all key claims were verified, return an empty array [].
                           Do NOT omit this field.
"""


def build_individual_prompt(symbol: str, context: dict) -> str:
    """Build a Claude-native deep-research prompt for buy-the-dip individual review.

    Args:
        symbol:  Ticker symbol (e.g. "AAPL").
        context: Dict containing council outputs.  Required keys: pm_decision,
                 bull_case, bear_case, technical_data, drop_percent, raw_news.
                 Optional new keys: sensor_summaries, disagreement_points.

    Returns:
        Prompt string ready to send as the user turn in the research phase.
    """
    pm_decision = context.get("pm_decision", {})
    bull_case = context.get("bull_case", "Not available")
    bear_case = context.get("bear_case", "Not available")
    tech_data = context.get("technical_data", {})
    drop_percent = context.get("drop_percent", 0)
    raw_news = context.get("raw_news", [])
    transcript_summary = context.get("transcript_summary", "")
    transcript_date = context.get("transcript_date", "Unknown")
    data_depth = context.get("data_depth", {})

    # Optional Step-2b keys — degrade gracefully when absent
    sensor_summaries: Optional[dict] = context.get("sensor_summaries")
    disagreement_points: Optional[list] = context.get("disagreement_points")

    # ── Format PM decision ────────────────────────────────────────────────
    pm_summary = json.dumps(pm_decision, indent=2)
    tech_str = json.dumps(tech_data, indent=2) if tech_data else "No technical data."

    # ── Format news ───────────────────────────────────────────────────────
    news_count = (
        data_depth.get("news", {}).get("total_count", 0)
        if isinstance(data_depth, dict) else 0
    )
    news_str = ""
    for n in raw_news[:20]:
        date = n.get("datetime_str", "N/A")
        source = n.get("source", "Unknown")
        source_type = n.get("source_type", "WIRE")
        headline = n.get("headline", "No Headline")
        content = n.get("content", "")
        summary = n.get("summary", "")
        news_str += f"- {date} [{source_type}] [{source}]: {headline}\n"
        if content:
            news_str += f"  {content[:1500]}\n\n"
        elif summary:
            news_str += f"  {summary[:500]}\n\n"

    evidence_note = (
        f"Council analyzed {news_count} news articles total. "
        f"{len(raw_news)} articles are reproduced below as supplementary evidence. "
        "These came from premium/paywalled feeds (Benzinga, Polygon, Finnhub, etc.) — "
        "web_fetch may reach some of them; treat them as council-provided context "
        "and supplement with fresh primary-source searches."
    )

    # ── Transcript section ────────────────────────────────────────────────
    transcript_section = ""
    skip_phrases = {
        "No transcript summary available from council.",
        "No transcript summary available from backfill.",
    }
    if transcript_summary and transcript_summary not in skip_phrases:
        transcript_section = f"""\

═══════════════════════════════════════════════════════
EARNINGS TRANSCRIPT SUMMARY (Date: {transcript_date}):
═══════════════════════════════════════════════════════
(Condensed by the News Agent from the full earnings call — key points preserved)
{transcript_summary}
"""

    # ── Council agents section ────────────────────────────────────────────
    if sensor_summaries:
        agent_lines = "\n".join(
            f"  • {name}: {summary}"
            for name, summary in sensor_summaries.items()
        )
        agents_section = f"""\

═══════════════════════════════════════════════════════
COUNCIL AGENT SUMMARIES (what each agent already found):
═══════════════════════════════════════════════════════
Five specialist agents ran before you: Technical Analysis, News Analysis,
Market Sentiment, Competitive Landscape, and Seeking Alpha.
Their condensed findings are below — use these to target YOUR search hops
on what those agents could NOT see (primary filings, very recent developments,
contested claims).

{agent_lines}
"""
    else:
        agents_section = """\

═══════════════════════════════════════════════════════
COUNCIL AGENTS (already ran — target your hops on their gaps):
═══════════════════════════════════════════════════════
Five specialist agents have already analyzed this stock:
  • Technical Analysis  — price action, RSI, Bollinger Bands, SMA levels
  • News Analysis       — recent news headlines, earnings-call transcript
  • Market Sentiment    — analyst ratings, short interest, put/call ratio
  • Competitive Landscape — sector peers, competitive position
  • Seeking Alpha       — analyst commentary, quant ratings

Do NOT duplicate what those agents covered. Use your search hops on:
  primary filings (EDGAR), very recent developments they could not see,
  contested claims between the bull and bear cases.
"""

    # ── Disagreement / bull-bear priority section ─────────────────────────
    if disagreement_points:
        points_str = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(disagreement_points))
        disagreement_section = f"""\
The council's bull and bear researchers explicitly disagreed on the following
points. Resolving these IS YOUR HIGHEST-PRIORITY RESEARCH OBJECTIVE — search
until you have an independent, primary-source answer to each:

{points_str}

After resolving these, continue with the remaining steps."""
    else:
        disagreement_section = """\
Derive the key contradictions from the BULL CASE and BEAR CASE below, then
independently resolve those contradictions using primary sources before
forming your verdict. Typical areas of disagreement: is the drop-cause
one-time or structural? Is guidance realistic? Do insider / institutional
actions confirm or deny the bull thesis?"""

    # ── Bull/bear truncation (match Gemini service: 4000 chars in live mode) ─
    bull_display = bull_case[:4000]
    bear_display = bear_case[:4000]

    # Inject symbol and disagreement_section into the task-steps template
    task_steps = _INDIVIDUAL_TASK_STEPS.format(
        symbol=symbol,
        disagreement_section=disagreement_section,
    )

    return f"""\
You are a **Senior Investment Reviewer** at a hedge fund. An internal AI council
has already analyzed stock {symbol}, which dropped {drop_percent:.2f}% today
and recommends it as a potential "buy the dip" opportunity.

Your job is NOT to redo their analysis. Your job is to:
1. CHALLENGE the council's recommendation — find what they might have missed
2. VERIFY their key claims using fresh web research (primary sources)
3. REFINE the trading levels (entry, stop-loss, take-profit) if incorrect
4. CONFIRM or OVERRIDE the final verdict

You are the last line of defense before real money is deployed.

{_INDIVIDUAL_SEARCH_DIRECTIVE}

═══════════════════════════════════════════════════════
COUNCIL DECISION (This is what you are reviewing):
═══════════════════════════════════════════════════════
{pm_summary}

═══════════════════════════════════════════════════════
BULL CASE (Constructed by Council's Bull Researcher):
═══════════════════════════════════════════════════════
{bull_display}

═══════════════════════════════════════════════════════
BEAR CASE (Constructed by Council's Bear Researcher):
═══════════════════════════════════════════════════════
{bear_display}

═══════════════════════════════════════════════════════
TECHNICAL DATA (Raw Indicators):
═══════════════════════════════════════════════════════
{tech_str}
{transcript_section}
{agents_section}
═══════════════════════════════════════════════════════
COUNCIL NEWS ARTICLES (Supplementary Evidence):
═══════════════════════════════════════════════════════
SOURCE PRIORITY (each article is tagged with a source_type):
1. OFFICIAL (press releases, SEC filings) — ground truth
2. WIRE (Benzinga, Reuters, Finnhub) — factual reporting
3. ANALYST (Seeking Alpha, Motley Fool) — informed opinion, check for bias
4. MARKET_CONTEXT — broad signals, not company-specific
When an ANALYST article contradicts a WIRE report, trust WIRE for facts.

{news_str if news_str else "No council news articles available."}

═══════════════════════════════════════════════════════
DATA QUALITY NOTE:
═══════════════════════════════════════════════════════
{evidence_note}

{task_steps}

{_INDIVIDUAL_OUTPUT_FORMAT}"""


# ── Sell reassessment ─────────────────────────────────────────────────────────

def build_sell_prompt(symbol: str, context: dict) -> str:
    """Build a Claude-native sell-reassessment prompt for owned-position review.

    Mirrors _construct_sell_reassessment_prompt() in deep_research_service.py,
    reworded to be provider-neutral (no Gemini/Google Search references).

    Args:
        symbol:  Ticker symbol.
        context: Dict with original_decision, current_price, performance_since_entry,
                 technical_data, sensor_reports, raw_news.

    Returns:
        Prompt string ready to send as the user turn.
    """
    original = context.get("original_decision", {})
    entry_low = original.get("entry_price_low") or 0
    entry_high = original.get("entry_price_high") or 0
    current_price = context.get("current_price", 0)
    performance = context.get("performance_since_entry", "N/A")
    stop_loss = original.get("stop_loss", "N/A")
    sell_low = original.get("sell_price_low", "N/A")
    sell_high = original.get("sell_price_high", "N/A")
    ceiling = original.get("ceiling_exit", "N/A")
    reason = original.get("reason", "N/A")

    sensor_reports = json.dumps(context.get("sensor_reports", {}), indent=2)
    technical_data = json.dumps(context.get("technical_data", {}), indent=2)

    raw_news = context.get("raw_news", [])
    news_str = ""
    for n in raw_news[:25]:
        date = n.get("datetime_str", "N/A")
        source = n.get("source", "Unknown")
        headline = n.get("headline", "No Headline")
        summary = (n.get("summary", "") or n.get("content", ""))[:500]
        news_str += f"- {date} [{source}]: {headline}\n  {summary}\n\n"

    return f"""\
You are a **Senior Sell-Side Analyst** at a hedge fund. You are reviewing an
EXISTING OWNED position to decide whether to HOLD, TAKE PARTIAL PROFITS, or
EXIT FULLY.

You have access to web_search and web_fetch tools. Use web search to check for
any developments since this position was entered — earnings updates, analyst
rating changes, news events, or macro shifts that affect the thesis.

POSITION CONTEXT:
- Ticker: {symbol}
- Original Entry: ${entry_low} - ${entry_high}
- Current Price: ${current_price} ({performance})
- Current Stop Loss: ${stop_loss}
- Current Sell Zone: ${sell_low} - ${sell_high}
- Ceiling Exit: ${ceiling}
- Original Buy Thesis: {reason}

FRESH COUNCIL SENSOR DATA (collected just now):
{sensor_reports}

FRESH TECHNICAL INDICATORS:
{technical_data}

RECENT NEWS:
{news_str if news_str else "No recent news provided."}

YOUR TASK:
STEP 1: THESIS STATUS — Is the original buy thesis still INTACT, WEAKENING,
  or BROKEN?  Use the fresh news and sentiment data. Search for any
  developments since the entry date.
STEP 2: TECHNICAL PICTURE — Analyze current indicators. Is RSI overbought?
  Has price hit resistance (bb_upper, SMA50, SMA200)? Is volume supporting
  the move or declining?
STEP 3: UPDATED SELL RANGE — Recalculate sell_price_low, sell_price_high,
  ceiling_exit using fresh technicals. If thesis is weakening, lower targets.
  If intact with momentum, raise.
STEP 4: ACTION RECOMMENDATION — HOLD / SELL_PARTIAL / SELL_FULL / TIGHTEN_STOP
STEP 5: STOP LOSS UPDATE — Can only go UP (trailing stop). Never lower it.

OUTPUT FORMAT (valid JSON only):
Do NOT include inline source markers like [Source 1], [Source 2], etc. in any
string value.
{{
  "thesis_status": "INTACT" | "WEAKENING" | "BROKEN",
  "sell_action": "HOLD" | "SELL_PARTIAL" | "SELL_FULL" | "TIGHTEN_STOP",
  "updated_sell_price_low": <number>,
  "updated_sell_price_high": <number>,
  "updated_ceiling_exit": <number>,
  "updated_stop_loss": <number or null — only if raised>,
  "exit_trigger": "Specific condition for selling",
  "next_reassess_in_days": <number>,
  "thesis_reasoning": "One sentence on thesis status",
  "action_reasoning": "One sentence on recommended action",
  "key_observations": ["observation 1", "observation 2", "observation 3"]
}}
"""

# Deep Research Redesign Proposal

## Current State (Problems)

### 1. Trigger Logic is Too Broad
**Current trigger** (stock_service.py line 1532):
```python
should_trigger = "BUY" in recommendation.upper()
```
This fires on BUY, BUY_LIMIT, STRONG BUY — anything with "BUY" in it. That's **37% of all decisions** (84 out of 229 in the last 2 weeks). At ~$0.10-0.50 per deep research call + 60s cooldown between calls, this is expensive and wasteful.

### 2. Council Data is Partially Passed
**Currently passed to deep research:**
- `raw_news` (raw news items — duplicates what News Agent already processed)
- `technical_data` (raw indicators dict)
- `transcript_text` (full transcript — huge token cost)
- `market_sentiment_report` (agent output)
- `competitive_report` (agent output)

**NOT passed (but should be):**
- Bull/Bear debate reports (the most synthesized analysis)
- Risk Agent assessment
- PM decision + trading levels (the new structured output)
- Drop type classification
- Evidence barometer (data quality score)

### 3. Output Schema Doesn't Align with PM
The deep research outputs `STRONG_BUY / SPECULATIVE_BUY / WAIT_FOR_STABILIZATION / HARD_AVOID` while the PM now outputs `BUY / BUY_LIMIT / WATCH / AVOID`. These need to be the same vocabulary.

### 4. Deep Research Duplicates Work
The deep research prompt asks the agent to independently research news, catalyst, SWOT, etc. — all of which the council already did. The deep research should be a **verification and refinement** step, not a redo.

---

## Proposed Changes

### 1. New Trigger Logic: Only High-Probability Bets

Replace the current `"BUY" in recommendation` with a multi-condition gate:

```python
def _should_trigger_deep_research(self, report_data: dict) -> bool:
    """
    Gate deep research on HIGH-PROBABILITY candidates only.
    Deep research is expensive — only trigger when the council
    has already identified a strong setup.
    """
    action = report_data.get("recommendation", "AVOID").upper()
    conviction = report_data.get("conviction", "LOW").upper()
    risk_reward = report_data.get("risk_reward_ratio", 0)
    drop_type = report_data.get("drop_type", "UNKNOWN")

    # RULE 1: Must be a BUY or BUY_LIMIT action
    if action not in ("BUY", "BUY_LIMIT"):
        return False

    # RULE 2: Must have at least MODERATE conviction
    if conviction == "LOW":
        return False

    # RULE 3: Risk/reward must be favorable (> 1.5)
    try:
        if float(risk_reward) < 1.5:
            return False
    except (TypeError, ValueError):
        return False  # If we can't parse R/R, don't trigger

    # RULE 4: Drop type must be recoverable
    # Structural company-specific issues (fraud, permanent loss) rarely recover
    non_recoverable = ("COMPANY_SPECIFIC",)  # Fraud, scandal, structural damage
    if drop_type in non_recoverable and conviction != "HIGH":
        return False

    return True
```

**Expected impact:** Reduces deep research triggers from ~37% to ~10-15% of decisions, focusing spend on the best candidates.

**Why these criteria:**
- `BUY` or `BUY_LIMIT` = council already thinks this is actionable
- `MODERATE` or `HIGH` conviction = not too many unresolved risks
- R/R > 1.5 = the math works (upside > 1.5x downside)
- Recoverable drop type = not a structural trap

### 2. New Data Flow: Pass the Full Council Context

Instead of re-passing raw data, pass the **synthesized council output** as a summary report. The deep research agent should act as a senior reviewer, not a junior analyst redoing the work.

```python
def _build_deep_research_context(self, report_data: dict, raw_data: dict) -> dict:
    """
    Builds a condensed but complete context package for deep research.
    Passes SYNTHESIZED council output, not raw data.
    """
    return {
        # PM Decision (new structured output)
        "pm_decision": {
            "action": report_data.get("recommendation"),
            "conviction": report_data.get("conviction"),
            "drop_type": report_data.get("drop_type"),
            "entry_price_low": report_data.get("entry_price_low"),
            "entry_price_high": report_data.get("entry_price_high"),
            "stop_loss": report_data.get("stop_loss"),
            "take_profit_1": report_data.get("take_profit_1"),
            "take_profit_2": report_data.get("take_profit_2"),
            "upside_percent": report_data.get("upside_percent"),
            "downside_risk_percent": report_data.get("downside_risk_percent"),
            "risk_reward_ratio": report_data.get("risk_reward_ratio"),
            "pre_drop_price": report_data.get("pre_drop_price"),
            "entry_trigger": report_data.get("entry_trigger"),
            "reason": report_data.get("executive_summary"),
            "key_factors": report_data.get("key_factors", []),
        },
        # Synthesized agent reports (not raw data)
        "bull_case": report_data.get("bull_report", ""),
        "bear_case": report_data.get("bear_report", ""),
        # Technical indicators (compact — already processed)
        "technical_data": raw_data.get("indicators", {}),
        # Drop context
        "drop_percent": raw_data.get("change_percent", 0),
        # Paywalled news — deep research can't access Benzinga/Polygon via Google Search,
        # so we MUST pass the raw news items (headlines + content/summaries)
        "raw_news": raw_data.get("news_items", []),
        # Transcript — use the News Agent's "Extended Transcript Summary" from council reports
        # instead of the full raw transcript (~1,300 chars vs ~30,000 chars = 95% cheaper)
        "transcript_summary": self._extract_transcript_summary(report_data),
        "transcript_date": raw_data.get("transcript_date"),
        # Evidence quality
        "data_depth": report_data.get("data_depth", {}),
    }

def _extract_transcript_summary(self, report_data: dict) -> str:
    """
    Extracts the 'Extended Transcript Summary' section from the News Agent's output.
    The News Agent already produces a high-quality summary of the earnings call
    focusing on guidance, management tone, and strategic shifts.
    Falls back to a truncated raw transcript if the summary can't be found.
    """
    news_report = report_data.get("macro_report", "")  # News Agent output is in macro_report for legacy reasons

    # Look for the Extended Transcript Summary section
    marker = "Extended Transcript Summary"
    if marker in news_report:
        # Extract everything from the marker to the next major header
        start = news_report.index(marker)
        # Find the next ## or ### header after the summary
        rest = news_report[start + len(marker):]
        # Common headers that follow: "Key Drivers", "Narrative Check", "Top 5 Sources"
        end_markers = ["## Key Drivers", "### Key Drivers", "## Narrative Check",
                       "### Narrative Check", "## Top 5 Sources", "### Top 5 Sources",
                       "## MACRO CHECK", "NEEDS_ECONOMICS"]
        end_pos = len(rest)
        for em in end_markers:
            if em in rest:
                pos = rest.index(em)
                end_pos = min(end_pos, pos)

        summary = rest[:end_pos].strip()
        if len(summary) > 100:  # Sanity check — must have actual content
            return summary

    # Fallback: No summary found, return empty (deep research will use Google Search)
    return "No transcript summary available from council."
```

**What changed from current:**
- Added: PM decision with trading levels, bull/bear reports, evidence quality
- **KEPT**: Raw news list — Benzinga/Polygon content is behind a paywall and can't be found via Google Search
- **Replaced**: Full raw transcript (~30,000 chars) with News Agent's "Extended Transcript Summary" (~1,300 chars) — 95% token reduction while preserving the key insights (guidance, tone, strategic shifts)
- Removed: market_sentiment_report and competitive_report as separate fields (they're in the council reports; deep research can verify independently)

### 3. New Deep Research Prompt

```python
def _construct_prompt(self, symbol, context: dict) -> str:
    """
    Deep Research prompt — acts as a SENIOR REVIEWER
    of the council's decision, not a redo of the analysis.
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

    # Format PM decision compactly
    pm_summary = json.dumps(pm_decision, indent=2)
    tech_str = json.dumps(tech_data, indent=2) if tech_data else "No technical data."

    # Format news (paywalled — deep research can't access these via Google Search)
    news_str = ""
    if raw_news:
        for n in raw_news[:20]:  # Top 20 most recent
            date = n.get('datetime_str', 'N/A')
            source = n.get('source', 'Unknown')
            headline = n.get('headline', 'No Headline')
            summary = n.get('summary', '')
            content = n.get('content', '')
            news_str += f"- {date} [{source}]: {headline}\n"
            # Include content/summary (truncated per article to manage tokens)
            if content:
                news_str += f"  {content[:1500]}\n\n"
            elif summary:
                news_str += f"  {summary[:500]}\n\n"

    # Evidence quality note
    news_count = data_depth.get("news", {}).get("total_count", 0) if isinstance(data_depth, dict) else 0
    evidence_note = f"Council analyzed {news_count} news articles. {len(raw_news)} articles provided below (paywalled sources — not available via Google Search)."

    transcript_section = ""
    if transcript_summary and transcript_summary != "No transcript summary available from council.":
        transcript_section = f"""
EARNINGS TRANSCRIPT SUMMARY (Date: {transcript_date}):
(Condensed by the News Agent from the full earnings call — key points preserved)
{transcript_summary}
"""

    return f"""
You are a **Senior Investment Reviewer** at a hedge fund. An internal AI council
has already analyzed stock {symbol} which dropped {drop_percent:.2f}% today
and recommends it as a potential "buy the dip" opportunity.

Your job is NOT to redo the analysis. Your job is to:
1. **CHALLENGE** the council's recommendation — find what they might have missed
2. **VERIFY** their key claims using fresh Google Search data
3. **REFINE** the trading levels (entry, stop-loss, take-profit) if the council got them wrong
4. **CONFIRM or OVERRIDE** the final verdict

You are the last line of defense before real money is deployed.

═══════════════════════════════════════════════════════
COUNCIL DECISION (This is what you are reviewing):
═══════════════════════════════════════════════════════
{pm_summary}

═══════════════════════════════════════════════════════
BULL CASE (Constructed by Council's Bull Researcher):
═══════════════════════════════════════════════════════
{bull_case[:4000]}

═══════════════════════════════════════════════════════
BEAR CASE (Constructed by Council's Bear Researcher):
═══════════════════════════════════════════════════════
{bear_case[:4000]}

═══════════════════════════════════════════════════════
TECHNICAL DATA (Raw Indicators):
═══════════════════════════════════════════════════════
{tech_str}

{transcript_section}

═══════════════════════════════════════════════════════
NEWS ARTICLES (Paywalled Sources — NOT available via Google Search):
═══════════════════════════════════════════════════════
These articles are from Benzinga/Polygon, Alpha Vantage, Finnhub, and other
premium sources. You CANNOT access these via Google Search. Use this data
as primary evidence and verify/supplement with your own Google Search.
{news_str if news_str else "No paywalled news articles available."}

═══════════════════════════════════════════════════════
DATA QUALITY NOTE:
═══════════════════════════════════════════════════════
{evidence_note}

═══════════════════════════════════════════════════════
YOUR TASK:
═══════════════════════════════════════════════════════

STEP 1: VERIFY KEY CLAIMS
Use Google Search to independently verify the top 3 claims from the council:
- Is the drop reason accurate? Search for the actual news.
- Is the earnings data correct? Check the actual numbers.
- Are there NEW developments since the council ran (breaking news, analyst notes, insider trades)?

STEP 2: CHALLENGE THE THESIS
Play devil's advocate against the council's BUY recommendation:
- What's the worst-case scenario they didn't consider?
- Is there a liquidity risk, delisting risk, or regulatory action pending?
- Did they misjudge what's "priced in"?

STEP 3: VALIDATE TRADING LEVELS
Review the council's entry zone, stop-loss, and take-profit:
- Is the stop-loss realistic? (Too tight = will get stopped out on noise. Too wide = too much risk.)
- Is the take-profit achievable? (Pre-drop price may not be realistic if the fundamental story changed.)
- Would YOU adjust any of these levels based on your research?

STEP 4: SWOT ANALYSIS
Based on your independent research, construct a SWOT:
- Strengths: What competitive advantages protect this company?
- Weaknesses: What structural problems exist?
- Opportunities: What catalysts could drive recovery?
- Threats: What risks could prevent recovery?

STEP 5: FINAL VERDICT
After your review, decide:
- **CONFIRMED**: Council's recommendation stands. You agree with the setup.
- **UPGRADED**: You found additional positive evidence the council missed. Even better than they thought.
- **ADJUSTED**: The thesis is okay but trading levels need correction. Provide corrected levels.
- **OVERRIDDEN**: You found critical issues the council missed. Do NOT buy this stock.

> **Philosophical Reminder (Elm Partners Paradox):**
> Even traders with tomorrow's news often lose because they misjudge what's priced in.
> If the news driving this drop is obvious to everyone, the recovery may already be priced in.
> Look for the REACTION to the news, not just the news itself.
> Humility: If you can't verify the "why," the risk is higher than the council thinks.

OUTPUT FORMAT:
Your output must be valid JSON. All price fields must be numbers. All percentage fields must be numbers.
{{
  "review_verdict": "CONFIRMED" | "UPGRADED" | "ADJUSTED" | "OVERRIDDEN",
  "action": "BUY" | "BUY_LIMIT" | "WATCH" | "AVOID",
  "conviction": "HIGH" | "MODERATE" | "LOW",
  "drop_type": "EARNINGS_MISS" | "ANALYST_DOWNGRADE" | "SECTOR_ROTATION" | "MACRO_SELLOFF" | "COMPANY_SPECIFIC" | "TECHNICAL_BREAKDOWN" | "UNKNOWN",
  "risk_level": "Low" | "Medium" | "High" | "Extreme",
  "catalyst_type": "Structural" | "Temporary" | "Noise",
  "entry_price_low": <number>,
  "entry_price_high": <number>,
  "stop_loss": <number>,
  "take_profit_1": <number>,
  "take_profit_2": <number or null>,
  "upside_percent": <number>,
  "downside_risk_percent": <number>,
  "risk_reward_ratio": <number>,
  "pre_drop_price": <number>,
  "entry_trigger": "Specific condition for entry",
  "reassess_in_days": <number>,
  "global_market_analysis": "Brief analysis of global market conditions",
  "local_market_analysis": "Brief analysis of local/sector conditions",
  "swot_analysis": {{
    "strengths": ["point 1", "point 2"],
    "weaknesses": ["point 1", "point 2"],
    "opportunities": ["point 1", "point 2"],
    "threats": ["point 1", "point 2"]
  }},
  "verification_results": [
    "Claim 1: [VERIFIED/DISPUTED] — explanation",
    "Claim 2: [VERIFIED/DISPUTED] — explanation",
    "Claim 3: [VERIFIED/DISPUTED] — explanation"
  ],
  "council_blindspots": ["Issue 1 the council missed", "Issue 2"],
  "knife_catch_warning": true | false,
  "reason": "One sentence: your final assessment as the senior reviewer."
}}
"""
```

### 4. Updated `_handle_completion` — Score Mapping

Replace the old verdict-to-score mapping:

```python
# OLD:
score_map = {
    "STRONG_BUY": 90,
    "SPECULATIVE_BUY": 75,
    "WAIT_FOR_STABILIZATION": 50,
    "HARD_AVOID": 10
}

# NEW:
def _calculate_deep_research_score(self, result: dict) -> int:
    """
    Composite score based on multiple factors, not just verdict.
    """
    score = 50  # Base

    # Review verdict weight (±20)
    verdict_map = {"CONFIRMED": 15, "UPGRADED": 20, "ADJUSTED": 5, "OVERRIDDEN": -20}
    score += verdict_map.get(result.get("review_verdict", ""), 0)

    # Conviction weight (±15)
    conviction_map = {"HIGH": 15, "MODERATE": 5, "LOW": -10}
    score += conviction_map.get(result.get("conviction", "LOW"), 0)

    # Risk/reward weight (±15)
    rr = result.get("risk_reward_ratio", 0)
    try:
        rr = float(rr)
        if rr >= 3.0: score += 15
        elif rr >= 2.0: score += 10
        elif rr >= 1.5: score += 5
        elif rr < 1.0: score -= 10
    except (TypeError, ValueError):
        pass

    # Knife catch penalty (-15)
    if result.get("knife_catch_warning") in (True, "True", "true"):
        score -= 15

    # Verification results (count disputes)
    verifications = result.get("verification_results", [])
    disputes = sum(1 for v in verifications if "DISPUTED" in str(v).upper())
    score -= disputes * 5  # -5 per disputed claim

    return max(0, min(100, score))  # Clamp to 0-100
```

### 5. New DB Columns for Deep Research

```sql
-- Add to decision_points (alongside existing deep_research_* columns):
deep_research_review_verdict TEXT,    -- CONFIRMED/UPGRADED/ADJUSTED/OVERRIDDEN
deep_research_action TEXT,            -- BUY/BUY_LIMIT/WATCH/AVOID
deep_research_conviction TEXT,        -- HIGH/MODERATE/LOW
deep_research_entry_low REAL,
deep_research_entry_high REAL,
deep_research_stop_loss REAL,
deep_research_tp1 REAL,
deep_research_tp2 REAL,
deep_research_upside REAL,
deep_research_downside REAL,
deep_research_rr_ratio REAL,
deep_research_drop_type TEXT,
deep_research_entry_trigger TEXT,
deep_research_verification TEXT,      -- JSON array of verification results
deep_research_blindspots TEXT         -- JSON array of council blindspots
```

---

## Updated Data Flow Diagram

```
PHASE 1: Council Agents (Parallel)
    ├── Technical Agent
    ├── News Agent → [trigger Economics if US]
    ├── Competitive Landscape Agent
    ├── Market Sentiment Agent
    └── Seeking Alpha Agent
         ↓
PHASE 2: Bull/Bear/Risk Debate (Parallel)
    ├── Bull Researcher
    ├── Bear Researcher
    └── Risk Management Agent
         ↓
PHASE 3: Portfolio Manager Decision
    └── Outputs: action, conviction, drop_type, trading levels, R/R ratio
         ↓
GATE CHECK: Should trigger deep research?
    ├── action ∈ {BUY, BUY_LIMIT}?
    ├── conviction ∈ {MODERATE, HIGH}?
    ├── risk_reward_ratio >= 1.5?
    └── drop_type is recoverable?
         ↓ (YES → ~10-15% of decisions)
PHASE 4: Deep Research (Async, queued)
    INPUT: PM decision + bull/bear reports + tech data + transcript snippet
    ROLE: Senior Reviewer (verify, challenge, refine)
    OUTPUT: review_verdict + refined trading levels + SWOT + verification results
         ↓
    Updated DB: deep_research_* columns populated
         ↓
PHASE 5: Batch Comparison (when 4+ deep research results exist for same day)
    INPUT: Deep research results + council summaries
    OUTPUT: Winner symbol + ranking + rationale
```

---

## Cost Impact Estimate

| Metric | Current | Proposed | Change |
|--------|---------|----------|--------|
| Deep research triggers per day | ~8-10 (37% of ~23 decisions) | ~2-4 (10-15% of ~23 decisions) | **-60%** |
| Token input per call | ~15-20k (raw news + full transcript 30k chars) | ~10-14k (news kept + transcript summary ~1.3k instead of ~30k) | **-30%** |
| API cost per day (est.) | ~$1-5 | ~$0.30-1.50 | **-65%** |
| Quality per call | Moderate (redoes council work) | Higher (focused verification + paywalled news) | **Better** |

**Token breakdown per call (proposed):**
- PM decision JSON: ~500 tokens
- Bull/Bear reports (truncated to 4k each): ~5,000 tokens
- Technical data: ~500 tokens
- News articles (20 articles, truncated): ~4,000 tokens ← kept because paywalled
- Transcript summary (from News Agent): ~400 tokens ← was ~8,000 for raw transcript
- Prompt instructions: ~1,500 tokens
- **Total: ~12,000 tokens** (down from ~18,000)

---

## What the Deep Research Now Does vs. Before

### Before (Current):
1. Receives raw news + raw tech data + full transcript (~30k chars)
2. Independently researches the stock from scratch (duplicates council work)
3. Constructs its own SWOT, catalyst analysis, market context
4. Outputs a verdict (STRONG_BUY, WAIT, etc.) that DOESN'T influence the PM decision
5. Stored in DB, never used

### After (Proposed):
1. Receives PM decision + bull/bear synthesis + paywalled news + transcript SUMMARY (~1.3k chars)
2. Focuses on VERIFYING claims and FINDING what the council missed
3. Has access to paywalled Benzinga/Polygon articles that Google Search can't reach
4. Outputs same schema as PM (aligned vocabulary, comparable fields)
5. Can OVERRIDE or ADJUST the PM's trading levels
6. **Final authority** — the deep research verdict is the one that should drive your buy decision

---

## How to Use This in Practice (Simon's Workflow)

1. System runs every 20 minutes, finds a stock that dropped >5%
2. Council analyzes it → PM says "BUY_LIMIT at $142, conviction MODERATE, R/R 1.8"
3. Gate check passes → deep research queued
4. Deep research runs (~5-15 min) → comes back "CONFIRMED, adjusted stop-loss to $138"
5. You check the database:
   ```sql
   SELECT symbol,
          recommendation AS council_action,
          deep_research_review_verdict AS dr_review,
          deep_research_action AS dr_action,
          deep_research_conviction AS dr_conviction,
          entry_price_low, entry_price_high,
          deep_research_stop_loss AS dr_stop,
          deep_research_tp1 AS dr_tp1,
          upside_percent, deep_research_upside AS dr_upside,
          deep_research_rr_ratio AS dr_rr
   FROM decision_points
   WHERE date(timestamp) = date('now')
     AND deep_research_review_verdict IN ('CONFIRMED', 'UPGRADED')
   ORDER BY deep_research_rr_ratio DESC
   ```
6. You see the best candidates with verified trading levels → place your orders

---

## Migration Steps

1. Add new DB columns (non-breaking, NULL defaults)
2. Add `_should_trigger_deep_research()` gate function to stock_service.py
3. Add `_build_deep_research_context()` to stock_service.py
4. Update `deep_research_service._construct_prompt()` with new prompt
5. Update `deep_research_service._handle_completion()` with new score logic and field mapping
6. Update `_repair_json_using_flash()` schema definition to match new output format
7. Update backfill logic to use new trigger criteria
8. Update trade report CSV generation to include new columns

# Portfolio Manager Prompt Redesign Proposal

## Summary of Changes

### What changes and why

1. **New structured JSON output** — replaces the vague `action` + `score` with concrete trading levels (entry zone, stop-loss, take-profit targets), an upside/downside percentage, and a 3-tier conviction system.

2. **Upside potential field** — `upside_percent` and `downside_risk_percent` give you a quick ratio to scan in the database. If upside is 2x the downside, the risk/reward is favorable.

3. **Entry trigger** — instead of "WAIT_FOR_STABILIZATION" the system now says *what to wait for* (e.g., "RSI crossing back above 30" or "Price holding $142 for 2 days").

4. **Stop-loss & take-profit** — derived from ATR and Support/Resistance levels that your technical agent already provides.

5. **Time horizon** — forces the model to commit to a reassessment window, preventing stale watchlist items.

6. **Drop type classification** — new required field so you can track recovery rates by category over time.

### What stays the same

- The 3-phase architecture (Agents → Bull/Bear → PM)
- All agent reports still flow in as context
- Risk flags still get passed in
- Google Search verification still happens
- The PM still reads bull and bear cases

---

## Data Availability Check (Verified Against Codebase)

| Field | Available? | Key Name | Notes |
|-------|-----------|----------|-------|
| ATR | YES | `atr` | From TradingView Screener |
| RSI | YES | `rsi` | From TradingView Screener |
| BB Lower | YES | `bb_lower` | From TradingView Screener |
| BB Upper | YES | `bb_upper` | From TradingView Screener |
| Current Price | YES | `close` | From TradingView Screener |
| SMA50 | YES | `sma50` | From TradingView Screener |
| SMA200 | YES | `sma200` | From TradingView TA_Handler |
| 52W High | YES | `high52` | From TradingView Screener |
| 52W Low | YES | `low52` | From TradingView Screener |
| Drop % | YES | `change_percent` | Passed in raw_data |
| Support Level | NO | — | Use bb_lower / low52 as proxy |
| Resistance Level | NO | — | Use bb_upper / high52 as proxy |
| BB Middle (SMA20) | NO | — | Approximate as (bb_lower + bb_upper) / 2 |

The prompt below is written to use **only available fields**. No missing data.

---

## New `_create_fund_manager_prompt` Method

```python
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
3. **CLASSIFY THE DROP**: Determine WHY the stock dropped. This is critical for predicting recovery.
4. **CALCULATE TRADING LEVELS**: Using the technical data (ATR, Support, Resistance, Bollinger Bands) from the reports, determine concrete price levels.

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
- **entry_price_low / entry_price_high**: The price zone where buying makes sense. Use bb_lower and the current close price as guides. If "BUY" (immediate), set this to the current close price ± 1%.
- **stop_loss**: Set at 2x ATR below entry_price_low, or below the bb_lower if that is tighter. This is the "thesis is broken" level. Must be a concrete number.
- **take_profit_1**: Conservative target. Typically the pre-drop price (calculate from close and drop_percent) or the BB middle (average of bb_lower and bb_upper). This is the recovery target.
- **take_profit_2**: Optimistic target. bb_upper, SMA50, or SMA200 — whichever is above TP1 and realistic. Set to null if no clear upside beyond TP1.
- **upside_percent**: Calculate from current close to take_profit_1. Example: close is $100, TP1 is $112 → upside is 12.0.
- **downside_risk_percent**: Calculate from current close to stop_loss. Example: close is $100, SL is $90 → downside is 10.0.
- **pre_drop_price**: Calculate from close and drop_percent. Formula: close / (1 + drop_percent/100). Example: close=$93, drop=-7% → pre_drop = 93 / 0.93 = $100. Include this for reference.

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
  "reason": "One sentence: why this is or isn't a good trade right now.",
  "key_factors": [
      "String (Factor 1 — most important evidence for/against)",
      "String (Factor 2 — verification result from Google Search)",
      "String (Factor 3 — technical or risk consideration)"
  ]
}}
"""
```

---

## New Database Columns Needed

Add these columns to `decision_points` (via your migration pattern in `database.py`):

```sql
-- Trading levels
entry_price_low REAL,
entry_price_high REAL,
stop_loss REAL,
take_profit_1 REAL,
take_profit_2 REAL,
pre_drop_price REAL,
upside_percent REAL,
downside_risk_percent REAL,
risk_reward_ratio REAL,

-- Classification
drop_type TEXT,
conviction TEXT,
entry_trigger TEXT,
reassess_in_days INTEGER
```

---

## Changes to `research_service.py` Return Dict

Update the return block in `analyze_stock()` to include the new fields:

```python
return {
    "recommendation": recommendation,    # Now: BUY, BUY_LIMIT, WATCH, AVOID
    "score": final_decision.get("score", 50),  # REMOVE or keep for backwards compat
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
    "entry_trigger": final_decision.get("entry_trigger"),
    "reassess_in_days": final_decision.get("reassess_in_days"),
    "executive_summary": final_decision.get("reason", "No reason provided."),
    # ... rest stays the same
}
```

---

## Changes to `stock_service.py` Database Insert

Update `add_decision_point()` and `update_decision_point()` to persist the new fields.

---

## Changes to Deep Research Prompt (Optional Phase 2)

The deep research prompt (`deep_research_service.py` line 708-723) should also adopt the new output schema, replacing the verdict categories:

**Old:**
```json
"verdict": "STRONG_BUY | SPECULATIVE_BUY | WAIT_FOR_STABILIZATION | HARD_AVOID"
```

**New:**
```json
"verdict": "BUY | BUY_LIMIT | WATCH | AVOID",
"conviction": "HIGH | MODERATE | LOW",
"drop_type": "...",
"take_profit_1": <number>,
"stop_loss": <number>,
"upside_percent": <number>,
"downside_risk_percent": <number>
```

This aligns both prompts so verdicts are directly comparable and you eliminate the mapping problem between the two systems.

---

## What This Looks Like in Practice

### Before (current system):
```
Stock: AAPL dropped -7.2%
Action: HOLD
Score: 62/100
Reason: "Mixed signals, earnings beat but guidance lowered"
```
→ You go to Seeking Alpha, check yourself, decide manually.

### After (new system):
```
Stock: AAPL dropped -7.2%
Action: BUY_LIMIT
Conviction: MODERATE
Drop Type: EARNINGS_MISS
Entry Zone: $178.50 - $182.00
Stop Loss: $171.20 (2x ATR below support)
Take Profit 1: $192.40 (pre-drop price)
Take Profit 2: $198.00 (resistance / BB upper)
Upside: 7.2%
Downside Risk: 5.2%
Risk/Reward: 1.4
Entry Trigger: "Price holds above $178 for 2 sessions with declining volume"
Reassess In: 5 trading days
Reason: "Earnings beat on revenue but guidance cut 3%; market overreacted given 22x forward PE vs sector 28x"
```
→ You have a complete trade plan. Enter limit order, set stop, set alerts.

---

## Impact on Existing Features

| Feature | Impact | Notes |
|---------|--------|-------|
| Email notifications | Minor update | Include upside% and entry zone in email |
| Trade report CSV | Add columns | New fields in CSV export |
| Performance tracking | Better | Can now measure if stop_loss hit vs take_profit hit |
| Deep research | Align later | Phase 2: same schema for both prompts |
| Dashboard | Update | Show entry zone and risk/reward |
| Batch comparisons | Better | Compare by risk_reward_ratio instead of vague verdicts |

---

## Migration Strategy

1. **Add DB columns** with NULL defaults (non-breaking)
2. **Update PM prompt** (this proposal)
3. **Update return dict** in research_service.py
4. **Update stock_service.py** to persist new fields
5. **Keep old `score` field** for backwards compatibility (but stop using it for decisions)
6. **Update trade report CSV** to include new columns
7. **Phase 2**: Align deep research prompt to same schema
8. **Phase 3**: Build ML model on the new structured features

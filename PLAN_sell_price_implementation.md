# Sell Price Implementation Plan — Cursor Handoff

**Date:** 2026-02-12
**Status:** Ready for implementation

---

## What Already Exists (Done)

The Bull, Bear, and Fund Manager prompts have already been extended. The Fund Manager now outputs `sell_price_low`, `sell_price_high`, `ceiling_exit`, `exit_trigger` alongside the existing entry fields. The database schema and `update_decision_point` already accept these 4 columns. `stock_service.py` already passes them to the DB.

These changes live in:
- `app/services/research_service.py` — Bull prompt (SELL TARGET ESTIMATION section), Bear prompt (REALISTIC EXIT CEILING section), Fund Manager prompt (INSTRUCTIONS FOR SELL RANGE + 4 new JSON fields)
- `app/database.py` — 4 new columns in `new_columns` dict + `trading_fields` list
- `app/services/stock_service.py` — 4 new kwargs in the `update_decision_point()` call

**What's NOT done yet:** Plan A (Sell Council for owned positions) and Plan B (Deep Research + PM sell price output in their own DB columns).

---

## PLAN A — Sell Council: Reassess Owned Positions

### Purpose

A standalone Python function that takes ticker symbols from the DB, re-runs Council 1 sensors (Technical, News, Sentiment, Competitive, Seeking Alpha) to gather fresh evidence, and feeds that directly to a Deep Research agent with a sell-focused prompt. The output writes updated sell prices back to the DB.

### Trigger

A Python function callable from bash:

```bash
python -m scripts.reassess_positions          # all owned positions
python -m scripts.reassess_positions AAPL NVDA # specific tickers
```

### Architecture

```
[DB: status="Owned" positions]
        │
        ▼
[Council 1 Sensors — REUSED, run in parallel]
  ├── Technical Agent  (TradingView fresh indicators)
  ├── News Agent       (Benzinga/Polygon + Finnhub + Alpha Vantage)
  ├── Sentiment Agent  (Google Search Grounding)
  ├── Competitive Agent (Google Search Grounding)
  └── Seeking Alpha Agent
        │
        ▼
[Collected into a JSON context package]
        │
        ▼
[Deep Research Agent — sell-focused prompt]
  Model: deep-research-pro-preview-12-2025
  Task: Evaluate thesis status, recommend sell action, output updated sell range
        │
        ▼
[DB: write updated sell columns]
```

### File: `scripts/reassess_positions.py` (NEW)

This is the entry point. It should:

1. **Parse CLI args**: Accept optional ticker symbols. If none provided, query DB for all `status="Owned"` positions.

2. **For each ticker**, do the following (sequentially, one at a time to respect rate limits):

   a. **Fetch original decision from DB** — use `get_decision_points()` from `app/database.py` (line 272), filter by symbol. Extract:
      - `entry_price_low`, `entry_price_high`, `stop_loss`, `take_profit_1`, `take_profit_2`
      - `sell_price_low`, `sell_price_high`, `ceiling_exit` (current sell targets)
      - `price_at_decision` (original entry price)
      - `reasoning` (original buy thesis)
      - `recommendation`, `conviction`, `drop_type`
      - `id` (decision_id for DB update)

   b. **Fetch fresh technical indicators** — call `tradingview_service.get_technical_indicators(symbol, region)` (defined at `app/services/tradingview_service.py` line 497). This returns a dict with: `close`, `rsi`, `bb_lower`, `bb_upper`, `sma50`, `sma200`, `atr`, `high52`, `low52`, `macd`, `macd_signal`, `adx`, `volume`, etc.

   c. **Collect fresh news** — call the same news pipeline used in Council 1:
      - `benzinga_service.get_news(symbol)` → returns list of article dicts
      - `finnhub_service.get_company_news(symbol)` → returns news items
      - `alpha_vantage_service.get_news(symbol)` → returns news items

   d. **Run Council 1 Sensors in parallel** — reuse the exact pattern from `research_service.py` lines 96-138. Create a `MarketState`, build prompts using the existing prompt factory methods, execute with `concurrent.futures.ThreadPoolExecutor(max_workers=4)`:
      - Technical Agent: `_create_technical_agent_prompt(state, raw_data, drop_str)` → `_call_agent(prompt, "Technical Agent", state)`
      - News Agent: `_create_news_agent_prompt(state, raw_data, drop_str)` → `_call_agent(prompt, "News Agent", state)`
      - Sentiment Agent: `_call_market_sentiment_agent(ticker, state, raw_data)`
      - Competitive Agent: `_create_competitive_agent_prompt(state, drop_str)` → `_call_agent(prompt, "Competitive Landscape Agent", state)`
      - Seeking Alpha: `seeking_alpha_service.get_evidence(ticker)`

   e. **Collect sensor output into a JSON** — same as `state.reports` dict (line 202-209 in research_service.py):
      ```python
      sensor_data = {
          "technical": tech_report,
          "news": news_report,
          "market_sentiment": sentiment_report,
          "competitive": comp_report,
          "seeking_alpha": sa_report
      }
      ```

   f. **Build Deep Research context** — package it as:
      ```python
      context = {
          "original_decision": {
              "action": decision["recommendation"],
              "conviction": decision["conviction"],
              "entry_price_low": decision["entry_price_low"],
              "entry_price_high": decision["entry_price_high"],
              "stop_loss": decision["stop_loss"],
              "take_profit_1": decision["take_profit_1"],
              "take_profit_2": decision["take_profit_2"],
              "sell_price_low": decision["sell_price_low"],
              "sell_price_high": decision["sell_price_high"],
              "ceiling_exit": decision["ceiling_exit"],
              "reason": decision["reasoning"],
          },
          "current_price": fresh_technicals["close"],
          "performance_since_entry": f"{pct_change:+.2f}%",
          "sensor_reports": sensor_data,        # Council 1 output
          "technical_data": fresh_technicals,    # Raw indicators
          "raw_news": news_items,                # For paywalled sources
      }
      ```

   g. **Call Deep Research agent** — use `deep_research_service.execute_deep_research(symbol, context, decision_id)` (line 772 in deep_research_service.py). BUT: the prompt must be different. You need to add a new prompt constructor method (see below).

   h. **Write results to DB** — call `update_decision_point()` with the new sell fields.

### New Method in `deep_research_service.py`: `_construct_sell_reassessment_prompt(symbol, context)`

This is the sell-focused Deep Research prompt. Add it alongside the existing `_construct_prompt()` method (line 840). It should follow the same structure but with a sell focus:

```
You are a **Senior Sell-Side Analyst** at a hedge fund. You are reviewing an EXISTING
OWNED position to decide whether to HOLD, TAKE PARTIAL PROFITS, or EXIT FULLY.

POSITION CONTEXT:
- Ticker: {symbol}
- Original Entry: ${entry_price_low} - ${entry_price_high}
- Current Price: ${current_price} ({performance_since_entry})
- Current Stop Loss: ${stop_loss}
- Current Sell Zone: ${sell_price_low} - ${sell_price_high}
- Ceiling Exit: ${ceiling_exit}
- Original Buy Thesis: {reason}

FRESH COUNCIL SENSOR DATA (collected just now):
[Insert sensor_reports JSON]

FRESH TECHNICAL INDICATORS:
[Insert technical_data JSON]

RECENT NEWS:
[Insert raw_news]

YOUR TASK:
STEP 1: THESIS STATUS — Is the original buy thesis still INTACT, WEAKENING, or BROKEN?
  Use the fresh news and sentiment data. Search Google for any developments since the entry.
STEP 2: TECHNICAL PICTURE — Analyze current indicators. Is RSI overbought? Has price hit
  resistance (bb_upper, SMA50, SMA200)? Is volume supporting the move or declining?
STEP 3: UPDATED SELL RANGE — Recalculate sell_price_low, sell_price_high, ceiling_exit
  using fresh technicals. If thesis is weakening, lower targets. If intact with momentum, raise.
STEP 4: ACTION RECOMMENDATION — HOLD / SELL_PARTIAL / SELL_FULL / TIGHTEN_STOP
STEP 5: STOP LOSS UPDATE — Can only go UP (trailing stop). Never lower it.

OUTPUT FORMAT:
{
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
}
```

### New Method in `deep_research_service.py`: `execute_sell_reassessment(symbol, context, decision_id)`

This is a variant of `execute_deep_research()` (line 772) that:
1. Calls `_construct_sell_reassessment_prompt()` instead of `_construct_prompt()`
2. Uses the same API call pattern (same model, same polling)
3. Returns the parsed JSON result

### DB Update — New Columns for Reassessment Output

Add these columns to `database.py` in the `new_columns` dict (around line 86):

```python
# Sell reassessment fields (v1.1)
"reassess_sell_action": "TEXT",         # HOLD | SELL_PARTIAL | SELL_FULL | TIGHTEN_STOP
"reassess_thesis_status": "TEXT",       # INTACT | WEAKENING | BROKEN
"reassess_sell_price_low": "REAL",      # Updated sell zone low
"reassess_sell_price_high": "REAL",     # Updated sell zone high
"reassess_ceiling_exit": "REAL",        # Updated ceiling
"reassess_updated_stop_loss": "REAL",   # Trailing stop (only goes up)
"reassess_exit_trigger": "TEXT",        # Updated exit condition
"reassess_timestamp": "TEXT",           # When last reassessed
"reassess_reasoning": "TEXT",           # Action + thesis reasoning
```

Also add these to the `trading_fields` list in `update_decision_point()` (line 248).

The script should also update the MAIN sell columns (`sell_price_low`, `sell_price_high`, `ceiling_exit`, `exit_trigger`) so the dashboard always shows the latest sell targets. The `reassess_*` columns preserve the reassessment metadata.

### Stop Loss Logic

The stop loss can ONLY go up, never down. In the script:

```python
new_stop = result.get("updated_stop_loss")
current_stop = decision.get("stop_loss")
if new_stop and current_stop and new_stop > current_stop:
    update_kwargs["stop_loss"] = new_stop  # Raise it
# Otherwise: keep current stop_loss unchanged
```

### Rate Limiting

Deep Research has a 60-second cooldown (`self.cooldown_seconds = 60` in deep_research_service.py line 31). The script should `time.sleep(60)` between processing each ticker to respect this.

---

## PLAN B — Portfolio Manager + Deep Research Sell Price Output

### Purpose

The existing Portfolio Manager and Deep Research agents already run during the initial "Buy the Dip" analysis. They should ALSO output a sell price recommendation, stored in their own dedicated DB columns.

### Part B1: Portfolio Manager Sell Price (ALREADY DONE)

The Fund Manager prompt already outputs `sell_price_low`, `sell_price_high`, `ceiling_exit`, `exit_trigger` (this was implemented earlier). These already flow to the main `sell_price_low` / `sell_price_high` / `ceiling_exit` / `exit_trigger` columns in the DB via `stock_service.py`.

**No further work needed for the PM.**

### Part B2: Deep Research Sell Price (TODO)

The Deep Research agent (`deep_research_service.py`) runs after the PM for BUY signals. It already outputs its own independent trading levels (`deep_research_entry_low`, `deep_research_tp1`, etc.). It should also output sell range fields.

#### Step 1: Extend the Deep Research prompt

In `deep_research_service.py`, find `_construct_prompt()` (line 840). In the JSON output schema (around line 1017), add these fields after `reassess_in_days`:

```json
"sell_price_low": <number — conservative exit target, where to start taking profits>,
"sell_price_high": <number — optimistic exit target, where to fully exit>,
"ceiling_exit": <number — absolute max target beyond which gains unlikely>,
"exit_trigger": "String — specific condition for selling, e.g. 'RSI > 70 and price in $142-$148 zone'"
```

Also add instructions in the prompt body (in the STEP 3: VALIDATE TRADING LEVELS section, around line 998) telling the agent to also calculate sell range:

```
STEP 3b: CALCULATE SELL RANGE
Using your independent analysis, determine where to take profits:
- sell_price_low: Conservative exit (pre-drop price recovery or BB middle)
- sell_price_high: Optimistic exit (BB upper, SMA50, or SMA200 as resistance)
- ceiling_exit: Maximum target = min(52-week high, BB upper + 1×ATR)
- exit_trigger: Specific condition combining price level + technical signal
```

#### Step 2: Add Deep Research sell columns to database

In `database.py`, add to the `new_columns` dict (after line 103, near the other `deep_research_*` fields):

```python
# Deep Research sell range fields
"deep_research_sell_price_low": "REAL",
"deep_research_sell_price_high": "REAL",
"deep_research_ceiling_exit": "REAL",
"deep_research_exit_trigger": "TEXT",
```

#### Step 3: Update the field mapping in deep_research_service.py

In `_handle_completion()` (around line 455), and specifically in the `new_field_map` dict (line 386 in `database.py`'s `update_deep_research_data()`), add the mapping:

```python
"sell_price_low": "deep_research_sell_price_low",
"sell_price_high": "deep_research_sell_price_high",
"ceiling_exit": "deep_research_ceiling_exit",
"exit_trigger": "deep_research_exit_trigger",
```

#### Step 4: Apply sell range overrides

In `_apply_trading_level_overrides()` (deep_research_service.py, lines 503-565), add the sell fields to the override logic. When Deep Research outputs sell prices, they should overwrite the PM's sell prices in the MAIN columns (`sell_price_low`, `sell_price_high`, `ceiling_exit`, `exit_trigger`) — same pattern as how Deep Research already overwrites entry/stop/TP levels.

---

## Summary of DB Columns

| Column | Set By | When |
|--------|--------|------|
| `sell_price_low` | PM initially, Deep Research overrides, Sell Council updates | Always latest |
| `sell_price_high` | PM initially, Deep Research overrides, Sell Council updates | Always latest |
| `ceiling_exit` | PM initially, Deep Research overrides, Sell Council updates | Always latest |
| `exit_trigger` | PM initially, Deep Research overrides, Sell Council updates | Always latest |
| `deep_research_sell_price_low` | Deep Research only | At initial analysis |
| `deep_research_sell_price_high` | Deep Research only | At initial analysis |
| `deep_research_ceiling_exit` | Deep Research only | At initial analysis |
| `deep_research_exit_trigger` | Deep Research only | At initial analysis |
| `reassess_sell_action` | Sell Council only | At reassessment |
| `reassess_thesis_status` | Sell Council only | At reassessment |
| `reassess_sell_price_low` | Sell Council only | At reassessment |
| `reassess_sell_price_high` | Sell Council only | At reassessment |
| `reassess_ceiling_exit` | Sell Council only | At reassessment |
| `reassess_updated_stop_loss` | Sell Council only | At reassessment |
| `reassess_exit_trigger` | Sell Council only | At reassessment |
| `reassess_timestamp` | Sell Council only | At reassessment |
| `reassess_reasoning` | Sell Council only | At reassessment |

---

## Files to Create / Modify

### Plan A (Sell Council):
| File | Action | What |
|------|--------|------|
| `scripts/reassess_positions.py` | **CREATE** | CLI entry point, orchestrates the sell council |
| `app/services/deep_research_service.py` | **MODIFY** | Add `_construct_sell_reassessment_prompt()` + `execute_sell_reassessment()` |
| `app/database.py` | **MODIFY** | Add 9 reassess_* columns to schema + trading_fields |

### Plan B (Deep Research + PM sell output):
| File | Action | What |
|------|--------|------|
| `app/services/deep_research_service.py` | **MODIFY** | Extend prompt JSON schema + `_apply_trading_level_overrides()` |
| `app/database.py` | **MODIFY** | Add 4 deep_research_sell_* columns + field mapping |

### Already Done (no changes needed):
| File | Status |
|------|--------|
| `app/services/research_service.py` | Bull/Bear/FM prompts already extended with sell range |
| `app/services/stock_service.py` | Already passes sell fields to DB |

---

## Key Reference Points in Codebase

| What | File | Line |
|------|------|------|
| Council 1 parallel execution pattern | `research_service.py` | 96-138 |
| Council 1 output structure | `research_service.py` | 202-209 |
| Deep Research prompt template | `deep_research_service.py` | 840-1052 |
| Deep Research JSON output schema | `deep_research_service.py` | 1017-1051 |
| Deep Research API call pattern | `deep_research_service.py` | 772-838 |
| Deep Research DB field mapping | `database.py` | 386-403 |
| Deep Research trading level overrides | `deep_research_service.py` | 503-565 |
| `_build_deep_research_context()` | `stock_service.py` | 634-672 |
| `_should_trigger_deep_research()` | `stock_service.py` | 610-632 |
| `get_decision_points()` | `database.py` | 272 |
| `update_decision_point()` | `database.py` | 225-264 |
| `trading_fields` list | `database.py` | 244-248 |
| `new_columns` dict (schema) | `database.py` | 56-104 |
| TradingView indicators fetch | `tradingview_service.py` | 497 |
| Deep Research cooldown | `deep_research_service.py` | 31 (60 seconds) |
| Deep Research model | `deep_research_service.py` | 789 (`deep-research-pro-preview-12-2025`) |

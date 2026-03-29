# Selling Price Range Recommendation — Concept Document

**Version:** 0.1 — Draft
**Date:** 2026-02-12
**Scope:** Extend StockDrop's Fund Manager output with a dynamic selling price range, reusing the existing AI Council architecture, data sources, and technical indicator pipeline.

---

## 1. Problem Statement

StockDrop currently generates **entry-side trading levels** at the moment of analysis: `entry_price_low`, `entry_price_high`, `stop_loss`, `take_profit_1`, `take_profit_2`. These are static snapshots — once written to the database, they are never re-evaluated even though market conditions change daily.

What's missing is a **selling price range recommendation**: a continuously updated zone (low–high) where the system recommends taking profits, along with the reasoning and conviction behind it. This transforms StockDrop from a "buy signal generator" into a **complete trade lifecycle manager**.

---

## 2. Core Idea

Introduce a **Sell Range** — a price band `[sell_price_low, sell_price_high]` — for every position with status `Owned`. Unlike the static TP1/TP2 (which are set once at buy time), the sell range is **re-evaluated periodically** using fresh technical data, news, and a dedicated AI reassessment cycle.

Think of it as the selling counterpart to the buying range:

| Field | Buy Side (exists) | Sell Side (new) |
|---|---|---|
| Price band | `entry_price_low` — `entry_price_high` | `sell_price_low` — `sell_price_high` |
| Hard boundary | `stop_loss` (floor) | `ceiling_exit` (max optimism cap) |
| Conviction | `conviction` (at entry) | `sell_conviction` (current) |
| Trigger condition | `entry_trigger` | `exit_trigger` |
| Reassess timer | `reassess_in_days` | Same mechanism, reused |

---

## 3. Data Sources (All Existing — No New APIs)

The sell range calculation reuses the **same data pipeline** the buy side already collects. No new API keys or services are required.

| Source | Buy-Side Role | Sell-Side Role |
|---|---|---|
| **TradingView** (technicals) | BB lower, ATR, RSI for entry zone | BB upper, SMA50/200, RSI overbought for exit zone |
| **TradingView** (screener) | Detect >5% drops | Detect momentum shifts, volume divergence |
| **Benzinga / Polygon** (news) | Catalyst identification | Recovery confirmation or new risk emergence |
| **Seeking Alpha** | Analyst sentiment at entry | Updated analyst targets, rating changes |
| **Finnhub** (earnings) | Earnings proximity gate | Post-earnings re-evaluation trigger |
| **FRED** (macro) | Macro headwinds/tailwinds | Rate changes, CPI shifts affecting thesis |
| **yfinance** (history) | Price validation | Daily price vs. sell range monitoring |
| **Alpaca** (market data) | Real-time snapshots | Position tracking, current P&L |
| **Google Search Grounding** | Fact verification | Thesis drift detection (new catalysts) |

---

## 4. Method: The Sell Range Calculation

### 4.1 Technical Anchors (Mirroring the Buy Side)

The buy range is anchored to `bb_lower` and `close ± 1%`. The sell range mirrors this with **upper-band technicals**:

```
sell_price_low  = conservative exit  → typically bb_middle or pre_drop_price
sell_price_high = optimistic exit    → bb_upper, SMA50, SMA200, or 52-week high retracement
ceiling_exit    = maximum cap        → min(high52, bb_upper + 1×ATR)
```

The Fund Manager already has access to all of these fields (`bb_upper`, `bb_lower`, `sma50`, `sma200`, `high52`, `atr`). The sell range calculation is a natural extension of the existing prompt.

### 4.2 Sell Range Heuristics by Drop Type

Different drop types have different recovery profiles. The sell range should reflect this:

| Drop Type | sell_price_low (conservative) | sell_price_high (optimistic) | Rationale |
|---|---|---|---|
| `EARNINGS_MISS` | `pre_drop_price × 0.95` | `pre_drop_price × 1.02` | Earnings drops rarely fully recover in one quarter; target near pre-drop |
| `ANALYST_DOWNGRADE` | `bb_middle` | `pre_drop_price` | Sentiment-driven; recovers as new analysts weigh in |
| `SECTOR_ROTATION` | `pre_drop_price` | `sma50` or `sma200` | Company fundamentals intact; sector rotates back |
| `MACRO_SELLOFF` | `pre_drop_price` | `bb_upper` | Broad recovery when macro fear subsides |
| `COMPANY_SPECIFIC` | `entry_price_high + 1×ATR` | `bb_middle` | Uncertain recovery; tighter, more conservative range |
| `TECHNICAL_BREAKDOWN` | `bb_middle` | `bb_upper` | Purely technical; reverts to mean |

These are **starting heuristics** — the AI agent refines them using the full context.

### 4.3 Dynamic Adjustment Factors

The sell range isn't static. It shifts based on how the position evolves:

**Raise the range (more optimistic) when:**
- RSI is rising but still below 60 (momentum building, not yet overbought)
- Positive news flow confirmed via Benzinga / Google Search
- Volume increasing on up days (accumulation)
- Analyst upgrades appear on Seeking Alpha

**Lower the range (take profits earlier) when:**
- RSI approaching 70+ (overbought territory)
- Negative news emerging post-entry
- Earnings date approaching (uncertainty premium)
- Macro conditions deteriorating (FRED signals)
- Volume declining on up days (distribution)

---

## 5. Architecture: The Reassessment Cycle

### 5.1 Trigger: When to Reassess

The system already stores `reassess_in_days` (typically 3–10 trading days). This becomes the natural trigger:

```
Every 20-minute scan cycle (existing scheduler in main.py):
  For each decision with status = "Owned":
    If days_since_decision >= reassess_in_days:
      → Queue a Sell Reassessment
    If current_price >= take_profit_1:
      → Queue an Urgent Sell Reassessment
    If current_price <= stop_loss:
      → Queue a Stop-Loss Alert (no AI needed — hard exit)
```

### 5.2 The Sell Council (Reusing Phase 1–3)

The reassessment follows the **same three-phase architecture** as the buy-side, but with modified prompts:

**Phase 1 — Sensors (existing agents, sell-side prompts):**
- **Technical Agent**: Fresh indicators — has the stock hit resistance? Is RSI overbought? Has momentum stalled?
- **News Agent**: Any new catalysts since entry? Has the narrative changed?
- **Sentiment Agent**: Has market sentiment shifted? Is the "buy the dip" trade crowded?

These agents already exist and run in parallel. Only their prompt framing changes from "should we buy?" to "should we hold or sell?"

**Phase 2 — The Debate (reframed):**
- **Hold Advocate** (replaces Bull): "The recovery thesis is intact. Here's why we should keep holding."
- **Exit Advocate** (replaces Bear): "The easy gains are made. Here's why we should take profits now."

**Phase 3 — The Sell Manager (extends Fund Manager):**
The Fund Manager receives both cases and outputs a sell recommendation with the new fields.

### 5.3 Sell Manager Output Schema

```json
{
  "sell_action": "HOLD" | "SELL_PARTIAL" | "SELL_FULL" | "TIGHTEN_STOP",
  "sell_conviction": "HIGH" | "MODERATE" | "LOW",
  "sell_price_low": "<number — lower bound of recommended sell zone>",
  "sell_price_high": "<number — upper bound of recommended sell zone>",
  "ceiling_exit": "<number — absolute maximum target, beyond which gains are unlikely>",
  "updated_stop_loss": "<number — revised stop loss (can be raised, never lowered)>",
  "profit_taken_percent": "<number — for SELL_PARTIAL: recommended % of position to sell>",
  "exit_trigger": "<string — condition for execution, e.g. 'RSI crosses above 70' or 'Price enters $142-$148 zone'>",
  "thesis_status": "INTACT" | "WEAKENING" | "BROKEN",
  "next_reassess_in_days": "<number>",
  "reason": "<one sentence>",
  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"]
}
```

### 5.4 Sell Actions Explained

| Action | Meaning | When |
|---|---|---|
| `HOLD` | Keep full position, thesis intact | Recovery still in progress, sell range not reached |
| `SELL_PARTIAL` | Take partial profits | Price entered sell range but further upside possible |
| `SELL_FULL` | Exit entire position | Price reached sell_price_high or thesis broken |
| `TIGHTEN_STOP` | Raise stop-loss, hold position | Protecting gains; moved stop from loss-prevention to profit-protection |

---

## 6. Database Schema Extension

New columns for the `decision_points` table (mirrors existing PM trading-level fields):

```python
# Sell Range fields (v1.0)
"sell_price_low": "REAL",
"sell_price_high": "REAL",
"ceiling_exit": "REAL",
"sell_action": "TEXT",              # HOLD | SELL_PARTIAL | SELL_FULL | TIGHTEN_STOP
"sell_conviction": "TEXT",          # HIGH | MODERATE | LOW
"updated_stop_loss": "REAL",
"profit_taken_percent": "REAL",
"exit_trigger": "TEXT",
"thesis_status": "TEXT",            # INTACT | WEAKENING | BROKEN
"sell_reassessed_at": "TEXT",       # ISO timestamp of last reassessment
"sell_reason": "TEXT",
"sell_key_factors": "TEXT",         # JSON array stored as text
```

These follow the same pattern as the existing `entry_price_low`, `entry_price_high`, etc. — REAL for prices, TEXT for classifications.

---

## 7. Fund Manager Prompt Extension

The existing Fund Manager prompt (lines 835–934 of `research_service.py`) would gain a new section. The key addition to the prompt:

```
INSTRUCTIONS FOR SELL RANGE (only for reassessment of existing positions):
- **sell_price_low**: The conservative exit target. Where would you start scaling out?
  Use bb_middle (midpoint of bb_lower and bb_upper) or pre_drop_price as guides.
- **sell_price_high**: The optimistic exit target. Where does the recovery thesis max out?
  Use bb_upper, SMA50, SMA200, or high52 as guides — whichever represents realistic resistance.
- **ceiling_exit**: The absolute cap. Beyond this, further gains require a NEW catalyst.
  Typically: min(high52, bb_upper + 1×ATR).
- **updated_stop_loss**: Re-evaluate the stop loss. It can only go UP (trail higher), never down.
  If the position is profitable, raise stop to at least entry_price_low (breakeven protection).
- **thesis_status**: Has the original buy thesis changed?
  INTACT = original catalyst still valid, WEAKENING = mixed signals, BROKEN = sell regardless of price.
```

This mirrors the existing "INSTRUCTIONS FOR TRADING LEVELS" section in structure and style.

---

## 8. Notification Logic

Extending the existing email notification pattern (currently BUY-only):

```python
if sell_action == "SELL_FULL":
    email_service.send_sell_notification(...)    # New template: urgent
elif sell_action == "SELL_PARTIAL":
    email_service.send_sell_notification(...)    # New template: advisory
elif sell_action == "TIGHTEN_STOP":
    email_service.send_stop_update(...)          # New template: informational
elif thesis_status == "BROKEN":
    email_service.send_sell_notification(...)    # Override: urgent regardless of action
```

---

## 9. Example Walkthrough

**Day 0 — Buy Signal:**
StockDrop detects NVDA dropped -8%. The Council runs. Fund Manager says BUY at $112–$114, stop loss $104, TP1 $121, TP2 $128. Reassess in 5 days.

**Day 5 — First Reassessment:**
NVDA is now $119. The Sell Council runs:
- Technical Agent: RSI 58 (rising, not overbought), price approaching bb_middle ($121)
- News Agent: No new negative catalysts; positive AI spending news
- Hold Advocate: "Thesis intact, momentum building, hold for TP1"
- Exit Advocate: "Easy 6% gain, earnings in 12 days — take some off"

**Sell Manager Output:**
```json
{
  "sell_action": "HOLD",
  "sell_price_low": 121,
  "sell_price_high": 128,
  "ceiling_exit": 133,
  "updated_stop_loss": 114,
  "thesis_status": "INTACT",
  "exit_trigger": "Begin scaling out if RSI > 68 or price enters $121-$128 zone",
  "next_reassess_in_days": 3
}
```
Note: stop_loss raised from $104 → $114 (breakeven protection).

**Day 8 — Second Reassessment:**
NVDA is now $124. RSI at 66.

**Sell Manager Output:**
```json
{
  "sell_action": "SELL_PARTIAL",
  "sell_price_low": 122,
  "sell_price_high": 129,
  "profit_taken_percent": 50,
  "updated_stop_loss": 119,
  "thesis_status": "INTACT",
  "exit_trigger": "Sell 50% now in $122-$129 zone. Hold remainder for earnings catalyst.",
  "next_reassess_in_days": 2
}
```

---

## 10. Implementation Phases (Suggested)

**Phase A — Schema + Prompt (low effort):**
Add the new DB columns. Extend the Fund Manager prompt to output sell range fields alongside existing buy fields. This means every new analysis already includes a projected sell range from day one — no monitoring loop needed yet.

**Phase B — Reassessment Loop (medium effort):**
Add a scan in the existing 20-minute scheduler that checks `Owned` positions against `reassess_in_days`. When triggered, run a lightweight version of the Council (Technical + News sensors only, Hold/Exit debate, Sell Manager verdict).

**Phase C — Notifications + Dashboard (medium effort):**
Add email templates for sell signals. Extend the existing web dashboard views to show the sell range alongside entry levels.

**Phase D — Trailing Stop Automation (optional, higher effort):**
Use Alpaca API to submit/update stop-loss orders automatically when `TIGHTEN_STOP` is issued. This is the only phase that touches execution.

---

## 11. Design Principles

1. **Reuse, don't rebuild.** Every data source, every agent, every prompt pattern already exists. The sell side is a reframing, not a rewrite.
2. **AI-generated, not formula-driven.** Just like the buy range, the sell range is a Fund Manager judgment call informed by technicals — not a rigid formula. This preserves the Council's ability to weigh context.
3. **Stop loss only goes up.** Once a position is profitable, the trailing stop protects gains. This is the one hard rule the AI cannot override.
4. **Thesis-first exit logic.** The sell range is secondary to thesis status. If the thesis is BROKEN, the recommendation is SELL_FULL regardless of where the price sits relative to the sell range.
5. **Same cadence, same infrastructure.** The reassessment runs inside the existing 20-minute scan cycle and uses the same Gemini model pipeline. No new background workers needed.

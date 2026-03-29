# Plan: Stock Trading Decision Analysis Script (2026)

## 1. Overview

A Python script to analyze stock trading decisions from **January 1, 2026 onward**, using the v0.9+ nomenclature and providing structured readouts for:
1. **Council readout** — Phase 1 agents + Phase 2 Bull/Bear/Risk synthesis
2. **Deep research readout** — Senior reviewer verification, blindspots, refined levels

---

## 2. 2026 Nomenclature

### 2.1 Action Values (Primary)

| Value | Meaning | Legacy Equivalents |
|-------|---------|--------------------|
| `BUY` | Immediate entry at current price | STRONG BUY |
| `BUY_LIMIT` | Limit order at entry zone | SPECULATIVE BUY |
| `WATCH` | Add to watchlist, wait for trigger | HOLD |
| `AVOID` | Do not trade | HOLD, SELL, HARD_AVOID |

### 2.2 Supporting Fields

- **conviction**: `HIGH` | `MODERATE` | `LOW`
- **drop_type**: `EARNINGS_MISS` | `ANALYST_DOWNGRADE` | `SECTOR_ROTATION` | `MACRO_SELLOFF` | `COMPANY_SPECIFIC` | `TECHNICAL_BREAKDOWN` | `UNKNOWN`
- **Trading levels**: `entry_price_low`, `entry_price_high`, `stop_loss`, `take_profit_1`, `take_profit_2`, `sell_price_low`, `sell_price_high`, `ceiling_exit`, `exit_trigger`
- **Risk metrics**: `upside_percent`, `downside_risk_percent`, `risk_reward_ratio`

### 2.3 Deep Research Outputs

- **review_verdict**: `CONFIRMED` | `UPGRADED` | `ADJUSTED` | `OVERRIDDEN`
- Same action schema as council: `BUY` | `BUY_LIMIT` | `WATCH` | `AVOID`
- Additional: `deep_research_verification`, `deep_research_blindspots`, `deep_research_reason`

---

## 3. Data Sources

| Source | Location | Content |
|--------|----------|---------|
| Decision points | `decision_points` table (SQLite) | PM recommendation, conviction, trading levels, deep_research_* |
| Council Phase 1 | `data/council_reports/{symbol}_{date}_council1.json` | technical, news, market_sentiment, economics, competitive, seeking_alpha |
| Council Phase 2 | `data/council_reports/{symbol}_{date}_council2.json` | bull, bear, risk (superset of council1) |

---

## 4. Script Architecture

### 4.1 Entry Point & Date Filter

- **Cutoff date**: `2026-01-01`
- Query: `SELECT * FROM decision_points WHERE date(timestamp) >= '2026-01-01' ORDER BY timestamp DESC`
- Optional CLI args: `--since 2026-01-01`, `--until 2026-02-13`, `--symbol AAPL`

### 4.2 Output Formats

| Mode | Description | Output |
|------|-------------|--------|
| `summary` | Aggregate stats by action, conviction, drop_type | Terminal table + optional CSV |
| `council` | Per-decision council readout | Markdown/JSON |
| `deep_research` | Per-decision deep research readout | Markdown/JSON |
| `full` | Both council + deep research for each decision | Report file |

---

## 5. Council Readout Structure

For each decision, when `data/council_reports/{symbol}_{date}_council2.json` (or council1) exists:

### 5.1 Phase 1 (Sensors)

- **Technical** — RSI, support levels, trend
- **News** — Why is the stock down? Structural vs temporary
- **Market Sentiment** — Google-grounding pulse
- **Economics** (if triggered) — FRED macro data
- **Competitive** — Company vs sector
- **Seeking Alpha** — Contrarian viewpoints

### 5.2 Phase 2 (Debate)

- **Bull case** — Value investor thesis
- **Bear case** — Forensic bear argument
- **Risk** — Risk flags + PM decision context

### 5.3 Council Summary Fields (from DB)

- `recommendation`, `conviction`, `drop_type`
- `entry_price_low`, `entry_price_high`, `stop_loss`, `take_profit_1`, `take_profit_2`
- `reasoning` (executive summary)

---

## 6. Deep Research Readout Structure

For each decision with `deep_research_*` columns populated:

### 6.1 Review Outcome

- **review_verdict**: CONFIRMED / UPGRADED / ADJUSTED / OVERRIDDEN
- **action** (if overridden): New recommendation vs council

### 6.2 Trading Level Adjustments

| Council | Deep Research |
|---------|---------------|
| entry_price_low/high | deep_research_entry_low, deep_research_entry_high |
| stop_loss | deep_research_stop_loss |
| take_profit_1/2 | deep_research_tp1, deep_research_tp2 |
| — | deep_research_sell_price_low, deep_research_sell_price_high, deep_research_ceiling_exit |

### 6.3 Qualitative Readout

- **verification_results** — Claim 1: [VERIFIED/DISPUTED] — explanation
- **council_blindspots** — Issues the council missed
- **reason** — Senior reviewer’s final assessment
- **knife_catch_warning** — Boolean flag

---

## 7. Script Modules (Proposed)

```
scripts/analysis/
├── analyze_stock_decisions.py   # Main entry point
├── decision_loader.py           # DB + council file loading
├── council_readout.py           # Council report formatting
├── deep_research_readout.py     # Deep research formatting
└── report_writer.py             # Output (markdown, CSV, JSON)
```

### 7.1 `decision_loader.py`

- `load_decisions_since(date_str, until=None, symbol=None)` → list of dicts
- `load_council_report(symbol, date_str)` → dict or None
- Handles missing council files gracefully

### 7.2 `council_readout.py`

- `format_council_readout(decision, council_data)` → structured dict / markdown
- Sections: Phase 1 summary, Phase 2 (Bull/Bear/Risk), PM decision

### 7.3 `deep_research_readout.py`

- `format_deep_research_readout(decision)` → structured dict / markdown
- Sections: review_verdict, action vs council, level adjustments, verification, blindspots, reason

### 7.4 `analyze_stock_decisions.py`

- CLI via argparse
- Modes: `summary | council | deep_research | full`
- Aggregation stats for `summary`
- Per-decision reports for `council`, `deep_research`, `full`

---

## 8. Example Outputs

### 8.1 Summary Mode (Example)

```
Stock Trading Decision Analysis (2026-01-01 → 2026-02-13)
========================================================
Total decisions: 47

By Action:
  BUY         : 12 (25.5%)
  BUY_LIMIT   : 18 (38.3%)
  WATCH       : 11 (23.4%)
  AVOID       : 6 (12.8%)

By Conviction:
  HIGH        : 8
  MODERATE    : 22
  LOW         : 17

By Drop Type:
  EARNINGS_MISS       : 9
  SECTOR_ROTATION     : 7
  MACRO_SELLOFF       : 6
  ...
```

### 8.2 Council Readout (Per Decision)

```
--- Council Readout: AAPL 2026-01-15 ---
Phase 1: technical, news, sentiment, competitive, seeking_alpha
Phase 2: Bull case (excerpt), Bear case (excerpt), Risk flags
PM Decision: BUY_LIMIT | conviction=MODERATE | drop_type=EARNINGS_MISS
Entry: $142.50–$145.00 | Stop: $138 | TP1: $161 | TP2: $170
```

### 8.3 Deep Research Readout (Per Decision)

```
--- Deep Research: AAPL 2026-01-15 ---
Review Verdict: ADJUSTED
Action: BUY_LIMIT (unchanged)
Level Changes: stop_loss $138 → $136 (widened), TP1 $161 → $158
Verification: [Claim 1: VERIFIED], [Claim 2: DISPUTED — ...]
Blindspots: Council underweighted macro headwinds.
Reason: Thesis intact; adjusted stop for volatility.
```

---

## 9. Implementation Order

1. **Phase 1**: `decision_loader.py` + date filter + basic `summary` mode
2. **Phase 2**: `council_readout.py` + `council` mode
3. **Phase 3**: `deep_research_readout.py` + `deep_research` mode
4. **Phase 4**: `full` mode + report file output + CSV export
5. **Phase 5**: Optional performance metrics (1W, 2W returns vs recommendation) — can reuse `generate_trade_report.py` logic

---

## 10. Dependencies

- Existing: `app.database`, `sqlite3`, `pandas`, `json`, `os`
- No new packages required
- DB path: `os.getenv("DB_PATH", "subscribers.db")`

---

## 11. Files to Create/Modify

| File | Action |
|------|--------|
| `scripts/analysis/analyze_stock_decisions.py` | Create |
| `scripts/analysis/decision_loader.py` | Create |
| `scripts/analysis/council_readout.py` | Create |
| `scripts/analysis/deep_research_readout.py` | Create |
| `scripts/analysis/report_writer.py` | Create (optional, can inline) |
| `PLAN_analysis_script_stock_decisions.md` | This document |

---

## 12. Backward Compatibility

- For decisions before 2026 with legacy `recommendation` (e.g. "STRONG BUY", "HOLD"):
  - Map in analysis: `STRONG BUY` → treat as `BUY`, `HOLD` → treat as `WATCH`
  - Or exclude pre-2026 from default analysis (configurable)

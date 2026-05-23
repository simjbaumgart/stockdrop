# 2026-05-23 Token Usage Tracking — Design

## Context

The pipeline runs ~10 Gemini calls per ticker (5 sensors + 3 debate agents + PM + deep research) and there is currently no way to answer three basic questions:

1. **Per single run** — what did one ticker cost end-to-end?
2. **Per full day** — what did today's screening session cost?
3. **Per agent** — which agents are token-heavy / cost-heavy across runs?

Today only the News Agent has any persisted token data, and only inside the `news_shadow_runs` A/B table. Per-call extraction exists in `app/services/research_service.py:1828-1829` (`prompt_token_count`, `candidates_token_count` go into a `metrics_sink` dict) but is not written to the DB.

Goal: capture every successful Gemini call to a new table, denormalize per-run totals onto `decision_points`, and ship it as DB-only — no dashboard / report / email surface in this iteration.

## Storage

### New table `agent_token_usage`

One row per LLM API call.

```
id              INTEGER PRIMARY KEY AUTOINCREMENT
decision_id     INTEGER NOT NULL  FK → decision_points.id
ticker          TEXT    NOT NULL  -- denormalized for fast filtering
run_date        TEXT    NOT NULL  -- denormalized YYYY-MM-DD for daily rollups
stage           TEXT    NOT NULL  -- 'sensor' | 'debate' | 'pm' | 'deep_research'
agent_name      TEXT    NOT NULL  -- see fixed enum below
model           TEXT    NOT NULL  -- e.g. 'gemini-3.1-pro-preview'
tokens_in       INTEGER NOT NULL
tokens_out      INTEGER NOT NULL
cost_usd        REAL              -- NULL if model is not in the pricing table
created_at      TEXT    NOT NULL
```

Indexes:

- `(decision_id)` — per-run lookups
- `(run_date)` — daily rollups
- `(agent_name, run_date)` — per-agent trend queries

### Stable `agent_name` enum

These values are **immutable once shipped**. Renaming silently breaks every historical per-agent trend query.

| stage          | agent_name              |
|----------------|-------------------------|
| sensor         | sensor_technical        |
| sensor         | sensor_news             |
| sensor         | sensor_market_sentiment |
| sensor         | sensor_competitive      |
| sensor         | sensor_seeking_alpha    |
| debate         | debate_bull             |
| debate         | debate_bear             |
| debate         | debate_risk             |
| pm             | pm                      |
| deep_research  | deep_research           |

No `gatekeeper` entry. Gatekeeper is deterministic (Bollinger %B + SPY/SMA200) and makes no LLM calls. If that ever changes, add `gatekeeper` to the enum then.

### Denormalized totals on `decision_points`

New columns:

```
total_tokens_in   INTEGER
total_tokens_out  INTEGER
total_cost_usd    REAL
total_llm_calls   INTEGER
```

These are **derived** — `agent_token_usage` is the source of truth. The denormalized totals are always recomputable by re-running the rollup query against the source table. If the pipeline crashes between calls and the rollup, the four columns stay stale but no data is lost.

### `decision_points.status` column (also new)

```
status   TEXT NOT NULL DEFAULT 'complete'
         -- 'in_progress' | 'complete' | 'failed' | 'gated_out'
         -- Default is 'complete' so existing rows backfill cleanly.
         -- New stub inserts at pipeline start explicitly write 'in_progress'.
```

Needed because the row is now inserted as a stub at pipeline start (see "decision_id ordering" below) and will sometimes never reach PM. Reports filter by `status = 'complete'` for valid recommendation data, but `agent_token_usage` joins on all statuses — partial runs still consumed tokens and should appear in cost totals.

## decision_id ordering — stub-first

**The decision_points row is inserted at the very start of the pipeline, before gatekeeper.** It contains:

```
id          (autoincrement)
ticker
run_date
created_at
status      'in_progress'
-- all other columns NULL / default
```

Every downstream stage receives `decision_id` and writes `agent_token_usage` rows against a valid FK from its first call onward. At pipeline end the existing decision-write logic does an `UPDATE` instead of an `INSERT` to fill in verdicts, prices, etc. Final status transitions to `complete` (or `failed` / `gated_out`).

Alternative considered: buffer token records in memory and bulk-insert at run end. Rejected — buffering loses data on crash, and stub-insert is a one-line change at a single chokepoint.

## Thread safety

The 5 sensors run in a `ThreadPoolExecutor(max_workers=8)` and the 3 debate agents in another `ThreadPoolExecutor(max_workers=6)`. All can call `record_llm_call` concurrently.

**Strategy: per-call short-lived connection.** `record_llm_call` opens its own SQLite connection, does a single INSERT, closes. No shared connection, no shared cursor.

**Enable WAL mode** on `subscribers.db` at startup if not already on:

```python
conn.execute("PRAGMA journal_mode=WAL")
```

WAL allows concurrent readers and serializes writes via the WAL file rather than locking the whole DB. With per-call INSERTs from up to 8 sensor threads + 6 debate threads, write contention is bounded and short.

The run-end rollup that updates `decision_points.total_*` runs on the main async path **after** all agent futures resolve via `executor.map(...)`. No contention with sensor/debate threads.

## Pricing

### Module `app/services/token_pricing.py`

```python
"""
Gemini pricing for token cost computation.

UNVERIFIED — values below must be confirmed against Google's current
Gemini 3 rate card before the cost numbers in agent_token_usage can be
trusted. Do NOT copy stale values from scripts/analysis/news_shadow_report.py.

Unit convention: USD per 1,000,000 tokens (matches Google's published format).
"""

# USD per 1M tokens
GEMINI_PRICING = {
    "gemini-3-pro-preview":      {"in": 0.00, "out": 0.00},  # TODO verify
    "gemini-3.1-pro-preview":    {"in": 0.00, "out": 0.00},  # TODO verify
    "gemini-3-flash-preview":    {"in": 0.00, "out": 0.00},  # TODO verify
    "deep-research-pro":         {"in": 0.00, "out": 0.00},  # TODO verify (if separate)
}

def compute_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    rates = GEMINI_PRICING.get(model)
    if rates is None:
        logger.warning("Unknown model for cost computation: %s", model)
        return None
    return (tokens_in / 1_000_000) * rates["in"] + (tokens_out / 1_000_000) * rates["out"]
```

Unknown model → `cost_usd = NULL` (not 0). `SUM(cost_usd)` will then visibly under-report by however many NULL rows exist, forcing the pricing table to be filled in.

**Until rates are verified and filled in**, every known-model row will compute to `cost_usd = 0.0` (0.00 × anything = 0). That is the placeholder state, not a bug. Cost numbers should be ignored until the TODOs in `GEMINI_PRICING` are replaced with real rates.

### Frozen cost — deliberate trade-off

`cost_usd` is computed at insert time using whatever rates are in `GEMINI_PRICING` then, and stored on the row forever.

- **Pro:** reports show what we actually paid; numbers are deterministic.
- **Con:** cannot retroactively reprice (e.g. to back out a Google rate cut, or to model "what if we'd used Flash for sensors").

Raw `tokens_in` / `tokens_out` are preserved on every row, so a future versioned pricing table could recompute historical cost. We are choosing not to build that now.

## Instrumentation

### Helper `app/services/token_tracker.py`

```python
def record_llm_call(
    decision_id: int,
    ticker: str,
    run_date: str,
    stage: str,
    agent_name: str,
    model: str,
    usage_metadata,   # Gemini response.usage_metadata
) -> None:
    tokens_in = (usage_metadata.prompt_token_count or 0)
    tokens_out = (usage_metadata.candidates_token_count or 0)
    cost = compute_cost(model, tokens_in, tokens_out)
    # Per-call short-lived sqlite3 connection; single INSERT; close.
```

Called immediately after every Gemini response in:

- `app/services/research_service.py` — 5 sensors, 3 debate agents, PM
- `app/services/deep_research_service.py` — deep research reviewer

The existing `metrics_sink` plumbing in `research_service.py:1828-1829` already pulls the two counts out of `usage_metadata`; we extend rather than replace it — `record_llm_call` is invoked alongside the existing sink write.

### Run-end rollup

After all agents complete (after PM + deep research, before the existing decision_points UPDATE):

```sql
UPDATE decision_points
SET total_tokens_in   = (SELECT SUM(tokens_in)   FROM agent_token_usage WHERE decision_id = ?),
    total_tokens_out  = (SELECT SUM(tokens_out)  FROM agent_token_usage WHERE decision_id = ?),
    total_cost_usd    = (SELECT SUM(cost_usd)    FROM agent_token_usage WHERE decision_id = ?),
    total_llm_calls   = (SELECT COUNT(*)         FROM agent_token_usage WHERE decision_id = ?),
    status            = 'complete'
WHERE id = ?
```

Idempotent — safe to re-run.

## Query patterns this enables

- **Per single run totals:** `SELECT total_tokens_in, total_tokens_out, total_cost_usd, total_llm_calls FROM decision_points WHERE id = ?`
- **Per agent for one run:** `SELECT agent_name, tokens_in, tokens_out, cost_usd FROM agent_token_usage WHERE decision_id = ? ORDER BY stage, agent_name`
- **Full day rollup:** `SELECT SUM(cost_usd), SUM(tokens_in+tokens_out), COUNT(DISTINCT decision_id) FROM agent_token_usage WHERE run_date = '2026-05-23'`
- **Per-agent evaluation:** `SELECT agent_name, AVG(tokens_in), AVG(tokens_out), AVG(cost_usd), COUNT(*) FROM agent_token_usage WHERE run_date >= '2026-05-01' GROUP BY agent_name ORDER BY AVG(cost_usd) DESC`
- **Efficiency drift:** `SELECT run_date, agent_name, AVG(tokens_out) FROM agent_token_usage GROUP BY run_date, agent_name`

## Migration

`app/database.py` already runs migrations at startup. Add:

1. `CREATE TABLE IF NOT EXISTS agent_token_usage (...)` plus three indexes.
2. `ALTER TABLE decision_points ADD COLUMN total_tokens_in INTEGER` (and the other three).
3. `ALTER TABLE decision_points ADD COLUMN status TEXT NOT NULL DEFAULT 'complete'` — existing rows are stamped `'complete'` since the pipeline was end-to-end before this change; new rows start as `'in_progress'`.
4. `PRAGMA journal_mode=WAL` (if not already set).

`decision_points` already has 40+ columns with migration history; follow the existing pattern in `app/database.py`.

## Out of scope (acknowledged under-reporting)

Each item below means **the cost numbers in this table will be lower than the Google Cloud bill**. Listed in rough order of expected magnitude.

- **Google Search grounding cost for Deep Research.** DR uses grounding which is billed per query, not per token. Plausibly the single largest cost line in the system. DR cost in this table is a **lower bound — text tokens only**; expect the true number to be meaningfully higher.
- **News shadow service spend.** `news_shadow_runs` continues to record its own shadow A/B Gemini calls, but those rows are NOT replicated into `agent_token_usage`. Daily totals will under-report by the shadow model's full spend (≈ a second news-sensor call per ticker).
- **Retries and failed calls.** Only the successful final attempt is recorded. A retry storm consumes tokens that stay invisible. Same defect as current code; not fixed here.
- **Cached input tokens.** `cached_content_token_count` is counted as normal input. Prompt-cache wins won't be visible as a separate line.
- **Latency.** `metrics_sink` already captures `latency_ms` but it is not persisted.
- **Dashboard / report / email surface.** DB-only by design. Future iteration if/when cost becomes interesting enough to monitor passively.

## Non-goals

- No retroactive backfill from existing logs.
- No re-architecting of the existing `metrics_sink` dict — it stays; `record_llm_call` runs alongside it.
- No changes to `news_shadow_runs`.
- No alerts, no budget caps, no rate-limiting based on cost.

## Risks

- **Pricing table drift.** If Google changes Gemini rates and the table isn't updated, costs silently diverge from the bill. Mitigation: NULL-on-unknown surfaces missing rates; periodic manual reconciliation against the actual bill.
- **Write throughput.** 14+ concurrent per-call INSERTs per ticker, with multiple tickers in flight. Per-call connections + WAL should handle it; if write latency becomes visible, switch to a single writer thread fed by a queue.
- **Stub `decision_points` rows.** Any external code that assumes a row in `decision_points` represents a completed analysis will see `in_progress` / `failed` / `gated_out` rows. Anything reporting on recommendation quality must filter on `status = 'complete'`.

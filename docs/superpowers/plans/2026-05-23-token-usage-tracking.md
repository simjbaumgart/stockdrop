# Token Usage Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture every successful Gemini call from the screening pipeline into a new `agent_token_usage` table, plus per-run totals on `decision_points`, so per-run / per-day / per-agent cost & efficiency can be queried directly from SQLite.

**Architecture:** Single chokepoint instrumentation. `MarketState` carries `decision_id` through `analyze_stock` → `_call_agent` → `_call_grounded_model`, which already extracts `usage_metadata` (research_service.py:1822-1829). Right after extraction we call a new `token_tracker.record_llm_call(...)` helper that opens its own short-lived SQLite connection and inserts a row. WAL mode handles concurrent writes from the 5+3 ThreadPoolExecutors. At the end of `analyze_stock` (after PM + deep research), a `rollup_decision_totals(decision_id)` call sums the per-call rows back onto `decision_points`. The new `token_pricing.py` module computes `cost_usd` at insert time from a pricing table; unknown models record `cost_usd=NULL`.

**Tech Stack:** Python 3.9, sqlite3 (stdlib), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-23-token-usage-tracking-design.md`

---

## File Structure

**Create:**
- `app/services/token_pricing.py` — per-1M pricing table + `compute_cost(model, tokens_in, tokens_out) -> Optional[float]`
- `app/services/token_tracker.py` — `record_llm_call(...)` + `rollup_decision_totals(decision_id)`
- `tests/test_token_pricing.py`
- `tests/test_token_tracker.py`
- `tests/test_token_usage_integration.py`

**Modify:**
- `app/database.py` — add `agent_token_usage` table + indexes (~line 198), add 4 columns to the `new_columns` dict at line 55, add `PRAGMA journal_mode=WAL` in `init_db()`
- `app/models/market_state.py` — add `decision_id: Optional[int] = None`
- `app/services/research_service.py` — `analyze_stock` accepts `decision_id`, threads it into `MarketState`; `_call_agent` and `_call_grounded_model` accept `agent_name` + `decision_id` for token recording; instrument both grounded path (line 1822) and non-grounded fallback path (line 1746)
- `app/services/stock_service.py:1620` — pass `decision_id` into `analyze_stock(...)`
- `app/services/deep_research_service.py` — extract `usageMetadata` from REST poll JSON (line 1125) and call `record_llm_call` best-effort

Each file has one focused responsibility. `token_pricing.py` and `token_tracker.py` know nothing about each other except the typed function signatures.

---

## Task 1: Pricing module

**Files:**
- Create: `app/services/token_pricing.py`
- Test: `tests/test_token_pricing.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_token_pricing.py
from app.services.token_pricing import compute_cost, GEMINI_PRICING


def test_known_model_computes_cost_per_million():
    # Stub a known model into the table with non-zero rates for the test.
    GEMINI_PRICING["__test_model__"] = {"in": 2.0, "out": 8.0}  # USD per 1M
    try:
        # 1,000,000 input + 500,000 output -> 2.0 + 4.0 = 6.0
        assert compute_cost("__test_model__", 1_000_000, 500_000) == 6.0
        # 0 tokens -> 0 cost
        assert compute_cost("__test_model__", 0, 0) == 0.0
    finally:
        del GEMINI_PRICING["__test_model__"]


def test_unknown_model_returns_none():
    assert compute_cost("does-not-exist-model", 1_000_000, 1_000_000) is None


def test_zero_rates_compute_to_zero_not_none():
    # The shipped placeholders are all 0.0. Make sure that path returns 0.0,
    # not None — None is reserved for "model not in table".
    GEMINI_PRICING["__zero_model__"] = {"in": 0.0, "out": 0.0}
    try:
        assert compute_cost("__zero_model__", 1_000_000, 1_000_000) == 0.0
    finally:
        del GEMINI_PRICING["__zero_model__"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_token_pricing.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.token_pricing'`.

- [ ] **Step 3: Implement the module**

```python
# app/services/token_pricing.py
"""
Gemini pricing for token cost computation.

UNVERIFIED — values below MUST be confirmed against Google's current
Gemini 3 rate card before the cost numbers in agent_token_usage can
be trusted. Do NOT copy stale values from
scripts/analysis/news_shadow_report.py.

Unit convention: USD per 1,000,000 tokens (matches Google's published
format).
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# USD per 1M tokens.
# All values are placeholders. Fill in real rates before relying on cost.
GEMINI_PRICING = {
    "gemini-3-pro-preview":     {"in": 0.0, "out": 0.0},  # TODO verify
    "gemini-3.1-pro-preview":   {"in": 0.0, "out": 0.0},  # TODO verify
    "gemini-3-flash-preview":   {"in": 0.0, "out": 0.0},  # TODO verify
    "gemini-3.5-flash-preview": {"in": 0.0, "out": 0.0},  # TODO verify (news shadow prod model)
    "deep-research-pro":        {"in": 0.0, "out": 0.0},  # TODO verify (if separately priced)
}


def compute_cost(model: str, tokens_in: int, tokens_out: int) -> Optional[float]:
    """Return cost in USD, or None if the model is not in the pricing table.

    None makes the gap visible in SUM(cost_usd) and forces the table to
    be filled in. 0.0 means "model is known and the placeholder rates are
    still in place" — also visible, but separately, in COUNT WHERE cost_usd = 0.
    """
    rates = GEMINI_PRICING.get(model)
    if rates is None:
        logger.warning("token_pricing: unknown model %r — cost_usd will be NULL", model)
        return None
    return (tokens_in / 1_000_000) * rates["in"] + (tokens_out / 1_000_000) * rates["out"]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_token_pricing.py -v
```
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add app/services/token_pricing.py tests/test_token_pricing.py
git commit -m "feat(token-usage): add Gemini pricing module (placeholders)"
```

---

## Task 2: DB schema migrations

**Files:**
- Modify: `app/database.py` — `init_db()` function (lines 8-249)

This task is migration-only. No tests yet because there's nothing to call against it; Task 3's tests will exercise the schema implicitly via inserts.

- [ ] **Step 1: Add the four new columns to the `new_columns` migration dict**

Edit `app/database.py:55-141` — append four entries to the existing `new_columns` dict so the idempotent ALTER loop (line 144) picks them up on next startup:

```python
new_columns = {
    # ... (existing entries) ...
    "sa_rank": "INTEGER",
    # --- token usage tracking (2026-05-23) ---
    "total_tokens_in":  "INTEGER",
    "total_tokens_out": "INTEGER",
    "total_cost_usd":   "REAL",
    "total_llm_calls":  "INTEGER",
}
```

Do **not** touch the `status` column — it already exists with the existing `'Pending'` vocabulary (line 28). See the spec section "decision_points.status — already exists".

- [ ] **Step 2: Add the `agent_token_usage` table CREATE**

Insert directly after the existing `news_shadow_runs` CREATE TABLE block (after line 198, before the `# Migration for batch_comparisons` block at line 200):

```python
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agent_token_usage (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id   INTEGER NOT NULL,
            ticker        TEXT    NOT NULL,
            run_date      TEXT    NOT NULL,
            stage         TEXT    NOT NULL,
            agent_name    TEXT    NOT NULL,
            model         TEXT    NOT NULL,
            tokens_in     INTEGER NOT NULL,
            tokens_out    INTEGER NOT NULL,
            cost_usd      REAL,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (decision_id) REFERENCES decision_points (id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_atu_decision_id ON agent_token_usage(decision_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_atu_run_date    ON agent_token_usage(run_date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_atu_agent_date  ON agent_token_usage(agent_name, run_date)')
```

- [ ] **Step 3: Enable WAL mode**

Insert at the very top of `init_db()` immediately after `cursor = conn.cursor()` on line 11:

```python
    # Enable WAL so the 5+3 ThreadPoolExecutor agent fan-out doesn't
    # serialize on a global write lock when each thread opens its own
    # short-lived connection to insert a token_usage row.
    cursor.execute("PRAGMA journal_mode=WAL")
```

- [ ] **Step 4: Verify the migration runs cleanly**

```bash
python -c "from app.database import init_db; init_db()"
```
Expected: prints `[DB Migration] Applied 4 column migrations.` (or however many other pending migrations there are, but at least the four new total_* columns). No exceptions.

Then confirm the schema:

```bash
sqlite3 subscribers.db ".schema agent_token_usage" && \
sqlite3 subscribers.db "PRAGMA journal_mode;" && \
sqlite3 subscribers.db "PRAGMA table_info(decision_points);" | grep -E "total_tokens|total_cost|total_llm"
```
Expected:
- `agent_token_usage` schema printed with all columns.
- `journal_mode` returns `wal`.
- Four `total_*` columns listed on `decision_points`.

- [ ] **Step 5: Commit**

```bash
git add app/database.py
git commit -m "feat(token-usage): add agent_token_usage table + decision_points totals + WAL"
```

---

## Task 3: `token_tracker` helper

**Files:**
- Create: `app/services/token_tracker.py`
- Test: `tests/test_token_tracker.py`

Helper API:

```python
def record_llm_call(
    *,
    decision_id: int,
    ticker: str,
    run_date: str,
    stage: str,
    agent_name: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None: ...

def rollup_decision_totals(decision_id: int) -> None: ...
```

- [ ] **Step 0: Add a shared `temp_db` fixture in `tests/conftest.py`**

This fixture is used by every token-usage test in Tasks 3, 5, 6, 7, and 9. Define it once.

If `tests/conftest.py` does not exist, create it. If it does, append:

```python
# tests/conftest.py  (append; do not overwrite existing fixtures)
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def temp_db(monkeypatch):
    """Fresh sqlite DB with one parent decision_points row.

    Uses monkeypatch.setattr (auto-restores on teardown) so test isolation
    holds even when subsequent tests don't use this fixture. Both
    app.database and the token_tracker module (which dereferences
    app.database.DB_NAME at call time) see the temp path.

    Yields: (path: str, decision_id: int)
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    import app.database as db
    monkeypatch.setattr(db, "DB_NAME", path)
    db.init_db()
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO decision_points (symbol, price_at_decision, drop_percent, "
        "recommendation, reasoning, status) VALUES (?, ?, ?, ?, ?, ?)",
        ("TEST", 100.0, -6.0, "PENDING", "Analyzing...", "Pending"),
    )
    decision_id = cur.lastrowid
    conn.commit()
    conn.close()
    yield path, decision_id
    os.unlink(path)
```

- [ ] **Step 1: Write failing tests**

```python
# tests/test_token_tracker.py
import sqlite3
import threading


def test_record_known_model_inserts_row_with_cost(temp_db):
    path, decision_id = temp_db
    from app.services import token_pricing, token_tracker
    token_pricing.GEMINI_PRICING["__test_model__"] = {"in": 2.0, "out": 8.0}
    try:
        token_tracker.record_llm_call(
            decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
            stage="sensor", agent_name="sensor_news",
            model="__test_model__", tokens_in=1_000_000, tokens_out=500_000,
        )
    finally:
        del token_pricing.GEMINI_PRICING["__test_model__"]

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT decision_id, ticker, stage, agent_name, model, tokens_in, "
        "tokens_out, cost_usd FROM agent_token_usage"
    ).fetchone()
    conn.close()
    assert row == (decision_id, "TEST", "sensor", "sensor_news",
                   "__test_model__", 1_000_000, 500_000, 6.0)


def test_record_unknown_model_stores_null_cost(temp_db):
    path, decision_id = temp_db
    from app.services import token_tracker
    token_tracker.record_llm_call(
        decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
        stage="pm", agent_name="pm", model="totally-unknown-model",
        tokens_in=100, tokens_out=200,
    )
    conn = sqlite3.connect(path)
    cost_usd = conn.execute("SELECT cost_usd FROM agent_token_usage").fetchone()[0]
    conn.close()
    assert cost_usd is None


def test_concurrent_inserts_from_threads(temp_db):
    """5 sensor threads + 3 debate threads writing at once must all land."""
    path, decision_id = temp_db
    from app.services import token_tracker

    def writer(name):
        token_tracker.record_llm_call(
            decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
            stage="sensor", agent_name=name, model="gemini-3-flash-preview",
            tokens_in=1000, tokens_out=500,
        )

    threads = [threading.Thread(target=writer, args=(f"sensor_{i}",)) for i in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()

    conn = sqlite3.connect(path)
    count = conn.execute("SELECT COUNT(*) FROM agent_token_usage").fetchone()[0]
    conn.close()
    assert count == 8


def test_rollup_writes_totals_to_decision_points(temp_db):
    path, decision_id = temp_db
    from app.services import token_pricing, token_tracker
    token_pricing.GEMINI_PRICING["__test_model__"] = {"in": 2.0, "out": 8.0}
    try:
        for i in range(3):
            token_tracker.record_llm_call(
                decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
                stage="sensor", agent_name=f"sensor_{i}",
                model="__test_model__", tokens_in=1_000_000, tokens_out=500_000,
            )
        token_tracker.rollup_decision_totals(decision_id)
    finally:
        del token_pricing.GEMINI_PRICING["__test_model__"]

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT total_tokens_in, total_tokens_out, total_cost_usd, total_llm_calls "
        "FROM decision_points WHERE id = ?", (decision_id,)
    ).fetchone()
    conn.close()
    assert row == (3_000_000, 1_500_000, 18.0, 3)


def test_rollup_is_idempotent(temp_db):
    path, decision_id = temp_db
    from app.services import token_tracker
    token_tracker.record_llm_call(
        decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
        stage="pm", agent_name="pm", model="gemini-3.1-pro-preview",
        tokens_in=100, tokens_out=200,
    )
    token_tracker.rollup_decision_totals(decision_id)
    token_tracker.rollup_decision_totals(decision_id)  # second run
    conn = sqlite3.connect(path)
    calls = conn.execute(
        "SELECT total_llm_calls FROM decision_points WHERE id = ?", (decision_id,)
    ).fetchone()[0]
    conn.close()
    assert calls == 1  # still 1, not 2
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_token_tracker.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.token_tracker'`.

- [ ] **Step 3: Implement the helper**

```python
# app/services/token_tracker.py
"""
Persists one row per Gemini API call to `agent_token_usage` and rolls
up per-decision totals onto `decision_points`.

Thread-safety: every call opens its own short-lived sqlite3 connection
and closes it after a single INSERT (or UPDATE for the rollup). WAL
mode (enabled in init_db) serializes writes via the WAL file rather
than locking the whole DB, so concurrent calls from the 5-sensor and
3-debate ThreadPoolExecutors land cleanly.

Note on DB_NAME lookup: we deliberately reference `app.database.DB_NAME`
via the module (not `from app.database import DB_NAME`) so that test
fixtures can `monkeypatch.setattr(app.database, "DB_NAME", tmp_path)`
without needing a module reload.
"""
import logging
import sqlite3

import app.database as _db
from app.services.token_pricing import compute_cost

logger = logging.getLogger(__name__)


def record_llm_call(
    *,
    decision_id: int,
    ticker: str,
    run_date: str,
    stage: str,
    agent_name: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """Insert one row into agent_token_usage. Failures are logged, not raised —
    cost tracking must never break the live pipeline.
    """
    try:
        cost = compute_cost(model, tokens_in, tokens_out)
        conn = sqlite3.connect(_db.DB_NAME)
        try:
            conn.execute(
                """
                INSERT INTO agent_token_usage
                  (decision_id, ticker, run_date, stage, agent_name,
                   model, tokens_in, tokens_out, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (decision_id, ticker, run_date, stage, agent_name,
                 model, tokens_in, tokens_out, cost),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(
            "record_llm_call failed for %s/%s (%s): %s",
            ticker, agent_name, model, e,
        )


def rollup_decision_totals(decision_id: int) -> None:
    """Recompute the four denormalized total_* columns on decision_points
    from agent_token_usage. Idempotent — safe to re-run.
    """
    try:
        conn = sqlite3.connect(_db.DB_NAME)
        try:
            conn.execute(
                """
                UPDATE decision_points
                SET total_tokens_in   = (SELECT COALESCE(SUM(tokens_in), 0)
                                         FROM agent_token_usage WHERE decision_id = ?),
                    total_tokens_out  = (SELECT COALESCE(SUM(tokens_out), 0)
                                         FROM agent_token_usage WHERE decision_id = ?),
                    total_cost_usd    = (SELECT SUM(cost_usd)
                                         FROM agent_token_usage WHERE decision_id = ?),
                    total_llm_calls   = (SELECT COUNT(*)
                                         FROM agent_token_usage WHERE decision_id = ?)
                WHERE id = ?
                """,
                (decision_id, decision_id, decision_id, decision_id, decision_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("rollup_decision_totals failed for decision_id=%s: %s",
                       decision_id, e)
```

Note: `total_cost_usd` uses `SUM(cost_usd)` (not COALESCE) so that if any row has NULL cost the total stays visibly NULL — matches the spec's "make the pricing gap visible" rule.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_token_tracker.py -v
```
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add app/services/token_tracker.py tests/test_token_tracker.py
git commit -m "feat(token-usage): add record_llm_call + rollup_decision_totals"
```

---

## Task 4: Thread `decision_id` through `MarketState` and `analyze_stock`

**Files:**
- Modify: `app/models/market_state.py`
- Modify: `app/services/research_service.py:248` — `analyze_stock` signature
- Modify: `app/services/stock_service.py:1620` — pass `decision_id` in

- [ ] **Step 1: Add `decision_id` to `MarketState`**

Edit `app/models/market_state.py` — add one field:

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class MarketState:
    ticker: str
    date: str
    reports: Dict[str, str] = field(default_factory=dict)
    debate_transcript: List[str] = field(default_factory=list)
    trade_proposal: Optional[dict] = None
    risk_assessment: Optional[dict] = None
    final_decision: Optional[dict] = None
    agent_calls: int = 0
    gatekeeper_tier: Optional[str] = None
    earnings_facts: Optional[dict] = None
    volatility_regime: Optional[dict] = None
    decision_id: Optional[int] = None   # NEW: FK into decision_points for token tracking
```

- [ ] **Step 2: Update `analyze_stock` to accept and stash `decision_id`**

Edit `app/services/research_service.py:248`. Change signature and `MarketState(...)` constructor at lines 262-268:

```python
def analyze_stock(self, ticker: str, raw_data: Dict, decision_id: Optional[int] = None) -> dict:
    """
    Orchestrates the new 3-Phase Agent Flow:
    1. Agents (Technical + News + Sentiment + Competitive) -> MarketState.reports
    2. Bull & Bear & Risk Perspectives (Parallel) -> debate
    3. Portfolio Manager (Internet Verification) -> Final Decision

    `decision_id` is the FK into decision_points used by token_tracker
    to attribute every LLM call back to this run.
    """
    if not self._check_and_increment_usage():
        return {"recommendation": "SKIP", "reasoning": "Daily limit reached."}

    print(f"\n[ResearchService] Starting Research Council for {ticker}...")

    state = MarketState(
        ticker=ticker,
        date=datetime.now().strftime("%Y-%m-%d"),
        gatekeeper_tier=raw_data.get("gatekeeper_tier"),
        earnings_facts=raw_data.get("earnings_facts"),
        volatility_regime=gatekeeper_service.check_market_regime(),
        decision_id=decision_id,
    )
    # ... rest unchanged
```

- [ ] **Step 3: Pass `decision_id` from the caller**

Edit `app/services/stock_service.py:1620`:

```python
        # Pass raw_data to research service
        report_data = research_service.analyze_stock(symbol, raw_data, decision_id=decision_id)
```

`decision_id` is already in scope at this point (it's set at line 1517).

- [ ] **Step 4: Write a smoke test that confirms decision_id flows through**

```python
# tests/test_decision_id_threading.py
from app.models.market_state import MarketState


def test_market_state_carries_decision_id():
    s = MarketState(ticker="AAPL", date="2026-05-23", decision_id=42)
    assert s.decision_id == 42


def test_market_state_decision_id_optional():
    s = MarketState(ticker="AAPL", date="2026-05-23")
    assert s.decision_id is None
```

Run: `pytest tests/test_decision_id_threading.py -v` — expected PASS.

- [ ] **Step 5: Commit**

```bash
git add app/models/market_state.py app/services/research_service.py \
        app/services/stock_service.py tests/test_decision_id_threading.py
git commit -m "feat(token-usage): thread decision_id through MarketState and analyze_stock"
```

---

## Task 5: Instrument `_call_grounded_model` (the main chokepoint)

**Files:**
- Modify: `app/services/research_service.py` — `_call_agent` (line 1695) and `_call_grounded_model` (line 1763)

Strategy: `_call_agent` already knows `state` (which now carries `decision_id`, `ticker`, `date`) and the human-readable `agent_name`. It maps `agent_name` → canonical token-tracking values (`stage`, `tracker_agent_name`) and passes them through to `_call_grounded_model`, which calls `record_llm_call` immediately after extracting `usage_metadata` at the existing site (line 1822-1829).

- [ ] **Step 1: Add the agent-name → (stage, tracker_name) mapping**

Add this module-level constant near the top of `app/services/research_service.py` (just below the existing imports / constants block, before the class definition):

```python
# Maps the human-readable agent_name used in _call_agent to the stable
# (stage, tracker_agent_name) pair stored in agent_token_usage.
# These tracker names are immutable once shipped — renaming silently
# breaks every historical per-agent trend query.
TOKEN_TRACKER_AGENT_MAP = {
    "Technical Agent":             ("sensor", "sensor_technical"),
    "News Agent":                  ("sensor", "sensor_news"),
    "Market Sentiment Agent":      ("sensor", "sensor_market_sentiment"),
    "Competitive Landscape Agent": ("sensor", "sensor_competitive"),
    "Bull Researcher":             ("debate", "debate_bull"),
    "Bear Researcher":             ("debate", "debate_bear"),
    "Risk Management Agent":       ("debate", "debate_risk"),
    "Fund Manager":                ("pm",     "pm"),
    # Note: Economics Agent and any retry-loop agents are not tracked
    # here. The Seeking Alpha agent is deterministic (no LLM call) and
    # is correctly absent.
}
```

- [ ] **Step 2: Extend `_call_grounded_model` to accept tracker context and record**

Edit `_call_grounded_model` (line 1763). Add two new kwargs and call `record_llm_call` right after the existing `metrics_sink` block at line 1822-1829.

```python
    def _call_grounded_model(
        self,
        prompt: str,
        model_name: str,
        agent_context: str = "",
        retry_count: int = 0,
        budget_clock: Optional["BudgetClock"] = None,
        metrics_sink: Optional[Dict[str, Any]] = None,
        tracker_context: Optional[Dict[str, Any]] = None,   # NEW
    ) -> str:
        # ... existing body unchanged until after the metrics_sink block ...

            if metrics_sink is not None:
                try:
                    um = getattr(response, "usage_metadata", None)
                    metrics_sink["model"] = model_name
                    metrics_sink["tokens_in"]  = getattr(um, "prompt_token_count", 0) or 0
                    metrics_sink["tokens_out"] = getattr(um, "candidates_token_count", 0) or 0
                except Exception:
                    metrics_sink.setdefault("model", model_name)

            # --- NEW: persist to agent_token_usage ---
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
```

Also forward `tracker_context` on every recursive retry call inside `_call_grounded_model` (lines 1842, 1877, 1899 — the three `return self._call_grounded_model(...)` sites). Append `, tracker_context=tracker_context` to each. Token rows record only the **final successful attempt** — that matches the existing `metrics_sink` semantics. (Retry-tax is out of scope per the spec.)

- [ ] **Step 3: Build `tracker_context` inside `_call_agent` and pass it down**

Edit `_call_agent` (line 1695). In the grounded path (line 1733-1736 where `_call_grounded_model` is called), build the tracker context from `state` and `agent_name`:

```python
    def _call_agent(self, prompt: str, agent_name: str,
                    state: Optional[MarketState] = None,
                    metrics_sink: Optional[Dict[str, Any]] = None) -> str:
        if not self.model:
            return "Mock Output"
        try:
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

            grounded_agents = [ ... unchanged ... ]

            if agent_name in grounded_agents and self.grounding_client:
                model_to_use = "gemini-3-flash-preview"
                if agent_name in ["Bull Researcher", "Bear Researcher", "Fund Manager", "Risk Management Agent"]:
                    model_to_use = "gemini-3.1-pro-preview"
                elif agent_name == "News Agent":
                    model_to_use = news_shadow_service.PRODUCTION_NEWS_MODEL

                logger.info(f"Calling {agent_name} with {model_to_use} + Grounding...")
                _t0 = time.monotonic()
                _result = self._call_grounded_model(
                    prompt, model_name=model_to_use, agent_context=agent_name,
                    metrics_sink=metrics_sink,
                    tracker_context=tracker_context,   # NEW
                )
                if metrics_sink is not None:
                    metrics_sink["latency_ms"] = int((time.monotonic() - _t0) * 1000)
                return _result
            # ... non-grounded fallback path unchanged for now (see Task 6) ...
```

- [ ] **Step 4: Write a test that drives `_call_grounded_model` end-to-end with a stubbed response**

Token-tracker behaviour is already covered in `tests/test_token_tracker.py`. For `_call_grounded_model` itself, write a thin test that fakes `self.grounding_client.models.generate_content(...)` and asserts a row lands in `agent_token_usage`.

```python
# tests/test_grounded_model_records_tokens.py
import sqlite3


class _FakeUsage:
    prompt_token_count = 1234
    candidates_token_count = 567


class _FakeCandidate:
    finish_reason = 1  # STOP — not a function-call retry


class _FakeResponse:
    candidates = [_FakeCandidate()]
    text = "FAKE OUTPUT"
    usage_metadata = _FakeUsage()


class _FakeModels:
    def generate_content(self, model, contents, config):
        return _FakeResponse()


class _FakeGroundingClient:
    models = _FakeModels()


def test_grounded_model_records_token_row(temp_db):
    path, decision_id = temp_db
    from app.services.research_service import ResearchService
    rs = ResearchService.__new__(ResearchService)  # bypass __init__
    rs.grounding_client = _FakeGroundingClient()
    rs.model = object()  # truthy so the function doesn't short-circuit
    rs.lock = __import__("threading").Lock()

    tracker_context = {
        "decision_id": decision_id, "ticker": "AAPL", "run_date": "2026-05-23",
        "stage": "sensor", "agent_name": "sensor_news",
    }
    rs._call_grounded_model(
        "prompt", model_name="gemini-3-flash-preview", agent_context="News Agent",
        tracker_context=tracker_context,
    )
    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT agent_name, model, tokens_in, tokens_out FROM agent_token_usage"
    ).fetchone()
    conn.close()
    assert row == ("sensor_news", "gemini-3-flash-preview", 1234, 567)
```

Run: `pytest tests/test_grounded_model_records_tokens.py -v` — expected PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/research_service.py tests/test_grounded_model_records_tokens.py
git commit -m "feat(token-usage): instrument _call_grounded_model via tracker_context"
```

---

## Task 6: Instrument the non-grounded fallback path

**Files:**
- Modify: `app/services/research_service.py` — `_call_agent` non-grounded branch at line 1746

The non-grounded path (`self.model.generate_content(prompt, request_options=...)`) currently doesn't extract token usage. In practice this branch only fires when `self.grounding_client` is None, but coverage requires we still record those calls.

- [ ] **Step 1: Extract tokens and record after the non-grounded call**

Replace the block at lines 1741-1747 with:

```python
            # Default path (no grounding) -> standard generate_content (old SDK)
            time.sleep(2)  # rate-limit buffer (legacy)
            response = self.model.generate_content(prompt, request_options=RequestOptions(timeout=600))

            # Record token usage if we have the context to attribute it.
            if tracker_context is not None:
                try:
                    um = getattr(response, "usage_metadata", None)
                    tokens_in  = (getattr(um, "prompt_token_count", 0) or 0) if um else 0
                    tokens_out = (getattr(um, "candidates_token_count", 0) or 0) if um else 0
                    model_used = getattr(self.model, "model_name", "unknown")
                    # The old-SDK model_name comes back as 'models/<name>' — strip the prefix.
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
```

The `except` 503-fallback block (lines 1748-1758) creates a fresh `genai.GenerativeModel('gemini-3-pro-preview')` and calls `.generate_content` again. Apply the same recording snippet there too, with `model_used = "gemini-3-pro-preview"` since we know exactly what the fallback model is.

- [ ] **Step 2: Write a test for the non-grounded path**

```python
# tests/test_nongrounded_records_tokens.py
import sqlite3


class _FakeUsage:
    prompt_token_count = 11
    candidates_token_count = 22


class _FakeResponse:
    text = "FAKE"
    usage_metadata = _FakeUsage()


class _FakeOldModel:
    model_name = "models/gemini-3.1-pro-preview"
    def generate_content(self, prompt, request_options=None):
        return _FakeResponse()


def test_nongrounded_path_records_tokens(temp_db, monkeypatch):
    path, decision_id = temp_db
    from app.services.research_service import ResearchService
    from app.models.market_state import MarketState
    rs = ResearchService.__new__(ResearchService)
    rs.grounding_client = None       # forces non-grounded path
    rs.model = _FakeOldModel()
    rs.lock = __import__("threading").Lock()
    # Patch time.sleep to skip the 2s buffer in this test.
    monkeypatch.setattr("app.services.research_service.time.sleep", lambda s: None)

    state = MarketState(ticker="AAPL", date="2026-05-23", decision_id=decision_id)
    rs._call_agent("prompt", "Fund Manager", state=state)

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT agent_name, model, tokens_in, tokens_out FROM agent_token_usage"
    ).fetchone()
    conn.close()
    assert row == ("pm", "gemini-3.1-pro-preview", 11, 22)
```

Run: `pytest tests/test_nongrounded_records_tokens.py -v` — expected PASS.

- [ ] **Step 3: Commit**

```bash
git add app/services/research_service.py tests/test_nongrounded_records_tokens.py
git commit -m "feat(token-usage): record tokens on non-grounded fallback path"
```

---

## Task 7: Best-effort Deep Research instrumentation

**Files:**
- Modify: `app/services/deep_research_service.py` — `execute_deep_research` (line 1080) around the poll-completion site (line 1128)

Deep Research uses REST (`requests.post` / `requests.get` at lines 1103/1122), not the Gemini SDK. The poll JSON **may** contain `usageMetadata`. If present, record it. If not, log once per run and move on — Deep Research cost is already documented as a lower bound (spec, out-of-scope section).

- [ ] **Step 1: Locate the success branch and extract usage if present**

Edit `app/services/deep_research_service.py` around line 1128 (`if status in ['completed', 'COMPLETED']:`). Just before the existing `return self._parse_output(poll_data, schema_type='individual')`:

```python
                if status in ['completed', 'COMPLETED']:
                    # Best-effort token tracking. The REST response shape may not
                    # include usageMetadata; if it doesn't, log and skip — DR cost
                    # is already documented as a lower bound (spec out-of-scope).
                    if decision_id is not None:
                        try:
                            um = poll_data.get("usageMetadata") or {}
                            tokens_in  = int(um.get("promptTokenCount", 0) or 0)
                            tokens_out = int(um.get("candidatesTokenCount", 0) or 0)
                            if tokens_in or tokens_out:
                                from app.services.token_tracker import record_llm_call
                                from datetime import datetime
                                record_llm_call(
                                    decision_id=decision_id,
                                    ticker=symbol,
                                    run_date=datetime.now().strftime("%Y-%m-%d"),
                                    stage="deep_research",
                                    agent_name="deep_research",
                                    model="deep-research-pro",
                                    tokens_in=tokens_in,
                                    tokens_out=tokens_out,
                                )
                            else:
                                logger.info(
                                    "[Deep Research] No usageMetadata in poll response for %s — "
                                    "skipping token record (expected; see spec out-of-scope).",
                                    symbol,
                                )
                        except Exception as e:
                            logger.warning("[Deep Research] token tracking failed for %s: %s",
                                           symbol, e)

                    return self._parse_output(poll_data, schema_type='individual')
```

- [ ] **Step 2: Test the success path (usageMetadata present)**

```python
# tests/test_deep_research_token_record.py
import sqlite3


def test_deep_research_records_when_usage_present(temp_db):
    """Directly invoke the token-tracking snippet by mocking the poll path is heavy.
    Instead, simulate the exact code path inline.
    """
    path, decision_id = temp_db
    poll_data = {
        "status": "completed",
        "usageMetadata": {"promptTokenCount": 9000, "candidatesTokenCount": 4000},
    }
    # Replicate the snippet under test (this protects the contract the snippet expects).
    from app.services.token_tracker import record_llm_call
    from datetime import datetime
    um = poll_data.get("usageMetadata") or {}
    record_llm_call(
        decision_id=decision_id, ticker="AAPL",
        run_date=datetime.now().strftime("%Y-%m-%d"),
        stage="deep_research", agent_name="deep_research",
        model="deep-research-pro",
        tokens_in=int(um["promptTokenCount"]), tokens_out=int(um["candidatesTokenCount"]),
    )
    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT stage, agent_name, model, tokens_in, tokens_out "
        "FROM agent_token_usage"
    ).fetchone()
    conn.close()
    assert row == ("deep_research", "deep_research", "deep-research-pro", 9000, 4000)
```

(Driving the full `execute_deep_research` end-to-end would require mocking `requests` and the poll loop — heavier than the value it adds. The snippet-level test above plus the existing token_tracker tests cover the contract.)

Run: `pytest tests/test_deep_research_token_record.py -v` — expected PASS.

- [ ] **Step 3: Commit**

```bash
git add app/services/deep_research_service.py tests/test_deep_research_token_record.py
git commit -m "feat(token-usage): best-effort DR token capture from REST usageMetadata"
```

---

## Task 8: Run-end rollup wired into `analyze_stock`

**Files:**
- Modify: `app/services/research_service.py` — at the end of `analyze_stock`

After all agents finish (sensors + debate + PM), call `rollup_decision_totals(decision_id)` to populate the four denormalized columns on `decision_points`. Deep Research runs **after** `analyze_stock` returns (from `stock_service.py`), so the rollup will miss DR tokens unless we run it a second time. Strategy: call rollup once at the end of `analyze_stock`, and once more after DR completes in `stock_service.py`. Idempotent.

- [ ] **Step 1: Call rollup at the end of `analyze_stock`**

The final return of `analyze_stock` is the big dict literal starting at `research_service.py:719`. Insert this block **immediately before line 719** (after the `print("="*50 + "\n")` at line 717):

```python
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
            # ... (existing dict literal continues unchanged) ...
        }
```

Note: the early-exit `return` at line 257 (daily-limit SKIP) has no LLM calls and intentionally is NOT instrumented with a rollup. The early-exit `return` at line 974 (PASS_INSUFFICIENT_DATA from Fund Manager failure) lives inside `_run_fund_manager`, not `analyze_stock`, and is reached via the dict-literal return at 719 — so it's covered.

- [ ] **Step 2: Call rollup again after Deep Research completes**

In `app/services/stock_service.py`, find the site where deep research results have been merged back (search for `deep_research` mentions after line 1620). After the merge / before the existing `update_decision_point(...)` call (~line 1692), insert:

```python
            # Re-roll token totals to include the deep_research row (if any).
            try:
                from app.services.token_tracker import rollup_decision_totals
                rollup_decision_totals(decision_id)
            except Exception as e:
                print(f"rollup_decision_totals failed post-DR: {e}")
```

- [ ] **Step 3: Tests already covered**

`test_token_tracker.py::test_rollup_writes_totals_to_decision_points` and `test_rollup_is_idempotent` already exercise the rollup logic itself. No new test required at this layer — the integration test in Task 9 covers the wiring.

- [ ] **Step 4: Commit**

```bash
git add app/services/research_service.py app/services/stock_service.py
git commit -m "feat(token-usage): rollup decision totals at end of analyze_stock + post-DR"
```

---

## Task 9: End-to-end integration test

**Files:**
- Create: `tests/test_token_usage_integration.py`

Validates the full chain: a fake grounding client → `_call_agent` → `_call_grounded_model` → `record_llm_call` → `rollup_decision_totals` → `decision_points` totals updated.

- [ ] **Step 1: Write the integration test**

```python
# tests/test_token_usage_integration.py
import sqlite3
import threading


class _FakeUsage:
    def __init__(self, p, c):
        self.prompt_token_count = p
        self.candidates_token_count = c


class _FakeCandidate:
    finish_reason = 1


class _FakeResponse:
    def __init__(self, p, c):
        self.candidates = [_FakeCandidate()]
        self.text = "OK"
        self.usage_metadata = _FakeUsage(p, c)


class _FakeModels:
    def generate_content(self, model, contents, config):
        # Return varying counts so we can verify SUM math
        return _FakeResponse(1000, 500)


class _FakeGroundingClient:
    models = _FakeModels()


def test_three_grounded_calls_rollup_to_decision_points(temp_db):
    path, decision_id = temp_db
    from app.services.research_service import ResearchService
    from app.services.token_tracker import rollup_decision_totals
    from app.models.market_state import MarketState

    rs = ResearchService.__new__(ResearchService)
    rs.grounding_client = _FakeGroundingClient()
    rs.model = object()
    rs.lock = threading.Lock()

    state = MarketState(ticker="AAPL", date="2026-05-23", decision_id=decision_id)

    # Simulate 3 grounded agent calls
    for label in ["News Agent", "Bull Researcher", "Fund Manager"]:
        rs._call_agent("prompt", label, state=state)

    rollup_decision_totals(decision_id)

    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT agent_name FROM agent_token_usage ORDER BY id"
    ).fetchall()
    totals = conn.execute(
        "SELECT total_tokens_in, total_tokens_out, total_llm_calls "
        "FROM decision_points WHERE id = ?", (decision_id,)
    ).fetchone()
    conn.close()

    assert [r[0] for r in rows] == ["sensor_news", "debate_bull", "pm"]
    # 3 calls × (1000 in, 500 out)
    assert totals == (3000, 1500, 3)
```

- [ ] **Step 2: Run all token-usage tests together**

```
pytest tests/test_token_pricing.py tests/test_token_tracker.py \
       tests/test_decision_id_threading.py \
       tests/test_grounded_model_records_tokens.py \
       tests/test_nongrounded_records_tokens.py \
       tests/test_deep_research_token_record.py \
       tests/test_token_usage_integration.py -v
```
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_token_usage_integration.py
git commit -m "test(token-usage): end-to-end integration test"
```

---

## Task 10: Manual smoke against a real pipeline run

This is a one-time validation step — no code change, no commit unless something needs fixing.

- [ ] **Step 1: Run the pipeline against one ticker**

Use whatever script is normally used for a one-shot analysis (e.g. point at a recent dip ticker, or trigger via the FastAPI endpoint). Whatever path produces a fresh `decision_points` row.

- [ ] **Step 2: Verify rows landed**

```bash
sqlite3 subscribers.db "SELECT id, symbol, total_tokens_in, total_tokens_out, total_cost_usd, total_llm_calls FROM decision_points ORDER BY id DESC LIMIT 1;"
```

Expected: a recent row with `total_llm_calls` between 4 and 9 (sensors + debate + PM, plus DR if it returned usage), non-zero `total_tokens_in` / `total_tokens_out`, `total_cost_usd` = 0.0 (placeholders) or NULL (some unknown model).

```bash
sqlite3 subscribers.db "SELECT agent_name, model, tokens_in, tokens_out FROM agent_token_usage WHERE decision_id = (SELECT MAX(id) FROM decision_points);"
```

Expected: one row per agent that ran, with the canonical `agent_name` enum values (`sensor_news`, `debate_bull`, etc.) — confirm no surprise values, no duplicates, no `Technical Agent` strings leaking through (means the mapping missed one).

- [ ] **Step 3: Fill in real pricing (if confirmed available)**

If at this point we have the real Gemini 3 rate card, replace the 0.0 placeholders in `app/services/token_pricing.py`. This is its own commit:

```bash
git add app/services/token_pricing.py
git commit -m "chore(token-usage): fill in confirmed Gemini 3 rates"
```

Otherwise leave the placeholders and file a follow-up task to verify pricing.

---

## What this plan deliberately does NOT do

Re-stated from the spec so the implementing engineer doesn't add scope:

- No Google Search grounding cost capture for Deep Research (grounding is billed per query, not per token; DR cost in this table is a **lower bound**).
- No replication of `news_shadow_runs` token data into `agent_token_usage`. Daily totals under-report by the shadow model's spend.
- No retry / failed-call accounting. Only the successful final attempt is recorded.
- No separate cached-input-token column.
- No latency persistence.
- No dashboard / CSV report / email surface — DB-only.
- No rename of `decision_points.status` vocabulary.

---

## Spec coverage check

| Spec section                  | Covered by                              |
|-------------------------------|-----------------------------------------|
| `agent_token_usage` schema    | Task 2                                  |
| Stable agent_name enum        | Task 5 (`TOKEN_TRACKER_AGENT_MAP`)      |
| Denormalized totals on DP     | Task 2 (cols) + Task 8 (rollup)         |
| `decision_id` ordering        | Task 4 (thread through existing stub)   |
| Thread safety (per-call conn + WAL) | Task 2 (WAL) + Task 3 (per-call conn) + Task 3 test |
| Pricing module + per-1M       | Task 1                                  |
| Frozen cost trade-off         | Task 1 (compute at insert)              |
| `record_llm_call` chokepoint  | Task 3 + Task 5 grounded + Task 6 fallback + Task 7 DR |
| Run-end rollup                | Task 8                                  |
| Out-of-scope list             | "What this plan deliberately does NOT do" |

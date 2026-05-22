# News Agent Gemini 3.5 Flash Upgrade + Shadow Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the News Agent to Gemini 3.5 Flash in production immediately, and run the previous Flash model as a non-blocking shadow call for the next 20 decision points to validate the upgrade.

**Architecture:** The production model swap is a one-line change in the model-selection logic. The shadow path is a separate, isolated module (`news_shadow_service.py`) that runs the old model on the identical prompt via a dedicated single-thread executor. Shadow output is logged to a new `news_shadow_runs` table, never feeds the pipeline, and self-disables once 20 completed pairs exist. A standalone analysis script produces the side-by-side comparison report with cost math.

**Tech Stack:** Python 3.9, FastAPI, SQLite (`subscribers.db`), Google GenAI v2 SDK (`google.genai`), pytest.

---

## Design notes (read before starting)

- **Model strings.** Production = `gemini-3.5-flash-preview`, shadow = `gemini-3-flash-preview` (the current value). The 3.5 string is the documented preview id at time of writing — **confirm against the live Gemini model list before deploying.** Both strings live as constants in `news_shadow_service.py`; change them in one place if the published id differs.
- **Isolation contract.** The shadow call runs on its own `ThreadPoolExecutor(max_workers=1)`. The live pipeline never reads its result for any branching decision. Any shadow exception/timeout is caught and logged; `news_report` (the live News Agent output on 3.5 Flash) is unaffected.
- **Counting pairs.** "20 decision points" = 20 rows in `news_shadow_runs` where the shadow call *succeeded* (`shadow_error IS NULL`). Errored shadow rows are still logged for visibility but do not count toward the 20, so the report always has 20 real pairs. The active-check is read once per `analyze_stock` run; minor overshoot from concurrent scans is acceptable and the report caps at the first 20.
- **No retroactive comparison.** Shadow only ever runs forward, inside `analyze_stock`.

## File structure

| File | Responsibility |
|------|----------------|
| `app/database.py` (modify) | Add `news_shadow_runs` table + `count_news_shadow_runs()`, `insert_news_shadow_run()`, `get_news_shadow_runs()` helpers |
| `app/services/news_shadow_service.py` (create) | Model constants, `is_shadow_active()`, `run_shadow_call()`, `build_shadow_record()`, `extract_needs_economics()` |
| `app/services/research_service.py` (modify) | Swap News Agent production model; add `metrics_sink` plumbing; dispatch + collect shadow call inside `analyze_stock` |
| `app/services/stock_service.py` (modify) | Persist `news_shadow_data` to the DB once `decision_id` is known |
| `scripts/analysis/news_shadow_report.py` (create) | Generate the 20-pair side-by-side comparison report with cost math + LLM-judged accuracy dimensions |
| `tests/test_news_shadow_service.py` (create) | Unit tests for the shadow service + DB helpers |
| `tests/test_news_shadow_report.py` (create) | Unit tests for the report generator |

---

## Task 1: Database table and helpers for shadow runs

**Files:**
- Modify: `app/database.py` (add table in `init_db()` after the `batch_comparisons` block ~line 175; add helper functions at end of file)
- Test: `tests/test_news_shadow_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_news_shadow_service.py`:

```python
import importlib
import pytest

from app import database


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_NAME", str(db_file))
    database.init_db()
    return str(db_file)


def _sample_record(symbol="AAPL"):
    return {
        "symbol": symbol,
        "decision_date": "2026-05-22",
        "production_model": "gemini-3.5-flash-preview",
        "production_report": "Production report. NEEDS_ECONOMICS: TRUE",
        "production_tokens_in": 1000,
        "production_tokens_out": 400,
        "production_latency_ms": 5200,
        "production_needs_economics": True,
        "shadow_model": "gemini-3-flash-preview",
        "shadow_report": "Shadow report. NEEDS_ECONOMICS: FALSE",
        "shadow_tokens_in": 1010,
        "shadow_tokens_out": 380,
        "shadow_latency_ms": 6100,
        "shadow_needs_economics": False,
        "shadow_error": None,
    }


def test_count_starts_at_zero(temp_db):
    assert database.count_news_shadow_runs() == 0


def test_insert_and_count(temp_db):
    database.insert_news_shadow_run(1, _sample_record())
    assert database.count_news_shadow_runs() == 1


def test_errored_shadow_does_not_count(temp_db):
    rec = _sample_record()
    rec["shadow_report"] = None
    rec["shadow_error"] = "timeout"
    database.insert_news_shadow_run(2, rec)
    assert database.count_news_shadow_runs() == 0


def test_get_returns_inserted_rows(temp_db):
    database.insert_news_shadow_run(1, _sample_record("AAPL"))
    database.insert_news_shadow_run(2, _sample_record("MSFT"))
    rows = database.get_news_shadow_runs()
    assert len(rows) == 2
    assert {r["symbol"] for r in rows} == {"AAPL", "MSFT"}
    assert rows[0]["decision_point_id"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_shadow_service.py -v`
Expected: FAIL with `AttributeError: module 'app.database' has no attribute 'count_news_shadow_runs'`

- [ ] **Step 3: Add the table to `init_db()`**

In `app/database.py`, immediately after the `CREATE TABLE IF NOT EXISTS batch_comparisons (...)` block (ends ~line 175), add:

```python
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS news_shadow_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_point_id INTEGER,
            symbol TEXT,
            decision_date TEXT,
            production_model TEXT,
            production_report TEXT,
            production_tokens_in INTEGER,
            production_tokens_out INTEGER,
            production_latency_ms INTEGER,
            production_needs_economics BOOLEAN,
            shadow_model TEXT,
            shadow_report TEXT,
            shadow_tokens_in INTEGER,
            shadow_tokens_out INTEGER,
            shadow_latency_ms INTEGER,
            shadow_needs_economics BOOLEAN,
            shadow_error TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (decision_point_id) REFERENCES decision_points (id)
        )
    ''')
```

- [ ] **Step 4: Add the helper functions**

At the end of `app/database.py`, add:

```python
def count_news_shadow_runs() -> int:
    """Number of completed shadow pairs (shadow call succeeded)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM news_shadow_runs "
        "WHERE shadow_report IS NOT NULL AND shadow_error IS NULL"
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count


def insert_news_shadow_run(decision_point_id: Optional[int], record: dict) -> None:
    """Persist one production/shadow comparison pair."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO news_shadow_runs (
            decision_point_id, symbol, decision_date,
            production_model, production_report, production_tokens_in,
            production_tokens_out, production_latency_ms, production_needs_economics,
            shadow_model, shadow_report, shadow_tokens_in,
            shadow_tokens_out, shadow_latency_ms, shadow_needs_economics,
            shadow_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            decision_point_id,
            record.get("symbol"),
            record.get("decision_date"),
            record.get("production_model"),
            record.get("production_report"),
            record.get("production_tokens_in"),
            record.get("production_tokens_out"),
            record.get("production_latency_ms"),
            record.get("production_needs_economics"),
            record.get("shadow_model"),
            record.get("shadow_report"),
            record.get("shadow_tokens_in"),
            record.get("shadow_tokens_out"),
            record.get("shadow_latency_ms"),
            record.get("shadow_needs_economics"),
            record.get("shadow_error"),
        ),
    )
    conn.commit()
    conn.close()


def get_news_shadow_runs() -> List[dict]:
    """Return all shadow runs, oldest first."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM news_shadow_runs ORDER BY id ASC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_news_shadow_service.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add app/database.py tests/test_news_shadow_service.py
git commit -m "feat(db): add news_shadow_runs table and helpers"
```

---

## Task 2: News shadow service module

**Files:**
- Create: `app/services/news_shadow_service.py`
- Test: `tests/test_news_shadow_service.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_news_shadow_service.py`:

```python
from app.services import news_shadow_service as nss


def test_extract_needs_economics_true():
    assert nss.extract_needs_economics("blah\nNEEDS_ECONOMICS: TRUE") is True


def test_extract_needs_economics_false():
    assert nss.extract_needs_economics("blah\nNEEDS_ECONOMICS: FALSE") is False
    assert nss.extract_needs_economics("") is False
    assert nss.extract_needs_economics(None) is False


def test_models_differ():
    assert nss.PRODUCTION_NEWS_MODEL != nss.SHADOW_NEWS_MODEL


def test_is_shadow_active_under_target(monkeypatch):
    monkeypatch.setattr(nss.database, "count_news_shadow_runs", lambda: 5)
    assert nss.is_shadow_active() is True


def test_is_shadow_active_at_target(monkeypatch):
    monkeypatch.setattr(nss.database, "count_news_shadow_runs", lambda: 20)
    assert nss.is_shadow_active() is False


def test_is_shadow_active_swallows_errors(monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(nss.database, "count_news_shadow_runs", boom)
    assert nss.is_shadow_active() is False


def test_run_shadow_call_passes_shadow_model():
    captured = {}

    def fake_call(prompt, model_name, agent_context, metrics_sink):
        captured["model"] = model_name
        captured["prompt"] = prompt
        metrics_sink["model"] = model_name
        metrics_sink["tokens_in"] = 100
        metrics_sink["tokens_out"] = 50
        return "shadow output"

    result = nss.run_shadow_call(fake_call, "the prompt")
    assert captured["model"] == nss.SHADOW_NEWS_MODEL
    assert captured["prompt"] == "the prompt"
    assert result["report"] == "shadow output"
    assert result["metrics"]["tokens_in"] == 100
    assert "latency_ms" in result["metrics"]


def test_build_shadow_record_with_success():
    prod_metrics = {"model": "gemini-3.5-flash-preview",
                    "tokens_in": 900, "tokens_out": 300, "latency_ms": 4000}
    shadow_result = {"report": "Shadow. NEEDS_ECONOMICS: TRUE",
                     "metrics": {"tokens_in": 950, "tokens_out": 310, "latency_ms": 5000}}
    rec = nss.build_shadow_record("AAPL", "2026-05-22",
                                  "Prod. NEEDS_ECONOMICS: FALSE",
                                  prod_metrics, shadow_result)
    assert rec["symbol"] == "AAPL"
    assert rec["production_needs_economics"] is False
    assert rec["shadow_needs_economics"] is True
    assert rec["shadow_tokens_in"] == 950
    assert rec["shadow_error"] is None


def test_build_shadow_record_with_failure():
    prod_metrics = {"model": "gemini-3.5-flash-preview",
                    "tokens_in": 900, "tokens_out": 300, "latency_ms": 4000}
    rec = nss.build_shadow_record("AAPL", "2026-05-22",
                                  "Prod report", prod_metrics, None)
    assert rec["shadow_report"] is None
    assert rec["shadow_error"] is not None
    assert rec["shadow_needs_economics"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_news_shadow_service.py -v -k "shadow_service or extract or models_differ or run_shadow or build_shadow"`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.news_shadow_service'`

- [ ] **Step 3: Create the module**

Create `app/services/news_shadow_service.py`:

```python
"""Shadow-model comparison for the News Agent.

For a fixed number of decision points after the Gemini 3.5 Flash upgrade, the
previous News Agent model runs as a non-blocking shadow call alongside
production. The shadow output is logged for offline comparison and never feeds
the live pipeline. The shadow self-disables once SHADOW_RUN_TARGET completed
pairs exist.
"""
import logging
import time
from typing import Any, Callable, Dict, Optional

from app import database

logger = logging.getLogger(__name__)

# Production News Agent model (the upgrade target).
# NOTE: confirm this id against the live Gemini model list before deploying.
PRODUCTION_NEWS_MODEL = "gemini-3.5-flash-preview"

# Previous News Agent model, kept running in shadow for validation.
SHADOW_NEWS_MODEL = "gemini-3-flash-preview"

# Number of completed (successful) shadow pairs after which shadow disables.
SHADOW_RUN_TARGET = 20


def extract_needs_economics(report_text: Optional[str]) -> bool:
    """True if the report sets the downstream Economics Agent trigger flag."""
    return "NEEDS_ECONOMICS: TRUE" in (report_text or "")


def is_shadow_active() -> bool:
    """True while fewer than SHADOW_RUN_TARGET completed pairs exist."""
    try:
        return database.count_news_shadow_runs() < SHADOW_RUN_TARGET
    except Exception as e:
        logger.warning("news shadow active-check failed, disabling shadow: %s", e)
        return False


def run_shadow_call(call_fn: Callable[..., str], prompt: str) -> Dict[str, Any]:
    """Run the shadow model on the identical prompt.

    `call_fn` must be ResearchService._call_grounded_model. Raises on failure;
    the caller is responsible for catching so the live pipeline is unaffected.
    """
    metrics: Dict[str, Any] = {}
    t0 = time.monotonic()
    report = call_fn(
        prompt,
        model_name=SHADOW_NEWS_MODEL,
        agent_context="News Agent (Shadow)",
        metrics_sink=metrics,
    )
    metrics["latency_ms"] = int((time.monotonic() - t0) * 1000)
    return {"report": report, "metrics": metrics}


def build_shadow_record(
    ticker: str,
    date: str,
    production_report: str,
    production_metrics: Dict[str, Any],
    shadow_result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble one comparison row from production + shadow outputs."""
    record: Dict[str, Any] = {
        "symbol": ticker,
        "decision_date": date,
        "production_model": production_metrics.get("model", PRODUCTION_NEWS_MODEL),
        "production_report": production_report,
        "production_tokens_in": production_metrics.get("tokens_in", 0),
        "production_tokens_out": production_metrics.get("tokens_out", 0),
        "production_latency_ms": production_metrics.get("latency_ms", 0),
        "production_needs_economics": extract_needs_economics(production_report),
        "shadow_model": SHADOW_NEWS_MODEL,
        "shadow_report": None,
        "shadow_tokens_in": 0,
        "shadow_tokens_out": 0,
        "shadow_latency_ms": 0,
        "shadow_needs_economics": None,
        "shadow_error": None,
    }
    if shadow_result is None:
        record["shadow_error"] = "shadow call failed or timed out"
        return record
    sm = shadow_result.get("metrics", {})
    sr = shadow_result.get("report")
    record["shadow_report"] = sr
    record["shadow_tokens_in"] = sm.get("tokens_in", 0)
    record["shadow_tokens_out"] = sm.get("tokens_out", 0)
    record["shadow_latency_ms"] = sm.get("latency_ms", 0)
    record["shadow_needs_economics"] = extract_needs_economics(sr)
    return record
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_news_shadow_service.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/news_shadow_service.py tests/test_news_shadow_service.py
git commit -m "feat(news-shadow): add shadow service module"
```

---

## Task 3: Swap News Agent production model and add metrics plumbing

**Files:**
- Modify: `app/services/research_service.py` — `_call_agent` model-selection block (~lines 1600-1622), `_call_grounded_model` (~lines 1646-1776), imports

This task changes production behavior (News Agent → 3.5 Flash) and adds an optional `metrics_sink` dict so token/latency numbers can be captured. `metrics_sink` defaults to `None`, so every existing caller is unaffected.

- [ ] **Step 1: Add the import**

Near the top of `app/services/research_service.py`, with the other `from app.services import ...` imports, add:

```python
from app.services import news_shadow_service
```

Confirm `import time` and `from typing import Any, Dict, Optional` are present at the top of the file; add any that are missing.

- [ ] **Step 2: Add `metrics_sink` to `_call_grounded_model`**

Read `_call_grounded_model` (~lines 1646-1776) in full first. Change its signature from:

```python
def _call_grounded_model(
    self,
    prompt: str,
    model_name: str,
    agent_context: str = "",
    retry_count: int = 0,
    budget_clock: Optional["BudgetClock"] = None,
) -> str:
```

to add the new keyword argument:

```python
def _call_grounded_model(
    self,
    prompt: str,
    model_name: str,
    agent_context: str = "",
    retry_count: int = 0,
    budget_clock: Optional["BudgetClock"] = None,
    metrics_sink: Optional[Dict[str, Any]] = None,
) -> str:
```

Immediately after the successful `response = self.grounding_client.models.generate_content(...)` call (~line 1698-1702), add:

```python
        if metrics_sink is not None:
            try:
                um = getattr(response, "usage_metadata", None)
                metrics_sink["model"] = model_name
                metrics_sink["tokens_in"] = getattr(um, "prompt_token_count", 0) or 0
                metrics_sink["tokens_out"] = getattr(um, "candidates_token_count", 0) or 0
            except Exception:
                metrics_sink.setdefault("model", model_name)
```

Then find **every** recursive `self._call_grounded_model(...)` call inside this function (the retry paths) and add `metrics_sink=metrics_sink` to each call's arguments, so the metrics dict reaches whichever attempt ultimately succeeds.

- [ ] **Step 3: Swap the News Agent model and capture latency in `_call_agent`**

Read `_call_agent` and locate the grounded-agent dispatch block (~lines 1611-1622). It currently reads:

```python
    if agent_name in grounded_agents and self.grounding_client:
         model_to_use = "gemini-3-flash-preview"

         # Bull, Bear, and Fund Manager should use Gemini 3 Pro
         if agent_name in ["Bull Researcher", "Bear Researcher", "Fund Manager", "Risk Management Agent"]:
             model_to_use = "gemini-3.1-pro-preview"

         logger.info(f"Calling {agent_name} with {model_to_use} + Grounding...")
         return self._call_grounded_model(prompt, model_name=model_to_use, agent_context=agent_name)
```

Replace it with:

```python
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
         )
         if metrics_sink is not None:
             metrics_sink["latency_ms"] = int((time.monotonic() - _t0) * 1000)
         return _result
```

- [ ] **Step 4: Add `metrics_sink` to the `_call_agent` signature**

Change the `_call_agent` signature to accept the optional sink. It currently looks like:

```python
def _call_agent(self, prompt, agent_name, state):
```

Change to:

```python
def _call_agent(self, prompt, agent_name, state, metrics_sink: Optional[Dict[str, Any]] = None):
```

If `_call_agent` has a non-grounded fallback path that also calls a model, leave it as-is — `metrics_sink` only needs populating for the grounded News Agent path.

- [ ] **Step 5: Verify the module still imports**

Run: `python -c "import app.services.research_service"`
Expected: no error (exit code 0).

- [ ] **Step 6: Manual smoke verification**

The model swap is config-level and the production path needs a real API key, so verify by running the pipeline against a known recent drop per CLAUDE.md ("Run the pipeline against a known recent drop and verify the report structure"). In the logs, confirm the line `Calling News Agent with gemini-3.5-flash-preview + Grounding...` appears. If the 3.5 model id is rejected by the API, correct `PRODUCTION_NEWS_MODEL` in `news_shadow_service.py` to the actual published id.

- [ ] **Step 7: Commit**

```bash
git add app/services/research_service.py
git commit -m "feat(news-agent): upgrade production model to Gemini 3.5 Flash + metrics plumbing"
```

---

## Task 4: Dispatch and collect the shadow call in `analyze_stock`

**Files:**
- Modify: `app/services/research_service.py` — `analyze_stock`, Phase 1 region (~lines 263-309) and the final return dict (~lines 650-682)

- [ ] **Step 1: Initialise shadow state before the Phase 1 executor**

In `analyze_stock`, after `news_prompt` is built and **before** the `with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:` block (~line 286), add:

```python
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
```

- [ ] **Step 2: Pass the production metrics sink into the News Agent dispatch**

In the Phase 1 `futures = { ... }` dict, the News Agent submit currently reads:

```python
        executor.submit(run_agent, "News Agent", self._call_agent, news_prompt, "News Agent", state): "news",
```

Change it to pass `news_metrics` as the trailing argument (it becomes `_call_agent`'s `metrics_sink`):

```python
        executor.submit(run_agent, "News Agent", self._call_agent, news_prompt, "News Agent", state, news_metrics): "news",
```

- [ ] **Step 3: Collect the shadow result after Phase 1**

After the `for future in concurrent.futures.as_completed(futures):` loop completes and `news_report` has been assigned (~after line 303), add:

```python
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
                    _shadow_executor.shutdown(wait=False)
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
```

- [ ] **Step 4: Add `news_shadow_data` to the return dict**

In the dict returned at the end of `analyze_stock` (~lines 650-682, the dict that already contains `"checklist": {...}`), add the key:

```python
            "news_shadow_data": news_shadow_data,
```

- [ ] **Step 5: Verify the module still imports**

Run: `python -c "import app.services.research_service"`
Expected: no error.

- [ ] **Step 6: Manual smoke verification**

Run the pipeline against a known recent drop with the shadow active (fewer than 20 rows in `news_shadow_runs`). Confirm in the logs that both `Calling News Agent with gemini-3.5-flash-preview` and `Calling News Agent (Shadow) with gemini-3-flash-preview` appear, and that the run completes normally even if the shadow line errors.

- [ ] **Step 7: Commit**

```bash
git add app/services/research_service.py
git commit -m "feat(news-shadow): dispatch isolated shadow call in analyze_stock"
```

---

## Task 5: Persist the shadow run once the decision point exists

**Files:**
- Modify: `app/services/stock_service.py` — near the `add_decision_point(...)` call (~line 1493)

The shadow record is built inside `analyze_stock` but the `decision_point_id` only exists after `add_decision_point()` returns. This task links them.

- [ ] **Step 1: Locate the integration point**

Read `app/services/stock_service.py` around line 1493. Identify:
- the variable holding the dict returned by `research_service.analyze_stock(...)` (referred to below as `analysis` — use the real name)
- the `decision_id` returned by `add_decision_point(...)`

Confirm `from app import database` is imported at the top of the file (add it if not).

- [ ] **Step 2: Add the persistence call**

Immediately after `decision_id` is obtained from `add_decision_point(...)`, add:

```python
        # Persist the News Agent shadow comparison, if one was run.
        shadow_data = analysis.get("news_shadow_data")  # use the real result-dict variable name
        if shadow_data:
            try:
                database.insert_news_shadow_run(decision_id, shadow_data)
            except Exception as e:
                logger.warning(f"Failed to persist News Agent shadow run: {e}")
```

If `stock_service.py` has no `logger`, use the module's existing logging mechanism (e.g. `print(...)`) to match the file's convention.

- [ ] **Step 3: Verify the module still imports**

Run: `python -c "import app.services.stock_service"`
Expected: no error.

- [ ] **Step 4: Verify persistence end-to-end**

Run the pipeline against a known recent drop with shadow active, then check the DB:

Run: `sqlite3 subscribers.db "SELECT id, decision_point_id, symbol, production_model, shadow_model, shadow_error FROM news_shadow_runs;"`
Expected: one row per analysed candidate, with a non-null `decision_point_id`, `production_model = gemini-3.5-flash-preview`, `shadow_model = gemini-3-flash-preview`.

- [ ] **Step 5: Commit**

```bash
git add app/services/stock_service.py
git commit -m "feat(news-shadow): persist shadow runs linked to decision points"
```

---

## Task 6: Comparison report — deterministic metrics and cost math

**Files:**
- Create: `scripts/analysis/news_shadow_report.py`
- Test: `tests/test_news_shadow_report.py`

This produces the side-by-side report. Task 6 covers the deterministic parts (economics-flag agreement, token/cost/latency math, raw side-by-side dump). Task 7 adds the LLM-judged accuracy dimensions.

- [ ] **Step 1: Write the failing test**

Create `tests/test_news_shadow_report.py`:

```python
import pytest

from scripts.analysis import news_shadow_report as nsr


def _row(idx, prod_econ, shadow_econ, perr=None):
    return {
        "id": idx,
        "decision_point_id": idx,
        "symbol": f"SYM{idx}",
        "decision_date": "2026-05-22",
        "production_model": "gemini-3.5-flash-preview",
        "production_report": "Prod report",
        "production_tokens_in": 1000,
        "production_tokens_out": 400,
        "production_latency_ms": 5000,
        "production_needs_economics": prod_econ,
        "shadow_model": "gemini-3-flash-preview",
        "shadow_report": "Shadow report",
        "shadow_tokens_in": 1000,
        "shadow_tokens_out": 400,
        "shadow_latency_ms": 6000,
        "shadow_needs_economics": shadow_econ,
        "shadow_error": perr,
    }


def test_economics_flag_agreement():
    rows = [_row(1, 1, 1), _row(2, 1, 0), _row(3, 0, 0)]
    stats = nsr.compute_deterministic_stats(rows)
    assert stats["economics_flag_agree"] == 2
    assert stats["economics_flag_disagree"] == 1


def test_cost_math_uses_pricing():
    rows = [_row(1, 1, 1)]
    stats = nsr.compute_deterministic_stats(rows)
    # 1000 in + 400 out at the configured per-Mtok rates.
    pin, pout = nsr.PRICING["gemini-3.5-flash-preview"]["in"], nsr.PRICING["gemini-3.5-flash-preview"]["out"]
    expected = (1000 / 1_000_000) * pin + (400 / 1_000_000) * pout
    assert stats["production_cost_per_dp"] == pytest.approx(expected)


def test_errored_shadow_excluded_from_pairs():
    rows = [_row(1, 1, 1), _row(2, 1, 1, perr="timeout")]
    stats = nsr.compute_deterministic_stats(rows)
    assert stats["completed_pairs"] == 1


def test_render_report_contains_sections():
    rows = [_row(1, 1, 1)]
    md = nsr.render_report(rows, judge_results=None)
    assert "# News Agent Shadow Comparison Report" in md
    assert "Cost per decision point" in md
    assert "SYM1" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_shadow_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.analysis.news_shadow_report'`

- [ ] **Step 3: Create the report module**

Create `scripts/analysis/news_shadow_report.py`:

```python
"""Generate the News Agent shadow-comparison report.

Reads the news_shadow_runs table and produces a side-by-side markdown report
for the production (Gemini 3.5 Flash) vs shadow (Gemini 3 Flash) models.

Usage:
    python -m scripts.analysis.news_shadow_report [--no-judge]
"""
import argparse
import datetime
import os
import sys
from typing import Any, Dict, List, Optional

# Allow running as a standalone script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app import database  # noqa: E402

# $ per 1,000,000 tokens. CONFIRM against current Gemini pricing before
# trusting the dollar figures in the report.
PRICING: Dict[str, Dict[str, float]] = {
    "gemini-3.5-flash-preview": {"in": 0.30, "out": 2.50},
    "gemini-3-flash-preview": {"in": 0.30, "out": 2.50},
}

OUTPUT_DIR = "audit_reports"


def _cost(model: str, tokens_in: int, tokens_out: int) -> float:
    rate = PRICING.get(model, {"in": 0.0, "out": 0.0})
    return (tokens_in / 1_000_000) * rate["in"] + (tokens_out / 1_000_000) * rate["out"]


def _as_bool(v: Any) -> Optional[bool]:
    if v is None:
        return None
    return bool(v)


def compute_deterministic_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Economics-flag agreement, cost, and latency aggregates."""
    pairs = [r for r in rows if r.get("shadow_report") and not r.get("shadow_error")]
    n = len(pairs)

    economics_agree = economics_disagree = 0
    prod_cost_total = shadow_cost_total = 0.0
    prod_latency_total = shadow_latency_total = 0

    for r in pairs:
        if _as_bool(r["production_needs_economics"]) == _as_bool(r["shadow_needs_economics"]):
            economics_agree += 1
        else:
            economics_disagree += 1
        prod_cost_total += _cost(r["production_model"],
                                 r["production_tokens_in"] or 0,
                                 r["production_tokens_out"] or 0)
        shadow_cost_total += _cost(r["shadow_model"],
                                   r["shadow_tokens_in"] or 0,
                                   r["shadow_tokens_out"] or 0)
        prod_latency_total += r["production_latency_ms"] or 0
        shadow_latency_total += r["shadow_latency_ms"] or 0

    divisor = n or 1
    return {
        "completed_pairs": n,
        "economics_flag_agree": economics_agree,
        "economics_flag_disagree": economics_disagree,
        "production_cost_per_dp": prod_cost_total / divisor,
        "shadow_cost_per_dp": shadow_cost_total / divisor,
        "production_avg_latency_ms": prod_latency_total / divisor,
        "shadow_avg_latency_ms": shadow_latency_total / divisor,
        "shadow_total_cost": shadow_cost_total,
    }


def render_report(rows: List[Dict[str, Any]],
                  judge_results: Optional[Dict[int, Dict[str, Any]]]) -> str:
    """Render the full markdown report. judge_results keyed by row id."""
    stats = compute_deterministic_stats(rows)
    errored = [r for r in rows if r.get("shadow_error")]
    n = stats["completed_pairs"]

    lines: List[str] = []
    lines.append("# News Agent Shadow Comparison Report")
    lines.append("")
    lines.append(f"Generated: {datetime.datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(f"- Production model: `gemini-3.5-flash-preview`")
    lines.append(f"- Shadow model: `gemini-3-flash-preview`")
    lines.append(f"- Completed paired runs: **{n}**")
    if errored:
        lines.append(f"- Shadow runs that errored (excluded from pairs): {len(errored)}")
    lines.append("")

    lines.append("## Summary metrics")
    lines.append("")
    lines.append("| Metric | Production (3.5 Flash) | Shadow (3 Flash) |")
    lines.append("|---|---|---|")
    lines.append(f"| Cost per decision point | ${stats['production_cost_per_dp']:.5f} "
                 f"| ${stats['shadow_cost_per_dp']:.5f} |")
    lines.append(f"| Avg latency (ms) | {stats['production_avg_latency_ms']:.0f} "
                 f"| {stats['shadow_avg_latency_ms']:.0f} |")
    lines.append("")
    lines.append(f"**Economics trigger flag agreement:** "
                 f"{stats['economics_flag_agree']}/{n} agree, "
                 f"{stats['economics_flag_disagree']}/{n} disagree.")
    lines.append("")
    lines.append(f"**Cost note:** the shadow validation cost a one-time total of "
                 f"${stats['shadow_total_cost']:.4f} across {n} runs. Ongoing "
                 f"production cost is ${stats['production_cost_per_dp']:.5f} per "
                 f"decision point. Pricing constants in this script must be "
                 f"confirmed against current Gemini pricing.")
    lines.append("")

    if judge_results:
        lines.append("## LLM-judged accuracy dimensions")
        lines.append("")
        _render_judge_summary(lines, rows, judge_results)
        lines.append("")

    lines.append("## Per-pair detail")
    lines.append("")
    for r in rows:
        lines.append(f"### Pair {r['id']} — {r['symbol']} ({r['decision_date']})")
        lines.append("")
        if r.get("shadow_error"):
            lines.append(f"_Shadow errored: {r['shadow_error']}_")
            lines.append("")
            continue
        lines.append(f"- Economics flag — production: "
                     f"`{_as_bool(r['production_needs_economics'])}`, "
                     f"shadow: `{_as_bool(r['shadow_needs_economics'])}`")
        if judge_results and r["id"] in judge_results:
            j = judge_results[r["id"]]
            lines.append(f"- Source classification: {j.get('source_classification', 'n/a')}")
            lines.append(f"- Hard-event detection: {j.get('hard_event_detection', 'n/a')}")
            lines.append(f"- Narrative coherence — production: "
                         f"{j.get('production_coherence', 'n/a')}, "
                         f"shadow: {j.get('shadow_coherence', 'n/a')}")
            if j.get("disagreements"):
                lines.append(f"- **Flagged for manual review:** {j['disagreements']}")
        lines.append("")
        lines.append("<details><summary>Production report (3.5 Flash)</summary>")
        lines.append("")
        lines.append("```")
        lines.append((r.get("production_report") or "").strip())
        lines.append("```")
        lines.append("</details>")
        lines.append("")
        lines.append("<details><summary>Shadow report (3 Flash)</summary>")
        lines.append("")
        lines.append("```")
        lines.append((r.get("shadow_report") or "").strip())
        lines.append("```")
        lines.append("</details>")
        lines.append("")

    lines.append("## Outcome")
    lines.append("")
    lines.append("Pick one based on the data above:")
    lines.append("")
    lines.append("- [ ] **Confirm** — 3.5 Flash stays in production, shadow disabled permanently.")
    lines.append("- [ ] **Roll back** — revert the News Agent to `gemini-3-flash-preview`.")
    lines.append("- [ ] **Conditional** — keep 3.5 Flash but flag scenario types where 3 Flash won.")
    lines.append("")
    return "\n".join(lines)


def _render_judge_summary(lines: List[str], rows: List[Dict[str, Any]],
                          judge_results: Dict[int, Dict[str, Any]]) -> None:
    """Aggregate the judge output. Filled in by Task 7."""
    judged = [judge_results[r["id"]] for r in rows if r["id"] in judge_results]
    src_better_prod = sum(1 for j in judged if j.get("source_classification") == "production_better")
    src_better_shadow = sum(1 for j in judged if j.get("source_classification") == "shadow_better")
    he_better_prod = sum(1 for j in judged if j.get("hard_event_detection") == "production_better")
    he_better_shadow = sum(1 for j in judged if j.get("hard_event_detection") == "shadow_better")
    lines.append(f"- Source classification: production better in {src_better_prod} pairs, "
                 f"shadow better in {src_better_shadow} pairs.")
    lines.append(f"- Hard-event detection: production better in {he_better_prod} pairs, "
                 f"shadow better in {he_better_shadow} pairs.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate News Agent shadow comparison report")
    parser.add_argument("--no-judge", action="store_true",
                        help="Skip the LLM-judged accuracy dimensions")
    args = parser.parse_args()

    rows = database.get_news_shadow_runs()
    if not rows:
        print("No news_shadow_runs found. Nothing to report.")
        return

    judge_results = None
    if not args.no_judge:
        try:
            from scripts.analysis.news_shadow_judge import judge_all_pairs
            judge_results = judge_all_pairs(rows)
        except Exception as e:
            print(f"LLM judge step failed, continuing without it: {e}")

    md = render_report(rows, judge_results)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(
        OUTPUT_DIR,
        f"news_shadow_comparison_{datetime.date.today().isoformat()}.md",
    )
    with open(out_path, "w") as f:
        f.write(md)
    print(f"Report written to {out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Ensure `scripts/analysis` is importable**

Run: `ls scripts/analysis/__init__.py`
If the file does not exist, create an empty `scripts/analysis/__init__.py`, and also ensure `scripts/__init__.py` exists (create empty if missing). This lets `from scripts.analysis import ...` resolve in tests.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_news_shadow_report.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add scripts/analysis/news_shadow_report.py tests/test_news_shadow_report.py scripts/__init__.py scripts/analysis/__init__.py
git commit -m "feat(news-shadow): add comparison report generator with cost math"
```

---

## Task 7: LLM-judged accuracy dimensions

**Files:**
- Create: `scripts/analysis/news_shadow_judge.py`
- Test: `tests/test_news_shadow_report.py` (append)

The economics flag is deterministic (Task 6). Source classification, hard-event detection, and narrative coherence require reading the free-text narratives — this task adds a Gemini Pro judge that compares each pair and emits structured JSON.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_news_shadow_report.py`:

```python
from scripts.analysis import news_shadow_judge as nsj


def test_parse_judge_response_valid():
    raw = '''```json
{"source_classification": "tie",
 "hard_event_detection": "production_better",
 "production_coherence": "high",
 "shadow_coherence": "medium",
 "disagreements": "shadow misclassified a wire source as official"}
```'''
    parsed = nsj.parse_judge_response(raw)
    assert parsed["hard_event_detection"] == "production_better"
    assert parsed["production_coherence"] == "high"


def test_parse_judge_response_malformed_returns_fallback():
    parsed = nsj.parse_judge_response("not json at all")
    assert parsed["source_classification"] == "parse_error"
    assert "disagreements" in parsed


def test_build_judge_prompt_includes_both_reports():
    prompt = nsj.build_judge_prompt("PROD TEXT HERE", "SHADOW TEXT HERE")
    assert "PROD TEXT HERE" in prompt
    assert "SHADOW TEXT HERE" in prompt
    assert "source" in prompt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_shadow_report.py -v -k judge`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.analysis.news_shadow_judge'`

- [ ] **Step 3: Create the judge module**

Create `scripts/analysis/news_shadow_judge.py`:

```python
"""LLM judge for the News Agent shadow comparison.

Compares the production and shadow News Agent reports for each pair across the
accuracy dimensions that cannot be measured deterministically: source
classification, hard-event detection, and narrative coherence.
"""
import json
import os
import re
import time
from typing import Any, Dict, List

JUDGE_MODEL = "gemini-3.1-pro-preview"

JUDGE_INSTRUCTIONS = """You are a neutral evaluator comparing two News Agent reports for the same stock, same news data, same time window. Only the underlying model differs. Report A is the production model; Report B is the shadow model.

Evaluate across these dimensions:
1. source_classification — which report more accurately classifies cited articles as official / wire / analyst / opinion. Answer one of: "production_better", "shadow_better", "tie".
2. hard_event_detection — which report more correctly identifies event-driven catalysts (earnings, guidance cuts, M&A, legal/regulatory) versus rumor/sentiment moves. Answer one of: "production_better", "shadow_better", "tie".
3. production_coherence — does Report A's synthesis hang together without hallucinating connections unsupported by cited articles. Answer one of: "high", "medium", "low".
4. shadow_coherence — same judgement for Report B. Answer one of: "high", "medium", "low".
5. disagreements — a short string describing any specific discrepancy worth manual review (especially source misclassification or a missed/invented hard event). Empty string if none.

Respond ONLY with a JSON object with exactly these keys: source_classification, hard_event_detection, production_coherence, shadow_coherence, disagreements."""


def build_judge_prompt(production_report: str, shadow_report: str) -> str:
    return (
        JUDGE_INSTRUCTIONS
        + "\n\n=== REPORT A (production) ===\n"
        + (production_report or "")
        + "\n\n=== REPORT B (shadow) ===\n"
        + (shadow_report or "")
        + "\n\nReturn the JSON object now."
    )


def parse_judge_response(raw: str) -> Dict[str, Any]:
    """Extract the JSON object from the judge response, tolerant of code fences."""
    fallback = {
        "source_classification": "parse_error",
        "hard_event_detection": "parse_error",
        "production_coherence": "parse_error",
        "shadow_coherence": "parse_error",
        "disagreements": "judge response could not be parsed",
    }
    if not raw:
        return fallback
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return fallback
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return fallback
    for key in fallback:
        data.setdefault(key, "n/a")
    return data


def _make_client():
    """Create a Google GenAI client. Kept separate so tests can skip it."""
    from google import genai as new_genai
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    return new_genai.Client(api_key=api_key)


def judge_all_pairs(rows: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Judge every completed pair. Returns a dict keyed by row id."""
    client = _make_client()
    results: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        if not r.get("shadow_report") or r.get("shadow_error"):
            continue
        prompt = build_judge_prompt(r.get("production_report", ""),
                                    r.get("shadow_report", ""))
        try:
            response = client.models.generate_content(
                model=JUDGE_MODEL, contents=prompt
            )
            results[r["id"]] = parse_judge_response(getattr(response, "text", ""))
        except Exception as e:
            results[r["id"]] = parse_judge_response("")
            results[r["id"]]["disagreements"] = f"judge call failed: {e}"
        time.sleep(1)  # gentle pacing; offline script, not latency-sensitive
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_news_shadow_report.py -v`
Expected: PASS (all tests — the judge tests exercise only `build_judge_prompt` and `parse_judge_response`, which need no API key)

- [ ] **Step 5: Commit**

```bash
git add scripts/analysis/news_shadow_judge.py tests/test_news_shadow_report.py
git commit -m "feat(news-shadow): add LLM judge for accuracy dimensions"
```

---

## Task 8: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full new test set**

Run: `pytest tests/test_news_shadow_service.py tests/test_news_shadow_report.py -v`
Expected: all tests PASS.

- [ ] **Step 2: Confirm no regression in the broader suite**

Run: `pytest tests/ -q`
Expected: no new failures introduced by this change (pre-existing failures unrelated to the News Agent are acceptable — note them).

- [ ] **Step 3: Confirm both modules import cleanly**

Run: `python -c "import app.services.research_service, app.services.stock_service, app.services.news_shadow_service"`
Expected: no error.

- [ ] **Step 4: Generate a dry report against current DB state**

Run: `python -m scripts.analysis.news_shadow_report --no-judge`
Expected: either `No news_shadow_runs found.` (if none yet) or a report written to `audit_reports/`.

---

## Post-completion: validation lifecycle

Once this plan is implemented and merged:

1. **Production effect is immediate** — the next decision point runs the News Agent on Gemini 3.5 Flash.
2. **Shadow runs automatically** for the next 20 completed pairs, then self-disables (`is_shadow_active()` returns `False` once `news_shadow_runs` has 20 successful rows). No code change needed to stop it; no ongoing cost.
3. **After 20 pairs**, run `python -m scripts.analysis.news_shadow_report` (with the judge) to produce the side-by-side report in `audit_reports/`.
4. **Make the call** — Confirm / Roll back / Conditional, using the checklist at the bottom of the generated report. Roll back, if chosen, is a one-line revert of `PRODUCTION_NEWS_MODEL` in `news_shadow_service.py`.
5. **Before trusting dollar figures**, confirm the `PRICING` constants in `news_shadow_report.py` against current Gemini pricing.

---

## Self-review notes

- **Spec coverage:** model swap (Task 3), shadow on previous model with identical prompt (Tasks 2+4), side-by-side storage tagged by model + decision point (Tasks 1+5), self-disable after 20 (Task 2 `is_shadow_active`), isolation/non-interference (Task 4 try/except + separate executor), all five tracked dimensions — source classification + hard events + narrative coherence (Task 7 judge), economics flag (Tasks 2+6), cost & latency (Tasks 3 metrics + 6 math) — and the three-outcome report with cost-per-decision-point math (Task 6). No retroactive comparison: shadow only runs forward inside `analyze_stock`.
- **Open dependency to verify during execution:** the exact published id for "Gemini 3.5 Flash" — `PRODUCTION_NEWS_MODEL` must be confirmed against the live model list (Task 3, Step 6).

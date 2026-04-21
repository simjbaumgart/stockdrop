# Deep Research Reliability Fix — Implementation Plan

**Date:** 2026-03-29
**Status:** Proposed
**Impact:** Fixes ~71% of BUY recs silently missing deep research + unlocks DR for all BUY_LIMITs
**Files changed:** 2 (`deep_research_service.py`, `stock_service.py`)

---

## Problem Summary

Two interacting bugs cause the system's best feature (deep research) to miss most of the stocks it should analyze:

1. **Queue loss on restart.** The deep research queue is an in-memory `Queue()` (line 19, `deep_research_service.py`). Each task takes up to 90 min. If the Render process restarts before the queue drains, pending tasks are lost silently. On March 17, 2 of 4 BUY recs were lost this way.

2. **BUY_LIMIT gate too restrictive.** The trigger (line 547, `stock_service.py`) requires `conviction == HIGH AND risk_reward > 1.5` for BUY_LIMIT. Data shows BUY_LIMIT with DR has 57.9% win rate (+2.71% median) vs 45.8% (-0.58% median) without. The gate blocks the stocks that need DR most.

3. **Backfill can't catch what the gate drops.** The backfill query (line 649, `stock_service.py`) also requires `conviction IN ('MODERATE', 'HIGH') AND risk_reward_ratio >= 1.5`, and BUY recs with missing conviction data slip through.

4. **Silent exception swallowing.** The DR trigger block (line 1600, `stock_service.py`) catches all exceptions with a bare `print()`, making failures invisible in production logs.

---

## Fix 1: Persist the Deep Research Queue

**File:** `app/services/deep_research_service.py`

### What to change

Replace the in-memory `Queue()` with a SQLite-backed persistent queue. Use the existing DB connection pattern (`DB_NAME = os.getenv("DB_PATH", "subscribers.db")`).

### New table: `deep_research_queue`

```sql
CREATE TABLE IF NOT EXISTS deep_research_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    task_type TEXT NOT NULL DEFAULT 'individual',  -- 'individual' or 'batch_comparison'
    context_json TEXT NOT NULL,                     -- JSON blob of the context dict
    decision_id INTEGER,
    status TEXT DEFAULT 'PENDING',                  -- PENDING, PROCESSING, COMPLETED, FAILED
    priority INTEGER DEFAULT 1,                     -- 1=individual (high), 2=batch (low)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT
);
```

### Changes to `__init__` (line 15-45)

- Remove: `self.individual_queue = Queue()` and `self.batch_queue = Queue()` (lines 19-20)
- Add: `self._init_queue_table()` method that creates the table if not exists
- Add: on startup, reset any tasks stuck in `PROCESSING` status back to `PENDING` (these are tasks that were running when the process died)

### Changes to `queue_research_task` (line 348-359)

Replace `self.individual_queue.put(...)` with an INSERT into `deep_research_queue`:

```python
def queue_research_task(self, symbol: str, context: dict, decision_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO deep_research_queue (symbol, task_type, context_json, decision_id, priority)
        VALUES (?, 'individual', ?, ?, 1)
    """, (symbol, json.dumps(context), decision_id))
    conn.commit()
    conn.close()
    logger.info(f"[Deep Research] Queued INDIVIDUAL task for {symbol} (Priority: High)")
```

Same pattern for `queue_batch_comparison_task` (line 361-372) with `priority=2`.

### Changes to `_worker_loop` (line 278-347)

Replace the `Queue.get_nowait()` calls with a DB query:

```python
# Instead of individual_queue.get_nowait() / batch_queue.get_nowait():
conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()
cursor.execute("""
    SELECT id, symbol, task_type, context_json, decision_id
    FROM deep_research_queue
    WHERE status = 'PENDING'
    ORDER BY priority ASC, created_at ASC
    LIMIT 1
""")
row = cursor.fetchone()
if not row:
    conn.close()
    time.sleep(1)
    continue

task_id, symbol, task_type, context_json, decision_id = row

# Mark as PROCESSING
cursor.execute("""
    UPDATE deep_research_queue SET status = 'PROCESSING', started_at = CURRENT_TIMESTAMP
    WHERE id = ?
""", (task_id,))
conn.commit()
conn.close()
```

After task completion, mark `COMPLETED`. On failure, mark `FAILED` with error message.

Remove `self.individual_queue.task_done()` and `self.batch_queue.task_done()` calls (lines 332, 335).

### Changes to `wait_for_completion` (around line 89)

Update to check DB instead of queue sizes:

```python
# Instead of checking individual_queue.qsize() and batch_queue.qsize():
cursor.execute("SELECT COUNT(*) FROM deep_research_queue WHERE status IN ('PENDING', 'PROCESSING')")
```

### Changes to monitor thread (around line 57)

Update queue size reporting to use DB counts instead of `self.individual_queue.qsize()`.

### Startup recovery

In `__init__`, after creating the table, add:

```python
# Reset zombie tasks from previous crash
conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()
cursor.execute("""
    UPDATE deep_research_queue SET status = 'PENDING', started_at = NULL
    WHERE status = 'PROCESSING'
""")
recovered = cursor.rowcount
conn.commit()
conn.close()
if recovered:
    logger.info(f"[Deep Research] Recovered {recovered} tasks from previous crash")
```

---

## Fix 2: Relax the BUY_LIMIT Deep Research Gate

**File:** `app/services/stock_service.py`

### Change `_should_trigger_deep_research` (line 547-569)

**Before:**
```python
def _should_trigger_deep_research(self, report_data: dict) -> bool:
    action = report_data.get("recommendation", "AVOID").upper()
    conviction = report_data.get("conviction", "LOW").upper()
    risk_reward = report_data.get("risk_reward_ratio", 0)

    if action == "BUY":
        return True

    if action == "BUY_LIMIT":
        try:
            if conviction == "HIGH" and float(risk_reward) > 1.5:
                return True
        except (TypeError, ValueError):
            return False

    return False
```

**After:**
```python
def _should_trigger_deep_research(self, report_data: dict) -> bool:
    action = report_data.get("recommendation", "AVOID").upper()
    conviction = report_data.get("conviction", "LOW").upper()

    # BUY: always trigger
    if action == "BUY":
        return True

    # BUY_LIMIT: trigger for MODERATE or HIGH conviction
    if action == "BUY_LIMIT":
        if conviction in ("MODERATE", "HIGH"):
            return True

    return False
```

The R/R requirement is dropped. Rationale: the data shows DR improves BUY_LIMIT win rate by +12pp regardless of R/R, and R/R is often missing or unreliable from the PM output.

---

## Fix 3: Align Backfill Query with Primary Trigger

**File:** `app/services/stock_service.py`

### Change backfill query (line 649-655)

**Before:**
```sql
SELECT * FROM decision_points
WHERE date(timestamp) = ?
AND recommendation IN ('BUY', 'BUY_LIMIT')
AND conviction IN ('MODERATE', 'HIGH')
AND risk_reward_ratio >= 1.5
AND (deep_research_verdict IS NULL OR deep_research_verdict = '' OR deep_research_verdict = '-'
     OR deep_research_verdict LIKE 'UNKNOWN%' OR deep_research_verdict = 'ERROR_PARSING')
```

**After:**
```sql
SELECT * FROM decision_points
WHERE date(timestamp) = ?
AND (
    -- BUY: always backfill (matches primary trigger)
    recommendation = 'BUY'
    OR
    -- BUY_LIMIT: MODERATE or HIGH conviction (matches primary trigger)
    (recommendation = 'BUY_LIMIT' AND conviction IN ('MODERATE', 'HIGH'))
)
AND (deep_research_verdict IS NULL OR deep_research_verdict = '' OR deep_research_verdict = '-'
     OR deep_research_verdict LIKE 'UNKNOWN%' OR deep_research_verdict = 'ERROR_PARSING')
```

Key change: BUY no longer requires conviction or R/R data to qualify for backfill. This catches the stocks with missing conviction that were slipping through.

---

## Fix 4: Replace Silent Exception with Proper Logging

**File:** `app/services/stock_service.py`

### Change exception handler (line 1600-1601)

**Before:**
```python
except Exception as e:
    print(f"Error checking Deep Research trigger: {e}")
```

**After:**
```python
except Exception as e:
    logger.error(
        f"[Deep Research Trigger] FAILED for {symbol} "
        f"(Rec: {recommendation}, Conviction: {report_data.get('conviction')}, "
        f"R/R: {report_data.get('risk_reward_ratio')}): {e}",
        exc_info=True  # includes full traceback
    )
```

Also add `import logging` and `logger = logging.getLogger(__name__)` at the top of `stock_service.py` if not already present.

---

## Implementation Order

1. **Fix 4 first** (logging) — 5 minutes, zero risk, immediately helps diagnose any other issues
2. **Fix 2** (relax gate) — 5 minutes, low risk, immediate impact on new BUY_LIMITs
3. **Fix 3** (backfill alignment) — 5 minutes, low risk, catches stocks missed by gate
4. **Fix 1** (queue persistence) — 30-60 minutes, moderate complexity, requires testing

## Testing

- **Fix 1:** Queue a task, kill the process, restart → verify task resumes. Queue 3 tasks, verify they process in order. Check monitor thread reports correct counts.
- **Fix 2:** Run a BUY_LIMIT stock with MODERATE conviction → verify DR triggers. Run one with LOW conviction → verify DR does NOT trigger.
- **Fix 3:** Insert a BUY with NULL conviction into `decision_points`, run backfill → verify it gets picked up.
- **Fix 4:** Force an exception in `_build_deep_research_context` → verify full traceback appears in logs.

## Expected Impact

Based on the data analysis:
- **BUY coverage:** 29% → ~90%+ (queue persistence prevents loss)
- **BUY_LIMIT coverage:** 20-30% → ~60-70% (MODERATE conviction included)
- **Estimated win rate improvement on BUY_LIMIT pool:** +12pp (from 45.8% to ~57.9%)
- **Processing capacity:** ~16 stocks/day is sufficient for current conservative regime (~7 BUYs + ~20 BUY_LIMITs per month)

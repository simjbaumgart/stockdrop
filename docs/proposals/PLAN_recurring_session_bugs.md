# Recurring Session Bugs — Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate four bugs that recur every session — Drive quota 403s burned after each breaker cooldown, the NLTK Security Violation at startup, thin single-retry coverage on Polygon/Benzinga timeouts, and an agent-quota label that resets on every process restart.

**Architecture:** Four independent task groups, each individually testable and committable. None share state. The Render service mounts a persistent disk at `/var/lib/data` (`DATA_DIR`) that survives deploys/restarts — breaker state and the agent-quota window move there; the NLTK corpus is vendored into the tracked repo so it ships with every deploy and is never downloaded.

**Tech Stack:** Python 3.9, FastAPI, `requests`, NLTK (via `defeatbeta_api`), pytest / pytest-asyncio.

**Out of scope:** The Drive **OAuth migration** is the only true fix for the quota 403s (the service account has ~0 Drive storage quota). It needs design decisions — OAuth flow choice, refresh-token storage, consent — and is tracked separately. Task Group D only *stops the bleed* (1 burned 403 per cycle instead of 3) until OAuth lands.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `nltk_data/tokenizers/punkt_tab/**` | Vendored NLTK corpus, ships with repo | Create (tracked) |
| `.gitignore` | Stop ignoring vendored corpus; relocate breaker state | Modify |
| `app/services/stock_service.py` | NLTK_DATA pinning + punkt seeding | Modify lines 38–76 |
| `tests/test_nltk_preseed.py` | NLTK seeding behaviour | Modify |
| `app/services/benzinga_service.py` | Benzinga HTTP fetch + retry | Modify lines 19–20, 69–97 |
| `app/services/polygon_service.py` | Polygon HTTP fetch + retry | Modify timeout consts + retry loop |
| `tests/test_api_retry.py` | Retry-loop coverage for both services | Create |
| `app/utils/agent_call_counter.py` | Agent-call telemetry, now durable | Modify whole file |
| `tests/test_agent_call_counter.py` | Counter persistence + eviction | Modify |
| `main.py` | agent-quota log line | Modify line 161–162 |
| `app/services/drive_service.py` | Drive breaker — persistent path + sticky quota | Modify lines 22–35, 73–97 |
| `tests/test_drive_breaker.py` | Breaker sticky-quota behaviour | Create |

---

## Task Group A — NLTK Security Violation

**Root cause:** `defeatbeta_api` lazy-downloads the `punkt_tab` corpus on import. `stock_service.py` currently pre-seeds it (`_ensure_nltk_punkt`) into `<repo>/.nltk_data`, but that path is `.gitignore`d (line 57) and rebuilt on every Render deploy — so a network download still fires at startup, and the sandbox flags that opaque fetch as an *NLTK Security Violation*. Fix: vendor the corpus into the **tracked** repo so it is always present and `download()` is never reached.

### Task A1: Vendor the punkt_tab corpus into the repo

**Files:**
- Create: `nltk_data/tokenizers/punkt_tab/**` (downloaded corpus)
- Modify: `.gitignore:57`

- [ ] **Step 1: Download the corpus into the tracked path**

Run:
```bash
python -c "import nltk; nltk.download('punkt_tab', download_dir='nltk_data', quiet=True)"
```
Expected: creates `nltk_data/tokenizers/punkt_tab/` with language `.pickle`/`.tab` files.

- [ ] **Step 2: Un-ignore the vendored corpus**

In `.gitignore`, change line 57 from:
```
.nltk_data/
```
to:
```
.nltk_data/
# Vendored NLTK corpus ships with the repo (see PLAN_recurring_session_bugs.md)
!nltk_data/
!nltk_data/**
```

- [ ] **Step 3: Verify the corpus is tracked and commit**

Run: `git status --short nltk_data/ | head`
Expected: untracked `nltk_data/tokenizers/punkt_tab/...` files listed.

```bash
git add nltk_data/ .gitignore
git commit -m "feat(nltk): vendor punkt_tab corpus into tracked repo"
```

### Task A2: Point NLTK_DATA at the vendored corpus, make seeding verify-only

**Files:**
- Modify: `app/services/stock_service.py:38-76`
- Test: `tests/test_nltk_preseed.py`

- [ ] **Step 1: Update the failing test**

Replace the body of `tests/test_nltk_preseed.py` with these tests:

```python
import os
import importlib


def test_vendored_corpus_exists():
    """The punkt_tab corpus must ship with the repo (no startup download)."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    punkt = os.path.join(repo_root, "nltk_data", "tokenizers", "punkt_tab")
    assert os.path.isdir(punkt), f"vendored corpus missing at {punkt}"


def test_ensure_nltk_punkt_never_downloads_when_present(monkeypatch, tmp_path):
    """When the corpus dir exists, _ensure_nltk_punkt must not call download()."""
    from app.services import stock_service

    punkt_dir = tmp_path / "tokenizers" / "punkt_tab"
    punkt_dir.mkdir(parents=True)

    class FakeNltk:
        def download(self, *a, **kw):
            raise AssertionError("download() must not be called when corpus present")

    stock_service._ensure_nltk_punkt(str(tmp_path), FakeNltk())


def test_ensure_nltk_punkt_warns_when_missing(monkeypatch, tmp_path, capsys):
    """When the corpus is absent it must warn — NOT download (avoids the violation)."""
    from app.services import stock_service

    class FakeNltk:
        def download(self, *a, **kw):
            raise AssertionError("download() must not be called — corpus is vendored")

    stock_service._ensure_nltk_punkt(str(tmp_path), FakeNltk())
    assert "punkt_tab corpus missing" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_nltk_preseed.py -v`
Expected: `test_vendored_corpus_exists` PASSES (corpus committed in A1); the two `_ensure_nltk_punkt` tests FAIL — current impl calls `download()` when the dir is missing.

- [ ] **Step 3: Rewrite the NLTK block in `stock_service.py`**

Replace lines 38–76 of `app/services/stock_service.py` with:

```python
# Pin NLTK's data dir to the vendored, tracked corpus that ships with the
# repo. DefeatBeta lazy-downloads 'punkt_tab' on first use; an uncontrolled
# network fetch at startup is flagged by the sandbox as an NLTK Security
# Violation. Because the corpus is committed under nltk_data/, the seeding
# function below only verifies presence — it never downloads.
import os as _os
_NLTK_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__)))), "nltk_data")
_os.environ["NLTK_DATA"] = _NLTK_DIR


def _ensure_nltk_punkt(nltk_dir: str, nltk_module) -> None:
    """Verify `<nltk_dir>/tokenizers/punkt_tab` is present. Never downloads.

    The corpus is vendored into the repo (see nltk_data/). If it is somehow
    missing we warn loudly rather than triggering an opaque network fetch,
    which the sandbox flags as a security violation.
    """
    if nltk_module is None:
        return
    punkt_dir = _os.path.join(nltk_dir, "tokenizers", "punkt_tab")
    if not _os.path.isdir(punkt_dir):
        print(f"[NLTK] punkt_tab corpus missing at {punkt_dir} — "
              f"re-run: python -c \"import nltk; "
              f"nltk.download('punkt_tab', download_dir='nltk_data')\"")


try:
    import nltk as _nltk  # transitively present via defeatbeta_api
    if _NLTK_DIR not in _nltk.data.path:
        _nltk.data.path.insert(0, _NLTK_DIR)
    _ensure_nltk_punkt(_NLTK_DIR, _nltk)
except Exception:
    # NLTK absent: defeatbeta's own lazy path still applies.
    pass
```

Note: this drops both the SSL-bypass block (lines 32–36) and the `_os.environ.setdefault` — set `NLTK_DATA` unconditionally so a stale env value cannot point seeding elsewhere. The SSL bypass existed only to make the download work; with no download it is dead code. Also remove the now-unused `import ssl as _ssl` at line 32.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_nltk_preseed.py -v`
Expected: all three tests PASS.

- [ ] **Step 5: Verify the service still imports cleanly**

Run: `python -c "import app.services.stock_service"`
Expected: no `NLTK` errors, no traceback.

- [ ] **Step 6: Commit**

```bash
git add app/services/stock_service.py tests/test_nltk_preseed.py
git commit -m "fix(nltk): verify vendored corpus instead of downloading at startup"
```

---

## Task Group B — Polygon / Benzinga timeout retries

**Root cause:** Both services use a flat `REQUEST_TIMEOUT = 10` and `for attempt in range(2)` — one initial call plus a single retry with a fixed 1s backoff. A single transient `Read timed out` exhausts the budget. Fix: split connect/read timeouts, raise the read budget, and give 2 retries with exponential backoff.

### Task B1: Add retry coverage tests

**Files:**
- Create: `tests/test_api_retry.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_retry.py`:

```python
import requests
from unittest.mock import patch, MagicMock

from app.services.benzinga_service import BenzingaService
from app.services.polygon_service import PolygonService


def _ok_response(payload):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = payload
    return r


def test_benzinga_retries_twice_then_succeeds():
    svc = BenzingaService()
    svc.api_key = "test-key"
    svc._breaker.reset()
    calls = []

    def fake_get(*a, **kw):
        calls.append(kw.get("timeout"))
        if len(calls) < 3:
            raise requests.exceptions.Timeout("read timed out")
        return _ok_response({"results": []})

    with patch("app.services.benzinga_service.requests.get", side_effect=fake_get), \
         patch("app.services.benzinga_service.time.sleep"):
        svc.get_company_news("FIGR")

    assert len(calls) == 3, "expected initial + 2 retries"
    assert calls[0] == (5, 20), "timeout must be a (connect, read) tuple"


def test_polygon_retries_twice_then_gives_up():
    svc = PolygonService()
    svc.api_key = "test-key"
    svc._breaker.reset()
    calls = []

    def fake_get(*a, **kw):
        calls.append(1)
        raise requests.exceptions.Timeout("read timed out")

    with patch("app.services.polygon_service.requests.get", side_effect=fake_get), \
         patch("app.services.polygon_service.time.sleep"):
        result = svc.get_company_news("FIGR")

    assert len(calls) == 3, "expected initial + 2 retries before giving up"
    assert result == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_api_retry.py -v`
Expected: FAIL — current code makes only 2 calls and passes an `int` timeout, not a tuple. (If `ApiCircuitBreaker` has no `reset()` method, add one in B2.)

### Task B2: Widen retries and timeouts in both services

**Files:**
- Modify: `app/services/benzinga_service.py:19-20,69-97`
- Modify: `app/services/polygon_service.py` (timeout consts + retry loop, ~lines 14-16,51-76)
- Modify: `app/utils/api_breaker.py` (add `reset()` if absent)

- [ ] **Step 1: Update Benzinga constants**

In `app/services/benzinga_service.py`, replace lines 19–20:
```python
    REQUEST_TIMEOUT = 10
    RETRY_BACKOFF_SECONDS = 1.0
```
with:
```python
    REQUEST_TIMEOUT = (5, 20)   # (connect, read) seconds
    RETRY_BACKOFF_SECONDS = 1.0
    MAX_ATTEMPTS = 3            # initial + 2 retries
```

- [ ] **Step 2: Update the Benzinga retry loop**

In `get_company_news`, replace `for attempt in range(2):` (line 73) with `for attempt in range(self.MAX_ATTEMPTS):` and replace the fixed backoff at lines 91–92:
```python
            if attempt == 0:
                time.sleep(self.RETRY_BACKOFF_SECONDS)
```
with exponential backoff on every non-final attempt:
```python
            if attempt < self.MAX_ATTEMPTS - 1:
                time.sleep(self.RETRY_BACKOFF_SECONDS * (2 ** attempt))
```
Update the comment at line 69 to read `# Initial + 2 retries on Timeout / RequestException / 5xx.`

- [ ] **Step 3: Apply the identical change to Polygon**

In `app/services/polygon_service.py`: set `REQUEST_TIMEOUT = (5, 20)`, add `MAX_ATTEMPTS = 3`, change `for attempt in range(2):` to `for attempt in range(self.MAX_ATTEMPTS):`, and replace:
```python
            if attempt == 0:
                time.sleep(self.RETRY_BACKOFF_SECONDS)
```
with:
```python
            if attempt < self.MAX_ATTEMPTS - 1:
                time.sleep(self.RETRY_BACKOFF_SECONDS * (2 ** attempt))
```
Update the `# initial + 1 retry` comment to `# initial + 2 retries`.

- [ ] **Step 4: Ensure `ApiCircuitBreaker.reset()` exists**

Read `app/utils/api_breaker.py`. If there is no `reset()` method, add one:
```python
    def reset(self) -> None:
        """Clear all failure state. Used by tests and manual recovery."""
        with self._lock:
            self._consecutive_failures = 0
            self._disabled_until = None
            self._save_state()
```
Match the actual attribute names in that file (the explore notes call them `_consecutive_failures` / `_disabled_until` — verify before editing).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_api_retry.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/benzinga_service.py app/services/polygon_service.py app/utils/api_breaker.py tests/test_api_retry.py
git commit -m "fix(api): widen Polygon/Benzinga timeouts and add a second retry"
```

---

## Task Group C — agent-quota true rolling 24h window

**Root cause:** `AgentCallCounter._rolling` is an in-memory `deque`. The 24h eviction is correct *within* a process, but every Render restart starts it empty — so `main.py:162` honestly logs `rolling(session)=` rather than a real 24h figure. Fix: persist the window to an append-only NDJSON file on the persistent disk, reload + evict on init.

### Task C1: Make the counter durable

**Files:**
- Modify: `app/utils/agent_call_counter.py` (whole file)
- Test: `tests/test_agent_call_counter.py`

- [ ] **Step 1: Add the failing persistence tests**

Append to `tests/test_agent_call_counter.py`:

```python
def test_rolling_window_survives_reinstantiation(tmp_path):
    from app.utils.agent_call_counter import AgentCallCounter

    path = tmp_path / "agent_calls.ndjson"
    c1 = AgentCallCounter(state_path=str(path))
    c1.record("pm")
    c1.record("phase1.technical")

    c2 = AgentCallCounter(state_path=str(path))
    assert c2.snapshot()["total_rolling_window"] == 2


def test_rolling_window_evicts_stale_entries_on_load(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone
    from app.utils.agent_call_counter import AgentCallCounter

    path = tmp_path / "agent_calls.ndjson"
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    path.write_text(
        json.dumps({"ts": old, "agent": "pm"}) + "\n"
        + json.dumps({"ts": fresh, "agent": "pm"}) + "\n"
    )

    c = AgentCallCounter(state_path=str(path))
    assert c.snapshot()["total_rolling_window"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_agent_call_counter.py -v`
Expected: FAIL — `AgentCallCounter.__init__` takes no `state_path` argument.

- [ ] **Step 3: Rewrite `agent_call_counter.py`**

Replace the whole file with:

```python
"""Process-durable agent-call counter. Thread-safe. Two views: a
reset-on-cycle per-cycle tally, and a true rolling 24h window persisted to
an append-only NDJSON file so it survives process restarts.

The provider dashboard remains the source of truth for billing; this is
zero-dependency ops telemetry."""

from __future__ import annotations

import json
import os
import threading
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Optional, Tuple


def _default_state_path() -> str:
    return os.path.join(os.getenv("DATA_DIR", "data"), "agent_calls.ndjson")


class AgentCallCounter:
    def __init__(self, window: timedelta = timedelta(hours=24),
                 state_path: Optional[str] = None):
        self._lock = threading.Lock()
        self._cycle: Counter = Counter()
        self._rolling: Deque[Tuple[datetime, str]] = deque()
        self._window = window
        self._state_path = state_path or _default_state_path()
        self._load()

    def record(self, agent: str) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            self._cycle[agent] += 1
            self._rolling.append((now, agent))
            self._evict_locked(now)
            self._append_locked(now, agent)

    def reset_cycle(self) -> None:
        with self._lock:
            self._cycle.clear()

    def snapshot(self) -> Dict:
        now = datetime.now(timezone.utc)
        with self._lock:
            self._evict_locked(now)
            return {
                "total_cycle": sum(self._cycle.values()),
                "total_rolling_window": len(self._rolling),
                "by_agent": dict(self._cycle),
            }

    def _evict_locked(self, now: datetime) -> None:
        cutoff = now - self._window
        while self._rolling and self._rolling[0][0] < cutoff:
            self._rolling.popleft()

    def _load(self) -> None:
        if not os.path.exists(self._state_path):
            return
        now = datetime.now(timezone.utc)
        try:
            with open(self._state_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["ts"])
                    self._rolling.append((ts, rec["agent"]))
        except Exception as e:
            print(f"[agent-quota] could not load state: {e}")
        self._evict_locked(now)
        self._compact_locked()

    def _append_locked(self, ts: datetime, agent: str) -> None:
        try:
            os.makedirs(os.path.dirname(self._state_path) or ".", exist_ok=True)
            with open(self._state_path, "a") as f:
                f.write(json.dumps({"ts": ts.isoformat(), "agent": agent}) + "\n")
        except Exception as e:
            print(f"[agent-quota] could not persist call: {e}")

    def _compact_locked(self) -> None:
        """Rewrite the file with only the live (non-evicted) window."""
        try:
            os.makedirs(os.path.dirname(self._state_path) or ".", exist_ok=True)
            tmp = self._state_path + ".tmp"
            with open(tmp, "w") as f:
                for ts, agent in self._rolling:
                    f.write(json.dumps({"ts": ts.isoformat(), "agent": agent}) + "\n")
            os.replace(tmp, self._state_path)
        except Exception as e:
            print(f"[agent-quota] could not compact state: {e}")


# Module-level singleton. Import from here at call sites.
counter = AgentCallCounter()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_agent_call_counter.py -v`
Expected: all tests PASS, including the pre-existing ones.

- [ ] **Step 5: Update the label in `main.py`**

In `main.py`, change line 162 from:
```python
                        f"rolling(session)={snap['total_rolling_window']} "
```
to:
```python
                        f"rolling(24h)={snap['total_rolling_window']} "
```

- [ ] **Step 6: Ignore the state file in git**

Add to `.gitignore`:
```
# Agent-quota rolling window (runtime state)
data/agent_calls.ndjson
```

- [ ] **Step 7: Commit**

```bash
git add app/utils/agent_call_counter.py tests/test_agent_call_counter.py main.py .gitignore
git commit -m "feat(agent-quota): persist rolling window for a true 24h figure"
```

---

## Task Group D — Drive breaker: stop re-burning 403s

**Root cause:** Two compounding problems. (1) `BREAKER_STATE_FILE = '.drive_breaker_state.json'` is a relative path in the ephemeral repo dir — lost on every deploy, so the breaker re-arms from scratch. (2) After the 24h cooldown lapses, `_breaker_tripped()` resets `_consecutive_quota_failures` to 0, so the next scan burns a fresh **3** real 403s before re-tripping. The service account has ~0 Drive quota, so these failures are not transient — once seen, they will always recur until the OAuth migration. Fix: move state to the persistent disk and make a quota exhaustion *sticky* so re-trip costs **1** failure, not 3.

> **Also verify:** `render.yaml:14-15` sets `DRIVE_UPLOAD_ENABLED=false`, yet production still burns 403s — so the deployed env var does not match. After this task, confirm in the Render dashboard whether the var is actually `false`. If uploads are intentionally on pending OAuth, this task limits the damage; if not, setting it `false` stops 403s entirely until OAuth lands.

### Task D1: Persistent state path + sticky quota flag

**Files:**
- Modify: `app/services/drive_service.py:22-35,73-97`
- Modify: `.gitignore:43`
- Test: `tests/test_drive_breaker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_drive_breaker.py`:

```python
import json
from app.services.drive_service import GoogleDriveService


def _svc(tmp_path, monkeypatch):
    monkeypatch.setenv("DRIVE_UPLOAD_ENABLED", "false")  # skip real auth
    svc = GoogleDriveService()
    svc._disabled_by_env = False
    svc._breaker_state_path = str(tmp_path / ".drive_breaker_state.json")
    svc._consecutive_quota_failures = 0
    svc._disabled_until = None
    svc._quota_exhausted_seen = False
    return svc


def test_first_quota_trip_takes_three_failures(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._record_quota_failure()
    svc._record_quota_failure()
    assert not svc._breaker_tripped()
    svc._record_quota_failure()
    assert svc._breaker_tripped()


def test_after_sticky_flag_one_failure_retrips(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    svc._quota_exhausted_seen = True
    svc._record_quota_failure()
    assert svc._breaker_tripped(), "with quota known-exhausted, 1 failure must trip"


def test_sticky_flag_persists_across_instances(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    for _ in range(3):
        svc._record_quota_failure()
    state = json.load(open(svc._breaker_state_path))
    assert state["quota_exhausted_seen"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_drive_breaker.py -v`
Expected: FAIL — `_quota_exhausted_seen` does not exist; sticky re-trip and persisted flag are absent.

- [ ] **Step 3: Persistent path + sticky-flag fields in `__init__`**

In `app/services/drive_service.py`, replace line 22:
```python
    BREAKER_STATE_FILE = '.drive_breaker_state.json'
```
with:
```python
    BREAKER_STATE_FILE = 'drive_breaker_state.json'
```
and in `__init__` replace line 28:
```python
        self._breaker_state_path = self.BREAKER_STATE_FILE
```
with:
```python
        self._breaker_state_path = os.path.join(
            os.getenv("DATA_DIR", "data"), self.BREAKER_STATE_FILE)
```
and add after line 30 (`self._disabled_until = ...`):
```python
        self._quota_exhausted_seen = False
```

- [ ] **Step 4: Load and save the sticky flag**

In `_load_breaker_state`, after the `disabled_until` line, add:
```python
                self._quota_exhausted_seen = bool(state.get("quota_exhausted_seen", False))
```
In `_save_breaker_state`, add `quota_exhausted_seen` to the `state` dict:
```python
            state = {
                "consecutive_quota_failures": self._consecutive_quota_failures,
                "disabled_until": self._disabled_until.isoformat() if self._disabled_until else None,
                "quota_exhausted_seen": self._quota_exhausted_seen,
            }
```
Also ensure the directory exists before writing — at the top of the `try` in `_save_breaker_state`:
```python
            os.makedirs(os.path.dirname(self._breaker_state_path) or ".", exist_ok=True)
```

- [ ] **Step 5: Sticky one-strike trip in `_record_quota_failure`**

Replace `_record_quota_failure` (lines 83–92) with:
```python
    def _record_quota_failure(self):
        self._consecutive_quota_failures += 1
        # Once Drive has ever returned a quota error, the service account is
        # known-exhausted — failures are not transient. Re-trip on the first
        # failure thereafter instead of burning a fresh batch of real 403s.
        threshold = 1 if self._quota_exhausted_seen else self.QUOTA_FAILURES_TO_TRIP
        if self._consecutive_quota_failures >= threshold:
            self._quota_exhausted_seen = True
            self._disabled_until = datetime.datetime.utcnow() + self.DISABLED_DURATION
            print(
                f"[Google Drive] Circuit breaker tripped after "
                f"{self._consecutive_quota_failures} quota error(s). "
                f"Disabled until {self._disabled_until.isoformat()}."
            )
        self._save_breaker_state()
```

- [ ] **Step 6: Keep the sticky flag across cooldown reset**

In `_breaker_tripped` (lines 73–81), the post-cooldown branch resets `_consecutive_quota_failures` and `_disabled_until` — leave those, but do **not** clear `_quota_exhausted_seen`. It already is untouched; add a clarifying comment above the reset:
```python
        if datetime.datetime.utcnow() >= self._disabled_until:
            # Cooldown lapsed. Clear the counter but keep _quota_exhausted_seen
            # so the next real failure re-trips on a single strike.
            self._consecutive_quota_failures = 0
            self._disabled_until = None
            self._save_breaker_state()
            return False
```

- [ ] **Step 7: Update `.gitignore`**

Change `.gitignore:43` from `.drive_breaker_state.json` to:
```
# Circuit-breaker state (now under DATA_DIR)
.drive_breaker_state.json
data/drive_breaker_state.json
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_drive_breaker.py -v`
Expected: all three tests PASS.

- [ ] **Step 9: Commit**

```bash
git add app/services/drive_service.py tests/test_drive_breaker.py .gitignore
git commit -m "fix(drive): persist breaker state and re-trip on one strike once quota-exhausted"
```

---

## Final Verification

- [ ] Run the full suite for touched areas:
  `pytest tests/test_nltk_preseed.py tests/test_api_retry.py tests/test_agent_call_counter.py tests/test_drive_breaker.py -v`
  Expected: all PASS.
- [ ] `python -c "import main"` — app imports with no NLTK violation, no traceback.
- [ ] Confirm `DRIVE_UPLOAD_ENABLED` in the Render dashboard matches intent (see Task Group D note).
- [ ] File a follow-up issue for the **Drive OAuth migration** — the only fix that removes 403s entirely.
```

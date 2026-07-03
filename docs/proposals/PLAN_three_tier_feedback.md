# Three-Tier Feedback Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the tiered feedback architecture from `docs/proposals/THREE_TIER_FEEDBACK_PROPOSAL.md`: hand-pinned prompt base rates (kill the nightly auto-update), degeneracy monitors that auto-suspend gates on mode-collapsed inputs, console-only rolling alpha tooling + findings ledger, and 28d reassess plumbing.

**Architecture:** Phase A splits the calibration card into a nightly *candidate* (console-only) and a manually *pinned* copy that prompts read. Phase B adds a nightly signal-rate monitor that writes `data/gate_suspensions.json`, which `apply_decision_gates` consults. Phase C adds a read-only analysis script and a markdown ledger. Phase D defaults `reassess_in_days` for buys and adds a `--due` filter to the Sell Council script.

**Tech Stack:** Python 3.9, sqlite3 (`app/database.py`), pytest, no new dependencies.

## Global Constraints

- Python 3.9: no `X | Y` union syntax, no `match` — use `typing.Optional/List/Dict` (copied from `runtime.txt`: 3.9.6).
- NEVER run the full pytest suite — it hangs on import-time API scripts. Run only the targeted test file given in each step (`python -m pytest tests/<file> -v`).
- `CALIBRATION_ENABLED` defaults to `"0"`; when off, prompts must remain byte-identical (empty-string injection).
- Do NOT modify any agent prompt text (PM prompt, DR prompt, Risk prompt). The PM prompt's `reassess_in_days ... typically 3-10` line stays as-is; the default applies only when the PM omits the field.
- Do NOT touch the Deep Research 60s rate limit.
- Type hints on all new functions.
- `data/` artifacts (`calibration_card*.json/.md`, `gate_suspensions.json`) are gitignored — never commit them.
- Commit after every task with the exact message given.

## File Structure

| File | Role |
|---|---|
| `scripts/analysis/build_calibration_card.py` (modify) | Nightly builder now writes `data/calibration_card_candidate.{json,md}` |
| `scripts/analysis/pin_calibration_card.py` (create) | Manual, validated candidate→pinned copy; the ONLY writer of the pinned card |
| `app/services/calibration_service.py` (modify) | Reads `data/calibration_card_pinned.json`, mtime cache, staleness guard |
| `main.py` (modify) | `run_outcome_marking`: drop hot-reload, add pinned-staleness QC + nightly degeneracy check |
| `app/database.py` (modify) | `get_recent_signal_rates()`; deprecation note on `decision_tracking` |
| `app/services/decision_gate_service.py` (modify) | Degeneracy ceilings, suspension file loader, `check_gate_degeneracy`, `run_nightly_degeneracy_check`, suspension enforcement |
| `scripts/analysis/verdict_alpha_rolling.py` (create) | Tier-3 console view: per-verdict returns at 1w/2w/4w/8w over trailing windows |
| `docs/audits/FINDINGS_LEDGER.md` (create) | Promotion state machine for findings |
| `app/services/research_service.py` (modify) | `_reassess_in_days_with_default()` at persistence time |
| `scripts/reassess_positions.py` (modify) | `--due` flag |
| Tests | `tests/test_calibration_pinning.py`, `tests/test_gate_degeneracy.py`, `tests/test_verdict_alpha_rolling.py`, `tests/test_reassess_default.py` (all create) |

---

### Task 1: Card builder writes candidate paths

**Files:**
- Modify: `scripts/analysis/build_calibration_card.py:30-31`
- Test: `tests/test_calibration_pinning.py` (create)

**Interfaces:**
- Produces: module constants `JSON_PATH` / `MD_PATH` now point at `data/calibration_card_candidate.json` / `.md`. `run(min_n)` signature unchanged (main.py:289 keeps calling `build_calibration_card.run`). Task 2's pin script reads `JSON_PATH` as its candidate input.

- [ ] **Step 1: Write the failing test**

Create `tests/test_calibration_pinning.py`:

```python
"""Tests for the candidate/pinned calibration-card split (Tier 2).

The nightly builder writes a *candidate* card (console-only); prompts read
only the hand-pinned card produced by scripts/analysis/pin_calibration_card.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


def _mk_row(**over):
    row = {
        "drop_type": "EARNINGS_MISS",
        "is_earnings_drop": 1,
        "recommendation": "WATCH",
        "deep_research_verdict": "HOLD_OFF",
        "gatekeeper_tier": "STANDARD_DIP",
        "ret_1w": -0.01,
        "ret_4w": 0.02,
        "recovered_4w": 1,
    }
    row.update(over)
    return row


def test_builder_writes_candidate_paths(tmp_path, monkeypatch):
    from scripts.analysis import build_calibration_card as bcc

    assert os.path.basename(bcc.JSON_PATH) == "calibration_card_candidate.json"
    assert os.path.basename(bcc.MD_PATH) == "calibration_card_candidate.md"

    rows = [_mk_row() for _ in range(25)] + [
        _mk_row(drop_type="SECTOR_ROTATION", is_earnings_drop=0) for _ in range(25)
    ]
    monkeypatch.setattr(bcc, "get_outcomes_joined", lambda: rows)
    monkeypatch.setattr(bcc, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(bcc, "JSON_PATH", str(tmp_path / "calibration_card_candidate.json"))
    monkeypatch.setattr(bcc, "MD_PATH", str(tmp_path / "calibration_card_candidate.md"))

    card = bcc.run(min_n=20)
    assert (tmp_path / "calibration_card_candidate.json").exists()
    assert (tmp_path / "calibration_card_candidate.md").exists()
    assert card["by_earnings"]["earnings"]["n"] == 25
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_calibration_pinning.py::test_builder_writes_candidate_paths -v`
Expected: FAIL — `AssertionError` on the basename check (`calibration_card.json` != `calibration_card_candidate.json`).

- [ ] **Step 3: Implement**

In `scripts/analysis/build_calibration_card.py`, replace lines 30-31:

```python
JSON_PATH = os.path.join(DATA_DIR, "calibration_card.json")
MD_PATH = os.path.join(DATA_DIR, "calibration_card.md")
```

with:

```python
# Candidate card — console-only (Tier 3). Prompts read the hand-pinned copy
# written by scripts/analysis/pin_calibration_card.py, never this file.
JSON_PATH = os.path.join(DATA_DIR, "calibration_card_candidate.json")
MD_PATH = os.path.join(DATA_DIR, "calibration_card_candidate.md")
```

Also update the module docstring (lines 3-5): change ``data/calibration_card.json`` / ``calibration_card.md`` mentions to the `_candidate` names and append the sentence: `Prompts never read this file — see pin_calibration_card.py.`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_calibration_pinning.py::test_builder_writes_candidate_paths -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/analysis/build_calibration_card.py tests/test_calibration_pinning.py
git commit -m "feat(calibration): nightly card builder writes console-only candidate paths"
```

---

### Task 2: Pin script — validated candidate→pinned copy

**Files:**
- Create: `scripts/analysis/pin_calibration_card.py`
- Test: `tests/test_calibration_pinning.py` (extend)

**Interfaces:**
- Consumes: `build_calibration_card.JSON_PATH` (candidate location, Task 1).
- Produces: `data/calibration_card_pinned.json`; functions `validate_candidate(card: dict) -> List[str]` (empty list = valid), `build_pinned(card: dict, audited_at: str, git_ref: str) -> dict`, `run(approve: bool, audited_at: Optional[str]) -> int` (exit code). The pinned dict has NO `by_pm_verdict` / `by_dr_verdict` / `by_gatekeeper_tier` sections and carries `audited_at` + `pinned_git_ref`. Task 3 reads this file.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_calibration_pinning.py`:

```python
def _candidate_card():
    return {
        "as_of": "2026-06-25",
        "min_n": 20,
        "primary_horizon": "4w",
        "total_labeled": 581,
        "by_drop_type": {"EARNINGS_MISS": {"n": 184, "recovery_rate_4w": 0.462,
                                           "mean_ret_4w": 0.0025, "mean_ret_1w": -0.008}},
        "by_earnings": {"earnings": {"n": 184, "recovery_rate_4w": 0.462,
                                     "mean_ret_4w": 0.0025, "mean_ret_1w": -0.008},
                        "non_earnings": {"n": 412, "recovery_rate_4w": 0.549,
                                         "mean_ret_4w": 0.073, "mean_ret_1w": 0.011}},
        "by_pm_verdict": {"AVOID": {"n": 300, "recovery_rate_4w": 0.483,
                                    "mean_ret_4w": 0.0426, "mean_ret_1w": 0.004}},
        "by_dr_verdict": {"BUY_NOW": {"n": 60, "recovery_rate_4w": 0.55,
                                      "mean_ret_4w": 0.05, "mean_ret_1w": 0.01}},
        "by_gatekeeper_tier": {"DEEP_DIP": {"n": 100, "recovery_rate_4w": 0.5,
                                            "mean_ret_4w": 0.03, "mean_ret_1w": 0.0}},
    }


def test_validate_candidate_accepts_good_card():
    from scripts.analysis.pin_calibration_card import validate_candidate
    assert validate_candidate(_candidate_card()) == []


def test_validate_candidate_rejects_thin_or_broken_cards():
    from scripts.analysis.pin_calibration_card import validate_candidate
    no_earnings = _candidate_card()
    no_earnings["by_earnings"] = {}
    assert any("by_earnings" in e for e in validate_candidate(no_earnings))

    low_min_n = _candidate_card()
    low_min_n["min_n"] = 5
    assert any("min_n" in e for e in validate_candidate(low_min_n))

    thin = _candidate_card()
    thin["total_labeled"] = 40
    assert any("total_labeled" in e for e in validate_candidate(thin))


def test_build_pinned_strips_verdict_sections_and_stamps_audit():
    from scripts.analysis.pin_calibration_card import build_pinned
    pinned = build_pinned(_candidate_card(), audited_at="2026-06-30", git_ref="abc1234")
    assert "by_pm_verdict" not in pinned
    assert "by_dr_verdict" not in pinned
    assert "by_gatekeeper_tier" not in pinned
    assert pinned["audited_at"] == "2026-06-30"
    assert pinned["pinned_git_ref"] == "abc1234"
    assert pinned["by_earnings"]["non_earnings"]["n"] == 412


def test_pin_run_requires_approve(tmp_path, monkeypatch):
    import scripts.analysis.pin_calibration_card as pin
    candidate = tmp_path / "calibration_card_candidate.json"
    pinned = tmp_path / "calibration_card_pinned.json"
    candidate.write_text(json.dumps(_candidate_card()))
    monkeypatch.setattr(pin, "CANDIDATE_JSON", str(candidate))
    monkeypatch.setattr(pin, "PINNED_JSON", str(pinned))

    assert pin.run(approve=False, audited_at="2026-06-30") == 0   # dry-run
    assert not pinned.exists()
    assert pin.run(approve=True, audited_at="2026-06-30") == 0
    assert json.loads(pinned.read_text())["audited_at"] == "2026-06-30"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_calibration_pinning.py -v`
Expected: the four new tests FAIL with `ModuleNotFoundError: No module named 'scripts.analysis.pin_calibration_card'`; Task 1's test still PASSES.

- [ ] **Step 3: Create `scripts/analysis/pin_calibration_card.py`**

```python
"""Pin the calibration-card candidate for prompt injection (Tier 2).

The nightly builder writes ``data/calibration_card_candidate.json`` (console
only). This script is the ONLY writer of ``data/calibration_card_pinned.json``
— the file prompts read via app/services/calibration_service.py. Pinning is a
deliberate manual act after a monthly audit, never automated: with ~30
actionable decisions/month, an auto-updated card amplifies noise into the very
prompts generating the next month's data (THREE_TIER_FEEDBACK_PROPOSAL.md).

Per-verdict sections are stripped at pin time: telling the PM how its own
verdicts performed is the mistake-narrative shape that causes mode collapse.

Usage:
    python -m scripts.analysis.pin_calibration_card                # dry-run
    python -m scripts.analysis.pin_calibration_card --approve \
        [--audited-at 2026-06-30]
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from typing import List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from scripts.analysis.build_calibration_card import JSON_PATH as CANDIDATE_JSON  # noqa: E402

PINNED_JSON = os.path.join(os.path.dirname(CANDIDATE_JSON), "calibration_card_pinned.json")

# Sections agents may never see (regime-dependent / self-referential — Tier 3).
DROPPED_SECTIONS = ("by_pm_verdict", "by_dr_verdict", "by_gatekeeper_tier")
MIN_MIN_N = 20
MIN_TOTAL_LABELED = 100


def validate_candidate(card: dict) -> List[str]:
    """Content rules from THREE_TIER_FEEDBACK_PROPOSAL.md. Empty list = valid."""
    errors: List[str] = []
    if not card.get("as_of"):
        errors.append("missing as_of stamp")
    if (card.get("min_n") or 0) < MIN_MIN_N:
        errors.append(f"min_n {card.get('min_n')} below floor {MIN_MIN_N}")
    if (card.get("total_labeled") or 0) < MIN_TOTAL_LABELED:
        errors.append(f"total_labeled {card.get('total_labeled')} below floor {MIN_TOTAL_LABELED}")
    if not (card.get("by_earnings") or {}):
        errors.append("no by_earnings bucket met min_n — nothing worth pinning")
    for section in ("by_drop_type", "by_earnings"):
        for key, stats in (card.get(section) or {}).items():
            if (stats.get("n") or 0) < MIN_MIN_N:
                errors.append(f"{section}/{key} has n={stats.get('n')} < {MIN_MIN_N}")
    return errors


def build_pinned(card: dict, audited_at: str, git_ref: str) -> dict:
    pinned = {k: v for k, v in card.items() if k not in DROPPED_SECTIONS}
    pinned["audited_at"] = audited_at
    pinned["pinned_git_ref"] = git_ref
    return pinned


def _git_ref() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def run(approve: bool, audited_at: Optional[str] = None) -> int:
    try:
        with open(CANDIDATE_JSON) as f:
            card = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[pin_calibration_card] cannot read candidate {CANDIDATE_JSON}: {e}")
        return 1

    errors = validate_candidate(card)
    if errors:
        print("[pin_calibration_card] candidate REJECTED:")
        for e in errors:
            print(f"  - {e}")
        return 1

    pinned = build_pinned(
        card,
        audited_at=audited_at or datetime.date.today().isoformat(),
        git_ref=_git_ref(),
    )
    print(f"[pin_calibration_card] candidate as_of={card['as_of']} "
          f"({card['total_labeled']} labeled) validates clean.")
    if not approve:
        print("Dry-run only. Re-run with --approve to write "
              f"{PINNED_JSON} (this changes live prompt content when "
              "CALIBRATION_ENABLED=1).")
        return 0

    with open(PINNED_JSON, "w") as f:
        json.dump(pinned, f, indent=2)
    print(f"[pin_calibration_card] pinned -> {PINNED_JSON} "
          f"(audited_at={pinned['audited_at']}, ref={pinned['pinned_git_ref']})")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Pin the calibration card for prompt injection")
    ap.add_argument("--approve", action="store_true",
                    help="Actually write the pinned card (default: dry-run)")
    ap.add_argument("--audited-at", default=None, metavar="YYYY-MM-DD",
                    help="Audit date stamp (default: today)")
    args = ap.parse_args()
    sys.exit(run(approve=args.approve, audited_at=args.audited_at))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_calibration_pinning.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/analysis/pin_calibration_card.py tests/test_calibration_pinning.py
git commit -m "feat(calibration): manual pin script — the only writer of the pinned card"
```

---

### Task 3: calibration_service reads the pinned card (mtime cache + staleness guard)

**Files:**
- Modify: `app/services/calibration_service.py:17-40` and `calibration_block` (lines 54-58)
- Test: `tests/test_calibration_pinning.py` (extend)

**Interfaces:**
- Consumes: `data/calibration_card_pinned.json` (Task 2 format, incl. `audited_at`).
- Produces: `_CARD_PATH` (pinned path), `STALE_PIN_MAX_DAYS = 45`, `pinned_card_age_days(today: Optional[datetime.date] = None) -> Optional[int]` (None = missing/unstamped card), `reload_card()` kept with same name/signature (main.py imports it until Task 4; the shadow A/B path in research_service uses `calibration_block(force=True)` unchanged). `calibration_block` returns `""` when the pin is stale.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_calibration_pinning.py`:

```python
def _write_pinned(tmp_path, monkeypatch, audited_at):
    import app.services.calibration_service as cs
    pinned = dict(_candidate_card())
    for sec in ("by_pm_verdict", "by_dr_verdict", "by_gatekeeper_tier"):
        pinned.pop(sec)
    pinned["audited_at"] = audited_at
    path = tmp_path / "calibration_card_pinned.json"
    path.write_text(json.dumps(pinned))
    monkeypatch.setattr(cs, "_CARD_PATH", str(path))
    cs.reload_card()
    return cs


def test_calibration_service_reads_pinned_path():
    import app.services.calibration_service as cs
    assert os.path.basename(cs._CARD_PATH) == "calibration_card_pinned.json"


def test_block_serves_fresh_pin(tmp_path, monkeypatch):
    import datetime
    today = datetime.date(2026, 7, 2)
    cs = _write_pinned(tmp_path, monkeypatch, audited_at="2026-06-30")
    monkeypatch.setattr(cs, "_today", lambda: today)
    block = cs.calibration_block(is_earnings=True, force=True)
    assert "earnings drops overall" in block
    assert cs.pinned_card_age_days(today) == 2


def test_block_refuses_stale_pin(tmp_path, monkeypatch):
    import datetime
    today = datetime.date(2026, 7, 2)
    cs = _write_pinned(tmp_path, monkeypatch, audited_at="2026-05-01")  # 62d old
    monkeypatch.setattr(cs, "_today", lambda: today)
    assert cs.calibration_block(is_earnings=True, force=True) == ""
    assert cs.pinned_card_age_days(today) == 62


def test_pin_edit_is_picked_up_without_reload(tmp_path, monkeypatch):
    """mtime cache: a manual re-pin must reach a long-lived process."""
    import datetime, time
    today = datetime.date(2026, 7, 2)
    cs = _write_pinned(tmp_path, monkeypatch, audited_at="2026-06-30")
    monkeypatch.setattr(cs, "_today", lambda: today)
    assert "n=412" in cs.calibration_block(is_earnings=False, force=True)

    path = tmp_path / "calibration_card_pinned.json"
    card = json.loads(path.read_text())
    card["by_earnings"]["non_earnings"]["n"] = 999
    time.sleep(0.01)
    path.write_text(json.dumps(card))
    os.utime(str(path))  # force a distinct mtime on coarse filesystems
    assert "n=999" in cs.calibration_block(is_earnings=False, force=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_calibration_pinning.py -v`
Expected: the four new tests FAIL (`_CARD_PATH` basename is `calibration_card.json`; `AttributeError` for `_today` / `pinned_card_age_days`).

- [ ] **Step 3: Implement**

In `app/services/calibration_service.py`:

(a) Replace the imports line `import functools` with `import datetime` (functools is no longer used); keep `json`, `os`, `typing`.

(b) Replace lines 17-19 (`_CARD_PATH = ...`) with:

```python
# The hand-pinned card — written ONLY by scripts/analysis/pin_calibration_card.py
# after a monthly audit. The nightly candidate card is console-only and is
# never read here (THREE_TIER_FEEDBACK_PROPOSAL.md, Tier 2).
_CARD_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "calibration_card_pinned.json")
)

# A pin older than this is treated as missing: stale base rates are worse
# than none. The nightly QC in main.run_outcome_marking alerts on this too.
STALE_PIN_MAX_DAYS = 45
```

(c) Replace the `@functools.lru_cache` `_load_card` and `reload_card` (lines 29-40) with:

```python
_card_cache = {"mtime": None, "card": None}


def _load_card(path: Optional[str] = None) -> Optional[dict]:
    """mtime-cached read so a manual re-pin reaches a long-lived process.

    The path is resolved at CALL time (not as a bound default) so tests can
    monkeypatch _CARD_PATH.
    """
    path = path or _CARD_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if _card_cache["mtime"] != mtime:
        try:
            with open(path) as f:
                _card_cache["card"] = json.load(f)
        except (OSError, json.JSONDecodeError):
            _card_cache["card"] = None
        _card_cache["mtime"] = mtime
    return _card_cache["card"]


def reload_card() -> None:
    """Drop the cached card (kept for compatibility; mtime check usually suffices)."""
    _card_cache["mtime"] = None
    _card_cache["card"] = None


def _today() -> datetime.date:
    return datetime.date.today()


def pinned_card_age_days(today: Optional[datetime.date] = None) -> Optional[int]:
    """Days since the pinned card's audit stamp; None = missing/unstamped card."""
    card = _load_card()
    if not card or not card.get("audited_at"):
        return None
    try:
        audited = datetime.date.fromisoformat(card["audited_at"])
    except ValueError:
        return None
    return ((today or _today()) - audited).days
```

(d) In `calibration_block`, after the existing `if not card: return ""` (line 57-58), insert:

```python
    age = pinned_card_age_days()
    if age is None or age > STALE_PIN_MAX_DAYS:
        logging.getLogger(__name__).warning(
            "[calibration] pinned card is %s — refusing to inject; "
            "re-run the monthly audit and pin_calibration_card --approve",
            "unstamped" if age is None else f"{age}d old",
        )
        return ""
```

and add `import logging` to the module imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_calibration_pinning.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Run the neighbouring regression tests**

Run: `python -m pytest tests/test_decision_gate_service.py tests/test_gate_persistence.py -v`
Expected: PASS (no behaviour change when flag off — `calibration_block` still returns `""`).

- [ ] **Step 6: Commit**

```bash
git add app/services/calibration_service.py tests/test_calibration_pinning.py
git commit -m "feat(calibration): prompts read hand-pinned card only, with 45d staleness refusal"
```

---

### Task 4: main.py — drop the hot-reload, add pinned-staleness QC

**Files:**
- Modify: `main.py:286-293` (inside `run_outcome_marking`)

**Interfaces:**
- Consumes: `calibration_service.is_enabled()`, `calibration_service.pinned_card_age_days()`, `calibration_service.STALE_PIN_MAX_DAYS` (Task 3).
- Produces: nothing new — the nightly job no longer touches prompt state.

- [ ] **Step 1: Implement**

In `main.py`, replace lines 286-293:

```python
                # Rebuild the calibration card so it always reflects fresh marks,
                # then invalidate the in-process cache so the live prompts pick it up.
                try:
                    await asyncio.to_thread(build_calibration_card.run)
                    from app.services.calibration_service import reload_card
                    reload_card()
                except Exception as e:
                    print(f"Error rebuilding calibration card: {e}")
```

with:

```python
                # Rebuild the CANDIDATE card (console-only, for the monthly
                # audit). Prompts read only the hand-pinned card — nothing the
                # nightly job writes may reach agents (three-tier feedback).
                try:
                    await asyncio.to_thread(build_calibration_card.run)
                except Exception as e:
                    print(f"Error rebuilding calibration card candidate: {e}")

                # QC: injection enabled but pin missing or past its audit window.
                try:
                    from app.services import calibration_service
                    if calibration_service.is_enabled():
                        age = calibration_service.pinned_card_age_days()
                        if age is None or age > calibration_service.STALE_PIN_MAX_DAYS:
                            desc = "missing/unstamped" if age is None else f"{age}d old"
                            logging.error(
                                "[QC ALERT] CALIBRATION_ENABLED=1 but pinned card is %s — "
                                "run the monthly audit, then "
                                "python -m scripts.analysis.pin_calibration_card --approve",
                                desc,
                            )
                except Exception as e:
                    print(f"Error in pinned-card staleness QC: {e}")
```

- [ ] **Step 2: Verify no hot-reload remains and the module parses**

Run: `grep -n "reload_card" main.py; python -c "import ast; ast.parse(open('main.py').read()); print('main.py parses')"`
Expected: no grep hits; `main.py parses`.

- [ ] **Step 3: Run targeted tests**

Run: `python -m pytest tests/test_calibration_pinning.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(calibration): nightly job builds candidate only — no auto-propagation to prompts"
```

---

### Task 5: DB helper — trailing signal rates for degeneracy monitoring

**Files:**
- Modify: `app/database.py` (add function after `get_outcomes_joined`, ~line 666)
- Test: `tests/test_gate_degeneracy.py` (create)

**Interfaces:**
- Consumes: `decision_points` columns `drop_type`, `risk_falling_knife`, `news_sentiment` (all persisted at decision time; `news_named_catalyst`/`news_drop_reason_confirmed` are NOT persisted, so the bearish-share proxy is monitored for the news gate).
- Produces: `get_recent_signal_rates(limit: int = 50, gated_drop_types: tuple = ("EARNINGS_MISS", "COMPANY_SPECIFIC", "ANALYST_DOWNGRADE")) -> dict` returning keys `n` (int), `drop_type_gated_rate` (float), `news_bearish_rate` (float), `knife_n` (int, non-null structured verdicts), `knife_yes_rate` (float over `knife_n`). Rates are `0.0` when their denominator is 0. Task 6 consumes this.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gate_degeneracy.py`:

```python
"""Tests for gate-input degeneracy monitoring (three-tier feedback, Tier 1).

The falling-knife collapse (structured verdict 97% YES, 256/264 since
2026-06-11) is the template failure: any gate input that stops discriminating
must auto-suspend its gate instead of downgrading the whole book.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.database import add_decision_point, update_decision_point


@pytest.fixture()
def fresh_db(_no_production_db):
    from app.database import init_db
    init_db()
    yield


def _add(symbol, drop_type, knife, sentiment):
    decision_id = add_decision_point(
        symbol=symbol, price=100.0, drop_percent=-6.0,
        recommendation="PENDING", reasoning="...", status="Pending",
    )
    update_decision_point(
        decision_id, "WATCH", "test", "Pending DR Review",
        drop_type=drop_type, risk_falling_knife=knife, news_sentiment=sentiment,
    )
    return decision_id


def test_recent_signal_rates(fresh_db):
    from app.database import get_recent_signal_rates
    for i in range(30):
        _add(f"T{i}", "EARNINGS_MISS" if i < 12 else "SECTOR_ROTATION",
             "YES" if i < 29 else "NO",
             "BEARISH" if i < 6 else "NEUTRAL")
    rates = get_recent_signal_rates(limit=50)
    assert rates["n"] == 30
    assert rates["drop_type_gated_rate"] == pytest.approx(12 / 30)
    assert rates["knife_n"] == 30
    assert rates["knife_yes_rate"] == pytest.approx(29 / 30)
    assert rates["news_bearish_rate"] == pytest.approx(6 / 30)


def test_recent_signal_rates_empty_db(fresh_db):
    from app.database import get_recent_signal_rates
    rates = get_recent_signal_rates()
    assert rates == {"n": 0, "drop_type_gated_rate": 0.0,
                     "news_bearish_rate": 0.0, "knife_n": 0, "knife_yes_rate": 0.0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gate_degeneracy.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_recent_signal_rates'`

- [ ] **Step 3: Implement**

In `app/database.py`, add after `get_outcomes_joined` (~line 666):

```python
def get_recent_signal_rates(
    limit: int = 50,
    gated_drop_types: tuple = ("EARNINGS_MISS", "COMPANY_SPECIFIC", "ANALYST_DOWNGRADE"),
) -> dict:
    """Trailing-N shares of gate-input signals across ALL decisions.

    The denominator is every decision, not just buys — that is the base the
    falling-knife collapse was measured on (256/264 YES since 2026-06-11).
    Used by decision_gate_service.run_nightly_degeneracy_check.
    """
    empty = {"n": 0, "drop_type_gated_rate": 0.0, "news_bearish_rate": 0.0,
             "knife_n": 0, "knife_yes_rate": 0.0}
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT drop_type, risk_falling_knife, news_sentiment
            FROM decision_points
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
    except Exception as e:
        print(f"Error fetching recent signal rates: {e}")
        return empty

    n = len(rows)
    if n == 0:
        return empty
    gated = set(gated_drop_types)
    knife_vals = [(r["risk_falling_knife"] or "").strip().upper()
                  for r in rows if r["risk_falling_knife"] is not None]
    return {
        "n": n,
        "drop_type_gated_rate": sum(
            1 for r in rows if (r["drop_type"] or "").strip().upper() in gated) / n,
        "news_bearish_rate": sum(
            1 for r in rows if (r["news_sentiment"] or "").strip().upper() == "BEARISH") / n,
        "knife_n": len(knife_vals),
        "knife_yes_rate": (sum(1 for v in knife_vals if v == "YES") / len(knife_vals))
        if knife_vals else 0.0,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gate_degeneracy.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add app/database.py tests/test_gate_degeneracy.py
git commit -m "feat(gates): trailing signal-rate query for degeneracy monitoring"
```

---

### Task 6: Degeneracy ceilings, suspension file, nightly check, gate enforcement

**Files:**
- Modify: `app/services/decision_gate_service.py`
- Test: `tests/test_gate_degeneracy.py` (extend)

**Interfaces:**
- Consumes: `app.database.get_recent_signal_rates` (Task 5; imported inside the function to keep module import light).
- Produces: `GATE_DEGENERACY_CEILINGS: dict`, `DEGENERACY_MIN_SIGNALS = 30`, `_SUSPENSIONS_PATH` (data/gate_suspensions.json), `_load_suspensions() -> frozenset`, `check_gate_degeneracy(rates: dict) -> List[str]`, `run_nightly_degeneracy_check(limit: int = 50) -> List[str]` (returns NEWLY suspended gate names). `apply_decision_gates` skips suspended gates. Task 7 wires the nightly call.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gate_degeneracy.py`:

```python
def test_check_degeneracy_flags_collapsed_knife():
    """The June collapse (97% YES) must trip the knife ceiling (0.60)."""
    from app.services.decision_gate_service import check_gate_degeneracy
    rates = {"n": 50, "drop_type_gated_rate": 0.35, "news_bearish_rate": 0.2,
             "knife_n": 50, "knife_yes_rate": 0.97}
    assert check_gate_degeneracy(rates) == ["RISK_KNIFE_GATE"]


def test_check_degeneracy_healthy_signals_pass():
    from app.services.decision_gate_service import check_gate_degeneracy
    rates = {"n": 50, "drop_type_gated_rate": 0.40, "news_bearish_rate": 0.25,
             "knife_n": 50, "knife_yes_rate": 0.30}
    assert check_gate_degeneracy(rates) == []


def test_check_degeneracy_needs_minimum_sample():
    from app.services.decision_gate_service import check_gate_degeneracy
    rates = {"n": 10, "drop_type_gated_rate": 1.0, "news_bearish_rate": 1.0,
             "knife_n": 10, "knife_yes_rate": 1.0}
    assert check_gate_degeneracy(rates) == []


def test_apply_gates_skips_suspended_gate(monkeypatch):
    import app.services.decision_gate_service as dgs
    monkeypatch.setattr(dgs, "_load_suspensions", lambda: frozenset({"DROP_TYPE_GATE"}))
    result = dgs.apply_decision_gates(
        action="BUY", drop_type="EARNINGS_MISS", conviction="HIGH",
        sa_quant_rating=3.5,
    )
    assert result.final_action == "BUY"
    assert result.gates_fired == []


def test_nightly_check_writes_suspension_file(tmp_path, monkeypatch):
    import app.services.decision_gate_service as dgs
    import app.database as db
    path = tmp_path / "gate_suspensions.json"
    monkeypatch.setattr(dgs, "_SUSPENSIONS_PATH", str(path))
    monkeypatch.setattr(db, "get_recent_signal_rates", lambda limit, gated_drop_types: {
        "n": 50, "drop_type_gated_rate": 0.9, "news_bearish_rate": 0.1,
        "knife_n": 50, "knife_yes_rate": 0.2,
    })
    newly = dgs.run_nightly_degeneracy_check()
    assert newly == ["DROP_TYPE_GATE"]
    assert json.loads(path.read_text())["suspended"] == ["DROP_TYPE_GATE"]
    assert dgs._load_suspensions() == frozenset({"DROP_TYPE_GATE"})
    # Second run: already suspended, so nothing is NEWLY suspended.
    assert dgs.run_nightly_degeneracy_check() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gate_degeneracy.py -v`
Expected: the five new tests FAIL (`ImportError`/`AttributeError` for the new names); Task 5's tests still PASS.

- [ ] **Step 3: Implement**

In `app/services/decision_gate_service.py`:

(a) Add `import datetime`, `import json`, `import os` to the imports (keep existing ones).

(b) After the `KNIFE_YES_RATE_CEILING = 0.60` line (line 57), add:

```python
# --- Degeneracy monitoring (THREE_TIER_FEEDBACK_PROPOSAL.md, Tier 1) ---
# Trailing share of decisions carrying each gate's triggering signal. If a
# signal stops discriminating (the falling-knife precedent: 97% YES), its
# gate is auto-suspended via data/gate_suspensions.json rather than left to
# downgrade the whole book. SA_QUANT_GATE has no ceiling: an external numeric
# rating cannot mode-collapse; UNCONFIRMED_DROP_GATE's input is not persisted.
GATE_DEGENERACY_CEILINGS = {
    "DROP_TYPE_GATE": 0.80,       # share classified into GATED_DROP_TYPES
    "NEWS_SENTIMENT_GATE": 0.80,  # share of BEARISH news sentiment
    "RISK_KNIFE_GATE": KNIFE_YES_RATE_CEILING,
}
DEGENERACY_MIN_SIGNALS = 30  # below this trailing sample, never suspend

_SUSPENSIONS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "gate_suspensions.json")
)
_suspensions_cache = {"mtime": None, "suspended": frozenset()}
```

(c) After the `risk_report_flags_knife` function (line 84), add:

```python
def _load_suspensions() -> frozenset:
    """Gates auto-suspended by the nightly degeneracy check (mtime-cached)."""
    try:
        mtime = os.path.getmtime(_SUSPENSIONS_PATH)
    except OSError:
        return frozenset()
    if _suspensions_cache["mtime"] != mtime:
        try:
            with open(_SUSPENSIONS_PATH) as f:
                _suspensions_cache["suspended"] = frozenset(json.load(f).get("suspended", []))
        except (OSError, json.JSONDecodeError, AttributeError):
            _suspensions_cache["suspended"] = frozenset()
        _suspensions_cache["mtime"] = mtime
    return _suspensions_cache["suspended"]


def check_gate_degeneracy(rates: dict) -> List[str]:
    """Gates whose input signal has stopped discriminating, given trailing rates
    from app.database.get_recent_signal_rates. Pure — no I/O."""
    out: List[str] = []
    n = rates.get("n") or 0
    if n >= DEGENERACY_MIN_SIGNALS:
        if rates.get("drop_type_gated_rate", 0.0) > GATE_DEGENERACY_CEILINGS["DROP_TYPE_GATE"]:
            out.append("DROP_TYPE_GATE")
        if rates.get("news_bearish_rate", 0.0) > GATE_DEGENERACY_CEILINGS["NEWS_SENTIMENT_GATE"]:
            out.append("NEWS_SENTIMENT_GATE")
    if (rates.get("knife_n") or 0) >= DEGENERACY_MIN_SIGNALS:
        if rates.get("knife_yes_rate", 0.0) > GATE_DEGENERACY_CEILINGS["RISK_KNIFE_GATE"]:
            out.append("RISK_KNIFE_GATE")
    return out


def run_nightly_degeneracy_check(limit: int = 50) -> List[str]:
    """Recompute trailing signal rates, persist the suspension set, and return
    the NEWLY suspended gates (for the caller's QC alert). Also logs the knife
    YES-rate every night — it is the re-enable condition for RISK_KNIFE_GATE."""
    from app.database import get_recent_signal_rates

    rates = get_recent_signal_rates(limit, tuple(sorted(GATED_DROP_TYPES)))
    degenerate = check_gate_degeneracy(rates)
    previously = set(_load_suspensions())

    os.makedirs(os.path.dirname(_SUSPENSIONS_PATH), exist_ok=True)
    with open(_SUSPENSIONS_PATH, "w") as f:
        json.dump({
            "suspended": sorted(degenerate),
            "rates": rates,
            "as_of": datetime.date.today().isoformat(),
        }, f, indent=2)
    _suspensions_cache["mtime"] = None  # force re-read

    logger.info(
        "[DecisionGate] degeneracy check: n=%s knife_yes=%.0f%% (ceiling %.0f%%) "
        "drop_type=%.0f%% bearish=%.0f%% -> suspended=%s",
        rates.get("n"), 100 * rates.get("knife_yes_rate", 0.0),
        100 * KNIFE_YES_RATE_CEILING, 100 * rates.get("drop_type_gated_rate", 0.0),
        100 * rates.get("news_bearish_rate", 0.0), sorted(degenerate) or "none",
    )
    newly = sorted(set(degenerate) - previously)
    for gate in newly:
        logger.error("[QC ALERT] %s input degenerate over trailing %s decisions — gate auto-suspended",
                     gate, rates.get("n"))
    return newly
```

(d) In `apply_decision_gates`, after `result = GateResult(...)` (line 108) add:

```python
    suspended = _load_suspensions()

    def _active(gate: str) -> bool:
        if gate in suspended:
            logger.warning("[DecisionGate] %s suspended (degenerate input) — skipping", gate)
            return False
        return True
```

then guard each gate condition (exact replacements):

- line 116: `if drop_type_norm in GATED_DROP_TYPES:` → `if drop_type_norm in GATED_DROP_TYPES and _active("DROP_TYPE_GATE"):`
- line 123: `if sa_quant_rating is not None and sa_quant_rating < SA_QUANT_FLOOR:` → `if sa_quant_rating is not None and sa_quant_rating < SA_QUANT_FLOOR and _active("SA_QUANT_GATE"):`
- line 135: `if RISK_KNIFE_GATE_ENABLED and knife and pre_gate == "BUY":` → `if RISK_KNIFE_GATE_ENABLED and knife and pre_gate == "BUY" and _active("RISK_KNIFE_GATE"):`
- line 142: `if (news_sentiment or "").strip().upper() == "BEARISH" and not (news_named_catalyst or "").strip():` → `if (news_sentiment or "").strip().upper() == "BEARISH" and not (news_named_catalyst or "").strip() and _active("NEWS_SENTIMENT_GATE"):`
- line 153: `if news_drop_reason_confirmed is False and pre_gate == "BUY":` → `if news_drop_reason_confirmed is False and pre_gate == "BUY" and _active("UNCONFIRMED_DROP_GATE"):`

(e) Append to the module docstring (after the Gate 6 paragraph):

```
Degeneracy monitoring: run_nightly_degeneracy_check (called from
main.run_outcome_marking) persists auto-suspensions to
data/gate_suspensions.json when a gate's input signal exceeds its
GATE_DEGENERACY_CEILINGS share over the trailing 50 decisions — the
generalization of the falling-knife collapse. Suspension is fail-open
(gate skipped, PM action kept) and reverses automatically once the
signal regains variance.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_gate_degeneracy.py tests/test_decision_gate_service.py -v`
Expected: all PASS (existing gate tests must not break — with no suspension file, `_load_suspensions()` returns an empty frozenset and behaviour is unchanged).

- [ ] **Step 5: Commit**

```bash
git add app/services/decision_gate_service.py tests/test_gate_degeneracy.py
git commit -m "feat(gates): degeneracy monitor auto-suspends gates on mode-collapsed inputs"
```

---

### Task 7: Wire the nightly degeneracy check into main.py

**Files:**
- Modify: `main.py` (inside `run_outcome_marking`, directly after the pinned-staleness QC block from Task 4)

**Interfaces:**
- Consumes: `decision_gate_service.run_nightly_degeneracy_check` (Task 6).

- [ ] **Step 1: Implement**

In `main.py`, insert after the pinned-staleness QC block (before `last_run_date = today_str`):

```python
                # Gate-input degeneracy check (Tier 1 guardrail): auto-suspend
                # gates whose signal has mode-collapsed, falling-knife style.
                try:
                    from app.services.decision_gate_service import run_nightly_degeneracy_check
                    newly = await asyncio.to_thread(run_nightly_degeneracy_check)
                    if newly:
                        print(f"[QC ALERT] gate inputs degenerate, auto-suspended: {', '.join(newly)}")
                except Exception as e:
                    print(f"Error in gate degeneracy check: {e}")
```

- [ ] **Step 2: Verify the module parses and the call sits before `last_run_date` is set**

Run: `python -c "import ast; ast.parse(open('main.py').read()); print('parses')" && grep -n "run_nightly_degeneracy_check\|last_run_date = today_str" main.py`
Expected: `parses`; the `run_nightly_degeneracy_check` lines appear inside `run_outcome_marking` above its `last_run_date = today_str` line.

- [ ] **Step 3: Run targeted tests**

Run: `python -m pytest tests/test_gate_degeneracy.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(gates): nightly degeneracy check wired into outcome-marking job"
```

---

### Task 8: Tier-3 console view — rolling per-verdict returns

**Files:**
- Create: `scripts/analysis/verdict_alpha_rolling.py`
- Test: `tests/test_verdict_alpha_rolling.py` (create)

**Interfaces:**
- Consumes: `app.database.get_outcomes_joined()` rows (keys: `timestamp`, `recommendation`, `deep_research_verdict`, `ret_1w/2w/4w/8w`).
- Produces: `compute_view(rows: List[dict], today: datetime.date, windows: tuple = (30, 60, 90)) -> dict` shaped `{window_days: {"by_pm_verdict": {verdict: {horizon: {"n": int, "win_rate": float, "mean": float, "median": float}}}, "by_dr_verdict": {...}}}`; `render_view(view: dict) -> str`. Console-only — nothing here is ever imported by prompt-building code. Horizons are the existing 1w/2w/4w/8w columns; the proposal's 21d point is intentionally skipped (no `ret_3w` column — YAGNI per the proposal's open question).

- [ ] **Step 1: Write the failing test**

Create `tests/test_verdict_alpha_rolling.py`:

```python
"""Tier-3 console view: rolling per-verdict forward returns (never injected)."""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


def _row(ts, rec, dr, r1, r4):
    return {"timestamp": ts, "recommendation": rec, "deep_research_verdict": dr,
            "ret_1w": r1, "ret_2w": None, "ret_4w": r4, "ret_8w": None}


def test_compute_view_windows_and_stats():
    from scripts.analysis.verdict_alpha_rolling import compute_view
    today = datetime.date(2026, 7, 2)
    rows = [
        _row("2026-06-20 10:00:00", "BUY", "BUY_NOW", 0.01, 0.10),   # 12d ago
        _row("2026-06-10 10:00:00", "BUY", None, -0.02, -0.04),      # 22d ago
        _row("2026-03-01 10:00:00", "BUY", "BUY_NOW", 0.05, 0.20),   # outside 90d
        _row("2026-06-25 10:00:00", "AVOID", "HOLD_OFF", None, 0.105),
    ]
    view = compute_view(rows, today, windows=(30, 90))

    buy30 = view[30]["by_pm_verdict"]["BUY"]
    assert buy30["4w"]["n"] == 2
    assert buy30["4w"]["mean"] == pytest.approx((0.10 - 0.04) / 2)
    assert buy30["4w"]["win_rate"] == pytest.approx(0.5)
    assert buy30["1w"]["n"] == 2

    avoid30 = view[30]["by_pm_verdict"]["AVOID"]
    assert avoid30["4w"]["n"] == 1
    assert avoid30["1w"]["n"] == 0          # None returns are excluded per-horizon

    assert view[90]["by_pm_verdict"]["BUY"]["4w"]["n"] == 2  # March row excluded
    assert view[30]["by_dr_verdict"]["BUY_NOW"]["4w"]["n"] == 1


def test_render_view_prints_tables():
    from scripts.analysis.verdict_alpha_rolling import compute_view, render_view
    today = datetime.date(2026, 7, 2)
    rows = [_row("2026-06-20 10:00:00", "BUY", "BUY_NOW", 0.01, 0.10)]
    out = render_view(compute_view(rows, today, windows=(30,)))
    assert "trailing 30d" in out
    assert "BUY" in out and "4w" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_verdict_alpha_rolling.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.analysis.verdict_alpha_rolling'`

- [ ] **Step 3: Create `scripts/analysis/verdict_alpha_rolling.py`**

```python
"""Rolling per-verdict forward returns — Tier 3, console only.

Per-verdict performance is regime-dependent (the AVOID finding flipped sign
between April and May-June 2026) and often small-n, so it is NEVER injected
into agent prompts (THREE_TIER_FEEDBACK_PROPOSAL.md). This view exists for the
monthly audit: a finding is promoted to the pinned card or a gate only after
it survives 2-3 audits here.

Horizons are the decision_outcomes columns (1w/2w/4w/8w trading-day marks);
there is deliberately no 21d column.

Usage:
    python -m scripts.analysis.verdict_alpha_rolling [--windows 30 60 90]
"""
import argparse
import datetime
import os
import statistics as st
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.database import get_outcomes_joined  # noqa: E402

HORIZONS = (("1w", "ret_1w"), ("2w", "ret_2w"), ("4w", "ret_4w"), ("8w", "ret_8w"))
DEFAULT_WINDOWS: Tuple[int, ...] = (30, 60, 90)


def _decision_date(row: dict) -> datetime.date:
    return datetime.date.fromisoformat(str(row.get("timestamp") or "")[:10])


def _stats(vals: List[float]) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0, "win_rate": 0.0, "mean": 0.0, "median": 0.0}
    return {
        "n": len(vals),
        "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 3),
        "mean": round(st.mean(vals), 4),
        "median": round(st.median(vals), 4),
    }


def compute_view(rows: List[dict], today: datetime.date,
                 windows: Tuple[int, ...] = DEFAULT_WINDOWS) -> dict:
    """Pure aggregation: {window: {by_pm_verdict/by_dr_verdict:
    {verdict: {horizon: stats}}}}."""
    view: Dict[int, dict] = {}
    for window in windows:
        cutoff = today - datetime.timedelta(days=window)
        in_window = []
        for r in rows:
            try:
                if _decision_date(r) >= cutoff:
                    in_window.append(r)
            except ValueError:
                continue
        sections = {}
        for section, key in (("by_pm_verdict", "recommendation"),
                             ("by_dr_verdict", "deep_research_verdict")):
            groups: Dict[str, list] = {}
            for r in in_window:
                verdict = (r.get(key) or "").strip().upper()
                if verdict:
                    groups.setdefault(verdict, []).append(r)
            sections[section] = {
                verdict: {h: _stats([r.get(col) for r in rs]) for h, col in HORIZONS}
                for verdict, rs in groups.items()
            }
        view[window] = sections
    return view


def render_view(view: dict) -> str:
    lines: List[str] = []
    for window in sorted(view):
        lines.append(f"\n=== trailing {window}d decisions ===")
        for section in ("by_pm_verdict", "by_dr_verdict"):
            groups = view[window].get(section) or {}
            lines.append(f"\n-- {section} --")
            if not groups:
                lines.append("(no decisions)")
                continue
            lines.append(f"{'verdict':<24}" + "".join(
                f"{h+' n/win/mean':>22}" for h, _ in HORIZONS))
            for verdict in sorted(groups, key=lambda v: -groups[v]["4w"]["n"]):
                cells = []
                for h, _ in HORIZONS:
                    s = groups[verdict][h]
                    cells.append(f"{s['n']:>5}/{s['win_rate']:>4.0%}/{s['mean']:>+7.1%}"
                                 if s["n"] else f"{'—':>22}".strip().rjust(22))
                lines.append(f"{verdict:<24}" + "".join(f"{c:>22}" for c in cells))
    return "\n".join(lines)


def run(windows: Tuple[int, ...] = DEFAULT_WINDOWS) -> None:
    rows = get_outcomes_joined()
    print(f"[verdict_alpha_rolling] {len(rows)} labeled decisions "
          f"(console-only view — never fed to agents)")
    print(render_view(compute_view(rows, datetime.date.today(), windows)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rolling per-verdict forward returns (Tier 3)")
    ap.add_argument("--windows", type=int, nargs="+", default=list(DEFAULT_WINDOWS),
                    help="Trailing windows in days (default: 30 60 90)")
    args = ap.parse_args()
    run(tuple(args.windows))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_verdict_alpha_rolling.py -v`
Expected: 2 PASS

- [ ] **Step 5: Smoke-run against the real DB**

Run: `python -m scripts.analysis.verdict_alpha_rolling`
Expected: prints the labeled-decision count and per-verdict tables without traceback (numbers should be sane vs. `data/calibration_card_candidate.json` — e.g. hundreds of labeled decisions, AVOID/BUY buckets present).

- [ ] **Step 6: Commit**

```bash
git add scripts/analysis/verdict_alpha_rolling.py tests/test_verdict_alpha_rolling.py
git commit -m "feat(analysis): rolling per-verdict returns view (Tier 3, console only)"
```

---

### Task 9: Findings ledger

**Files:**
- Create: `docs/audits/FINDINGS_LEDGER.md`

- [ ] **Step 1: Create `docs/audits/FINDINGS_LEDGER.md`**

```markdown
# Findings Ledger — promotion state machine for feedback

Every audited finding lives here with an explicit tier. Promotion path is
strictly **console → prompt → gate**; demotion may skip levels. Rules
(THREE_TIER_FEEDBACK_PROPOSAL.md):

- **Gate** (enforced in `app/services/decision_gate_service.py`): ≥3 audits
  survived (or ≥2 spanning ≥2 regimes), stable sign, deterministic predicate on
  structured fields, escape hatch defined.
- **Prompt** (pinned card via `scripts/analysis/pin_calibration_card.py`):
  ≥2 audits survived, n ≥ 20, expressed as base rates — never instructions,
  never per-verdict lines, never mistake narratives.
- **Console** (`scripts/analysis/verdict_alpha_rolling.py`, candidate card):
  everything else, including anything regime-dependent or small-n.

The monthly audit MUST re-test every non-console row (the "Next review"
column) before hunting new findings.

| Finding | First seen | Audits survived | Regimes | Tier | Evidence | Next review |
|---|---|---|---|---|---|---|
| Earnings-drop penalty (EARNINGS_MISS/COMPANY_SPECIFIC/ANALYST_DOWNGRADE buys underperform) | Apr 2026 | Apr, May, Jun (3) | 2 | **Gate** — DROP_TYPE_GATE, NAMED_EVENT lift | card 2026-06-25: earnings n=184 mean_ret_4w +0.25% vs non-earnings n=412 +7.3% | Aug 2026 audit |
| Non-earnings dip edge (+5.7 alpha @28d, 56% win) | May 2026 | May, Jun (2) | 2 | **Prompt** — pinned card by_earnings slice | card 2026-06-25 | Jul 2026 audit |
| Edge materializes at ~4 weeks, not 1 | May 2026 | May, Jun (2) | 2 | **Plumbing** — 4w primary label; reassess default 20 trading days | PLAN_calibration_feedback_option1.md; decision_outcomes | Jul 2026 audit |
| AVOID positive alpha (Jun +10.5 @28d) | Jun 2026 | 1 — sign-flipped vs Apr | 1 | **Console** | Jun audit, n=10 | needs 2 more audits |
| BUY_LIMIT is the worst action (median excess −1.9) | Jun 2026 | 1 | 1 | **Console** (already applied as downgrade-target fix 2026-07-02) | research_service.py:1304 docstring; June: 1/9 win at 14d | Jul 2026 audit |
| Falling-knife penalty | May 2026 | signal degenerate since 2026-06-11 | — | **Suspended gate** — RISK_KNIFE_GATE | 256/264 YES (97%); re-enable when trailing-50 YES-rate < 0.60 (nightly log) | on signal recovery |
```

- [ ] **Step 2: Verify the table renders**

Run: `python - <<'EOF'
content = open("docs/audits/FINDINGS_LEDGER.md").read()
rows = [l for l in content.splitlines() if l.startswith("|")]
assert all(l.count("|") == rows[0].count("|") for l in rows), "ragged table"
print(f"ledger ok: {len(rows) - 2} findings")
EOF`
Expected: `ledger ok: 6 findings`

- [ ] **Step 3: Commit**

```bash
git add docs/audits/FINDINGS_LEDGER.md
git commit -m "docs(audits): findings ledger — explicit tier per finding, promotion rules"
```

---

### Task 10: Reassess default for buys + decision_tracking deprecation note

**Files:**
- Modify: `app/services/research_service.py` (persistence dict, line 1013; new helper + constant near the top-level helpers)
- Modify: `app/database.py` (comment at the `decision_tracking` CREATE TABLE)
- Test: `tests/test_reassess_default.py` (create)

**Interfaces:**
- Produces: `REASSESS_DEFAULT_TRADING_DAYS = 20` and `_reassess_in_days_with_default(raw: Optional[int], final_action: str) -> Optional[int]` in `research_service`. Task 11 mirrors the 20-trading-day default in the Sell Council script.

Unit note: `reassess_in_days` is in **trading days** (PM prompt: "trading days before this analysis expires"). The proposal's "default of 28" is calendar days; 4 calendar weeks = **20 trading days**, matching the locked 4w outcome horizon (`backfill_outcomes.HORIZONS["4w"] = 20`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_reassess_default.py`:

```python
"""Phase D plumbing: buys default to a 4-week reassess cadence when the PM
omits reassess_in_days (the edge materializes at ~4 weeks, not 1)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_buy_actions_default_to_four_weeks():
    from app.services.research_service import (
        REASSESS_DEFAULT_TRADING_DAYS, _reassess_in_days_with_default)
    assert REASSESS_DEFAULT_TRADING_DAYS == 20
    assert _reassess_in_days_with_default(None, "BUY") == 20
    assert _reassess_in_days_with_default(None, "BUY_LIMIT") == 20
    assert _reassess_in_days_with_default(0, "BUY") == 20


def test_pm_value_and_non_buys_pass_through():
    from app.services.research_service import _reassess_in_days_with_default
    assert _reassess_in_days_with_default(5, "BUY") == 5
    assert _reassess_in_days_with_default(None, "WATCH") is None
    assert _reassess_in_days_with_default(None, "AVOID") is None
    assert _reassess_in_days_with_default(7, "WATCH") == 7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reassess_default.py -v`
Expected: FAIL with `ImportError: cannot import name 'REASSESS_DEFAULT_TRADING_DAYS'`

- [ ] **Step 3: Implement**

(a) In `app/services/research_service.py`, add near the module-level constants (find a top-level spot after the imports; do NOT place inside a class or function). Confirm `Optional` is already imported from `typing` in this module (it is — but if the import line lacks it, extend it):

```python
# The recovery edge materializes at ~4 weeks, not 1 (locked 4w outcome
# horizon). When the PM omits reassess_in_days on a buy, default the cadence
# to 4 calendar weeks = 20 TRADING days. Plumbing only — the PM prompt is
# deliberately not told this (THREE_TIER_FEEDBACK_PROPOSAL.md, "28d ramp").
REASSESS_DEFAULT_TRADING_DAYS = 20


def _reassess_in_days_with_default(raw: Optional[int], final_action: str) -> Optional[int]:
    if raw:
        return raw
    if (final_action or "").upper() in ("BUY", "BUY_LIMIT"):
        return REASSESS_DEFAULT_TRADING_DAYS
    return raw
```

(b) In the persistence dict (research_service.py:1013), replace:

```python
            "reassess_in_days": final_decision.get("reassess_in_days"),
```

with:

```python
            "reassess_in_days": _reassess_in_days_with_default(
                final_decision.get("reassess_in_days"), gate_result.final_action),
```

(`gate_result` is already in scope — see `"pre_gate_action": gate_result.pre_gate_action` a few lines below.)

(c) In `app/database.py`, locate the `decision_tracking` table (run `grep -n "decision_tracking" app/database.py` to find the CREATE TABLE) and add this comment directly above its creation:

```python
    # DEPRECATED (2026-07-02): decision_tracking was a raw price log with no
    # scheduled writer. Superseded by decision_outcomes (fixed-horizon forward
    # returns, nightly marking). Kept for history — do not write new code
    # against it; use decision_outcomes / get_outcomes_joined instead.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_reassess_default.py -v`
Expected: 2 PASS

- [ ] **Step 5: Run neighbouring regression tests**

Run: `python -m pytest tests/test_gate_persistence.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/research_service.py app/database.py tests/test_reassess_default.py
git commit -m "feat(tracking): buys default to 4-week reassess cadence; deprecate decision_tracking"
```

---

### Task 11: `--due` filter for the Sell Council script

**Files:**
- Modify: `scripts/reassess_positions.py` (helper near `_get_owned_positions`, ~line 50; `main()`, ~line 287)
- Test: `tests/test_reassess_default.py` (extend)

**Interfaces:**
- Consumes: decision dict keys `reassess_timestamp` (last reassess, may be None), `timestamp` (decision time), `reassess_in_days` (trading days, may be None).
- Produces: `_is_due(decision: Dict, today) -> bool`; CLI flag `--due` filtering owned positions to those whose reassess cadence has elapsed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reassess_default.py`:

```python
def test_is_due_uses_cadence_and_last_reassess():
    import datetime
    from scripts.reassess_positions import _is_due
    today = datetime.date(2026, 7, 2)

    # Decision 30 calendar days ago, default cadence (20td ≈ 28cd) -> due.
    assert _is_due({"timestamp": "2026-06-02 10:00:00",
                    "reassess_timestamp": None, "reassess_in_days": None}, today)
    # Decision 10 days ago -> not due yet.
    assert not _is_due({"timestamp": "2026-06-22 10:00:00",
                        "reassess_timestamp": None, "reassess_in_days": None}, today)
    # PM said 5 trading days (=> 7 calendar): 8 days ago -> due.
    assert _is_due({"timestamp": "2026-06-24 10:00:00",
                    "reassess_timestamp": None, "reassess_in_days": 5}, today)
    # Reassessed yesterday resets the clock -> not due.
    assert not _is_due({"timestamp": "2026-06-02 10:00:00",
                        "reassess_timestamp": "2026-07-01 10:00:00",
                        "reassess_in_days": None}, today)
    # Unparseable timestamp -> due (surface it rather than silently skip).
    assert _is_due({"timestamp": "garbage", "reassess_timestamp": None,
                    "reassess_in_days": None}, today)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reassess_default.py -v`
Expected: new test FAILS with `ImportError: cannot import name '_is_due'`

- [ ] **Step 3: Implement**

In `scripts/reassess_positions.py`:

(a) Add `import math` and extend the existing datetime import to `from datetime import datetime, date, timedelta`.

(b) Add after `_get_owned_positions` (~line 54):

```python
# reassess_in_days is in TRADING days; convert to calendar for date math.
TRADING_TO_CALENDAR = 7.0 / 5.0
DEFAULT_REASSESS_TRADING_DAYS = 20  # 4 calendar weeks — the locked outcome horizon


def _is_due(decision: Dict, today: date) -> bool:
    """True when the position's reassess cadence has elapsed since the last
    reassessment (or the original decision, if never reassessed)."""
    base = str(decision.get("reassess_timestamp") or decision.get("timestamp") or "")[:10]
    try:
        base_date = datetime.strptime(base, "%Y-%m-%d").date()
    except ValueError:
        return True  # unknown age — surface it rather than silently skip
    trading_days = decision.get("reassess_in_days") or DEFAULT_REASSESS_TRADING_DAYS
    return today >= base_date + timedelta(days=math.ceil(trading_days * TRADING_TO_CALENDAR))
```

(c) In `main()`, add the flag after the `tickers` argument:

```python
    parser.add_argument(
        "--due",
        action="store_true",
        help="Only reassess positions whose reassess_in_days cadence has elapsed "
             "since the last reassessment (default cadence: 20 trading days).",
    )
```

(d) In the `else` branch (no tickers given), after `decisions = _get_owned_positions()` and before `symbols = ...`, insert:

```python
        if args.due:
            today = datetime.now().date()
            skipped = [d for d in decisions if not _is_due(d, today)]
            decisions = [d for d in decisions if _is_due(d, today)]
            if skipped:
                print(f"--due: skipping {len(skipped)} not-yet-due position(s): "
                      + ", ".join(d["symbol"] for d in skipped))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_reassess_default.py -v`
Expected: 3 PASS

- [ ] **Step 5: Smoke-check the CLI wiring (no API calls)**

Run: `python -m scripts.reassess_positions --help`
Expected: help text shows the `--due` flag; exits 0.

- [ ] **Step 6: Commit**

```bash
git add scripts/reassess_positions.py tests/test_reassess_default.py
git commit -m "feat(sell-council): --due filter driven by reassess_in_days cadence"
```

---

## Final verification (after all tasks)

- [ ] Run every test file touched by this plan, in one command:

```bash
python -m pytest tests/test_calibration_pinning.py tests/test_gate_degeneracy.py \
  tests/test_verdict_alpha_rolling.py tests/test_reassess_default.py \
  tests/test_decision_gate_service.py tests/test_gate_persistence.py -v
```

Expected: all PASS. (Do NOT run the full suite — see Global Constraints.)

- [ ] End-to-end dry run of the new operational flow:

```bash
python -m scripts.analysis.build_calibration_card          # writes candidate
python -m scripts.analysis.pin_calibration_card            # dry-run validates
python -m scripts.analysis.pin_calibration_card --approve  # writes pinned
python -m scripts.analysis.verdict_alpha_rolling            # Tier-3 view
```

Expected: candidate written; validation clean; pinned card written with `audited_at` + `pinned_git_ref`; rolling view prints. Then confirm the pinned card drops per-verdict sections: `python -c "import json; c=json.load(open('data/calibration_card_pinned.json')); assert 'by_pm_verdict' not in c; print('pinned card clean')"`.

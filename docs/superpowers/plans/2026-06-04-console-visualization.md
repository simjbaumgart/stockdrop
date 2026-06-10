# Console Visualization Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-shot `python main.py --visualization` mode that prints performance tables (rich) and cumulative-return line charts (plotext) to the terminal, then exits — no files written.

**Architecture:** A thin flag in `main.py`'s `__main__` block calls a new `app/services/visualization_service.py`. That module reuses the data-loading, price-reconstruction, and rich-table rendering already in `scripts/analysis/verdict_performance.py` (imported, not reimplemented), and adds two new pieces: a pure `build_basket_curves()` that computes equal-weight cumulative-basket returns over calendar time, and a `render_basket_chart()` that draws them with plotext. The mode runs synchronously and `sys.exit(0)` before `uvicorn.run(...)`.

**Tech Stack:** Python 3.9+, pandas, yfinance (already pinned), `rich` (installed, will be pinned), `plotext` (new dependency). pytest for the pure-logic tests.

---

## Reuse contract (read before starting)

`scripts/analysis/verdict_performance.py` is importable as a package module (`scripts/__init__.py` and `scripts/analysis/__init__.py` exist) and has **no import side effects** beyond `warnings.filterwarnings`. The new module imports these symbols from it — do **not** reimplement them:

| Symbol | Signature / shape | Used for |
|---|---|---|
| `load_decisions()` | `-> pd.DataFrame` | Two-DB merge + de-dup; adds `date`, `council_intent`, `dr_intent`, keeps `symbol`, `price_at_decision`, `recommendation`, `deep_research_verdict` |
| `fetch_prices(symbols, start, end)` | `-> dict[str, pd.Series]` | Batch yfinance daily adjusted close (`auto_adjust=True`), tz-naive index; always includes `SPY` |
| `build_table(df, prices, spy, windows, intent_col)` | `-> dict[intent, dict[week, stats]]` | Alpha-vs-SPY table data (Output 1) |
| `render_console(title, table, windows, min_n)` | prints a `rich` table | Output 1 rendering |
| `INTENT_ORDER` | `["ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL"]` | bucket iteration order |
| `INTENT_LABEL` | `dict[intent, str]` | human labels |
| `BENCHMARK` | `"SPY"` | benchmark key into `prices` |
| `ROI_CLIP` | `3.0` | ±300% per-position clip |

**Locked design decisions (do not change):**
- Output 1 (tables) reuses the existing alpha logic verbatim — entry is the yfinance close on/after the decision date, exactly as `verdict_performance.py` already does it.
- Output 2 (charts) uses **`price_at_decision` (the DB column) as the entry price**, per the spec ("each decision opens an equal-weight position at its `price_at_decision`"). `close_t` comes from yfinance. This is an intentional, spec-mandated difference from Output 1.
- Ignore `decision_tracking` entirely.
- Console only. No PNG, no CSV, no cache files — never call `fig.savefig`, never import matplotlib here.

---

## File structure

- **Create** `app/services/visualization_service.py` — all visualization logic (`build_basket_curves`, `render_basket_chart`, `run_visualization`).
- **Create** `tests/test_visualization_service.py` — unit tests for the pure logic (`build_basket_curves`) + a render smoke test.
- **Modify** `main.py` — add `--visualization` flag and the early-exit call (keep thin: import + call + exit only).
- **Modify** `requirements.txt` — add `plotext` and pin `rich`.

---

## Task 1: Add dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add plotext and rich to requirements.txt**

`rich` is already installed (15.0.0) and used by `verdict_performance.py` but is missing from `requirements.txt`; `plotext` (5.3.2) is new. Add both. Insert these two lines after the existing `yfinance==0.2.66` line (keep the file's one-package-per-line, pinned style):

```
plotext==5.3.2
rich==15.0.0
```

- [ ] **Step 2: Install**

Run: `python3 -m pip install plotext==5.3.2 rich==15.0.0`
Expected: `Successfully installed plotext-5.3.2` (rich already satisfied).

- [ ] **Step 3: Verify both import**

Run: `python3 -c "import plotext, rich; print('deps ok')"`
Expected: `deps ok`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "build: add plotext, pin rich for console visualization"
```

---

## Task 2: Pure logic — `build_basket_curves`

This is the only non-trivial new computation, and it is fully testable offline with synthetic data. Build it first, TDD.

**Files:**
- Create: `app/services/visualization_service.py`
- Test: `tests/test_visualization_service.py`

**Function contract:**

`build_basket_curves(df, prices, spy, intent_col) -> dict` returns
```python
{
  "curves": { intent: {"dates": [Timestamp...], "vals": [float...], "final_n": int} },
  "spy_dates": [Timestamp...],
  "spy_vals": [float...],
}
```
where, for each bucket, at date `t` the value is `(mean over positions entered by t of clip(close_t / entry_price) - 1) * 100`, and `spy_vals` is `(spy_close_t / spy_close_at_chart_start - 1) * 100`. `entry_price` is the row's `price_at_decision`. The chart axis starts at the earliest entry date present in `df` and runs over SPY's trading days.

- [ ] **Step 1: Write the failing test**

Create `tests/test_visualization_service.py`:

```python
import pandas as pd
import pytest

from app.services.visualization_service import build_basket_curves


def _series(dates, values):
    return pd.Series(values, index=pd.to_datetime(dates))


def test_basket_curve_single_position_tracks_price_ratio():
    # Trading-day axis = SPY's index.
    axis = ["2026-04-10", "2026-04-11", "2026-04-12"]
    spy = _series(axis, [100.0, 100.0, 110.0])          # SPY +10% by day 3
    # One AAA position, entered day 1 at price_at_decision=50, price doubles by day 3.
    prices = {
        "SPY": spy,
        "AAA": _series(axis, [50.0, 60.0, 100.0]),
    }
    df = pd.DataFrame({
        "symbol": ["AAA"],
        "price_at_decision": [50.0],
        "date": pd.to_datetime(["2026-04-10"]),
        "council_intent": ["ENTER_NOW"],
    })

    out = build_basket_curves(df, prices, spy, "council_intent")

    curve = out["curves"]["ENTER_NOW"]
    # close/entry - 1, *100: 50/50-1=0, 60/50-1=20%, 100/50-1=100%
    assert curve["vals"] == pytest.approx([0.0, 20.0, 100.0])
    assert curve["final_n"] == 1
    # SPY normalized at chart start (day 1): 0%, 0%, +10%
    assert out["spy_vals"] == pytest.approx([0.0, 0.0, 10.0])


def test_basket_curve_position_excluded_before_entry_then_averaged():
    axis = ["2026-04-10", "2026-04-11", "2026-04-12"]
    spy = _series(axis, [100.0, 100.0, 100.0])
    prices = {
        "SPY": spy,
        "AAA": _series(axis, [50.0, 50.0, 50.0]),   # flat, entered day 1
        "BBB": _series(axis, [10.0, 10.0, 20.0]),   # entered day 3 only
    }
    df = pd.DataFrame({
        "symbol": ["AAA", "BBB"],
        "price_at_decision": [50.0, 10.0],
        "date": pd.to_datetime(["2026-04-10", "2026-04-12"]),
        "council_intent": ["ENTER_NOW", "ENTER_NOW"],
    })

    out = build_basket_curves(df, prices, spy, "council_intent")
    curve = out["curves"]["ENTER_NOW"]

    # Day1: only AAA (0%). Day2: only AAA (0%). Day3: AAA 0% and BBB +100% -> mean 50%.
    assert curve["vals"] == pytest.approx([0.0, 0.0, 50.0])
    assert curve["final_n"] == 2


def test_basket_curve_clips_extreme_position_return():
    axis = ["2026-04-10", "2026-04-11"]
    spy = _series(axis, [100.0, 100.0])
    prices = {
        "SPY": spy,
        "AAA": _series(axis, [1.0, 100.0]),  # +9900%, must clip to +300%
    }
    df = pd.DataFrame({
        "symbol": ["AAA"],
        "price_at_decision": [1.0],
        "date": pd.to_datetime(["2026-04-10"]),
        "council_intent": ["ENTER_NOW"],
    })

    out = build_basket_curves(df, prices, spy, "council_intent")
    assert out["curves"]["ENTER_NOW"]["vals"][-1] == pytest.approx(300.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_visualization_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.visualization_service'` (or `ImportError` for `build_basket_curves`).

- [ ] **Step 3: Write the module with imports + `build_basket_curves`**

Create `app/services/visualization_service.py`:

```python
"""Console visualization mode for StockDrop.

Triggered by `python main.py --visualization`. Prints rich performance tables
and plotext line charts to the terminal, then the caller exits. Writes no files.

Output 1 (tables) reuses scripts/analysis/verdict_performance.py verbatim.
Output 2 (charts) plots equal-weight cumulative-basket returns vs an SPY
buy-and-hold reference, entering each position at its DB price_at_decision.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd

from scripts.analysis.verdict_performance import (
    BENCHMARK,
    INTENT_LABEL,
    INTENT_ORDER,
    ROI_CLIP,
    build_table,
    fetch_prices,
    load_decisions,
    render_console,
)

WINDOWS: List[int] = [2, 4, 12]
MIN_N: int = 3  # match verdict_performance.py default


def build_basket_curves(
    df: pd.DataFrame, prices: Dict[str, pd.Series], spy: pd.Series, intent_col: str
) -> dict:
    """Equal-weight cumulative-basket return per intent bucket over calendar time.

    At date t, a bucket's value = mean over positions already entered by t of
    clip(close_t / entry_price, 1±ROI_CLIP); plotted as (value-1)*100. entry_price
    is the row's price_at_decision; close_t is the yfinance close as-of t. The SPY
    reference is buy-and-hold normalized at the chart's start date.
    """
    axis = spy.index  # trading days we have benchmark prices for
    bucket_positions: Dict[str, list] = {}
    earliest = None

    for intent in INTENT_ORDER:
        sub = df[df[intent_col] == intent]
        positions = []
        for _, r in sub.iterrows():
            s = prices.get(r["symbol"])
            entry_price = r["price_at_decision"]
            if s is None or not entry_price or entry_price <= 0:
                continue
            entry_ts = pd.Timestamp(r["date"]).normalize()
            positions.append((entry_ts, float(entry_price), s))
            if earliest is None or entry_ts < earliest:
                earliest = entry_ts
        if positions:
            bucket_positions[intent] = positions

    if not bucket_positions or earliest is None:
        return {"curves": {}, "spy_dates": [], "spy_vals": []}

    axis = axis[axis >= earliest]

    curves: Dict[str, dict] = {}
    for intent, positions in bucket_positions.items():
        cols = {}
        for i, (entry_ts, entry_price, s) in enumerate(positions):
            reindexed = s.reindex(axis).ffill()
            ratio = (reindexed / entry_price).clip(
                lower=1.0 - ROI_CLIP, upper=1.0 + ROI_CLIP
            )
            ratio[axis < entry_ts] = float("nan")  # not entered yet
            cols[i] = ratio
        mat = pd.DataFrame(cols, index=axis)
        counts = mat.count(axis=1)
        basket = mat.mean(axis=1)
        mask = counts > 0
        dates = list(axis[mask])
        if not dates:
            continue
        vals = list(((basket[mask] - 1.0) * 100.0).values)
        curves[intent] = {
            "dates": dates,
            "vals": vals,
            "final_n": int(counts[mask].iloc[-1]),
        }

    spy_axis = spy.reindex(axis).ffill()
    spy_start = float(spy_axis.iloc[0])
    spy_dates = list(axis)
    spy_vals = list(((spy_axis / spy_start - 1.0) * 100.0).values)

    return {"curves": curves, "spy_dates": spy_dates, "spy_vals": spy_vals}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_visualization_service.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/visualization_service.py tests/test_visualization_service.py
git commit -m "feat(viz): add build_basket_curves cumulative-return logic"
```

---

## Task 3: plotext rendering — `render_basket_chart`

**Files:**
- Modify: `app/services/visualization_service.py`
- Test: `tests/test_visualization_service.py`

- [ ] **Step 1: Write the failing smoke test**

Append to `tests/test_visualization_service.py`:

```python
def test_render_basket_chart_runs_on_payload(capsys):
    from app.services.visualization_service import render_basket_chart

    payload = {
        "curves": {
            "ENTER_NOW": {
                "dates": pd.to_datetime(["2026-04-10", "2026-04-11"]).tolist(),
                "vals": [0.0, 5.0],
                "final_n": 2,
            }
        },
        "spy_dates": pd.to_datetime(["2026-04-10", "2026-04-11"]).tolist(),
        "spy_vals": [0.0, 1.0],
    }
    render_basket_chart("Test chart", payload)
    out = capsys.readouterr().out
    assert "Test chart" in out  # plotext renders the title into the terminal output


def test_render_basket_chart_handles_empty(capsys):
    from app.services.visualization_service import render_basket_chart

    render_basket_chart("Empty chart", {"curves": {}, "spy_dates": [], "spy_vals": []})
    out = capsys.readouterr().out
    assert "no data" in out.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_visualization_service.py -k render -v`
Expected: FAIL — `ImportError: cannot import name 'render_basket_chart'`.

- [ ] **Step 3: Add `render_basket_chart`**

Add to `app/services/visualization_service.py` (after `build_basket_curves`):

```python
def render_basket_chart(title: str, payload: dict) -> None:
    """Draw cumulative-return lines for each bucket + an SPY reference, in-terminal.

    plotext has no true dashed style, so the SPY reference is distinguished by a
    distinct marker and an explicit '(buy & hold ref)' label.
    """
    import plotext as plt

    curves = payload.get("curves", {})
    if not curves:
        print(f"\n{title}: no data to chart.")
        return

    plt.clear_figure()
    plt.date_form("Y-m-d")
    plt.theme("pro")

    for intent in INTENT_ORDER:
        c = curves.get(intent)
        if not c:
            continue
        xs = [d.strftime("%Y-%m-%d") for d in c["dates"]]
        plt.plot(xs, c["vals"], label=f"{INTENT_LABEL[intent]} (n={c['final_n']})")

    if payload.get("spy_dates"):
        sxs = [d.strftime("%Y-%m-%d") for d in payload["spy_dates"]]
        plt.plot(sxs, payload["spy_vals"], label="SPY (buy & hold ref)", marker="dot")

    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("Cumulative return %")
    plt.show()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_visualization_service.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/visualization_service.py tests/test_visualization_service.py
git commit -m "feat(viz): add plotext render_basket_chart"
```

---

## Task 4: Orchestration — `run_visualization`

Ties Output 1 (reused tables) and Output 2 (new charts) together. This function does network I/O (yfinance) and DB reads, so it is verified by the end-to-end run in Task 6 rather than a unit test.

**Files:**
- Modify: `app/services/visualization_service.py`

- [ ] **Step 1: Add `run_visualization`**

Add to the end of `app/services/visualization_service.py`:

```python
def run_visualization() -> None:
    """One-shot console report: alpha tables + cumulative-return charts. Writes no files."""
    df = load_decisions()
    print(
        f"Loaded {len(df)} decisions "
        f"({df['date'].min().date()} -> {df['date'].max().date()})"
    )

    start = df["date"].min() - timedelta(days=5)
    end = datetime.now()
    prices = fetch_prices(df["symbol"].tolist(), start, end)
    spy = prices.get(BENCHMARK)
    if spy is None:
        print("Could not fetch SPY benchmark — aborting.")
        return
    print(f"Got prices for {len(prices) - 1} / {df['symbol'].nunique()} symbols.\n")

    # ---- OUTPUT 1: alpha-vs-SPY tables (reuse verdict_performance) ----
    council_tbl = build_table(df, prices, spy, WINDOWS, "council_intent")
    dr_tbl = build_table(df, prices, spy, WINDOWS, "dr_intent")
    render_console(
        "COUNCIL / PM verdict — alpha vs SPY (market-on-decision entry)",
        council_tbl, WINDOWS, MIN_N,
    )
    print()
    render_console(
        "DEEP RESEARCH verdict — alpha vs SPY (market-on-decision entry)",
        dr_tbl, WINDOWS, MIN_N,
    )
    print()

    # ---- OUTPUT 2: cumulative-return line charts ----
    pm_payload = build_basket_curves(df, prices, spy, "council_intent")
    render_basket_chart("Council / PM verdict — cumulative return vs SPY", pm_payload)

    dr_df = df[df["dr_intent"] != ""].copy()
    dr_payload = build_basket_curves(dr_df, prices, spy, "dr_intent")
    render_basket_chart("Deep Research verdict — cumulative return vs SPY", dr_payload)

    print("\nFootnotes:")
    print("  * Cumulative basket = equal-weight, market-on-decision entry at price_at_decision,")
    print("    buy-and-hold to today; each position's return clipped at +/-300%.")
    print("  * SPY line = buy & hold from each chart's start date.")
    print("  * The pre-Apr 9 2026 stretch comes from the legacy DB (data/subscribers.db),")
    print("    an earlier regime of the tool.")
    print("  * Console-only: no files were written.")
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `python3 -c "from app.services.visualization_service import run_visualization; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/services/visualization_service.py
git commit -m "feat(viz): add run_visualization orchestration"
```

---

## Task 5: Wire the `--visualization` flag into main.py

Keep `main.py` thin: one argparse line, one early-exit block. The block must come **before** `uvicorn.run(...)`.

**Files:**
- Modify: `main.py:246-266` (the `if __name__ == "__main__":` block)

- [ ] **Step 1: Add the argparse flag**

In `main.py`, after the existing `--run-for` argument (line 253), add:

```python
    parser.add_argument("--visualization", action="store_true",
                        help="Print performance tables + console charts, then exit")
```

- [ ] **Step 2: Add the early-exit block before uvicorn.run**

In `main.py`, immediately before `uvicorn.run("main:app", ...)` (line 266), insert:

```python
    if args.visualization:
        import sys
        from app.services.visualization_service import run_visualization
        run_visualization()
        sys.exit(0)

```

The resulting tail of the `__main__` block reads: parse args → (email flag) → (run-for env var) → **visualization early-exit** → `uvicorn.run(...)`.

- [ ] **Step 3: Verify argparse accepts the flag (no server start)**

Run: `python3 main.py --help`
Expected: help text lists `--visualization  Print performance tables + console charts, then exit`, and the process exits 0 without starting uvicorn.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(viz): wire --visualization one-shot flag into main.py"
```

---

## Task 6: End-to-end verification (real DBs + yfinance)

**Files:** none (verification only).

- [ ] **Step 1: Run the full visualization mode**

Run: `python3 main.py --visualization`

Expected, in order:
1. `Loaded N decisions (... -> ...)` then a `Fetching prices for ... symbols` progress line.
2. Two **rich tables** ("COUNCIL / PM …" and "DEEP RESEARCH …") with rows per bucket and `Nw alpha / med · n · win%` cells.
3. Two **plotext line charts** rendered in the terminal: "Council / PM verdict — cumulative return vs SPY" and "Deep Research verdict — cumulative return vs SPY", each with one line per intent bucket **plus the SPY (buy & hold ref) line**, axis labels (Date / Cumulative return %), and sane values (single-position curves near 0% at start, no wild >±300% spikes).
4. The footnotes block, ending with "Console-only: no files were written."
5. Process exits 0 (does **not** start the web server / bind a port).

- [ ] **Step 2: Confirm no files were written**

Run: `git status --porcelain` and `ls docs/images/verdict_performance_*.png 2>/dev/null`
Expected: no new untracked files from this run (no new PNG/CSV), and only the already-known modified/untracked files from before this task remain. If any new file appeared, find and remove the offending write — the mode must be console-only.

- [ ] **Step 3: Run the unit tests once more**

Run: `python3 -m pytest tests/test_visualization_service.py -v`
Expected: PASS (5 passed).

- [ ] **Step 4: Final commit (if anything was adjusted during verification)**

Only if Step 1/2 surfaced a fix:
```bash
git add -A
git commit -m "fix(viz): <describe adjustment from verification>"
```

---

## Self-review checklist (completed during planning)

- **Spec coverage:** WIRING → Task 5. DATA reuse (`load_decisions`, `normalize_to_intent` via the imported funcs, two-DB merge, yfinance chunked download, no cache) → Tasks 2 & 4 (reuse contract). Entry = market-on-decision / `price_at_decision`, ±300% clip, ignore `decision_tracking` → locked-decisions section + Task 2. OUTPUT 1 (two rich tables, alpha by bucket over 2/4/12w) → Task 4 via reused `build_table`/`render_console`. OUTPUT 2 (plotext, cumulative basket, PM + DR plots, SPY reference, per-line n labels, legacy-DB footnote) → Tasks 2–4. CONSTRAINTS (type hints, no matplotlib, console-only) → module uses type hints, imports no matplotlib; Task 6 verifies no files written. Verification step → Task 6.
- **Placeholder scan:** no TBD/"handle edge cases"/uncoded steps — every code step shows full code.
- **Type/name consistency:** `build_basket_curves`, `render_basket_chart`, `run_visualization`, payload keys (`curves`/`spy_dates`/`spy_vals`) and curve keys (`dates`/`vals`/`final_n`) are used identically across Tasks 2, 3, 4 and the tests. `WINDOWS`/`MIN_N`/`BENCHMARK`/`ROI_CLIP`/`INTENT_ORDER`/`INTENT_LABEL` referenced consistently.
- **Known caveat (acceptable, spec-driven):** Output 1 uses a yfinance entry price; Output 2 uses the DB `price_at_decision`. This divergence is mandated by the spec and documented in the locked-decisions section. plotext has no true dashed line — SPY is distinguished by marker + label (documented in `render_basket_chart`).

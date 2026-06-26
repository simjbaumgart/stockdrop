# PLAN — Option 1: Static Calibration Injection (lightweight feedback loop)

**Status:** Proposed
**Owner:** Simon
**Author drafted with:** Claude (Cowork)
**Prereq:** Phase 0 (decision_tracking fix) — see §2 and §4.

Closes the open loop between past decisions (Council / PM / DR verdicts) and their realized
outcomes by computing **base rates** offline and injecting a small **calibration card** into the
PM and DR prompts at decision time. No retraining, no architecture change — the model still
decides, it just stops flying blind on historical hit rates.

This is the cheapest of the three feedback-loop options and maps directly onto findings already
proven manually in the profit audits (drop_type lever, DR-straight-BUY 94% win, earnings-dip
underperformance in tariff/oil regimes, anti-calibrated HIGH conviction).

---

## 1. Goal & scope

**In scope**
- A scheduled job that aggregates realized outcomes into a `calibration_card.json` (+ readable `.md`).
- Injection of a relevant slice of that card into the PM prompt (`_create_fund_manager_prompt`)
  and the DR prompt (`build_individual_prompt`), behind a feature flag.
- An A/B test (shadow-run design) to measure whether the card actually improves recommendations.

**Out of scope (later options)**
- Learned weights / meta-decision layer (Option 3).
- Per-candidate retrieval of similar past cases (Option 2).

---

## 2. Background — the decision_tracking dependency (THE TRACKING FIX)

A calibration card is only as good as the outcome labels feeding it. Right now those labels do
not exist. Here is exactly what's broken and what "the fix" is.

**What's broken**
1. **The live writer is never scheduled.** `main.py` startup (`asyncio.create_task(...)`, ~line 119)
   launches 5 loops: `run_periodic_check`, `run_storage_upload`, `run_daily_summary`,
   `run_performance_tracking`, `run_trade_report_update`. **None of them call
   `tracking_service.update_tracked_stocks()`.** So `decision_tracking` has effectively never been
   populated by a live loop — consistent with every audit (May 15 / 18 / 25 / 26, Jun 09) reporting
   the table at **0 rows lifetime** in the live `subscribers.db`.
2. **The schema is a raw price log, not forward returns.** `decision_tracking(decision_id, price,
   timestamp)` (database.py:189) just appends snapshots. Even if it ran, deriving a clean "+7d return"
   label means post-processing the nearest snapshot to each horizon — fragile and gappy.
3. **Live snapshots can't be recovered retroactively.** Because the writer never ran, there is no way
   to reconstruct intraday history from the live table for the ~745 decisions (Apr 9 → Jun 9).

**The fix (two parts)**
- **Part A — Backfill outcomes from yfinance (the real prerequisite for Option 1).** Forward returns
  are deterministic functions of historical daily closes, so reconstruct them. For every past decision,
  pull daily closes from `price_at_decision` date forward and compute return at fixed horizons
  (+1w/+2w/+4w/+8w) plus boolean "recovered" labels. Store in a new purpose-built table
  `decision_outcomes` (§4.1). This gives Option 1 trustworthy labels **immediately and reproducibly**,
  decoupled from the flaky live writer.
- **Part B — Restore forward-marking going forward.** Add a scheduled daily job that updates
  `decision_outcomes` as each horizon matures (a decision made today gets its +1w mark filled 7
  calendar/5 trading days later, etc.), so the card stays fresh without manual backfills. This replaces
  the snapshot-append model with a fixed-horizon-return model. (Keep `add_tracking_point` if other code
  depends on it, but the calibration loop reads `decision_outcomes`, not `decision_tracking`.)
- **Part C — QC guard.** A daily check that fails loudly if matured decisions are missing outcome marks,
  so this never silently rots for weeks again.

> Decision: Option 1 **requires Part A** (backfill) as Phase 0. Part B/C can land in parallel or
> immediately after — the card can be rebuilt from backfill until the live job proves stable.

---

## 3. Architecture / data flow

```
decision_points  ─┐
                  ├─►  build_calibration_card.py  ─►  data/calibration_card.json (+ .md)
decision_outcomes ┘        (scheduled weekly)                 │
   ▲                                                          │
   │ (Phase 0 backfill + daily forward-marking)               ▼
yfinance daily closes                          _create_fund_manager_prompt (PM)
                                               build_individual_prompt (DR)
                                                  └─► inject relevant base-rate slice
                                                      (feature-flagged: CALIBRATION_ENABLED)
```

---

## 4. Phase 0 — Tracking fix (executable)

### 4.1 New outcomes table

Add to `app/database.py` `init_db()` (alongside the other `CREATE TABLE IF NOT EXISTS` blocks):

```python
cursor.execute('''
    CREATE TABLE IF NOT EXISTS decision_outcomes (
        decision_id   INTEGER PRIMARY KEY,
        symbol        TEXT,
        decision_date TEXT,
        price_at_decision REAL,
        ret_1w  REAL, ret_2w  REAL, ret_4w  REAL, ret_8w  REAL,
        recovered_1w INTEGER, recovered_4w INTEGER,   -- 1 if ret >= 0 at horizon
        last_filled_horizon TEXT,                      -- '1w' | '2w' | '4w' | '8w' | 'complete'
        source        TEXT DEFAULT 'yfinance_backfill',
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (decision_id) REFERENCES decision_points (id)
    )
''')
```

Helpers in `database.py`: `upsert_decision_outcome(decision_id, **fields)`,
`get_decisions_missing_outcomes(as_of_date)`, `get_outcomes_joined()` (returns decision_points ⋈
decision_outcomes for the card builder).

### 4.2 Backfill script — `scripts/maintenance/backfill_outcomes.py`

```python
"""Backfill decision_outcomes from yfinance historical closes. Idempotent."""
import yfinance as yf, pandas as pd
from app.database import get_decision_points, upsert_decision_outcome

HORIZONS = {"1w": 5, "2w": 10, "4w": 20, "8w": 40}  # trading days

def fwd_return(closes: pd.Series, start_idx: int, td: int):
    if start_idx + td >= len(closes): return None
    p0, p1 = closes.iloc[start_idx], closes.iloc[start_idx + td]
    return None if not p0 else (p1 / p0 - 1.0)

def run(dry_run=False, limit=None):
    decisions = get_decision_points()
    for d in (decisions[:limit] if limit else decisions):
        sym, dec_date = d["symbol"], d["timestamp"][:10]
        hist = yf.Ticker(sym).history(start=dec_date, period="3mo")["Close"]
        if hist.empty: continue
        i0 = 0  # first session on/after decision date
        fields = {h: fwd_return(hist, i0, td) for h, td in HORIZONS.items()}
        rec = {f"ret_{h}": v for h, v in fields.items()}
        rec["recovered_1w"] = int(fields["1w"] >= 0) if fields["1w"] is not None else None
        rec["recovered_4w"] = int(fields["4w"] >= 0) if fields["4w"] is not None else None
        rec["last_filled_horizon"] = "complete" if fields["8w"] is not None else \
            next((h for h in reversed(HORIZONS) if fields[h] is not None), None)
        if not dry_run:
            upsert_decision_outcome(d["id"], symbol=sym, decision_date=dec_date,
                                    price_at_decision=d.get("price_at_decision"), **rec)

if __name__ == "__main__":
    import argparse
    a = argparse.ArgumentParser(); a.add_argument("--dry-run", action="store_true")
    a.add_argument("--limit", type=int); args = a.parse_args()
    run(dry_run=args.dry_run, limit=args.limit)
```

> Use the decision's own `price_at_decision` as the baseline where available (closer to the real
> fill than a daily close), falling back to the first session close. Mind yfinance split/dividend
> adjustment — use `auto_adjust=True` consistently for baseline and horizon.

### 4.3 Live forward-marking job

Add `run_outcome_marking()` to `main.py` background tasks and register it in startup. Daily, it calls
`get_decisions_missing_outcomes(today)` and fills any horizon that has now matured by reusing
`backfill_outcomes.run()` logic (single function, two callers). Cheap: one yfinance call per
decision with an unfilled-but-matured horizon.

### 4.4 QC guard

Extend the existing QC/daily-summary path: fail/alert if any decision older than 8 calendar days
has `last_filled_horizon` NULL, or if `decision_outcomes` had zero upserts in the last run.

**Phase 0 acceptance checks**
- `python scripts/maintenance/backfill_outcomes.py --dry-run --limit 5` prints sane returns.
- After full backfill: `SELECT COUNT(*) FROM decision_outcomes WHERE ret_4w IS NOT NULL` ≈ count of
  decisions ≥ 4w old.
- Spot-check 3 tickers against a manual yfinance pull.

---

## 5. Phase 1 — Calibration card builder

### 5.1 Script — `scripts/analysis/build_calibration_card.py`

Reads `get_outcomes_joined()`, computes per-bucket stats, writes `data/calibration_card.json` and a
readable `data/calibration_card.md`. **Suppress any bucket with n < `MIN_N` (default 20)** to avoid
feeding noise into the prompt.

Buckets (start coarse — these are where you have the sample and the proven signal):
- by `drop_type` (proven +2pts/trade lever)
- by `is_earnings_drop` (earnings dips underperform, regime-dependent)
- by PM `recommendation` (PM has no 7d edge — card should reveal this)
- by `deep_research_verdict` and DR-override-vs-agree (DR-straight-BUY ≈ 94% win — strongest signal)
- by `gatekeeper_tier`
- optional 2-way combos only where n ≥ MIN_N (e.g. earnings_drop × regime)

Each bucket emits: `n`, `recovery_rate_4w`, `mean_ret_4w`, `mean_ret_1w`. Keep the whole card under
~25 lines of numbers so prompt cost stays negligible.

```python
# core aggregation sketch
import json, statistics as st
from collections import defaultdict
from app.database import get_outcomes_joined
MIN_N = 20

def bucketize(rows, keyfn):
    b = defaultdict(list)
    for r in rows:
        if r.get("ret_4w") is None: continue
        b[keyfn(r)].append(r)
    out = {}
    for k, rs in b.items():
        if len(rs) < MIN_N: continue
        rets = [r["ret_4w"] for r in rs]
        out[k] = {"n": len(rs),
                  "recovery_rate_4w": round(sum(r["recovered_4w"] for r in rs)/len(rs), 3),
                  "mean_ret_4w": round(st.mean(rets), 4)}
    return out

def run():
    rows = get_outcomes_joined()
    card = {
        "as_of": __import__("datetime").date.today().isoformat(),
        "min_n": MIN_N,
        "by_drop_type": bucketize(rows, lambda r: r.get("drop_type") or "unknown"),
        "by_earnings": bucketize(rows, lambda r: "earnings" if r.get("is_earnings_drop") else "non_earnings"),
        "by_pm_verdict": bucketize(rows, lambda r: r.get("recommendation") or "unknown"),
        "by_dr_verdict": bucketize(rows, lambda r: r.get("deep_research_verdict") or "unknown"),
        "by_gatekeeper_tier": bucketize(rows, lambda r: r.get("gatekeeper_tier") or "unknown"),
    }
    json.dump(card, open("data/calibration_card.json", "w"), indent=2)
    # also render data/calibration_card.md (human-readable)
```

### 5.2 Schedule

Weekly (or nightly) — add to `main.py` background loops or run via the existing scheduling path. Card
is a cached artifact; nothing in the hot decision path recomputes it.

**Phase 1 acceptance checks**
- `data/calibration_card.json` exists, every bucket has `n ≥ MIN_N`.
- The card numbers reproduce known audit findings (DR-straight-BUY recovery_rate ≫ PM-BUY).

---

## 6. Phase 2 — Prompt injection (feature-flagged)

### 6.1 Formatter — `app/services/calibration_service.py`

```python
import json, os, functools

@functools.lru_cache(maxsize=1)
def _load_card(path="data/calibration_card.json"):
    try: return json.load(open(path))
    except FileNotFoundError: return None

def calibration_block(drop_type=None, is_earnings=None) -> str:
    """Return a short base-rate block for the relevant slice, or '' if disabled/missing."""
    if os.getenv("CALIBRATION_ENABLED", "0") != "1": return ""
    card = _load_card()
    if not card: return ""
    lines = ["HISTORICAL BASE RATES (4-week, n≥{}):".format(card["min_n"])]
    dt = (card["by_drop_type"] or {}).get(drop_type)
    if dt: lines.append(f"- This drop_type ({drop_type}): {dt['recovery_rate_4w']:.0%} recovered, "
                        f"mean {dt['mean_ret_4w']:+.1%} (n={dt['n']})")
    ek = "earnings" if is_earnings else "non_earnings"
    ev = (card["by_earnings"] or {}).get(ek)
    if ev: lines.append(f"- {ek} drops overall: {ev['recovery_rate_4w']:.0%} recovered, "
                        f"mean {ev['mean_ret_4w']:+.1%} (n={ev['n']})")
    lines.append("Weigh these base rates; do not let a compelling narrative override a poor base rate.")
    return "\n".join(lines)
```

### 6.2 Wire into prompts

- **PM:** in `research_service._create_fund_manager_prompt` (research_service.py:1782), insert
  `calibration_block(drop_type=..., is_earnings=...)` near the top of the instruction body.
- **DR:** in `claude_dr_prompts.build_individual_prompt` (claude_dr_prompts.py:257), same insertion.

Keep insertion additive and clearly delimited so it's trivial to A/B toggle and to diff. Default
`CALIBRATION_ENABLED=0` so prod behavior is unchanged until the A/B says otherwise.

**Phase 2 acceptance checks**
- With flag off: prompts byte-identical to today (regression-safe).
- With flag on: block appears, ≤ ~6 lines, correct numbers for the candidate's drop_type.

---

## 7. Phase 3 — A/B testing plan

Goal: decide whether the calibration card **improves recommendation quality** before flipping it on
for the live book. Reuse the existing **shadow-run pattern** (`news_shadow_runs` table +
`app/services/news_shadow_service.py`) — proven infrastructure for "run two variants, log both,
compare later."

### 7.1 Design — shadow first, then live arm

**Stage 1 — Shadow A/B (no impact on live trading, signal in days).**
For each live decision, after the real PM/DR run (control = card OFF), run a **second PM synthesis with
the card ON** and log both verdicts to a new `calibration_shadow_runs` table:

```
calibration_shadow_runs(
  id, decision_id, symbol, decision_date,
  control_verdict, control_conviction, control_score,
  treatment_verdict, treatment_conviction, treatment_score,
  card_slice TEXT,          -- exactly what was injected
  verdict_flipped INTEGER,  -- control != treatment
  timestamp )
```
This measures **how often and in which direction the card changes the call** immediately, and — once
`decision_outcomes` matures — lets us compare realized returns of control vs treatment verdicts on the
*same* candidates (paired, removing candidate-selection noise). Marginal cost: one extra PM call per
decision (DR is expensive/rate-limited, so shadow PM only; add DR to the shadow only for the subset
where the verdict flipped).

**Stage 2 — Live randomized arm (only if Stage 1 looks positive).**
Randomize *acted* decisions by deterministic hash of `decision_id` (50/50). Treatment gets the card
injected for real; control does not. This measures the effect on the **actual book**, not just shadow
verdicts. Pre-register the run length and metrics before starting.

### 7.2 Metrics

Primary
- **Mean realized 4w return per BUY**, treatment vs control (paired in Stage 1; unpaired in Stage 2).

Secondary
- Win rate (recovered_4w) per BUY.
- **Conviction calibration:** correlation between stated conviction and realized return (audit found
  HIGH conviction anti-calibrated — card should improve this).
- **Earnings-dip BUY loss rate** (card should reduce bad earnings-dip buys).
- **Verdict-flip rate** and flip direction (Stage 1 only) — sanity that the card moves decisions and
  in the expected direction (more caution on poor-base-rate buckets).

Guardrail (must NOT degrade)
- **DR-straight-BUY hit rate** (currently ~94%). If the card pulls this down, that's a stop.
- Added tokens/latency per call (cap card at ~6 lines; assert no material latency regression).

### 7.3 Sample size / duration

At ~10–29 decisions/day, BUYs are a fraction of that, so realized-return power is the binding
constraint. Plan: run **Stage 1 shadow for ≥ 4 weeks** to accumulate flips + maturing outcomes; only
launch Stage 2 if Stage 1 shows a positive, directionally-sensible return delta. Report effect sizes
with confidence intervals; treat fine-grained buckets as descriptive, not powered. Pre-register the
horizon to avoid peeking.

### 7.4 Decision rule

Ship the card to prod (`CALIBRATION_ENABLED=1`) iff:
1. Treatment mean 4w return ≥ control by a margin whose CI excludes ~0, **and**
2. No degradation in the DR-straight-BUY guardrail, **and**
3. Conviction calibration is flat-or-improved.
Otherwise iterate on card content (which buckets, MIN_N, wording) and re-run Stage 1.

**Phase 3 acceptance checks**
- `calibration_shadow_runs` accrues paired rows; flip-rate computable.
- A `scripts/analysis/eval_calibration_ab.py` produces the metric table (control vs treatment) with CIs.

---

## 8. Cost & resource estimate

| Phase | Eng effort | Runtime cost | Risk |
|---|---|---|---|
| 0 — tracking fix (backfill + live mark + QC) | ~2–3 days | yfinance calls (free), one daily job | Low; biggest unblocker |
| 1 — calibration card builder | ~1 day | one scheduled aggregation/week | Low |
| 2 — prompt injection (flagged) | ~0.5 day | +~150 tokens/PM+DR call | Low (flag-gated) |
| 3 — A/B (shadow + eval) | ~2–3 days | +1 shadow PM call/decision | Low (shadow = no live impact) |

Total ≈ **1 week of focused work**, dominated by Phase 0. No GPU, no new infra, no model changes.
Ongoing cost: a weekly aggregation job + a few hundred extra tokens per decision when enabled.

---

## 9. Rollout sequence

1. **Phase 0** — backfill outcomes, verify against manual spot-checks, stand up live marking + QC.
2. **Phase 1** — build the card; sanity-check it reproduces known audit findings.
3. **Phase 2** — wire injection behind `CALIBRATION_ENABLED=0` (prod unchanged).
4. **Phase 3 Stage 1** — shadow A/B for ≥4 weeks; analyze flips + maturing outcomes.
5. **Phase 3 Stage 2** — optional live randomized arm if shadow is positive.
6. **Ship or iterate** per §7.4 decision rule.

## 10. Open questions / decisions for Simon

- Primary horizon: lock on **4w** for the card label, or weight 1w+4w? (Audit work mostly used 7d/desk
  exits; 4w gives more recovery room for the dip thesis.)
- `MIN_N` threshold: 20 is a starting guess — raise if buckets look noisy.
- Baseline price for returns: `price_at_decision` vs first daily close after decision (affects gap-day
  drops). Recommend `price_at_decision` when present.

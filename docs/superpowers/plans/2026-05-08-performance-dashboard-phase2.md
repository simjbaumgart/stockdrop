# Performance Dashboard — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bake the diagnostic findings from Phase 1 into a persistent, auto-updated `/insights` web page, plus a small scoreboard tile on the existing `dashboard.html`.

**Architecture:** Reuse `app/services/analytics/` from Phase 1. New JSON endpoint computes aggregations (cached 60 min in-memory). Template uses Chart.js (already loaded in `performance.html`) for interactive charts. Single source of truth — same numbers as the deep-dive report.

**Spec:** `docs/superpowers/specs/2026-05-08-performance-analysis-design.md`

---

## File map

- Create: `app/services/analytics/summary.py` — orchestrates aggregations into a single `summary_json()` payload, with TTL cache.
- Create: `app/routers/insights.py` — `/insights` HTML route + `/api/insights/summary` JSON route.
- Create: `templates/insights.html` — page with Chart.js, fetches summary JSON.
- Modify: `templates/base.html` — add Insights nav link.
- Modify: `templates/dashboard.html` — prepend scoreboard tile.
- Modify: `main.py` — include the new router.

---

## Task 1 — Summary builder with TTL cache

Pulls cohort + bars, computes the views needed by the dashboard, returns JSON-serializable dict. Cached 60 min so repeated page loads don't re-fetch yfinance.

`summary_json()` returns:
```
{
  "generated_at": ISO-8601,
  "cohort_size": int,
  "headline": {
    "win_rate_4w_buys": float,        # ENTER_NOW + ENTER_LIMIT
    "avg_return_4w_buys": float,
    "buy_limit_fill_rate": float,
    "buy_limit_avg_filled_4w": float,
    "median_days_to_recover": float,
    "n_recovered": int
  },
  "winrate_by_intent": [{intent, count, win_rate, avg_return} ...],
  "winrate_by_horizon": [{horizon, intent, n, win_rate, avg_return} ...],
  "winrate_by_drop_bucket": [{bucket, count, win_rate, avg_return} ...],
  "winrate_by_dr_action": [...],
  "winrate_by_gatekeeper": [...],
  "equity_curve": [{date, equity, n} ...],
  "time_to_recover": [{days, count} ...]
}
```

## Task 2 — Insights router

`GET /insights` renders `insights.html`. `GET /api/insights/summary` returns the cached JSON (with `?refresh=1` to force rebuild).

## Task 3 — Insights template (Chart.js)

Embedded Chart.js charts that consume the JSON: equity-curve line, win-rate-by-intent bar, drop-bucket bar, time-to-recover histogram. Headline metrics shown as cards above the charts.

## Task 4 — Scoreboard tile on dashboard

Top of `dashboard.html`: 4 small cards showing
- Cohort size + last refresh time
- Win rate (4w) on BUY/BUY_LIMIT
- BUY_LIMIT fill rate
- Median days to recover

"View full insights →" link to `/insights`.

## Task 5 — Nav + wiring

- Add "Insights" link to `base.html` sidebar.
- `app.include_router(insights.router)` in `main.py`.

## Task 6 — Smoke test

```bash
./venv/bin/python -c "from app.services.analytics.summary import summary_json; import json; print(json.dumps(summary_json(refresh=True), default=str)[:500])"
```
Then start uvicorn, hit `/insights` and `/api/insights/summary`, confirm charts render.

---

## What's deliberately not here

- Live broker-side P&L (would require Alpaca integration that respects current decision_tracking schema).
- Alerts/notifications based on dashboard thresholds.
- Multi-user permissioning.

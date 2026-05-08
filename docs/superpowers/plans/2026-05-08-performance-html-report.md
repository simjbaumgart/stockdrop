# Performance HTML Report — Plan

> Replaces the earlier "Phase 2 dashboard" plan. The user opted to keep
> performance analysis fully offline rather than wiring it into the
> uvicorn app, so the persistent dashboard was reverted.

**Goal:** Generate a single self-contained interactive HTML report for exploring StockDrop's recommendation performance and the underlying per-decision data. Open it in a browser; no server.

**Architecture:**
- Pure analytics module from Phase 1 produces a JSON-friendly payload (`app/services/analytics/payload.py:build_payload`).
- Standalone script `scripts/analysis/deep_dive_html.py` inlines the payload into a single HTML file with Chart.js (CDN) for interactivity.
- Output: `docs/performance/<date>-deep-dive.html`.

**Why offline:**
- Same source-of-truth analytics functions as the markdown report — no drift.
- No FastAPI surface, no cache to invalidate, no nav clutter.
- File can be emailed, archived, diffed against future re-runs.

**Contents of the HTML:**
- Headline metric cards (win rate, fill rate, recovery, cohort size)
- Equity curve (Chart.js line)
- Win-rate breakdowns: by intent (with 1w/2w/4w/8w tabs), drop bucket, DR action, gatekeeper tier
- Time-to-recovery histogram
- Per-decision explorer table with text filter, intent filter, recovered filter, click-to-sort columns

**Run:**
```bash
./venv/bin/python scripts/analysis/deep_dive_html.py
# or
./venv/bin/python scripts/analysis/deep_dive_html.py --start all --out docs/performance/full-history.html
```

**Out of scope:**
- Live in-app dashboard (deliberately reverted).
- Auto-refresh / background generation.
- Multi-cohort comparison views.

# Monthly Browsable Data Snapshot — Design

**Date:** 2026-05-24
**Status:** Approved, ready for implementation plan
**Goal:** Publish a curated, browsable snapshot of the last 30 days of StockDrop decisions + outcomes inside the public GitHub repo, so visitors can inspect what the system decided, what happened to those positions, and how to read the data.

---

## 1. Problem & motivation

The repo already markets StockDrop as an autonomous AI hedge-fund analyst, but a visitor lands on the README with no way to see what the system actually produces. We have 560 decisions in `subscribers.db` (404 in the last 30 days) and 45 tracked positions in `desk_positions` with realized/unrealized P&L. Shipping a monthly snapshot turns the repo from a pitch into a verifiable demo.

The existing `docs/performance/YYYY-MM-DD-package/` pattern (last entry: `2026-05-11-package/`) is a research-oriented dump aimed at us. The new snapshot is aimed at outside readers: narrative-first, scannable, light on column count, and explicitly free of LLM free-text reasoning (so it can't be mistaken for marketing copy).

## 2. Scope

In-scope:
- One-shot snapshot at `docs/performance/2026-05-24-package/` covering decisions where `timestamp >= datetime('now', '-30 days')`.
- Hand-curated landing-page README with embedded charts, headline stats, and links to case studies + raw data.
- 3–4 hand-picked case studies (drafted from data, user reviews before commit).
- Trimmed CSVs: structured numerics and enums only; every free-text LLM column dropped.
- Static PNG charts rendered with matplotlib at build time.
- Update top-level README with a link to the snapshot.

Out-of-scope (deliberate YAGNI):
- Recurring/scheduled snapshot generation. We build the tooling so a re-run is one command, but no cron.
- GitHub Pages or any site generator. Markdown + CSV browsing on github.com is the target.
- Backfilling `decision_tracking` time-series. It's empty; outcome data comes from `desk_positions`.
- Time-series price overlays (would require live price fetching at build time).
- Anonymization of subscribers — table is empty today, but the export script will explicitly skip it as a guardrail.

## 3. Directory layout

```
docs/performance/2026-05-24-package/
├── README.md                    Landing page: pitch + headline stats + embedded charts + nav
├── charts/
│   ├── verdict-distribution.png BUY / BUY_LIMIT / WATCH / AVOID counts (last 30d)
│   ├── sector-breakdown.png     Decisions grouped by sector
│   ├── pnl-distribution.png     Histogram of realized P&L from desk_positions
│   └── score-vs-outcome.png     Scatter: AI score (x) vs realized return (y), closed positions
├── case-studies/
│   ├── 01-best-trade.md         Biggest realized winner in the window
│   ├── 02-worst-trade.md        Biggest realized loss or stop-out
│   ├── 03-avoided-correctly.md  AVOID where price kept dropping
│   └── 04-still-open.md         Interesting active position
└── data/
    ├── README.md                Data dictionary + column descriptions
    ├── decisions.csv            decision_points, last 30d, ~25-column allowlist
    ├── positions.csv            desk_positions full (24 cols, all structured)
    ├── monthly_summary.csv      Aggregates: counts/win-rate/mean-P&L by verdict
    ├── schema.sql               CREATE TABLE for decision_points + desk_positions
    └── manifest.csv             Row counts + generated_at timestamp per file
```

## 4. Why this layout works on GitHub

GitHub auto-renders these in the browser with zero extra infrastructure:
- `README.md` in any directory is the directory's landing page.
- `.csv` renders as a sortable, paginated table (up to ~10 MB).
- `.png` / `.svg` embed inline in markdown.
- Markdown cross-links between sibling files Just Work.

A visitor's path: repo root README → "📊 Monthly snapshot" section → package README → embedded charts visible immediately, case-studies linked, raw data one click away.

## 5. CSV column policy

### `decisions.csv` — KEEP (allowlist, ~25 cols)
`id, symbol, company_name, sector, timestamp, price_at_decision, drop_percent, recommendation, ai_score, conviction, drop_type, entry_price_low, entry_price_high, stop_loss, take_profit_1, take_profit_2, deep_research_action, deep_research_score, deep_research_conviction, deep_research_entry_low, deep_research_entry_high, deep_research_tp1, deep_research_tp2, sa_quant_rating, wall_street_rating, gatekeeper_tier, batch_winner`

### `decisions.csv` — DROP (free-text LLM output)
`reasoning, deep_research_risk, deep_research_catalyst, deep_research_knife_catch, deep_research_swot, deep_research_global_analysis, deep_research_local_analysis, deep_research_verification, deep_research_blindspots, deep_research_reason, reassess_reasoning, deep_research_review_verdict`

### `positions.csv`
Ship all 24 columns from `desk_positions` — every column is structured (numerics, dates, status enums). No LLM prose lives in that table.

### `monthly_summary.csv`
Pre-aggregated for visitors who want the punch line without filtering: one row per `recommendation` × `gatekeeper_tier`, columns: `count, mean_drop_pct, mean_ai_score, n_with_positions, n_closed, win_rate, mean_realized_pnl_pct`.

### `subscribers` table
Explicitly skipped in the export step, regardless of whether it has rows. Documented in `data/README.md`.

## 6. Charts

All four are matplotlib PNG, 1200×800, rendered from the trimmed CSVs (not the live DB) so the package is internally consistent and re-renderable.

1. **verdict-distribution.png** — horizontal bar, one bar per `recommendation`, sorted by count. Annotated with absolute counts.
2. **sector-breakdown.png** — horizontal bar, top 12 sectors by decision count, color-coded by majority verdict.
3. **pnl-distribution.png** — histogram of `realized_pnl_pct` from closed `desk_positions`. Vertical line at zero and at the mean.
4. **score-vs-outcome.png** — scatter, `ai_score` vs `realized_pnl_pct` for closed positions, with regression line and Pearson r in the corner.

Chart 4 may have a small N (closed-position subset). If N < 10, render the chart with a "small sample" note baked into the image and call it out in the README.

## 7. Case studies

Each is one short markdown file, structured the same way:
- **Heading:** Ticker + company + date of decision.
- **The setup:** sector, market cap, drop %, why it triggered the screener.
- **The verdict:** PM action + score, DR action + score (action + score only — no reasoning text).
- **The plan:** entry range, stop, TP1, TP2.
- **What happened:** entry price filled (from `desk_positions`), current/exit price, realized or unrealized P&L %, days held.
- **Takeaway:** one line on what this illustrates about the system.

User picks the 4 cases after seeing a candidate list the script prints. Drafts are generated for review; user edits before commit.

## 8. Implementation building blocks

1. **New script: `scripts/build_monthly_snapshot.py`** — the entry point. Orchestrates everything end-to-end.
2. **Reuse `scripts/analysis/export_database.py`** for the read-only DB connection pattern (already correct: `?mode=ro`, `PRAGMA query_only`, fail-fast timeout). Extend it (or extract a helper) to support `--since-days N`, `--tables decision_points,desk_positions`, and `--columns-allowlist FILE`.
3. **New module: `app/services/snapshot/charts.py`** — pure functions that take a DataFrame and write a PNG to a given path. Keeps matplotlib usage out of the orchestration script.
4. **New module: `app/services/snapshot/aggregates.py`** — `build_monthly_summary(decisions_df, positions_df) -> DataFrame` and `compute_headline_stats(...) -> dict[str, str]` for README template substitution.
5. **Template: `app/services/snapshot/templates/README.md.j2`** — Jinja2 template for the package landing page. Headline stats injected via `compute_headline_stats`.
6. **Template: `app/services/snapshot/templates/data_README.md.j2`** — data dictionary template, mostly static.
7. **Case-study drafter (in the orchestration script):** identifies the top candidate row per case-study category, prints the row + a draft markdown to stdout for user review/copy.
8. **Top-level README update:** add a "📊 Monthly snapshot" section pointing at the latest package.

## 9. Command-line interface

```
python scripts/build_monthly_snapshot.py --as-of 2026-05-24 [--since-days 30] [--db subscribers.db] [--dry-run]
```

- `--as-of` (required): determines the output directory name and the upper bound of the time window.
- `--since-days` (default 30): rolling window size.
- `--db` (default `subscribers.db`): DB to read from.
- `--dry-run`: log what would be written but produce no files. Used for review before commit.

Idempotent: re-running with the same `--as-of` overwrites the package directory contents.

## 10. Testing

- Unit tests in `tests/test_snapshot_*.py`:
  - `test_column_allowlist_drops_llm_fields` — assert no banned column appears in `decisions.csv`.
  - `test_subscribers_table_never_exported` — guardrail test, even on a fixture with rows.
  - `test_monthly_summary_aggregates` — fixture DB with known rows, assert win_rate / mean_pnl values.
  - `test_chart_writes_png_with_small_n_note` — chart 4 with N < 10 produces an image with the note (check via PIL pixel sampling or by inspecting a sidecar JSON the chart fn writes).
- Integration test: run the full script against `tests/fixtures/snapshot_test.db` with `--as-of 2026-05-24` and assert directory structure matches the spec exactly.
- Manual verification: open the produced package on github.com via a draft PR; confirm README renders, CSVs render as tables, PNGs embed.

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Free-text column accidentally leaks into `decisions.csv` after schema migration | Allowlist + unit test that fails when a non-allowlisted column appears (prevents drift) |
| `subscribers.csv` shipped with emails | Table explicitly excluded; guardrail test |
| `decisions.csv` exceeds GitHub's 10 MB inline-render limit | At ~25 cols × ~400 rows it's far under; if it ever grows, monitor `manifest.csv` size column |
| Stale snapshot — visitor sees old data | Date in the directory name makes recency obvious; top-level README always links to the latest |
| Cases curated to look favorable ("survivorship bias") | One slot is reserved for "worst trade" by construction |

## 12. Acceptance criteria

The snapshot is ready to ship when, with `git ls-files docs/performance/2026-05-24-package/`:
1. Every file in the directory layout above exists.
2. `data/decisions.csv` contains only allowlisted columns, validated by a passing unit test.
3. `data/positions.csv` row count matches the `desk_positions` row count at build time.
4. All four PNGs are present and non-empty.
5. The four case-study markdowns have been reviewed and edited by the user (not raw drafter output).
6. The package README renders correctly when previewed on github.com (manual check via draft PR).
7. The top-level README links to the snapshot.

# PM Verdict — Highlight R/R + Append External Ratings

**Date:** 2026-05-10
**Status:** Design approved, ready for implementation plan
**Scope:** UI/console + DB persistence only. No change to PM decision logic.

## Goal

After the Portfolio Manager prints its verdict for a candidate, two things should happen:

1. The **risk/reward** numbers (R/R, upside %, downside %) get visually highlighted in the console block — they are currently a single line buried among five.
2. **External ratings** (SA Quant, SA Authors, Wall Street, SA Rank) are appended to the verdict block as **informational context for the human reader**. They are also persisted to `decision_points` so the dashboard / trade reports can surface them later.

The external ratings are **never** shown to any LLM agent. They do not influence the PM verdict, the deep-research review, or the Sell Council. This is a deliberate constraint to avoid contaminating the model with a signal that has weak/no predictive correlation with our cohort's returns (per `scripts/analysis/sa_ranking_correlation.py`).

## Non-goals

- Changing PM, deep-research, or Phase 1/2 agent prompts
- Adding ratings to dashboard HTML or email summary (follow-up if useful)
- Auto-refreshing the SA grades CSV (manual snapshot for now)
- Using the ratings in any gating, scoring, or override logic

## Final output — what the user sees

```
==================================================
  [PORTFOLIO MANAGER DECISION]: BUY (Conviction: HIGH)
  Drop Type: SECTOR_ROTATION
  Entry Zone: $42.10 - $43.50
  Stop Loss: $39.80 | TP1: $48.00 | TP2: $52.00

  ┌─ RISK / REWARD ──────────────────────────────┐
  │  R/R: 2.0x  ✅                                │
  │  Upside  +12.5%   ↑ to TP1                   │
  │  Downside −6.4%   ↓ to Stop                  │
  └──────────────────────────────────────────────┘

  Sell Zone: $48.00 - $52.00 | Ceiling: $55.00
  Entry Trigger: <text>
  Exit Trigger: <text>
  Reassess In: 5 trading days
  Reason: <text>
  Key Factors:
   - <factor>

  ┌─ EXTERNAL RATINGS (informational, not seen by agents) ─┐
  │  SA Quant Rating:    4.62  (Strong Buy)                 │
  │  SA Analyst Rating:  3.80  (Buy)                        │
  │  Wall Street Rating: 4.10  (Buy)                        │
  │  SA Rank:            #312 / 3,958                       │
  └─────────────────────────────────────────────────────────┘

  Total Agent Calls: 17
==================================================
```

### R/R glyph thresholds

| R/R ratio | Glyph |
|---|---|
| `≥ 2.0` | ✅ |
| `1.5 ≤ x < 2.0` | ⚠️ |
| `< 1.5` | ❌ |
| `None` (couldn't compute) | print `R/R: n/a` with no glyph |

### Rating label bands

The CSV stores numeric ratings 1.0–5.0. Map to qualitative labels with the bands SA itself uses:

| Score range | Label |
|---|---|
| `≥ 4.5` | Strong Buy |
| `3.5 ≤ x < 4.5` | Buy |
| `2.5 ≤ x < 3.5` | Hold |
| `1.5 ≤ x < 2.5` | Sell |
| `< 1.5` | Strong Sell |
| missing | n/a |

### Ticker not in CSV

If the ticker isn't found, replace the entire ratings block with one line:

```
  External Ratings: n/a (ticker not in SA_Quant_Ranked_Clean.csv)
```

### CSV file missing / unreadable

Print a single warning line on first lookup:

```
  External Ratings: unavailable (snapshot CSV missing or unreadable)
```

Subsequent lookups in the same process don't re-warn. The pipeline must continue normally — this is purely cosmetic data.

## Architecture

### New module: `app/services/sa_grades_service.py`

Single-responsibility, ~80 LOC:

```python
class SAGradesService:
    def __init__(self, csv_path: Optional[str] = None): ...
    def lookup(self, ticker: str) -> dict:
        # Returns: {
        #   "sa_quant_rating": float | None,
        #   "sa_authors_rating": float | None,
        #   "wall_street_rating": float | None,
        #   "sa_rank": int | None,
        #   "total_ranked": int | None,   # 3958 etc — for "#X / Y" display
        #   "available": bool,            # False if CSV missing entirely
        # }
```

- Loads the CSV **lazily** on first call (not at import time, so tests / CLI tools that don't need it don't pay the cost).
- After load, keeps an in-memory dict `{ticker: row}` for O(1) lookup.
- Reuses the `parse_rating()` regex from `scripts/analysis/sa_ranking_correlation.py:52` (extract numeric tail from `"Rating: Strong Buy4.99"`). Move that helper into the new module so the analysis script imports from there — single source of truth.
- `total_ranked` = `len(df)` after load, used to render the rank denominator.
- Path resolution order:
  1. Constructor arg
  2. `SA_GRADES_CSV_PATH` env var
  3. Default: `data/SAgrades/SA_Quant_Ranked_Clean.csv` (repo-relative)
- Singleton instance `sa_grades_service = SAGradesService()` exported, mirroring the pattern used by other services.

### CSV snapshot

The snapshot lives at `data/SAgrades/SA_Quant_Ranked_Clean.csv` locally. **Not committed** — `data/` is in `.gitignore`, so the file is kept off-repo. Operators must place a copy at that path manually (or set `SA_GRADES_CSV_PATH` to point elsewhere). When the file is absent, the runtime degrades gracefully to the "unavailable" fallback line — the pipeline never blocks on missing ratings.

A brief `data/SAgrades/README.md` documents the snapshot date and refresh process for any operator who has the local snapshot.

### Print-block changes — `app/services/research_service.py:521-536`

Replace the existing block with a new helper `_print_pm_verdict(state, final_decision)` that prints in the order shown above. Two helpers inside it:

- `_format_rr_block(upside, downside, rr) -> str` — returns the bordered R/R block with glyph
- `_format_ratings_block(ratings) -> str` — returns the bordered external-ratings block (or single-line fallback)

Calls `sa_grades_service.lookup(state.ticker)` exactly once.

### Return dict — add four fields

In the dict returned by `analyze_stock()` (around `app/services/research_service.py:538`), add:

```python
"sa_quant_rating": ratings.get("sa_quant_rating"),
"sa_authors_rating": ratings.get("sa_authors_rating"),
"wall_street_rating": ratings.get("wall_street_rating"),
"sa_rank": ratings.get("sa_rank"),
```

These fields are added **last in the dict**, kept separate from the PM trading-level fields, with a comment marking them as `# External ratings (informational; never shown to agents)`.

### DB schema — `app/database.py`

Add a migration that adds four columns to `decision_points`:

```sql
ALTER TABLE decision_points ADD COLUMN sa_quant_rating REAL;
ALTER TABLE decision_points ADD COLUMN sa_authors_rating REAL;
ALTER TABLE decision_points ADD COLUMN wall_street_rating REAL;
ALTER TABLE decision_points ADD COLUMN sa_rank INTEGER;
```

Follow the existing migration pattern in `app/database.py` (idempotent ALTER guarded by a column-existence check, as the file does for prior additions).

The caller that writes a `decision_points` row needs to include these four fields. Find that insert site by searching for an existing column write (e.g. `risk_reward_ratio`) — same site needs the new columns plumbed through.

## LLM-leakage audit (CRITICAL)

Before merge, verify that none of these prompt/text surfaces include the new rating fields:

- [ ] PM prompt (`research_service.py` — find the agent that produces `final_decision`)
- [ ] `_format_full_report()` output (consumed by deep research)
- [ ] `evidence` strings produced by `seeking_alpha_service.get_evidence()` and other Phase 1 sensors
- [ ] Phase 2 debate prompts (Bull / Bear / Risk)
- [ ] `deep_research_service.py` — neither prompt nor any field it reads from `final_decision`
- [ ] `scripts/reassess_positions.py` (Sell Council)

The mechanism that guarantees this: `sa_grades_service.lookup()` is called **only** in the print/persist site after `final_decision` is finalized. The result is stored in **separate dict keys**, not merged into any field that flows into agent input.

A grep test in CI / a one-off verification is enough — there is no automated test for "string X is not in prompt Y" but a manual check of the 6 surfaces above is sufficient.

## Testing

- **Unit test** for `SAGradesService.lookup()`:
  - Hit on known ticker → correct floats + rank
  - Miss on unknown ticker → all `None`, `available=True`
  - Missing CSV → all `None`, `available=False`, no exception
  - Malformed rating string → `None` for that field, others still parse
- **Unit test** for `_format_rr_block()` covering all 4 R/R bands (incl. `None`)
- **Unit test** for `_format_ratings_block()` covering: full data, ticker miss, CSV missing
- **Integration check**: run the pipeline against one known recent drop, eyeball the console output

No mocking of the DB or APIs needed for the new tests — `SAGradesService` is pure file I/O + dict lookup.

## Open follow-ups (not in this spec)

- Add ratings columns to the dashboard tables and trade-report CSV
- Add a `/refresh-sa-grades` admin endpoint or a cron that re-pulls the CSV
- Decide whether to backfill the four columns for existing `decision_points` rows

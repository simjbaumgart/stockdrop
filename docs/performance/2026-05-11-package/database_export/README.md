# Database export — 2026-05-11

Exported from `subscribers.db` in read-only mode at 2026-05-11T16:59:40.203028.

The running FastAPI service was not interrupted; SQLite's URI read-only mode (`?mode=ro`) does not acquire write locks.

## Tables

| table             |   row_count |   columns | file                  |
|:------------------|------------:|----------:|:----------------------|
| batch_comparisons |          10 |         6 | batch_comparisons.csv |
| decision_points   |         373 |        84 | decision_points.csv   |
| decision_tracking |           0 |         4 | decision_tracking.csv |
| desk_positions    |          42 |        24 | desk_positions.csv    |
| desk_reviews      |         110 |        18 | desk_reviews.csv      |
| subscribers       |           0 |         2 | subscribers.csv       |
| transcript_cache  |           3 |         7 | transcript_cache.csv  |

## Files

- `batch_comparisons.csv` — 10 rows × 6 cols
- `decision_points.csv` — 373 rows × 84 cols
- `decision_tracking.csv` — 0 rows × 4 cols
- `desk_positions.csv` — 42 rows × 24 cols
- `desk_reviews.csv` — 110 rows × 18 cols
- `subscribers.csv` — 0 rows × 2 cols
- `transcript_cache.csv` — 3 rows × 7 cols
- `schema.sql` — CREATE TABLE statements
- `manifest.csv` — this table

# Fixes and Ideas

Running list of small fixes, polish items, and ideas to revisit. No code here — just capture.

## Fixes

- [ ] Reduce Deep Research Monitor update interval from 1m to 3m
  - Currently logs `[Deep Research Monitor] Active Agent: 0 | Queue: 0 (Ind), 0 (Batch) | HH:MM:SS` every 60s
  - Noisy when idle; 3-minute cadence is sufficient

- [ ] Add R/R (Risk/Reward) column to the decisions table
  - Currently shows: Date | Symbol | Market | Rec | Limit | Price @ Dec | Price +7d | Perf | Price +14d | Perf 2W | Price +28d | Perf 4W | SP500 7d | Dow 7d | DAX 7d | Verdict | Batch | Status | Evidence
  - Add R/R column (likely sourced from PM verdict / deep research output) so the ratio is visible alongside the recommendation
  - Decide placement (near Rec/Limit makes most sense) and source field

## Ideas

_(none yet)_

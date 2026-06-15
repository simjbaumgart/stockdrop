# Benzinga News A/B Test — Summary

Generated: 2026-04-27T17:31:56.509893+00:00
Tickers: 8

## Per-ticker counts

| Ticker | With | Without | Benzinga items | Δ chars | Headlines unique to WITH |
|---|---:|---:|---:|---:|---:|
| CYATY | 12 | 7 | 5 | +10138 | 5 |
| DLTR | 30 | 30 | 12 | +15247 | 12 |
| CHGCY | 16 | 11 | 5 | +10138 | 5 |
| TAL | 30 | 19 | 6 | +15646 | 20 |
| DPZ | 30 | 30 | 15 | +27799 | 14 |
| MZHOF | 14 | 9 | 5 | +10138 | 5 |
| IBRX | 30 | 25 | 15 | +19126 | 13 |
| CAR | 30 | 30 | 15 | +24673 | 12 |
| **avg** | 24.0 | 20.1 | 9.8 | +16613 | 10.8 |

## Interpretation guide

- **Benzinga items**: how many articles came from Benzinga/Massive + Market News combined.
- **Δ chars**: extra prompt characters Benzinga contributes (positive = Benzinga adds content).
- **Headlines unique to WITH**: news the agent would lose entirely if Benzinga were cancelled. 
  These are the items NOT covered by Alpha Vantage / Finnhub / yfinance / TradingView.
- **Headlines unique to WITHOUT** (in per-ticker JSON): items currently being deduped out by 
  Benzinga but that other providers do carry — these would re-appear if Benzinga were cancelled.

## Decision criteria (suggested)

- If `Headlines unique to WITH` averages < ~2 across the sample, Benzinga is largely redundant.
- If it averages ≥ 5 and includes substantive (not duplicate-paraphrased) headlines, 
  Benzinga is pulling its weight.
- Eyeball at least 2 `<TICKER>_with.txt` vs `<TICKER>_without.txt` pairs for qualitative 
  judgement — counts alone miss content depth (Benzinga `CONTENT:` blocks vs others' `SUMMARY:`).

## Conclusion (human review, 2026-04-27)

**Finding:** Benzinga adds substantive, non-redundant content. The avg of **10.8 unique-to-WITH headlines** and **+16.6K char delta** sits well above the "pulling its weight" threshold (≥5).

**Qualitative read (DPZ + TAL eyeballed):**
- Benzinga's `Massive` `CONTENT:` blocks contain pre-digested per-ticker sentiment with quantitative anchors (e.g. *"[DPZ] NEGATIVE: cut guidance to 0.9% vs 2.3% consensus, signals structural consumer weakness"*) — directly relevant to the news agent's "why did the stock drop?" task.
- A single Benzinga macro article often tags 20+ other tickers with NEG/POS/NEU calls, providing cross-ticker market intel free providers don't carry.
- Free providers (Finnhub, yfinance) give headlines + short summaries; Benzinga gives full bodies up to 8K chars each.
- Market News (SPY/DIA/QQQ aggregation) is genuinely unique — 5 macro-context items per US ticker that no other configured provider returns.
- For ADRs (CYATY, CHGCY, MZHOF) Benzinga adds only the 5 market-news items (no company-specific coverage).

**Methodological caveat:** Alpha Vantage hit its free-tier rate limit (5 calls/min) several times during the run, occasionally landing on only one of the WITH/WITHOUT pair (visible in TAL where `providers_with` has 17 AV items but `providers_without` has 0). This inflates "unique to WITH" for those tickers — the AV gap, not Benzinga, is responsible for some of those headlines. The Δchars metric is more robust (it's dominated by Benzinga's CONTENT bodies which AV doesn't have anyway).

**Recommendation:** **Keep Benzinga.** The pre-digested per-ticker sentiment with quantitative anchors is exactly the input the news agent and PM need, and no free source replicates it. The $100/mo is justified by (a) ticker-level analyst insights with NEG/POS/NEU tags, (b) per-ticker quantitative signal extraction, (c) macro market-news block.

**If revisiting later:** Re-run with `--from-db 20` and add a serial delay between WITH/WITHOUT runs to clear the Alpha Vantage rate-limit window, so the metric measures Benzinga in isolation rather than co-mingling with AV availability.

# Benzinga News A/B Test — Summary

Generated: 2026-04-27T17:20:36.426487+00:00
Tickers: 2

## Per-ticker counts

| Ticker | With | Without | Benzinga items | Δ chars | Headlines unique to WITH |
|---|---:|---:|---:|---:|---:|
| AAPL | 30 | 30 | 15 | +32260 | 14 |
| NVDA | 30 | 30 | 15 | +30575 | 13 |
| **avg** | 30.0 | 30.0 | 15.0 | +31418 | 13.5 |

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

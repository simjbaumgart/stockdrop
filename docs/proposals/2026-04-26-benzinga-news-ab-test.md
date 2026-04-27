# Benzinga News A/B Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-shot analysis script that, for 5–10 real tickers, fetches the news pipeline twice (with Benzinga/Massive enabled vs. disabled), formats the news section of the news-agent prompt for both, and produces a diff report so we can decide whether the $100/mo Polygon-Benzinga subscription pays for itself.

**Architecture:** Standalone runner under `scripts/analysis/`. Reuses `StockService.get_aggregated_news()` and re-implements the news-prompt-section builder copied verbatim from `research_service._create_news_agent_prompt` (lines 690–756). Benzinga is "disabled" by monkey-patching `benzinga_service.get_company_news` and `benzinga_service.get_market_news` to return `[]` — this is the only honest way to measure loss because the in-pipeline dedup currently drops Finnhub/yfinance/TV headlines that match Benzinga ones; bypass requires re-fetching, not post-filtering. Production code is not modified.

**Tech Stack:** Python 3.9, existing services (`stock_service`, `benzinga_service`, etc.), SQLite (`subscribers.db` for ticker pool), stdlib only for the script. No new dependencies.

**Output location:** `audit_reports/benzinga_ab_test/<run_timestamp>/` (already-gitignored directory).

**Non-goals:** No production-code changes. No removal of Benzinga calls. No automated decision — the report is for human review.

---

## File Structure

- **Create:** `scripts/analysis/ab_test_benzinga_news.py` — single-file script, ~250 lines
- **Output (created at runtime):** `audit_reports/benzinga_ab_test/<UTC-timestamp>/`
  - `<TICKER>_with.txt` — formatted news prompt section, Benzinga active
  - `<TICKER>_without.txt` — same, Benzinga forced empty
  - `<TICKER>_metrics.json` — per-ticker counters (article counts by provider, unique-headline overlap, char length delta, etc.)
  - `summary.md` — rollup table across all tickers, plus high-level conclusions

No production files are modified. No tests are added — the script is a research artefact; verification is by running it and reading the output.

---

## Task 1: Skeleton script with CLI

**Files:**
- Create: `scripts/analysis/ab_test_benzinga_news.py`

- [ ] **Step 1: Write the script skeleton with argparse**

```python
#!/usr/bin/env python3
"""
A/B test the news pipeline with vs without Benzinga/Massive.

Usage:
    python scripts/analysis/ab_test_benzinga_news.py --tickers AAPL,NVDA,TSLA
    python scripts/analysis/ab_test_benzinga_news.py --from-db 8
    python scripts/analysis/ab_test_benzinga_news.py --tickers AAPL --output-dir /tmp/ab

By default reads up to 10 distinct recent tickers from subscribers.db decision_points.
Writes <ticker>_with.txt, <ticker>_without.txt, <ticker>_metrics.json, and summary.md
to audit_reports/benzinga_ab_test/<UTC-timestamp>/.
"""
from __future__ import annotations  # required for `str | None` and PEP 585 generics on Py 3.9

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the project root importable when run from anywhere
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def parse_args():
    p = argparse.ArgumentParser(description="Benzinga news A/B test")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--tickers", help="Comma-separated tickers, e.g. AAPL,NVDA")
    src.add_argument("--from-db", type=int, metavar="N",
                     help="Pull the last N distinct tickers from decision_points")
    p.add_argument("--output-dir", default=None,
                   help="Override output directory (default: audit_reports/benzinga_ab_test/<ts>)")
    p.add_argument("--limit", type=int, default=10,
                   help="Cap on number of tickers (default 10)")
    return p.parse_args()


def main():
    args = parse_args()
    print(f"[ab-test] args: {args}")
    # rest of pipeline added in later tasks


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run skeleton and verify CLI parses**

Run: `python scripts/analysis/ab_test_benzinga_news.py --tickers AAPL,NVDA`
Expected: prints `[ab-test] args: Namespace(tickers='AAPL,NVDA', from_db=None, ...)` and exits 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/analysis/ab_test_benzinga_news.py
git commit -m "scripts: skeleton for benzinga news A/B test"
```

---

## Task 2: Ticker selection (CLI or DB)

**Files:**
- Modify: `scripts/analysis/ab_test_benzinga_news.py`

- [ ] **Step 1: Add ticker resolution functions**

Insert above `main()`:

```python
DB_PATH = ROOT / "subscribers.db"


def tickers_from_db(n: int) -> list[str]:
    """Return up to n most-recent distinct tickers from decision_points."""
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found at {DB_PATH}")
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute(
            "SELECT ticker FROM decision_points "
            "ORDER BY datetime(date) DESC, id DESC"
        )
        seen, out = set(), []
        for (t,) in cur:
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t)
            if len(out) >= n:
                break
        return out
    finally:
        con.close()


def resolve_tickers(args) -> list[str]:
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    elif args.from_db:
        tickers = tickers_from_db(args.from_db)
    else:
        tickers = tickers_from_db(args.limit)
    return tickers[: args.limit]
```

- [ ] **Step 2: Wire into main()**

Replace the body of `main()` with:

```python
def main():
    args = parse_args()
    tickers = resolve_tickers(args)
    if not tickers:
        raise SystemExit("No tickers resolved.")
    print(f"[ab-test] tickers ({len(tickers)}): {tickers}")
```

- [ ] **Step 3: Run and verify both modes**

Run: `python scripts/analysis/ab_test_benzinga_news.py --tickers aapl,nvda,tsla`
Expected: prints `[ab-test] tickers (3): ['AAPL', 'NVDA', 'TSLA']`

Run: `python scripts/analysis/ab_test_benzinga_news.py --from-db 5`
Expected: prints 5 distinct ticker symbols from decision_points.

- [ ] **Step 4: Commit**

```bash
git add scripts/analysis/ab_test_benzinga_news.py
git commit -m "scripts(ab-test): resolve tickers from CLI or decision_points"
```

---

## Task 3: Dual-fetch helper (with vs without Benzinga)

**Files:**
- Modify: `scripts/analysis/ab_test_benzinga_news.py`

Strategy: import `StockService` and `benzinga_service`, run `get_aggregated_news` once normally, then monkey-patch the two Benzinga functions to return `[]`, run again, restore the originals. This makes the "without" branch fetch fresh from Alpha Vantage/Finnhub/yfinance/TradingView **without** dedup-eviction by Benzinga headlines — i.e. it reflects what we'd actually see if we cancelled.

- [ ] **Step 1: Add fetch helper**

Insert above `main()`:

```python
from contextlib import contextmanager

from app.services.stock_service import StockService
from app.services import benzinga_service


@contextmanager
def benzinga_disabled():
    """Force Benzinga company + market news to return empty for the duration."""
    orig_company = benzinga_service.get_company_news
    orig_market = benzinga_service.get_market_news
    benzinga_service.get_company_news = lambda *a, **kw: []
    benzinga_service.get_market_news = lambda *a, **kw: []
    try:
        yield
    finally:
        benzinga_service.get_company_news = orig_company
        benzinga_service.get_market_news = orig_market


def fetch_both(ticker: str) -> tuple[list[dict], list[dict]]:
    """Return (with_benzinga_news, without_benzinga_news) for a ticker."""
    svc = StockService()
    print(f"  [{ticker}] fetching WITH Benzinga ...")
    with_news = svc.get_aggregated_news(ticker, region="US", exchange="", company_name="")
    print(f"  [{ticker}] fetching WITHOUT Benzinga ...")
    with benzinga_disabled():
        without_news = svc.get_aggregated_news(ticker, region="US", exchange="", company_name="")
    return with_news, without_news
```

- [ ] **Step 2: Smoke-test on a single ticker**

Append to bottom of `main()`:

```python
    # TEMP smoke-test — remove in next task
    sample = tickers[0]
    w, wo = fetch_both(sample)
    print(f"[ab-test] {sample}: with={len(w)} items, without={len(wo)} items")
```

Run: `python scripts/analysis/ab_test_benzinga_news.py --tickers AAPL`
Expected: prints something like `[ab-test] AAPL: with=22 items, without=14 items` (exact counts vary).
If you see API errors for any one provider, that's fine — the others should still produce items. If `with` < `without`, the monkey-patch is broken; debug.

- [ ] **Step 3: Commit**

```bash
git add scripts/analysis/ab_test_benzinga_news.py
git commit -m "scripts(ab-test): dual-fetch with monkey-patched benzinga"
```

---

## Task 4: Copy news-prompt-section builder

We must reproduce the exact formatting that goes into the news agent prompt — otherwise the diff is meaningless. The source of truth is `research_service._create_news_agent_prompt` lines 688–756. Copy the news-summary loop verbatim (it's a pure function over a list). Do **not** refactor the production code to share it — this is a throwaway analysis.

**Files:**
- Modify: `scripts/analysis/ab_test_benzinga_news.py`

- [ ] **Step 1: Add the formatter**

Insert above `main()`:

```python
def format_news_section(news_items: list[dict]) -> str:
    """
    Reproduces the news_summary block built in
    app/services/research_service.py::_create_news_agent_prompt
    (provider grouping + preferred ordering + per-item formatting).
    Keep in sync if research_service.py changes.
    """
    items = sorted(news_items, key=lambda x: x.get("datetime", 0), reverse=True)

    by_provider: dict[str, list[dict]] = {}
    for n in items:
        prov = n.get("provider", "Other Sources")
        by_provider.setdefault(prov, []).append(n)

    preferred = [
        "Market News (Benzinga)", "Benzinga/Massive",
        "Alpha Vantage", "Finnhub", "Yahoo Finance", "TradingView",
    ]

    out = []

    def render_group(prov: str, group: list[dict]):
        group_type = group[0].get("source_type", "WIRE") if group else "WIRE"
        if prov == "Market News (Benzinga)":
            out.append("--- BROAD MARKET CONTEXT (SPY/DIA/QQQ) [MARKET_CONTEXT] ---")
        else:
            out.append(f"--- SOURCE: {prov} [{group_type}] ---")
        for n in group:
            date_str = n.get("datetime_str", "N/A")
            headline = n.get("headline", "No Headline")
            source = n.get("source", "Unknown")
            stype = n.get("source_type", "WIRE")
            content = n.get("content", "") or ""
            summary = n.get("summary", "") or ""
            out.append(f"- {date_str}: [{stype}] {headline} ({source})")
            if content:
                txt = content if len(content) <= 8000 else content[:8000] + "..."
                out.append(f"  CONTENT:\n{txt}\n")
            elif summary:
                out.append(f"  SUMMARY: {summary}\n")
            else:
                out.append("")

    for prov in preferred:
        if prov in by_provider:
            render_group(prov, by_provider[prov])
    for prov, group in by_provider.items():
        if prov not in preferred:
            render_group(prov, group)

    return "\n".join(out)
```

- [ ] **Step 2: Wire smoke-test through formatter**

Replace the temp smoke-test block at the bottom of `main()` with:

```python
    sample = tickers[0]
    w, wo = fetch_both(sample)
    s_with = format_news_section(w)
    s_without = format_news_section(wo)
    print(f"[ab-test] {sample}: with={len(s_with)} chars, without={len(s_without)} chars")
    print("--- WITH (first 600 chars) ---")
    print(s_with[:600])
    print("--- WITHOUT (first 600 chars) ---")
    print(s_without[:600])
```

- [ ] **Step 3: Run and eyeball**

Run: `python scripts/analysis/ab_test_benzinga_news.py --tickers AAPL`
Expected: both sections start with `--- SOURCE: ... ---` headers; `with` should include `Benzinga/Massive` and likely `Market News (Benzinga)` blocks; `without` should not. Char counts should differ noticeably.

- [ ] **Step 4: Commit**

```bash
git add scripts/analysis/ab_test_benzinga_news.py
git commit -m "scripts(ab-test): port news-section formatter from research_service"
```

---

## Task 5: Per-ticker output files + metrics

**Files:**
- Modify: `scripts/analysis/ab_test_benzinga_news.py`

- [ ] **Step 1: Add metric computation**

Insert above `main()`:

```python
BENZINGA_PROVIDERS = {"Benzinga/Massive", "Market News (Benzinga)"}


def per_ticker_metrics(ticker: str, with_news: list[dict], without_news: list[dict],
                       s_with: str, s_without: str) -> dict:
    def by_prov(items):
        d: dict[str, int] = {}
        for n in items:
            d[n.get("provider", "Other")] = d.get(n.get("provider", "Other"), 0) + 1
        return d

    benzinga_only = [n for n in with_news if n.get("provider") in BENZINGA_PROVIDERS]
    headlines_with = {n.get("headline") for n in with_news if n.get("headline")}
    headlines_without = {n.get("headline") for n in without_news if n.get("headline")}
    unique_to_with = sorted(headlines_with - headlines_without)
    unique_to_without = sorted(headlines_without - headlines_with)

    has_insights = sum(1 for n in benzinga_only if n.get("content"))

    return {
        "ticker": ticker,
        "counts": {
            "with_total": len(with_news),
            "without_total": len(without_news),
            "benzinga_items": len(benzinga_only),
            "with_chars": len(s_with),
            "without_chars": len(s_without),
            "char_delta": len(s_with) - len(s_without),
        },
        "providers_with": by_prov(with_news),
        "providers_without": by_prov(without_news),
        "headlines_only_in_with": unique_to_with,
        "headlines_only_in_without": unique_to_without,
        "benzinga_items_with_full_content": has_insights,
    }
```

- [ ] **Step 2: Add output-dir setup and per-ticker writer**

Insert above `main()`:

```python
def setup_output_dir(override: str | None) -> Path:
    if override:
        out = Path(override)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = ROOT / "audit_reports" / "benzinga_ab_test" / ts
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_ticker_output(out: Path, ticker: str,
                        s_with: str, s_without: str, metrics: dict):
    (out / f"{ticker}_with.txt").write_text(s_with)
    (out / f"{ticker}_without.txt").write_text(s_without)
    (out / f"{ticker}_metrics.json").write_text(json.dumps(metrics, indent=2))
```

- [ ] **Step 3: Replace smoke-test with full per-ticker loop**

Replace the bottom of `main()` (the smoke-test block) with:

```python
    out = setup_output_dir(args.output_dir)
    print(f"[ab-test] writing output to {out}")
    all_metrics: list[dict] = []
    for t in tickers:
        try:
            w, wo = fetch_both(t)
        except Exception as e:
            print(f"  [{t}] FAILED to fetch: {e}")
            continue
        s_with = format_news_section(w)
        s_without = format_news_section(wo)
        m = per_ticker_metrics(t, w, wo, s_with, s_without)
        write_ticker_output(out, t, s_with, s_without, m)
        all_metrics.append(m)
        print(f"  [{t}] with={m['counts']['with_total']} "
              f"without={m['counts']['without_total']} "
              f"benzinga={m['counts']['benzinga_items']} "
              f"Δchars={m['counts']['char_delta']}")
    (out / "all_metrics.json").write_text(json.dumps(all_metrics, indent=2))
    print(f"[ab-test] done. {len(all_metrics)} tickers processed.")
```

- [ ] **Step 4: Run on 2 tickers and check files**

Run: `python scripts/analysis/ab_test_benzinga_news.py --tickers AAPL,NVDA`
Expected: `audit_reports/benzinga_ab_test/<ts>/` contains 6 files per ticker pair plus `all_metrics.json`. Open one `_metrics.json` and confirm fields are populated and `headlines_only_in_with` is non-empty for at least one ticker (otherwise Benzinga adds nothing — also a finding).

- [ ] **Step 5: Commit**

```bash
git add scripts/analysis/ab_test_benzinga_news.py
git commit -m "scripts(ab-test): per-ticker outputs and metrics json"
```

---

## Task 6: Rollup summary report

**Files:**
- Modify: `scripts/analysis/ab_test_benzinga_news.py`

- [ ] **Step 1: Add summary writer**

Insert above `main()`:

```python
def write_summary(out: Path, all_metrics: list[dict]):
    if not all_metrics:
        (out / "summary.md").write_text("# Benzinga A/B test\n\nNo tickers processed.\n")
        return

    lines = [
        "# Benzinga News A/B Test — Summary",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Tickers: {len(all_metrics)}",
        "",
        "## Per-ticker counts",
        "",
        "| Ticker | With | Without | Benzinga items | Δ chars | Headlines unique to WITH |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    tot_with = tot_without = tot_bz = tot_delta = 0
    tot_unique_with = 0
    for m in all_metrics:
        c = m["counts"]
        u = len(m["headlines_only_in_with"])
        lines.append(
            f"| {m['ticker']} | {c['with_total']} | {c['without_total']} "
            f"| {c['benzinga_items']} | {c['char_delta']:+d} | {u} |"
        )
        tot_with += c["with_total"]; tot_without += c["without_total"]
        tot_bz += c["benzinga_items"]; tot_delta += c["char_delta"]
        tot_unique_with += u
    n = len(all_metrics)
    lines += [
        f"| **avg** | {tot_with/n:.1f} | {tot_without/n:.1f} "
        f"| {tot_bz/n:.1f} | {tot_delta/n:+.0f} | {tot_unique_with/n:.1f} |",
        "",
        "## Interpretation guide",
        "",
        "- **Benzinga items**: how many articles came from Benzinga/Massive + Market News combined.",
        "- **Δ chars**: extra prompt characters Benzinga contributes (positive = Benzinga adds content).",
        "- **Headlines unique to WITH**: news the agent would lose entirely if Benzinga were cancelled. ",
        "  These are the items NOT covered by Alpha Vantage / Finnhub / yfinance / TradingView.",
        "- **Headlines unique to WITHOUT** (in per-ticker JSON): items currently being deduped out by ",
        "  Benzinga but that other providers do carry — these would re-appear if Benzinga were cancelled.",
        "",
        "## Decision criteria (suggested)",
        "",
        "- If `Headlines unique to WITH` averages < ~2 across the sample, Benzinga is largely redundant.",
        "- If it averages ≥ 5 and includes substantive (not duplicate-paraphrased) headlines, ",
        "  Benzinga is pulling its weight.",
        "- Eyeball at least 2 `<TICKER>_with.txt` vs `<TICKER>_without.txt` pairs for qualitative ",
        "  judgement — counts alone miss content depth (Benzinga `CONTENT:` blocks vs others' `SUMMARY:`).",
        "",
    ]
    (out / "summary.md").write_text("\n".join(lines))
```

- [ ] **Step 2: Call it from main()**

At the very end of `main()`, before the final print, add:

```python
    write_summary(out, all_metrics)
```

- [ ] **Step 3: Run and inspect**

Run: `python scripts/analysis/ab_test_benzinga_news.py --tickers AAPL,NVDA`
Expected: `summary.md` exists with a populated table. Open it.

- [ ] **Step 4: Commit**

```bash
git add scripts/analysis/ab_test_benzinga_news.py
git commit -m "scripts(ab-test): markdown rollup summary"
```

---

## Task 7: Run on the real sample (5–10 tickers)

This is the actual experiment. No code changes — just execution and human review.

- [ ] **Step 1: Run on a real sample of 8 from the DB**

Run: `python scripts/analysis/ab_test_benzinga_news.py --from-db 8`
Expected: 8 tickers processed; output dir printed at end.

- [ ] **Step 2: Open the report**

```bash
ls audit_reports/benzinga_ab_test/
# pick the latest timestamp dir
open audit_reports/benzinga_ab_test/<ts>/summary.md  # or cat it
```

- [ ] **Step 3: Eyeball at least 2 ticker pairs**

For 2 of the tickers in the sample, open both `<TICKER>_with.txt` and `<TICKER>_without.txt` and read them side by side. Look for:
- Headlines present only in `with` — are they substantive or duplicates of other-source items with slightly different wording?
- `CONTENT:` blocks under `Benzinga/Massive` — do they carry analyst insight beyond what `SUMMARY:` lines give from other providers?
- `Market News (Benzinga)` block — is the SPY/DIA/QQQ macro context appearing nowhere else?

- [ ] **Step 4: Write a 4–6 line conclusion under the existing summary.md**

Append to `audit_reports/benzinga_ab_test/<ts>/summary.md`:

```
## Conclusion (human review, YYYY-MM-DD)

- Average unique headlines lost without Benzinga: <N>
- Qualitative read on uniqueness/depth: <one paragraph>
- Recommendation: <keep | cancel | re-test in M months with larger sample>
```

- [ ] **Step 5: Commit the summary as evidence**

`audit_reports/` is gitignored, so the report itself doesn't get committed. Instead, copy the conclusion section back into this plan's directory if you want it under version control:

```bash
cp audit_reports/benzinga_ab_test/<ts>/summary.md \
   docs/proposals/2026-04-26-benzinga-news-ab-test-RESULT.md
git add docs/proposals/2026-04-26-benzinga-news-ab-test-RESULT.md
git commit -m "docs: benzinga A/B test results"
```

(Skip this commit if the user just wants the answer in chat without a permanent artefact.)

---

## Out-of-scope (deliberately not in this plan)

- **Running the actual news agent on both prompts.** Worth doing as a follow-up if the prompt diff is ambiguous, but doubles Gemini cost and adds non-determinism. Add only if Task 7 step 3 leaves the call genuinely unclear.
- **Removing Benzinga from production.** User explicitly said "for now nothing to change."
- **Refactoring `_create_news_agent_prompt` to share the formatter.** YAGNI — this script is a one-shot.

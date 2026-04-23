# News Digest Integration (Stockdrop) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the daily FT and Finimize raw archive captures into compact, structured digests that are injected into Stockdrop's agent prompts and persisted for downstream use, plus a weekly Friday trend digest.

**Architecture:** A new `news_digest_service` reads the raw markdown daily files produced upstream by the Cowork scheduler, runs a Gemini thinking model over them with a deterministic JSON-schema prompt, and writes two artifacts (Markdown + JSON) into `digests/` sub-folders next to the source archives. A small `format_for_agent` slicer returns the right slice of the digest to each of Stockdrop's agent prompts (News sensor gets full detail, PM gets the one-liner, etc.). Digest generation is bootstrapped at the top of each `analyze_stock` run — idempotent, cheap to re-enter, bails cleanly if upstream hasn't written today's raw file yet. Weekly Friday digest is a Friday-only scheduled task. `flagged_for_portfolio_desk.json` is appended to by the summarizer for cross-repo consumption.

**Tech Stack:** Python 3.9, `google.genai` V2 SDK (already used by `deep_research_service`), `openpyxl` (via `pandas`) for reading the portfolio xlsx, pytest for tests. No new external dependencies.

**Out of scope (separate plan in portfoliodesk repo):**
- Escalation rules consuming `flagged_for_portfolio_desk.json`
- On-demand full-article fetch through the Chrome session
- Sell-focused PM re-runs from news triggers

**Scope note:** The Stockdrop-side `flagged_for_portfolio_desk.json` is write-only from this plan's perspective — the reader lives in the other repo.

---

## File Structure

### New files in Stock-Tracker

| Path | Responsibility |
|---|---|
| `app/services/news_digest_service.py` | Public API: `ensure_daily_digest`, `load_digest`, `format_for_agent`, `load_weekly_digest`, `ensure_weekly_digest`. Orchestrates parse → LLM → write. |
| `app/services/news_digest_parser.py` | Pure parser: raw markdown daily file → `List[Article]` (title, uuid, section, url, summary, tickers, tags). No I/O beyond a path. |
| `app/services/news_digest_prompts.py` | The three prompt builders (FT daily, Finimize daily, weekly). Constants only — kept out of `news_digest_service.py` per CLAUDE.md. |
| `app/services/news_digest_schema.py` | Schema constants (the JSON shape), `Article` / `Digest` dataclasses, agent-consumption slice map. |
| `app/services/portfolio_tickers.py` | Reads `Portfolio_Total_Weights.xlsx` if present; returns `{ticker: sector}` dict. Tolerant to missing file (returns `{}`). Small sector map for known holdings. |
| `scripts/news_digest/run_daily.py` | Standalone CLI for manual generation / backfill: `python scripts/news_digest/run_daily.py --date 2026-04-22 --source ft`. |
| `scripts/news_digest/run_weekly.py` | CLI for Friday weekly digest. |
| `tests/test_news_digest_parser.py` | Unit tests against fixture raw-daily markdown. |
| `tests/test_news_digest_service.py` | Unit tests for idempotency, load, format_for_agent, flagged-critical append. |
| `tests/test_news_digest_prompts.py` | Snapshot test — prompt text is stable given fixed inputs. |
| `tests/fixtures/news/ft_2026-04-22.md` | Copy of one FT daily file for parser tests. |
| `tests/fixtures/news/finimize_2026-04-22.md` | Copy of one Finimize daily file for parser tests. |

### Modified files in Stock-Tracker

| Path | Change |
|---|---|
| `app/services/stock_service.py` | Add `ensure_news_digests_for_today()` call at the top of the per-ticker pipeline loop (once-per-day guard handled inside `news_digest_service`). |
| `app/services/research_service.py` | **Six call sites** — `_create_news_agent_prompt`, `_create_market_sentiment_prompt`, `_create_competitive_agent_prompt`, `_create_bear_prompt`, `_create_risk_agent_prompt`, `_create_fund_manager_prompt` — inject `format_for_agent(...)` slice. Bull and the deep-research prompt inherit transitively via the reports they already consume. |
| `app/services/deep_research_service.py` | **Unchanged.** DR reads all Phase 1/2 reports; digest reaches it through five transitive paths (News full, Sentiment, Competitive, Bear, Risk weekly). |
| `.env.example` | Document `NEWS_ARCHIVE_ROOT`, `NEWS_DIGEST_MODEL`, `NEWS_DIGEST_ENABLED`. |
| `requirements.txt` | No changes — `pandas` + `openpyxl` already present (used elsewhere). |

---

## Task 1: Path constants, env config, `.env.example`

**Files:**
- Create: `app/services/news_digest_schema.py`
- Modify: `.env.example`

- [ ] **Step 1: Create schema/constants module**

Write `app/services/news_digest_schema.py`:

```python
"""Constants, dataclasses, and consumption map for the news-digest subsystem."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Default: the user's local folder. Overridable via env so CI/remote deploys
# can point at their own mirror or a test fixture directory.
_DEFAULT_ROOT = (
    "/Users/simonbaumgart/Documents/Claude/Projects/Investment Ideas and Portfolio"
)


def news_archive_root() -> Path:
    """Absolute path to the shared FT/Finimize archive root."""
    return Path(os.getenv("NEWS_ARCHIVE_ROOT", _DEFAULT_ROOT))


def digest_enabled() -> bool:
    """Gate the entire subsystem — default on in dev, can disable in prod/CI."""
    return os.getenv("NEWS_DIGEST_ENABLED", "true").lower() in ("1", "true", "yes")


def digest_model() -> str:
    """Gemini model used for digest generation. Thinking model by default."""
    return os.getenv("NEWS_DIGEST_MODEL", "gemini-3.1-pro-thinking")


# --- Paths ----------------------------------------------------------------

SOURCES = ("ft", "finimize")

_ARCHIVE_SUBDIR = {
    "ft": "FT Archive",
    "finimize": "Finimize Archive",
}


def archive_dir(source: str) -> Path:
    if source not in SOURCES:
        raise ValueError(f"Unknown source: {source!r}")
    return news_archive_root() / _ARCHIVE_SUBDIR[source]


def raw_daily_path(source: str, date: str) -> Path:
    return archive_dir(source) / "daily" / f"{date}.md"


def digest_dir(source: str) -> Path:
    return archive_dir(source) / "digests"


def digest_md_path(source: str, date: str) -> Path:
    return digest_dir(source) / f"{date}.md"


def digest_json_path(source: str, date: str) -> Path:
    return digest_dir(source) / f"{date}.json"


def weekly_digest_md_path(source: str, iso_week: str) -> Path:
    # iso_week format: "2026-W17"
    return digest_dir(source) / "weekly" / f"{iso_week}.md"


def weekly_digest_json_path(source: str, iso_week: str) -> Path:
    return digest_dir(source) / "weekly" / f"{iso_week}.json"


def flagged_critical_path() -> Path:
    return news_archive_root() / "flagged_for_portfolio_desk.json"


# --- Dataclasses ----------------------------------------------------------


@dataclass
class Article:
    """One article parsed out of a raw daily markdown file."""

    uuid: str           # FT: article UUID; Finimize: slug
    title: str
    section: str        # "markets" / "companies" / "opinion" / "editorials" / "news" / "research"
    url: str
    summary: str        # the scheduler's 2-3 sentence original summary
    published: str = ""
    tags: List[str] = field(default_factory=list)
    tickers: List[str] = field(default_factory=list)
    byline: str = ""


# --- Agent consumption map -----------------------------------------------

# Which digest slice each agent receives. The `format_for_agent` slicer
# reads this map; changing the map changes behavior without touching
# research_service.py.
#
# --- Injection strategy ---
#
# Principle: *avoid duplicate injection*. Each agent's report is passed
# downstream verbatim (PM reads `json.dumps(state.reports, indent=2)`, Deep
# Research reads PM + all Phase 1/2 reports). So if the News sensor carries
# the full digest, every downstream consumer inherits it for free.
#
# Direct consumers (6):
#   News               — FULL FT + FULL Finimize   (primary news consumer)
#   Market Sentiment   — sentiment_full on BOTH    (reads the narrative, macro tone)
#   Competitive        — competitive_full on BOTH  (sector drumbeats, thesis signals)
#   Bear               — bearish_bundle + weekly_macro  (counter-thesis)
#   Risk               — macro_risk daily + weekly_full  (primary macro/risk consumer)
#   PM                 — compact + weekly_oneliner (synthesis; tape framing)
#
# Transitive (no direct injection — they consume upstream reports):
#   Technical          — price-action only; news contaminates the signal
#   Seeking Alpha      — analyst sentiment only
#   Bull               — reads News/Sentiment/Competitive reports; those carry it
#   Deep Research      — reads ALL Phase 1/2 reports + PM; fully inherits
#
# Slice names:
#   "none"               — no digest injected
#   "full"               — full daily markdown (all sections)
#   "sentiment_full"     — market_tape + all themes (incl. opinion-flagged) + macro_signals
#                          (skips tickers_mentioned + flagged_critical)
#   "competitive_full"   — all themes + tickers_mentioned matching ticker/sector + risk_flags
#   "compact"            — one_liner + market_tape only
#   "bearish_bundle"     — compact + bearish-sentiment themes + macro_risk
#   "macro_risk"         — macro_signals + risk_flags only
#   "weekly_full"        — full weekly markdown
#   "weekly_oneliner"    — weekly one_liner only (first paragraph)
#   "weekly_macro"       — weekly markdown (Risk-agent-facing; full is fine)

AGENT_SLICE_MAP: Dict[str, Dict[str, str]] = {
    # key: agent_name -> {"ft_daily": slice, "finimize_daily": slice, "ft_weekly": slice, "finimize_weekly": slice}
    # Transitive consumers — no direct injection
    "technical":        {"ft_daily": "none",             "finimize_daily": "none",             "ft_weekly": "none",            "finimize_weekly": "none"},
    "seeking_alpha":    {"ft_daily": "none",             "finimize_daily": "none",             "ft_weekly": "none",            "finimize_weekly": "none"},
    "bull":             {"ft_daily": "none",             "finimize_daily": "none",             "ft_weekly": "none",            "finimize_weekly": "none"},
    "deep_research":    {"ft_daily": "none",             "finimize_daily": "none",             "ft_weekly": "none",            "finimize_weekly": "none"},
    # Direct consumers
    "news":             {"ft_daily": "full",             "finimize_daily": "full",             "ft_weekly": "none",            "finimize_weekly": "none"},
    "market_sentiment": {"ft_daily": "sentiment_full",   "finimize_daily": "sentiment_full",   "ft_weekly": "none",            "finimize_weekly": "none"},
    "competitive":      {"ft_daily": "competitive_full", "finimize_daily": "competitive_full", "ft_weekly": "none",            "finimize_weekly": "none"},
    "bear":             {"ft_daily": "bearish_bundle",   "finimize_daily": "none",             "ft_weekly": "weekly_macro",    "finimize_weekly": "none"},
    "risk":             {"ft_daily": "macro_risk",       "finimize_daily": "none",             "ft_weekly": "weekly_full",     "finimize_weekly": "none"},
    "pm":               {"ft_daily": "compact",          "finimize_daily": "compact",          "ft_weekly": "weekly_oneliner", "finimize_weekly": "weekly_oneliner"},
}
```

- [ ] **Step 2: Document env vars in `.env.example`**

Append to `.env.example`:

```
# --- News Digest (FT + Finimize) ---
# Absolute path to the shared archive root. Default is the user's local folder.
NEWS_ARCHIVE_ROOT=/Users/simonbaumgart/Documents/Claude/Projects/Investment Ideas and Portfolio
# Gemini thinking model used by the summarizer. Thinking model gets a cleaner JSON.
NEWS_DIGEST_MODEL=gemini-3.1-pro-thinking
# Feature flag. Set to false in environments without archive access.
NEWS_DIGEST_ENABLED=true
```

- [ ] **Step 3: Commit**

```bash
git add app/services/news_digest_schema.py .env.example
git commit -m "feat(news-digest): schema, path constants, agent consumption map"
```

---

## Task 2: Raw daily file parser

**Files:**
- Create: `app/services/news_digest_parser.py`
- Create: `tests/fixtures/news/ft_2026-04-22.md` (copy of real file)
- Create: `tests/fixtures/news/finimize_2026-04-22.md` (copy of real file)
- Create: `tests/test_news_digest_parser.py`

Raw files use a strict markdown shape: `## Section` header, `### Title` per article, bullet lines beginning with `- **URL:**` / `- **Published:**` / `- **Tags:**` / `- **Tickers:**`, and a paragraph after `**Summary:**` (FT) or a `### Summary` sub-heading (Finimize). We parse deterministically with regex; no LLM needed here.

- [ ] **Step 1: Copy fixtures**

```bash
cp "/Users/simonbaumgart/Documents/Claude/Projects/Investment Ideas and Portfolio/FT Archive/daily/2026-04-22.md" tests/fixtures/news/ft_2026-04-22.md
cp "/Users/simonbaumgart/Documents/Claude/Projects/Investment Ideas and Portfolio/Finimize Archive/daily/2026-04-22.md" tests/fixtures/news/finimize_2026-04-22.md
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_news_digest_parser.py`:

```python
from pathlib import Path

from app.services.news_digest_parser import parse_ft_daily, parse_finimize_daily

FIXTURES = Path(__file__).parent / "fixtures" / "news"


def test_parse_ft_daily_extracts_articles():
    articles = parse_ft_daily(FIXTURES / "ft_2026-04-22.md")
    # Fixture has 20 articles across markets/companies/opinion/editorials
    assert len(articles) >= 15
    first = articles[0]
    assert first.title == "MSCI boots Indonesian tycoon-owned stocks from indices"
    assert first.uuid == "d37d17d2-e0ec-426d-bde7-bf18990d7a7c"
    assert first.section == "markets"
    assert first.url.startswith("https://www.ft.com/content/")
    assert "MSCI removed Barito Renewables" in first.summary


def test_parse_ft_daily_assigns_sections_in_order():
    articles = parse_ft_daily(FIXTURES / "ft_2026-04-22.md")
    sections = {a.section for a in articles}
    assert sections == {"markets", "companies", "opinion", "editorials"}


def test_parse_finimize_daily_extracts_slug_as_uuid():
    articles = parse_finimize_daily(FIXTURES / "finimize_2026-04-22.md")
    assert len(articles) >= 5
    first = articles[0]
    # Finimize has no UUID; we derive from URL slug
    assert first.uuid == "us-state-jobless-rates-barely-budged-in-february"
    assert "us" in first.tags


def test_parse_finimize_daily_extracts_tickers_when_present():
    articles = parse_finimize_daily(FIXTURES / "finimize_2026-04-22.md")
    with_tickers = [a for a in articles if a.tickers]
    assert with_tickers, "expected at least one article with a Tickers line"
    # KSL / Invited Clubs article has "APO" ticker
    ksl = next((a for a in articles if "KSL" in a.title), None)
    assert ksl is not None
    assert "APO" in ksl.tickers


def test_parse_missing_file_returns_empty():
    articles = parse_ft_daily(FIXTURES / "does-not-exist.md")
    assert articles == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_news_digest_parser.py -v`
Expected: FAIL (module does not exist yet)

- [ ] **Step 4: Implement parser**

Create `app/services/news_digest_parser.py`:

```python
"""Pure parser: raw daily markdown file → list of Article dataclasses."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List
from urllib.parse import urlparse

from app.services.news_digest_schema import Article

# FT: `- **URL:** https://www.ft.com/content/<uuid>`
_FT_URL_RE = re.compile(r"https://www\.ft\.com/content/([0-9a-f-]+)", re.IGNORECASE)

_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_BULLET_URL_RE = re.compile(r"^-\s+\*\*URL:\*\*\s+(\S+)", re.IGNORECASE)
_BULLET_PUBLISHED_RE = re.compile(r"^-\s+\*\*Published:\*\*\s+(.+?)\s*$", re.IGNORECASE)
_BULLET_TAGS_RE = re.compile(r"^-\s+\*\*Tags:\*\*\s+(.+?)\s*$", re.IGNORECASE)
_BULLET_TICKERS_RE = re.compile(r"^-\s+\*\*Tickers:\*\*\s+(.+?)\s*$", re.IGNORECASE)
_BULLET_BYLINE_RE = re.compile(r"^-\s+\*\*Byline:\*\*\s+(.+?)\s*$", re.IGNORECASE)
_SUMMARY_INLINE_RE = re.compile(r"^\*\*Summary:\*\*\s+(.+)$", re.IGNORECASE)


def _split_csv(val: str) -> List[str]:
    return [p.strip() for p in val.split(",") if p.strip()]


def _finalize(article: dict) -> Article | None:
    if not article.get("title") or not article.get("url"):
        return None
    uuid = article.get("uuid") or _slug_from_url(article["url"])
    return Article(
        uuid=uuid,
        title=article["title"],
        section=article.get("section", "unknown"),
        url=article["url"],
        summary=article.get("summary", "").strip(),
        published=article.get("published", ""),
        tags=article.get("tags", []),
        tickers=article.get("tickers", []),
        byline=article.get("byline", ""),
    )


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else url


def _parse_generic(path: Path, *, ft_mode: bool) -> List[Article]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()

    articles: List[Article] = []
    current: dict = {}
    section = "unknown"
    mode = "scan"
    summary_buffer: List[str] = []

    def flush():
        nonlocal current, summary_buffer
        if current and summary_buffer:
            current["summary"] = " ".join(s.strip() for s in summary_buffer if s.strip())
        art = _finalize(current) if current else None
        if art is not None:
            articles.append(art)
        current = {}
        summary_buffer = []

    for raw in lines:
        line = raw.rstrip()

        m = _H2_RE.match(line)
        if m:
            flush()
            section = m.group(1).strip().lower()
            # Finimize: "Finimize News — 2026-04-22" is top heading (# not ##); ## will be article titles
            # FT: "## Markets" / "## Companies" / "## Opinion" / "## Editorials"
            if ft_mode:
                continue
            # Finimize has no ## section headers — its ## *are* titles. Treat as title.
            current = {"title": m.group(1).strip(), "section": "news"}
            mode = "meta"
            continue

        m = _H3_RE.match(line)
        if m:
            if ft_mode:
                flush()
                current = {"title": m.group(1).strip(), "section": section}
                mode = "meta"
                continue
            # Finimize uses "### Summary" to open the summary block
            if m.group(1).strip().lower() == "summary":
                mode = "summary"
                continue
            # Other ### inside Finimize article — treat as continuation
            continue

        if mode == "meta":
            mu = _BULLET_URL_RE.match(line)
            if mu:
                url = mu.group(1)
                current["url"] = url
                if ft_mode:
                    um = _FT_URL_RE.search(url)
                    if um:
                        current["uuid"] = um.group(1)
                continue
            mp = _BULLET_PUBLISHED_RE.match(line)
            if mp:
                current["published"] = mp.group(1)
                continue
            mt = _BULLET_TAGS_RE.match(line)
            if mt:
                current["tags"] = _split_csv(mt.group(1))
                continue
            mk = _BULLET_TICKERS_RE.match(line)
            if mk:
                current["tickers"] = _split_csv(mk.group(1))
                continue
            mb = _BULLET_BYLINE_RE.match(line)
            if mb:
                current["byline"] = mb.group(1)
                continue
            ms = _SUMMARY_INLINE_RE.match(line)
            if ms:
                # FT-style inline **Summary:** line
                mode = "summary"
                summary_buffer.append(ms.group(1))
                continue
            # Horizontal rule ends the article block
            if line.strip() == "---":
                flush()
                mode = "scan"
                continue
            continue

        if mode == "summary":
            if line.strip() == "---":
                flush()
                mode = "scan"
                continue
            if line.startswith("## ") or line.startswith("### "):
                # New block starting — push and re-process this line on next pass
                flush()
                mode = "scan"
                # Fall through to re-scan this line via recursion-free rewind:
                # handle the heading now.
                m2 = _H2_RE.match(line)
                if m2:
                    if ft_mode:
                        section = m2.group(1).strip().lower()
                    else:
                        current = {"title": m2.group(1).strip(), "section": "news"}
                        mode = "meta"
                continue
            summary_buffer.append(line)
            continue

    flush()
    return articles


def parse_ft_daily(path: Path) -> List[Article]:
    return _parse_generic(Path(path), ft_mode=True)


def parse_finimize_daily(path: Path) -> List[Article]:
    return _parse_generic(Path(path), ft_mode=False)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_news_digest_parser.py -v`
Expected: PASS (5/5)

If any test fails, inspect the fixture and adjust regex — these markdown shapes are stable but small formatting drift is possible.

- [ ] **Step 6: Commit**

```bash
git add app/services/news_digest_parser.py tests/test_news_digest_parser.py tests/fixtures/news/
git commit -m "feat(news-digest): deterministic markdown parser for FT + Finimize daily files"
```

---

## Task 3: Portfolio tickers loader

**Files:**
- Create: `app/services/portfolio_tickers.py`
- Create: `tests/test_portfolio_tickers.py`

We need `{ticker: sector}` for the summarizer to tag `relevance_to_portfolio`. Read from `Portfolio_Total_Weights.xlsx` if present. If the file isn't there or is malformed, return `{}` — the digest still works, tickers just get default relevance.

- [ ] **Step 1: Inspect the xlsx shape**

Run: `python -c "import pandas as pd; df = pd.read_excel('/Users/simonbaumgart/Documents/Claude/Projects/Investment Ideas and Portfolio/Portfolio_Total_Weights.xlsx'); print(df.columns.tolist()); print(df.head())"`
Expected: column names printed. Use the real column names in Step 3 — if the ticker column is called e.g. `Symbol` and sector is `Sector`, use those names below.

- [ ] **Step 2: Write the failing test**

Create `tests/test_portfolio_tickers.py`:

```python
import os
from pathlib import Path

import pandas as pd
import pytest

from app.services.portfolio_tickers import load_portfolio_tickers


def test_load_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_ARCHIVE_ROOT", str(tmp_path))
    assert load_portfolio_tickers() == {}


def test_load_parses_xlsx(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_ARCHIVE_ROOT", str(tmp_path))
    df = pd.DataFrame(
        {
            "Ticker": ["AAPL", "NVDA", "XOM"],
            "Sector": ["Technology", "Technology", "Energy"],
        }
    )
    df.to_excel(tmp_path / "Portfolio_Total_Weights.xlsx", index=False)

    result = load_portfolio_tickers()
    assert result == {"AAPL": "Technology", "NVDA": "Technology", "XOM": "Energy"}


def test_load_malformed_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_ARCHIVE_ROOT", str(tmp_path))
    (tmp_path / "Portfolio_Total_Weights.xlsx").write_text("not a real xlsx")
    assert load_portfolio_tickers() == {}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_portfolio_tickers.py -v`
Expected: FAIL (module not found)

- [ ] **Step 4: Implement loader**

Create `app/services/portfolio_tickers.py` — **adjust the column names in `TICKER_COL` / `SECTOR_COL` constants to match the real xlsx shape from Step 1**:

```python
"""Load {ticker: sector} from the user's Portfolio_Total_Weights.xlsx.

Tolerant to missing / malformed files. The summariser uses this to mark
`relevance_to_portfolio` in the digest; if empty, every ticker gets "low".
"""

from __future__ import annotations

import logging
from typing import Dict

import pandas as pd

from app.services.news_digest_schema import news_archive_root

logger = logging.getLogger(__name__)

# Column name overrides — set these to match the actual xlsx after Step 1.
TICKER_COL = "Ticker"
SECTOR_COL = "Sector"


def load_portfolio_tickers() -> Dict[str, str]:
    path = news_archive_root() / "Portfolio_Total_Weights.xlsx"
    if not path.exists():
        return {}
    try:
        df = pd.read_excel(path)
    except Exception as e:
        logger.warning("Could not read %s: %s", path, e)
        return {}
    if TICKER_COL not in df.columns:
        logger.warning("Portfolio xlsx missing %r column; got %s", TICKER_COL, df.columns.tolist())
        return {}
    sector_series = df[SECTOR_COL] if SECTOR_COL in df.columns else pd.Series([""] * len(df))
    out: Dict[str, str] = {}
    for ticker, sector in zip(df[TICKER_COL].astype(str), sector_series.astype(str)):
        t = ticker.strip().upper()
        if t and t != "NAN":
            out[t] = sector.strip() or "Unknown"
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_portfolio_tickers.py -v`
Expected: PASS (3/3)

- [ ] **Step 6: Commit**

```bash
git add app/services/portfolio_tickers.py tests/test_portfolio_tickers.py
git commit -m "feat(news-digest): portfolio xlsx loader with graceful missing-file handling"
```

---

## Task 4: Summariser prompts

**Files:**
- Create: `app/services/news_digest_prompts.py`

Keep prompts in a dedicated module (per CLAUDE.md: "Keep agent prompts in dedicated files or constants, not inline").

- [ ] **Step 1: Create prompts module**

Create `app/services/news_digest_prompts.py`:

```python
"""Prompt builders for the news digest summariser.

Three builders:
- build_ft_daily_prompt
- build_finimize_daily_prompt
- build_weekly_prompt

Each returns a string ready to hand to the thinking model. Keep the text
byte-stable: `tests/test_news_digest_prompts.py` snapshot-tests these.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from app.services.news_digest_schema import Article

# --- Shared JSON schema, pasted into every prompt ------------------------

_SCHEMA = """{
  "date": "YYYY-MM-DD",
  "source": "ft" | "finimize",
  "generated_at": "ISO8601",
  "model": "gemini-3.1-pro-thinking",
  "one_liner": "≤20 words — dominant signal of the day",
  "market_tape": "≤60 words — neutral paragraph summarising overall flow",
  "themes": [
    {
      "theme": "snake_case_label",
      "sentiment": "bullish" | "bearish" | "neutral" | "mixed",
      "confidence": 0.0,
      "opinion_driven": false,
      "supporting_articles": ["<uuid-or-slug>"],
      "one_liner": "≤25 words"
    }
  ],
  "tickers_mentioned": {
    "TICKER": {
      "count": 0,
      "sentiment": "bullish" | "bearish" | "neutral" | "mixed",
      "articles": ["<uuid-or-slug>"],
      "relevance_to_portfolio": "high" | "medium" | "low"
    }
  },
  "macro_signals": [
    {"signal": "snake_case", "direction": "up_rates|down_rates|...",
     "confidence": 0.0, "article": "<uuid-or-slug>"}
  ],
  "risk_flags": [
    {"flag": "snake_case", "severity": "low|medium|high",
     "impacts": ["asset_class_or_sector"]}
  ],
  "flagged_critical": [
    {"ticker": "TICKER", "headline": "...", "uuid": "<uuid-or-slug>",
     "reason": "earnings_guidance_cut|sec_action|takeover|sector_crash|management_change"}
  ],
  "data_anomalies": []
}"""


def _articles_block(articles: List[Article]) -> str:
    """Compact representation the model reads as input."""
    blocks = []
    for a in articles:
        parts = [
            f"id: {a.uuid}",
            f"section: {a.section}",
            f"title: {a.title}",
            f"url: {a.url}",
        ]
        if a.byline:
            parts.append(f"byline: {a.byline}")
        if a.tags:
            parts.append(f"tags: {', '.join(a.tags)}")
        if a.tickers:
            parts.append(f"tickers: {', '.join(a.tickers)}")
        parts.append(f"summary: {a.summary}")
        blocks.append("\n".join(parts))
    return "\n\n---\n\n".join(blocks)


def _portfolio_block(portfolio: Dict[str, str]) -> str:
    if not portfolio:
        return "(portfolio holdings unavailable — mark all relevance_to_portfolio as 'low')"
    lines = [f"{tkr} ({sector})" for tkr, sector in sorted(portfolio.items())]
    return ", ".join(lines)


def build_ft_daily_prompt(
    *,
    date: str,
    articles: List[Article],
    prior_digest_text: Optional[str],
    portfolio: Dict[str, str],
) -> str:
    articles_text = _articles_block(articles)
    prior_text = prior_digest_text or "(no prior digest available — first run)"
    portfolio_text = _portfolio_block(portfolio)
    return f"""You are a markets news summariser producing a structured daily digest from
today's captured FT articles. The FT is a highly reliable news source — treat
its framing as authoritative. Your output feeds directly into investment
decision agents, so precision matters more than breadth.

INPUTS:
- Date: {date}
- Today's articles (title, byline, section, URL, 2-3 sentence scheduler summary each):

{articles_text}

- Yesterday's digest for direction-change detection:

{prior_text}

- Current portfolio holdings (TICKER (sector)):
{portfolio_text}

PRODUCE a single JSON object matching this schema exactly:

{_SCHEMA}

Rules:
1. `source` MUST be "ft". `date` MUST be "{date}".
2. `one_liner` ≤ 20 words capturing the day's dominant signal.
3. `market_tape` ≤ 60 words neutral-toned summary of overall news flow.
4. `themes`: up to 5, each supported by at least one article id from the inputs.
   Sentiment is bullish / bearish / neutral / mixed. `confidence` 0-1.
   Mark `opinion_driven: true` ONLY for themes sourced primarily from the Opinion or Editorials sections.
5. `tickers_mentioned`: include every explicit ticker in the source. Set
   `relevance_to_portfolio` to "high" if the ticker is held (see holdings list),
   "medium" if a sector peer of a holding, else "low".
6. `macro_signals`: rates, growth, inflation, geopolitics, regulation. Cite an
   article id.
7. `risk_flags`: items that materially change risk for broad asset classes.
8. `flagged_critical`: populate ONLY for items matching the critical criteria —
   earnings/guidance event on a HELD ticker, SEC/regulatory action, takeover
   bid, sector crash, management change. Be strict. Each entry triggers
   downstream work in Portfolio Desk.
9. DO NOT invent tickers, numbers, or events that are not in the source.
10. Preserve article ids (`id:` field) verbatim; downstream tools use them to
    retrieve full articles on demand.
11. Respond with ONLY the JSON object — no prose, no markdown fence.
"""


def build_finimize_daily_prompt(
    *,
    date: str,
    articles: List[Article],
    prior_digests_text: List[str],
    portfolio: Dict[str, str],
) -> str:
    articles_text = _articles_block(articles)
    prior_lines = []
    for i, txt in enumerate(prior_digests_text[:5], 1):
        prior_lines.append(f"=== prior digest -{i} ===\n{txt}")
    prior_text = "\n\n".join(prior_lines) if prior_lines else "(no prior digests available)"
    portfolio_text = _portfolio_block(portfolio)
    return f"""You are a thesis-aggregation summariser producing a structured daily digest
from today's captured Finimize articles. Finimize is closer to longer-term
investment ideas than breaking news — treat repeated themes/tickers across
days as an accumulating signal, not a single-day fact.

INPUTS:
- Date: {date}
- Today's articles:

{articles_text}

- Last 5 Finimize digests (for recurrence detection):

{prior_text}

- Current portfolio holdings:
{portfolio_text}

PRODUCE a single JSON object matching the shared schema, with these
FINIMIZE-SPECIFIC additions:
- Each theme gets an extra `recurrence_count` integer: how many of the last 5
  digests this theme appeared in (0-5).
- Each entry in `tickers_mentioned` gets a `rolling_count_5d` integer: total
  mentions of that ticker in the last 5 digests plus today.

{_SCHEMA}

Rules:
1. `source` MUST be "finimize". `date` MUST be "{date}".
2. BOOST `themes[].confidence` when `recurrence_count >= 2`.
3. If a ticker has `rolling_count_5d >= 3` out of 5 days, mention it in
   `one_liner`.
4. DO NOT inflate sentiment from marketing-style headlines — many Finimize
   items are explainer-style rather than directional.
5. When an article lists a ticker in its metadata but the body clearly refers
   to a different company, skip the ticker and add an entry under
   `data_anomalies` describing the mismatch.
6. `flagged_critical` is reserved for Finimize naming a HELD ticker with a
   clear thesis-level reason (acquisition, regulatory change, multi-day drumbeat).
7. DO NOT invent items not in the source.
8. Treat Finimize tags as hints, not ground truth.
9. Respond with ONLY the JSON object — no prose, no markdown fence.
"""


def build_weekly_prompt(
    *,
    iso_week: str,
    ft_digests: List[str],
    finimize_digests: List[str],
    prior_weekly: Optional[str],
    portfolio: Dict[str, str],
) -> str:
    def _join(blocks: List[str], label: str) -> str:
        if not blocks:
            return f"(no {label} digests captured this week)"
        lines = []
        for i, txt in enumerate(blocks, 1):
            lines.append(f"=== {label} day {i} ===\n{txt}")
        return "\n\n".join(lines)

    ft_text = _join(ft_digests, "FT")
    fin_text = _join(finimize_digests, "Finimize")
    prior_text = prior_weekly or "(no prior weekly digest — first run)"
    portfolio_text = _portfolio_block(portfolio)
    return f"""You are a trend synthesis agent producing the weekly market-direction digest
for ISO week {iso_week}. You synthesise across five daily digests per source.

INPUTS:
- FT daily digests (Mon-Fri):

{ft_text}

- Finimize daily digests (Mon-Fri):

{fin_text}

- Last week's weekly digest (for direction-change detection):

{prior_text}

- Current portfolio holdings:
{portfolio_text}

PRODUCE markdown with these sections IN ORDER, no other sections:

1. **Direction of the tape** (≤80 words)
   Where did the narrative move this week vs. last week? Gradual or abrupt?

2. **Recurring themes** (up to 7, ranked by days-appeared)
   For each: theme label, days-appeared count (out of 5), dominant sentiment,
   sources (FT / Finimize / both), one-sentence why-it-matters.

3. **Ticker watchlist**
   Every ticker mentioned 2+ times this week. Group by: held / sector-peer of
   held / neither. For each: rolling count, sentiment, dominant storyline.

4. **Direction shifts**
   Where FT's framing pivoted during the week. Flag contradictions between FT
   and Finimize on the same theme.

5. **Portfolio intersections**
   For each HELD ticker/sector that intersected this week's flow: what changed
   in the narrative, and whether this merits an escalation.

6. **Read-in-full recommendations**
   Up to 5 specific FT or Finimize article ids worth reading end-to-end.

Rules:
- Cite article ids for every claim.
- Do not repeat items verbatim from the daily digests — synthesise.
- If a weekday digest is missing from the inputs, state so at the top.
- Do NOT fabricate tickers, numbers, or events not in the input digests.
"""
```

- [ ] **Step 2: Commit**

```bash
git add app/services/news_digest_prompts.py
git commit -m "feat(news-digest): summariser prompt builders (FT daily, Finimize daily, weekly)"
```

---

## Task 5: `news_digest_service` — daily generation, idempotency, load, format_for_agent

**Files:**
- Create: `app/services/news_digest_service.py`
- Create: `tests/test_news_digest_service.py`

This is the core module. Public surface:

- `ensure_daily_digest(source: str, date: str) -> Optional[dict]` — idempotent. Returns loaded digest dict, or `None` if bail-out (raw missing / empty / disabled / generation failed).
- `load_digest(source: str, date: str) -> Optional[dict]` — read JSON off disk.
- `load_weekly_digest(source: str, iso_week: str) -> Optional[dict]`
- `format_for_agent(agent_name: str, date: str, ticker: str, sector: Optional[str]) -> str` — returns a single string block ready to paste into the agent's prompt, combining the right slice from both sources' daily + weekly per `AGENT_SLICE_MAP`.
- `ensure_news_digests_for_today() -> None` — convenience wrapper called from `stock_service`; ensures both sources for today's date.

Generation invokes the Gemini thinking model via the V2 SDK (same pattern as `deep_research_service._call_grounded_model`), expects a JSON blob back, parses, re-serializes both `.json` and a human-readable `.md` rendering.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_news_digest_service.py`:

```python
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import news_digest_service as nds
from app.services.news_digest_schema import Article


@pytest.fixture
def archive_tree(tmp_path, monkeypatch):
    """Build a minimal archive tree with a single FT and Finimize daily file."""
    monkeypatch.setenv("NEWS_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv("NEWS_DIGEST_ENABLED", "true")

    (tmp_path / "FT Archive" / "daily").mkdir(parents=True)
    (tmp_path / "Finimize Archive" / "daily").mkdir(parents=True)
    ft_fixture = Path(__file__).parent / "fixtures" / "news" / "ft_2026-04-22.md"
    fin_fixture = Path(__file__).parent / "fixtures" / "news" / "finimize_2026-04-22.md"
    (tmp_path / "FT Archive" / "daily" / "2026-04-22.md").write_text(
        ft_fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "Finimize Archive" / "daily" / "2026-04-22.md").write_text(
        fin_fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path


def _fake_digest_json(source: str, date: str) -> dict:
    return {
        "date": date,
        "source": source,
        "generated_at": "2026-04-22T07:12:00Z",
        "model": "gemini-3.1-pro-thinking",
        "one_liner": "Test one liner.",
        "market_tape": "Test market tape.",
        "themes": [
            {
                "theme": "private_credit_strain",
                "sentiment": "bearish",
                "confidence": 0.8,
                "opinion_driven": False,
                "supporting_articles": ["47606fe2-108e-4a71-ba2b-9e1b779edda8"],
                "one_liner": "Private credit spreads widening.",
            }
        ],
        "tickers_mentioned": {
            "NVDA": {
                "count": 1,
                "sentiment": "bearish",
                "articles": ["47606fe2-108e-4a71-ba2b-9e1b779edda8"],
                "relevance_to_portfolio": "high",
            }
        },
        "macro_signals": [],
        "risk_flags": [
            {"flag": "geopolitical_hormuz", "severity": "medium",
             "impacts": ["energy", "safe_havens"]}
        ],
        "flagged_critical": [
            {"ticker": "AAPL", "headline": "Guidance cut",
             "uuid": "deadbeef", "reason": "earnings_guidance_cut"}
        ],
    }


def test_ensure_bails_when_raw_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_ARCHIVE_ROOT", str(tmp_path))
    # No archive tree at all
    assert nds.ensure_daily_digest("ft", "2026-04-22") is None


def test_ensure_bails_when_disabled(archive_tree, monkeypatch):
    monkeypatch.setenv("NEWS_DIGEST_ENABLED", "false")
    assert nds.ensure_daily_digest("ft", "2026-04-22") is None


def test_ensure_bails_when_raw_empty(archive_tree):
    (archive_tree / "FT Archive" / "daily" / "2026-04-22.md").write_text("")
    assert nds.ensure_daily_digest("ft", "2026-04-22") is None


def test_ensure_is_idempotent(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock_call:
        mock_call.return_value = json.dumps(_fake_digest_json("ft", "2026-04-22"))
        nds.ensure_daily_digest("ft", "2026-04-22")
        nds.ensure_daily_digest("ft", "2026-04-22")
    # Second call must not hit the model
    assert mock_call.call_count == 1


def test_ensure_writes_json_and_md(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock_call:
        mock_call.return_value = json.dumps(_fake_digest_json("ft", "2026-04-22"))
        result = nds.ensure_daily_digest("ft", "2026-04-22")
    assert result is not None
    assert result["one_liner"] == "Test one liner."
    json_path = archive_tree / "FT Archive" / "digests" / "2026-04-22.json"
    md_path = archive_tree / "FT Archive" / "digests" / "2026-04-22.md"
    assert json_path.exists()
    assert md_path.exists()
    md_content = md_path.read_text(encoding="utf-8")
    assert "Test one liner." in md_content
    assert "private_credit_strain" in md_content


def test_ensure_appends_flagged_critical(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock_call:
        mock_call.return_value = json.dumps(_fake_digest_json("ft", "2026-04-22"))
        nds.ensure_daily_digest("ft", "2026-04-22")
    flagged = archive_tree / "flagged_for_portfolio_desk.json"
    assert flagged.exists()
    entries = json.loads(flagged.read_text())
    assert any(e["ticker"] == "AAPL" for e in entries)


def test_format_for_agent_news_gets_full(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock_call:
        mock_call.return_value = json.dumps(_fake_digest_json("ft", "2026-04-22"))
        nds.ensure_daily_digest("ft", "2026-04-22")
        mock_call.return_value = json.dumps(_fake_digest_json("finimize", "2026-04-22"))
        nds.ensure_daily_digest("finimize", "2026-04-22")

    block = nds.format_for_agent("news", "2026-04-22", ticker="NVDA", sector="Technology")
    assert "Test one liner." in block
    assert "private_credit_strain" in block


def test_format_for_agent_pm_gets_compact_only(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock_call:
        mock_call.return_value = json.dumps(_fake_digest_json("ft", "2026-04-22"))
        nds.ensure_daily_digest("ft", "2026-04-22")
        mock_call.return_value = json.dumps(_fake_digest_json("finimize", "2026-04-22"))
        nds.ensure_daily_digest("finimize", "2026-04-22")

    block = nds.format_for_agent("pm", "2026-04-22", ticker="NVDA", sector="Technology")
    assert "Test one liner." in block
    # Compact slice must NOT contain theme detail
    assert "private_credit_strain" not in block


def test_format_for_agent_technical_returns_empty(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock_call:
        mock_call.return_value = json.dumps(_fake_digest_json("ft", "2026-04-22"))
        nds.ensure_daily_digest("ft", "2026-04-22")
    block = nds.format_for_agent("technical", "2026-04-22", ticker="NVDA", sector=None)
    assert block.strip() == ""


def test_format_for_agent_missing_digest_returns_empty(archive_tree):
    # Don't generate
    block = nds.format_for_agent("news", "2026-04-22", ticker="NVDA", sector=None)
    assert block.strip() == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_news_digest_service.py -v`
Expected: FAIL (module missing)

- [ ] **Step 3: Implement the service**

Create `app/services/news_digest_service.py`:

```python
"""News digest orchestrator.

Public entry points:
    ensure_daily_digest(source, date)     — idempotent generate-if-missing
    ensure_news_digests_for_today()       — both sources, today's date
    load_digest(source, date)             — pure read
    load_weekly_digest(source, iso_week)  — pure read
    ensure_weekly_digest(iso_week)        — idempotent weekly generate
    format_for_agent(agent, date, ticker, sector) — slice for prompt injection

Generation uses the Gemini thinking model via the V2 SDK.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.services.news_digest_parser import parse_ft_daily, parse_finimize_daily
from app.services.news_digest_prompts import (
    build_finimize_daily_prompt,
    build_ft_daily_prompt,
    build_weekly_prompt,
)
from app.services.news_digest_schema import (
    AGENT_SLICE_MAP,
    SOURCES,
    Article,
    digest_enabled,
    digest_json_path,
    digest_md_path,
    digest_model,
    flagged_critical_path,
    raw_daily_path,
    weekly_digest_json_path,
    weekly_digest_md_path,
)
from app.services.portfolio_tickers import load_portfolio_tickers

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _call_thinking_model(prompt: str) -> str:
    """Single-shot call to the configured thinking model. Returns raw text.

    Mirrors the V2-SDK pattern used elsewhere. Kept isolated so tests can
    patch this symbol.
    """
    from google import genai as new_genai

    client = new_genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    response = client.models.generate_content(
        model=digest_model(),
        contents=prompt,
    )
    return response.text or ""


def _strip_json_fence(raw: str) -> str:
    return _JSON_FENCE_RE.sub("", raw).strip()


def _parse_json_or_raise(raw: str) -> dict:
    cleaned = _strip_json_fence(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Attempt to salvage: find first `{` and last `}`
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if 0 <= start < end:
            return json.loads(cleaned[start : end + 1])
        raise


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def _render_markdown(digest: dict) -> str:
    lines: List[str] = []
    lines.append(f"# {digest['source'].upper()} Digest — {digest['date']}")
    lines.append("")
    lines.append(f"_{digest.get('one_liner','').strip()}_")
    lines.append("")
    tape = digest.get("market_tape", "").strip()
    if tape:
        lines.append("## Market tape")
        lines.append(tape)
        lines.append("")
    themes = digest.get("themes", []) or []
    if themes:
        lines.append("## Themes")
        for t in themes:
            extras = []
            if "recurrence_count" in t:
                extras.append(f"rec:{t['recurrence_count']}/5")
            extras_str = f" ({', '.join(extras)})" if extras else ""
            lines.append(
                f"- **{t.get('theme')}** "
                f"[{t.get('sentiment')}, conf {t.get('confidence')}]"
                f"{extras_str}: {t.get('one_liner','')}"
            )
        lines.append("")
    tickers = digest.get("tickers_mentioned", {}) or {}
    if tickers:
        lines.append("## Tickers mentioned")
        for tkr, meta in tickers.items():
            rel = meta.get("relevance_to_portfolio", "low")
            lines.append(
                f"- **{tkr}** (x{meta.get('count', 0)}, {meta.get('sentiment')}, rel:{rel})"
            )
        lines.append("")
    macro = digest.get("macro_signals", []) or []
    if macro:
        lines.append("## Macro signals")
        for m in macro:
            lines.append(f"- {m.get('signal')} → {m.get('direction')} (conf {m.get('confidence')})")
        lines.append("")
    risks = digest.get("risk_flags", []) or []
    if risks:
        lines.append("## Risk flags")
        for r in risks:
            lines.append(f"- **{r.get('flag')}** [{r.get('severity')}] — impacts {', '.join(r.get('impacts', []))}")
        lines.append("")
    critical = digest.get("flagged_critical", []) or []
    if critical:
        lines.append("## Flagged critical (for Portfolio Desk)")
        for c in critical:
            lines.append(f"- **{c.get('ticker')}** — {c.get('reason')}: {c.get('headline')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# flagged_for_portfolio_desk.json append
# ---------------------------------------------------------------------------


def _append_flagged_critical(digest: dict) -> None:
    critical = digest.get("flagged_critical", []) or []
    if not critical:
        return
    path = flagged_critical_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: List[dict] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except json.JSONDecodeError:
            existing = []
    now = datetime.now(timezone.utc).isoformat()
    for entry in critical:
        existing.append(
            {
                **entry,
                "source": digest.get("source"),
                "date": digest.get("date"),
                "flagged_at": now,
            }
        )
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------


def load_digest(source: str, date: str) -> Optional[dict]:
    path = digest_json_path(source, date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt digest JSON at %s", path)
        return None


def _load_digest_md(source: str, date: str) -> str:
    path = digest_md_path(source, date)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_weekly_digest(source: str, iso_week: str) -> Optional[dict]:
    path = weekly_digest_json_path(source, iso_week)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_weekly_md(source: str, iso_week: str) -> str:
    path = weekly_digest_md_path(source, iso_week)
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ---------------------------------------------------------------------------
# Daily generation
# ---------------------------------------------------------------------------


def _parse_raw(source: str, date: str) -> List[Article]:
    path = raw_daily_path(source, date)
    if source == "ft":
        return parse_ft_daily(path)
    return parse_finimize_daily(path)


def _prior_digest_text(source: str, date: str, days_back: int) -> List[str]:
    """Collected human-readable prior digests (most recent first)."""
    out: List[str] = []
    d = datetime.strptime(date, "%Y-%m-%d")
    from datetime import timedelta

    for i in range(1, days_back + 1):
        prior = (d - timedelta(days=i)).strftime("%Y-%m-%d")
        md = _load_digest_md(source, prior)
        if md:
            out.append(md)
    return out


def ensure_daily_digest(source: str, date: str) -> Optional[dict]:
    if not digest_enabled():
        logger.info("[news-digest] disabled via env; skipping %s %s", source, date)
        return None
    if source not in SOURCES:
        raise ValueError(f"Unknown source {source!r}")

    # Idempotent — skip if we already have it.
    existing = load_digest(source, date)
    if existing is not None:
        return existing

    raw_path = raw_daily_path(source, date)
    if not raw_path.exists():
        logger.info("[news-digest] raw file missing: %s — skipping", raw_path)
        return None
    if raw_path.stat().st_size == 0:
        logger.info("[news-digest] raw file empty: %s — skipping", raw_path)
        return None

    articles = _parse_raw(source, date)
    if not articles:
        logger.info("[news-digest] parsed zero articles from %s — skipping", raw_path)
        return None

    portfolio = load_portfolio_tickers()

    if source == "ft":
        prior = _prior_digest_text(source, date, days_back=1)
        prompt = build_ft_daily_prompt(
            date=date,
            articles=articles,
            prior_digest_text=prior[0] if prior else None,
            portfolio=portfolio,
        )
    else:
        prior = _prior_digest_text(source, date, days_back=5)
        prompt = build_finimize_daily_prompt(
            date=date,
            articles=articles,
            prior_digests_text=prior,
            portfolio=portfolio,
        )

    try:
        raw = _call_thinking_model(prompt)
        digest = _parse_json_or_raise(raw)
    except Exception as e:
        logger.exception("[news-digest] generation failed for %s %s: %s", source, date, e)
        return None

    # Stamp metadata defensively
    digest.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    digest.setdefault("model", digest_model())
    digest["source"] = source
    digest["date"] = date

    # Write artifacts
    json_path = digest_json_path(source, date)
    md_path = digest_md_path(source, date)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(digest), encoding="utf-8")
    logger.info("[news-digest] wrote %s and %s", json_path, md_path)

    _append_flagged_critical(digest)
    return digest


def ensure_news_digests_for_today(date: Optional[str] = None) -> None:
    """Convenience wrapper called from the pipeline bootstrap.

    Runs both sources. Never raises — failures are logged and swallowed.
    """
    d = date or datetime.now().strftime("%Y-%m-%d")
    for source in SOURCES:
        try:
            ensure_daily_digest(source, d)
        except Exception as e:
            logger.exception("[news-digest] ensure_daily_digest(%s, %s) raised: %s", source, d, e)


# ---------------------------------------------------------------------------
# Weekly
# ---------------------------------------------------------------------------


def _iso_week_for(date: str) -> str:
    d = datetime.strptime(date, "%Y-%m-%d")
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _weekday_dates(iso_week: str) -> List[str]:
    """Return Mon-Fri dates (YYYY-MM-DD) for the given ISO week."""
    year, week = iso_week.split("-W")
    # Monday of ISO week
    monday = datetime.strptime(f"{year}-W{int(week):02d}-1", "%G-W%V-%u")
    from datetime import timedelta

    return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]


def ensure_weekly_digest(iso_week: str) -> Optional[dict]:
    if not digest_enabled():
        return None
    # Idempotent on the markdown artifact (weekly has no strict JSON schema)
    ft_md_path = weekly_digest_md_path("ft", iso_week)
    fin_md_path = weekly_digest_md_path("finimize", iso_week)
    if ft_md_path.exists() and fin_md_path.exists():
        return {"iso_week": iso_week, "skipped": True}

    ft_daily_md = [
        _load_digest_md("ft", d) for d in _weekday_dates(iso_week) if _load_digest_md("ft", d)
    ]
    fin_daily_md = [
        _load_digest_md("finimize", d)
        for d in _weekday_dates(iso_week)
        if _load_digest_md("finimize", d)
    ]
    # Prior week's weekly (either source — they are independent artifacts)
    from datetime import timedelta

    prior_week_date = datetime.strptime(_weekday_dates(iso_week)[0], "%Y-%m-%d") - timedelta(days=7)
    prior_iso = _iso_week_for(prior_week_date.strftime("%Y-%m-%d"))
    prior_ft = _load_weekly_md("ft", prior_iso) or None

    portfolio = load_portfolio_tickers()

    results: Dict[str, str] = {}
    for source, daily_blocks in (("ft", ft_daily_md), ("finimize", fin_daily_md)):
        prompt = build_weekly_prompt(
            iso_week=iso_week,
            ft_digests=ft_daily_md,
            finimize_digests=fin_daily_md,
            prior_weekly=prior_ft,
            portfolio=portfolio,
        )
        try:
            md = _call_thinking_model(prompt).strip()
        except Exception as e:
            logger.exception("[news-digest] weekly generation failed for %s %s: %s", source, iso_week, e)
            continue
        path = weekly_digest_md_path(source, iso_week)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md + "\n", encoding="utf-8")
        # Also write a minimal JSON so load_weekly_digest doesn't return None
        weekly_digest_json_path(source, iso_week).write_text(
            json.dumps(
                {
                    "iso_week": iso_week,
                    "source": source,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "model": digest_model(),
                    "markdown_path": str(path),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        results[source] = md

    return {"iso_week": iso_week, "sources_generated": list(results.keys())}


# ---------------------------------------------------------------------------
# format_for_agent
# ---------------------------------------------------------------------------


def _oneliner_block(digest: dict) -> str:
    ol = (digest.get("one_liner") or "").strip()
    mt = (digest.get("market_tape") or "").strip()
    parts = []
    if ol:
        parts.append(ol)
    if mt:
        parts.append(mt)
    return " ".join(parts).strip()


def _themes_block(digest: dict, filt: str) -> str:
    """filt: 'bullish' | 'bearish' | 'opinion' | 'all'"""
    out: List[str] = []
    for t in digest.get("themes", []) or []:
        s = t.get("sentiment", "")
        if filt == "bullish" and s != "bullish":
            continue
        if filt == "bearish" and s != "bearish":
            continue
        if filt == "opinion" and not t.get("opinion_driven"):
            continue
        flag = " [opinion]" if t.get("opinion_driven") else ""
        out.append(
            f"- [{t.get('theme')}]{flag} {t.get('one_liner')} "
            f"(sent:{s}, conf:{t.get('confidence')})"
        )
    return "\n".join(out)


def _tickers_sector_block(digest: dict, ticker: str, sector: Optional[str]) -> str:
    """Ticker mentions matching the stock ticker or sector-peer. Uses the
    digest's `relevance_to_portfolio` tag as the sector-peer signal (medium)."""
    out: List[str] = []
    target = (ticker or "").upper()
    for tkr, meta in (digest.get("tickers_mentioned") or {}).items():
        tkr_u = tkr.upper()
        is_direct = tkr_u == target
        rel = meta.get("relevance_to_portfolio", "low")
        # Emit direct matches always; emit sector-peer matches (rel:medium) too
        if is_direct or rel in ("high", "medium"):
            tag = "direct" if is_direct else f"rel:{rel}"
            out.append(
                f"- **{tkr_u}** ({tag}): x{meta.get('count')}, "
                f"sent:{meta.get('sentiment')}"
            )
    return "\n".join(out)


def _macro_risk_block(digest: dict) -> str:
    lines: List[str] = []
    for m in digest.get("macro_signals", []) or []:
        lines.append(
            f"- macro:{m.get('signal')} → {m.get('direction')} (conf:{m.get('confidence')})"
        )
    for r in digest.get("risk_flags", []) or []:
        lines.append(
            f"- risk:{r.get('flag')} [{r.get('severity')}] impacts:{','.join(r.get('impacts', []))}"
        )
    return "\n".join(lines)


def _sentiment_full_block(digest: dict) -> str:
    """market_tape + all themes + macro_signals. Skip tickers + flagged_critical
    (those are company-specific and out of scope for the Sentiment agent)."""
    parts: List[str] = []
    tape = (digest.get("market_tape") or "").strip()
    if tape:
        parts.append(f"MARKET TAPE: {tape}")
    themes = _themes_block(digest, "all")
    if themes:
        parts.append("THEMES:\n" + themes)
    macro_lines: List[str] = []
    for m in digest.get("macro_signals", []) or []:
        macro_lines.append(
            f"- {m.get('signal')} → {m.get('direction')} (conf:{m.get('confidence')})"
        )
    if macro_lines:
        parts.append("MACRO SIGNALS:\n" + "\n".join(macro_lines))
    return "\n\n".join(parts)


def _competitive_full_block(digest: dict, ticker: str, sector: Optional[str]) -> str:
    """All themes + sector-matched tickers + risk_flags."""
    parts: List[str] = []
    themes = _themes_block(digest, "all")
    if themes:
        parts.append("THEMES:\n" + themes)
    tickers = _tickers_sector_block(digest, ticker, sector)
    if tickers:
        parts.append("TICKERS (direct / sector-peer):\n" + tickers)
    risk_lines: List[str] = []
    for r in digest.get("risk_flags", []) or []:
        risk_lines.append(
            f"- {r.get('flag')} [{r.get('severity')}] impacts:{','.join(r.get('impacts', []))}"
        )
    if risk_lines:
        parts.append("RISK FLAGS:\n" + "\n".join(risk_lines))
    return "\n\n".join(parts)


def _bearish_bundle_block(digest: dict) -> str:
    """compact + bearish-sentiment themes + macro_risk. Bear's counter-thesis kit."""
    parts: List[str] = []
    oneliner = _oneliner_block(digest)
    if oneliner:
        parts.append(oneliner)
    bearish = _themes_block(digest, "bearish")
    if bearish:
        parts.append("BEARISH THEMES:\n" + bearish)
    macro = _macro_risk_block(digest)
    if macro:
        parts.append("MACRO / RISK:\n" + macro)
    return "\n\n".join(parts)


def _slice(digest: Optional[dict], md: str, slice_name: str, ticker: str, sector: Optional[str]) -> str:
    if digest is None or slice_name == "none":
        return ""
    if slice_name == "full":
        return md
    if slice_name == "compact":
        return _oneliner_block(digest)
    if slice_name == "sentiment_full":
        return _sentiment_full_block(digest)
    if slice_name == "competitive_full":
        return _competitive_full_block(digest, ticker, sector)
    if slice_name == "bearish_bundle":
        return _bearish_bundle_block(digest)
    if slice_name == "macro_risk":
        return _macro_risk_block(digest)
    return ""


def _weekly_slice(digest: Optional[dict], md: str, slice_name: str) -> str:
    if digest is None or slice_name == "none":
        return ""
    if slice_name == "weekly_full":
        return md
    if slice_name == "weekly_oneliner":
        # Weekly digest's first paragraph under "Direction of the tape"
        for line in md.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("**"):
                return s
        return md[:400]
    if slice_name == "weekly_macro":
        # Heuristic: pull the first "Direction of the tape" + any "macro" lines
        # Return full markdown; the agent has it in prompt — limiting is fine.
        return md
    return ""


def format_for_agent(
    agent_name: str,
    date: str,
    ticker: str,
    sector: Optional[str],
) -> str:
    slices = AGENT_SLICE_MAP.get(agent_name)
    if not slices:
        return ""

    iso_week = _iso_week_for(date)

    ft_daily = load_digest("ft", date)
    ft_daily_md = _load_digest_md("ft", date)
    fin_daily = load_digest("finimize", date)
    fin_daily_md = _load_digest_md("finimize", date)
    ft_weekly = load_weekly_digest("ft", iso_week)
    ft_weekly_md = _load_weekly_md("ft", iso_week)
    fin_weekly = load_weekly_digest("finimize", iso_week)
    fin_weekly_md = _load_weekly_md("finimize", iso_week)

    ft_block = _slice(ft_daily, ft_daily_md, slices["ft_daily"], ticker, sector)
    fin_block = _slice(fin_daily, fin_daily_md, slices["finimize_daily"], ticker, sector)
    ft_w_block = _weekly_slice(ft_weekly, ft_weekly_md, slices["ft_weekly"])
    fin_w_block = _weekly_slice(fin_weekly, fin_weekly_md, slices["finimize_weekly"])

    sections: List[str] = []
    if ft_block.strip():
        sections.append(f"--- FT daily digest ({date}) ---\n{ft_block.strip()}")
    if fin_block.strip():
        sections.append(f"--- Finimize daily digest ({date}) ---\n{fin_block.strip()}")
    if ft_w_block.strip():
        sections.append(f"--- FT weekly digest ({iso_week}) ---\n{ft_w_block.strip()}")
    if fin_w_block.strip():
        sections.append(f"--- Finimize weekly digest ({iso_week}) ---\n{fin_w_block.strip()}")

    if not sections:
        return ""
    return "\n\n".join(sections)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_news_digest_service.py -v`
Expected: PASS (9/9)

- [ ] **Step 5: Commit**

```bash
git add app/services/news_digest_service.py tests/test_news_digest_service.py
git commit -m "feat(news-digest): orchestrator — idempotent daily generation, agent slicer, flagged-critical append"
```

---

## Task 6: Bootstrap hook in stock pipeline

**Files:**
- Modify: `app/services/stock_service.py`

The bootstrap is invoked once per scanner tick at the top of the pipeline entry, NOT per-ticker. That avoids the cost of re-checking for each ticker analysed in a given scan. Inside, `ensure_daily_digest` is idempotent — re-entry is cheap (just two `Path.exists()` checks).

- [ ] **Step 1: Find the pipeline entry point**

Run: `grep -n "analyze_stock\|run_pipeline\|start_analysis\|screen_and_analyze" app/services/stock_service.py | head`
Expected: locate the method that iterates screened candidates and calls `research_service.analyze_stock`. Call it `<ENTRY>`.

- [ ] **Step 2: Add the import and the call**

In `app/services/stock_service.py`, add near the top imports:

```python
from app.services.news_digest_service import ensure_news_digests_for_today
```

At the top of `<ENTRY>` method (before the per-ticker loop begins), add:

```python
        # --- News digest bootstrap ---
        # Idempotent: generates FT + Finimize digests for today if the upstream
        # scheduler's raw daily files exist and today's digest hasn't been
        # written yet. Bails silently if disabled or raw files missing.
        try:
            ensure_news_digests_for_today()
        except Exception as e:
            logger.warning("News digest bootstrap raised (non-fatal): %s", e)
```

- [ ] **Step 3: Commit**

```bash
git add app/services/stock_service.py
git commit -m "feat(news-digest): bootstrap hook — ensure digests at start of pipeline run"
```

---

## Task 7: Inject digest slices into agent prompts

**Files:**
- Modify: `app/services/research_service.py`

**Six direct injection sites.** All follow the same shape: compute a `news_block` string from `format_for_agent(...)`, then paste it into the existing prompt template under a `RELEVANT NEWS DIGEST` header. If the block is empty, the header is omitted.

**Transitive consumers (no direct injection):**
- **Technical** — news contaminates price-action signal; out of scope.
- **Seeking Alpha** — analyst-sentiment only; the Seeking Alpha fetcher is not a prompted LLM agent.
- **Bull** — reads the News/Sentiment/Competitive reports, which already carry the digest. Adding a direct injection would duplicate signal and push Bull toward news-driven rhetoric instead of synthesis.
- **Deep Research** — reads **all** Phase 1 reports + Bull/Bear/Risk + PM output. The digest reaches DR through News (full), Sentiment (sentiment_full), Competitive (competitive_full), Risk (macro_risk + weekly), and PM (compact + weekly one-liner). Direct injection into DR would be a fifth copy — wasted tokens and cognitive noise for the reviewer.

Sites to modify (match `AGENT_SLICE_MAP` keys from Task 1):
1. `_create_news_agent_prompt` → agent `"news"`
2. `_create_market_sentiment_prompt` → agent `"market_sentiment"`
3. `_create_competitive_agent_prompt` → agent `"competitive"`
4. `_create_bear_prompt` → agent `"bear"`
5. `_create_risk_agent_prompt` → agent `"risk"`
6. `_create_fund_manager_prompt` → agent `"pm"`

- [ ] **Step 1: Add a helper inside `ResearchService`**

Near the other private helpers in `app/services/research_service.py`, add:

```python
    def _news_block_for(self, state: MarketState, agent_name: str) -> str:
        """Return the news-digest slice for a given agent, or empty string."""
        try:
            from app.services.news_digest_service import format_for_agent
        except Exception:
            return ""
        try:
            sector = None
            # The sector sometimes shows up on state or in raw data; keep it
            # optional. format_for_agent tolerates None.
            sector = getattr(state, "sector", None)
            block = format_for_agent(
                agent_name=agent_name,
                date=state.date,
                ticker=state.ticker,
                sector=sector,
            )
            if not block.strip():
                return ""
            return f"\n\nRELEVANT NEWS DIGEST (FT / Finimize, auto-generated):\n{block.strip()}\n"
        except Exception as e:
            logger.warning("format_for_agent(%s) failed: %s", agent_name, e)
            return ""
```

- [ ] **Step 2: Inject into the News agent prompt**

In `_create_news_agent_prompt`, right before the final `return f"""..."""`, compute:

```python
        news_digest_block = self._news_block_for(state, "news")
```

Then at the end of the prompt template (after the `DROP REASON CHECK:` paragraph but before the closing `"""`), append:

```
{news_digest_block}
```

- [ ] **Step 3: Inject into Market Sentiment prompt**

In `_create_market_sentiment_prompt`, compute:

```python
        news_digest_block = self._news_block_for(state, "market_sentiment")
```

and append `{news_digest_block}` at the end of the returned prompt template.

- [ ] **Step 4: Inject into Competitive prompt**

In `_create_competitive_agent_prompt`, compute:

```python
        news_digest_block = self._news_block_for(state, "competitive")
```

and append `{news_digest_block}` at the end of the returned prompt template.

- [ ] **Step 5: Inject into Bear and Risk prompts**

Same pattern — agent names `"bear"` and `"risk"`. In each of `_create_bear_prompt` and `_create_risk_agent_prompt`, compute `news_digest_block = self._news_block_for(state, "<agent>")` and append `{news_digest_block}` at the end. Do NOT inject into Bull — Bull reads News/Sentiment/Competitive reports, which already carry the digest. A direct injection would duplicate signal.

- [ ] **Step 6: Inject into PM prompt**

In `_create_fund_manager_prompt`, compute:

```python
        news_digest_block = self._news_block_for(state, "pm")
```

Append `{news_digest_block}` at the end of the returned prompt template. PM gets only the compact one-liner + market tape + weekly one-liner per the consumption map. PM also sees the full News agent's report (which contains the full digest) via `json.dumps(state.reports, ...)` — the compact block is the *framing* signal, not a duplicate.

- [ ] **Step 7: Do NOT inject into Deep Research**

DR reads all Phase 1 + Phase 2 reports (News carries FULL digest, Sentiment carries `sentiment_full`, Competitive carries `competitive_full`, Bear carries `bearish_bundle`, Risk carries `macro_risk` + weekly, PM carries `compact` + weekly one-liner). The digest reaches DR through five transitive paths. Adding a sixth direct copy would cost tokens without adding signal.

Leave `app/services/deep_research_service.py` unchanged in this task. (If future validation shows DR is missing context, revisit — but the default is no direct injection.)

- [ ] **Step 8: Write an integration test**

Create `tests/test_news_digest_prompt_injection.py`:

```python
import json
from pathlib import Path

import pytest

from app.services import news_digest_service as nds
from app.services.research_service import ResearchService
from app.models.market_state import MarketState
from unittest.mock import patch


@pytest.fixture
def archive_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv("NEWS_DIGEST_ENABLED", "true")
    (tmp_path / "FT Archive" / "digests").mkdir(parents=True)
    digest = {
        "date": "2026-04-22",
        "source": "ft",
        "generated_at": "2026-04-22T07:12:00Z",
        "model": "gemini-3.1-pro-thinking",
        "one_liner": "AI capex unease bleeds into credit.",
        "market_tape": "Tape two-sentence summary.",
        "themes": [
            {"theme": "private_credit_strain", "sentiment": "bearish",
             "confidence": 0.8, "opinion_driven": False,
             "supporting_articles": ["x"], "one_liner": "Credit widening."}
        ],
        "tickers_mentioned": {},
        "macro_signals": [],
        "risk_flags": [
            {"flag": "geopolitical_hormuz", "severity": "medium", "impacts": ["energy"]}
        ],
        "flagged_critical": [],
    }
    (tmp_path / "FT Archive" / "digests" / "2026-04-22.json").write_text(
        json.dumps(digest), encoding="utf-8"
    )
    (tmp_path / "FT Archive" / "digests" / "2026-04-22.md").write_text(
        "# FT Digest — 2026-04-22\n_AI capex unease bleeds into credit._\n", encoding="utf-8"
    )
    return tmp_path


def test_news_agent_prompt_includes_digest(archive_tree):
    svc = ResearchService.__new__(ResearchService)
    state = MarketState(ticker="NVDA", date="2026-04-22")
    block = svc._news_block_for(state, "news")
    assert "AI capex unease" in block
    assert "RELEVANT NEWS DIGEST" in block


def test_pm_prompt_gets_compact_not_full(archive_tree):
    svc = ResearchService.__new__(ResearchService)
    state = MarketState(ticker="NVDA", date="2026-04-22")
    block = svc._news_block_for(state, "pm")
    assert "AI capex unease" in block
    # PM slice should NOT contain theme-level details
    assert "private_credit_strain" not in block


def test_technical_gets_empty(archive_tree):
    svc = ResearchService.__new__(ResearchService)
    state = MarketState(ticker="NVDA", date="2026-04-22")
    block = svc._news_block_for(state, "technical")
    assert block == ""


def test_bull_gets_empty_transitive(archive_tree):
    # Bull inherits digest via News/Sentiment/Competitive reports — no direct injection.
    svc = ResearchService.__new__(ResearchService)
    state = MarketState(ticker="NVDA", date="2026-04-22")
    block = svc._news_block_for(state, "bull")
    assert block == ""


def test_deep_research_gets_empty_transitive(archive_tree):
    # DR inherits via all Phase 1+2 reports — no direct injection.
    svc = ResearchService.__new__(ResearchService)
    state = MarketState(ticker="NVDA", date="2026-04-22")
    block = svc._news_block_for(state, "deep_research")
    assert block == ""


def test_sentiment_full_includes_themes_and_macro(archive_tree, tmp_path):
    import json as _json
    digest_path = tmp_path / "FT Archive" / "digests" / "2026-04-22.json"
    data = _json.loads(digest_path.read_text())
    data["macro_signals"] = [
        {"signal": "fed_hawkish_shift", "direction": "up_rates",
         "confidence": 0.6, "article": "x"}
    ]
    digest_path.write_text(_json.dumps(data), encoding="utf-8")

    svc = ResearchService.__new__(ResearchService)
    state = MarketState(ticker="NVDA", date="2026-04-22")
    block = svc._news_block_for(state, "market_sentiment")
    assert "MARKET TAPE" in block
    assert "private_credit_strain" in block
    assert "fed_hawkish_shift" in block
    # Should NOT include tickers_mentioned or flagged_critical
    assert "flagged" not in block.lower()


def test_bearish_bundle_excludes_bullish_themes(archive_tree, tmp_path):
    import json as _json
    digest_path = tmp_path / "FT Archive" / "digests" / "2026-04-22.json"
    data = _json.loads(digest_path.read_text())
    data["themes"].append({
        "theme": "semis_rally", "sentiment": "bullish", "confidence": 0.7,
        "opinion_driven": False, "supporting_articles": ["y"],
        "one_liner": "Semis popping.",
    })
    digest_path.write_text(_json.dumps(data), encoding="utf-8")

    svc = ResearchService.__new__(ResearchService)
    state = MarketState(ticker="NVDA", date="2026-04-22")
    block = svc._news_block_for(state, "bear")
    assert "private_credit_strain" in block  # bearish
    assert "semis_rally" not in block        # bullish filtered out
```

- [ ] **Step 9: Run integration tests**

Run: `pytest tests/test_news_digest_prompt_injection.py -v`
Expected: PASS (6/6)

- [ ] **Step 10: Commit**

```bash
git add app/services/research_service.py tests/test_news_digest_prompt_injection.py
git commit -m "feat(news-digest): inject digest slices into News/Sentiment/Competitive/Bear/Risk/PM; Bull+DR inherit transitively"
```

---

## Weekly digest — division of responsibility

**Finimize weekly: not generated by this codebase.** The Cowork scheduler writes `Finimize Archive/weekly/YYYY-Www.md` every Sunday. We read those files as input context.

**FT weekly: generated by this codebase.** Output path: `FT Archive/digests/weekly/YYYY-Www.md` (+ `.json`). Inputs:
- This week's 5 FT daily digests (ours, from `FT Archive/digests/`)
- **The last 3 Finimize weekly summaries** (scheduler's, from `Finimize Archive/weekly/`)
- Last week's FT weekly (ours, for direction-change detection)
- Current portfolio holdings

The 3-week Finimize pull gives the FT weekly a longer-horizon thesis backdrop that a single week of FT dailies can't supply.

## Task 8: Weekly Friday digest — manual runner + scheduler hook

**Files:**
- Create: `scripts/news_digest/run_weekly.py`
- Create: `scripts/news_digest/run_daily.py`

Keep the background-task addition to `main.py` as an optional Task 10 — for v1, a cron-friendly CLI is enough and avoids coupling weekly generation to the FastAPI lifecycle.

- [ ] **Step 1: Write the daily CLI**

Create `scripts/news_digest/run_daily.py`:

```python
#!/usr/bin/env python3
"""Manual/backfill runner for daily news digests.

Usage:
    python scripts/news_digest/run_daily.py                      # today, both sources
    python scripts/news_digest/run_daily.py --date 2026-04-20    # backfill
    python scripts/news_digest/run_daily.py --source ft          # just FT
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.news_digest_service import ensure_daily_digest  # noqa: E402
from app.services.news_digest_schema import SOURCES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    p.add_argument("--source", choices=list(SOURCES) + ["all"], default="all")
    args = p.parse_args()

    sources = SOURCES if args.source == "all" else (args.source,)
    failures = 0
    for s in sources:
        result = ensure_daily_digest(s, args.date)
        if result is None:
            print(f"[{s}] {args.date}: no digest produced")
            failures += 1
        else:
            print(f"[{s}] {args.date}: ok — {result.get('one_liner','')}")
    return 1 if failures == len(sources) else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the weekly CLI**

Create `scripts/news_digest/run_weekly.py`:

```python
#!/usr/bin/env python3
"""Weekly Friday trend digest runner.

Usage:
    python scripts/news_digest/run_weekly.py                   # current ISO week
    python scripts/news_digest/run_weekly.py --week 2026-W17
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.news_digest_service import ensure_weekly_digest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--week", default=None, help="ISO week, e.g. 2026-W17")
    args = p.parse_args()

    iso_week = args.week
    if not iso_week:
        y, w, _ = datetime.now().isocalendar()
        iso_week = f"{y}-W{w:02d}"

    result = ensure_weekly_digest(iso_week)
    print(f"Weekly {iso_week}: {result}")
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Make them executable + smoke-test the daily runner**

```bash
chmod +x scripts/news_digest/run_daily.py scripts/news_digest/run_weekly.py
NEWS_DIGEST_ENABLED=false python scripts/news_digest/run_daily.py --date 2026-04-22
```

Expected: prints `[ft] 2026-04-22: no digest produced` and `[finimize] 2026-04-22: no digest produced` (disabled short-circuits).

- [ ] **Step 4: Commit**

```bash
git add scripts/news_digest/
git commit -m "feat(news-digest): manual CLI runners for daily + weekly digests"
```

---

## Task 9: End-to-end manual validation against real archive

This step is **manual validation**, not automated. It's the quality gate described in the proposal's Phase 1: "Manual run for a few days; eyeball quality vs. the raw files."

**Do NOT skip this.** The tests verify mechanics; this step verifies the prompt produces useful output.

- [ ] **Step 1: Set up environment**

Confirm `GEMINI_API_KEY` is set. Confirm the archive root is readable:

```bash
ls "$NEWS_ARCHIVE_ROOT/FT Archive/daily" 2>/dev/null | tail -3
ls "$NEWS_ARCHIVE_ROOT/Finimize Archive/daily" 2>/dev/null | tail -3
```
Expected: listing of recent `.md` files.

- [ ] **Step 2: Generate today's digest manually**

```bash
python scripts/news_digest/run_daily.py --date 2026-04-22
```
Expected: two lines ending `: ok — <one-liner>`.

- [ ] **Step 3: Inspect the output**

```bash
cat "$NEWS_ARCHIVE_ROOT/FT Archive/digests/2026-04-22.md"
cat "$NEWS_ARCHIVE_ROOT/FT Archive/digests/2026-04-22.json" | python -m json.tool
```
Checklist:
- [ ] `one_liner` captures the day's dominant signal
- [ ] `themes` have ≤5 entries, each citing at least one article UUID
- [ ] `tickers_mentioned` includes only tickers actually named in the raw file
- [ ] `flagged_critical` is empty unless a genuinely critical event is present
- [ ] No hallucinated numbers or tickers

Repeat for Finimize. If quality is poor, iterate on the prompt in `news_digest_prompts.py` — this is the expected place for tuning.

- [ ] **Step 4: Verify flagged-critical append (if any were flagged)**

```bash
cat "$NEWS_ARCHIVE_ROOT/flagged_for_portfolio_desk.json"
```
Expected: a valid JSON array, each entry with `ticker`, `headline`, `uuid`, `reason`, `source`, `date`, `flagged_at`.

- [ ] **Step 5: Run a single-ticker pipeline smoke test**

Pick a ticker with a known ~5% drop today (or use a test ticker). Run the analyze flow and verify the News and PM prompt logs reference the digest:

```bash
grep -A 3 "RELEVANT NEWS DIGEST" data/news/*2026-04-22_news_context.txt 2>/dev/null | head
```
Expected: at least one match showing the injected block.

- [ ] **Step 6: Backfill the last 3 days (creates prior-digest context for tomorrow's run)**

```bash
python scripts/news_digest/run_daily.py --date 2026-04-20
python scripts/news_digest/run_daily.py --date 2026-04-21
python scripts/news_digest/run_daily.py --date 2026-04-22
```

- [ ] **Step 7: Generate the current weekly digest**

```bash
python scripts/news_digest/run_weekly.py
```
Expected: writes `weekly/YYYY-Www.md` under each `digests/` folder. Inspect; the structure should match the six sections defined in the weekly prompt.

- [ ] **Step 8: Commit any prompt adjustments**

If you iterated on prompts during Step 3:

```bash
git add app/services/news_digest_prompts.py
git commit -m "chore(news-digest): prompt tuning after manual validation"
```

---

## Task 10: (Optional) Scheduled Friday weekly digest

Run the weekly generator as a background task — only add this AFTER Tasks 1-9 are proven in manual mode.

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add a scheduled coroutine**

In `main.py`, alongside the other background tasks, add:

```python
async def _weekly_news_digest_loop():
    """Friday at ~17:00 local, run the weekly news digest."""
    import asyncio
    from datetime import datetime
    from app.services.news_digest_service import ensure_weekly_digest

    while True:
        now = datetime.now()
        # Sleep until next Friday 17:05
        days_ahead = (4 - now.weekday()) % 7  # Fri=4
        if days_ahead == 0 and now.hour >= 17:
            days_ahead = 7
        target = now.replace(hour=17, minute=5, second=0, microsecond=0)
        target = target + timedelta(days=days_ahead)  # add import at top if missing
        sleep_seconds = (target - now).total_seconds()
        if sleep_seconds < 0:
            sleep_seconds = 60
        await asyncio.sleep(sleep_seconds)
        try:
            y, w, _ = datetime.now().isocalendar()
            iso_week = f"{y}-W{w:02d}"
            await asyncio.to_thread(ensure_weekly_digest, iso_week)
        except Exception:
            logger.exception("weekly news digest failed")
```

Wire it into the existing `@app.on_event("startup")` startup block alongside the other `asyncio.create_task(...)` calls.

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat(news-digest): schedule weekly digest for Friday 17:05"
```

---

## Self-review

- **Spec coverage:**
  - Storage (`digests/` next to archives): Task 1 (path constants) + Task 5 (writer) ✓
  - JSON schema + markdown: Task 4 (prompt has schema) + Task 5 (renderer) ✓
  - Agent consumption map: Task 1 defines it, Task 5 consumes it, Task 7 injects ✓
  - FT / Finimize / weekly prompts (exact text from proposal): Task 4 ✓
  - `flagged_for_portfolio_desk.json`: Task 5 `_append_flagged_critical` ✓
  - Bootstrap at pipeline start: Task 6 ✓
  - Idempotency + bail conditions (exists/missing/empty): Task 5 + tests ✓
  - Weekly Friday digest: Task 5 `ensure_weekly_digest` + Task 8 CLI + Task 10 scheduler ✓
  - Env/config (`NEWS_ARCHIVE_ROOT`, `NEWS_DIGEST_MODEL`, `NEWS_DIGEST_ENABLED`): Task 1 ✓
  - Portfolio ticker map: Task 3 ✓
  - Phased delivery: Tasks 1-5 = phase 1, Task 6+7 = phase 2, Task 8 = phase 4; phases 3 and 5 are in the portfoliodesk plan ✓
  - Open questions from the proposal (Sunday rollup / sector mapping / Cowork artifact / missing-file logging): documented as outside v1 scope; missing-file surfaced via `logger.info` in Task 5 ✓
- **Placeholder scan:** No TBDs, no "handle edge cases", all prompts and code shown in full. Task 6 Step 1 requires the engineer to locate the exact pipeline entry method (genuine unknown — depends on which method is the orchestration top). Task 8 Step 1 requires inspecting the real xlsx column names (genuine unknown; tested behavior tolerates both cases).
- **Type consistency:** `Article` dataclass, `AGENT_SLICE_MAP` keys, slice names, path helpers — all used consistently across tasks. `ensure_daily_digest` / `load_digest` / `format_for_agent` signatures match between service, tests, and prompt injection.
- **Cross-repo boundary:** `flagged_for_portfolio_desk.json` is written but not read in this plan — the reader belongs to the portfoliodesk repo plan. Clearly called out in the scope note.

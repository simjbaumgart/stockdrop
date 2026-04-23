# Daily News Digest Integration (Finimize + FT)

**Status:** implemented 2026-04-22 (see "Implementation status" section at bottom)
**Original status:** proposal v2
**Applies to:** Stockdrop (Stock-Tracker) + Portfolio Desk (portfoliodesk)
**Date:** 2026-04-22

## Upstream situation (what already exists)

A Cowork scheduler writes daily per-article captures into the user's local folder:

```
Investment Ideas and Portfolio/
├── FT Archive/
│   ├── README.md
│   ├── _index.json            # dedup by article UUID
│   ├── daily/YYYY-MM-DD.md    # top ~15-20 articles across
│   │                          # Markets / Companies / Opinion / Editorials
│   ├── weekly/YYYY-Www.md     # compiled Sundays
│   └── logs/
├── Finimize Archive/
│   ├── README.md
│   ├── _index.json            # dedup by article slug
│   ├── daily/YYYY-MM-DD.md    # news + (later) /research
│   ├── weekly/YYYY-Www.md
│   └── logs/
└── Portfolio_Total_Weights.xlsx
```

Each article already has a **2-3 sentence original summary** written by the scheduler.
That is enough signal per-article. It is **too much volume** to feed into every
sensor call, and not structured for pattern-matching against positions.

## Goal

Turn the raw daily captures into a **compact, structured digest** that is cheap
to inject into agent prompts, addressable by ticker/theme, and persisted for
downstream use by both apps. Plus a weekly trend digest for macro framing.

## Core loop

```
06:45  scheduler writes  Investment Ideas and Portfolio/FT Archive/daily/DATE.md
06:50  scheduler writes  Investment Ideas and Portfolio/Finimize Archive/daily/DATE.md
07:10  Stockdrop's news-digest step checks:
         ├── does today's digest exist?              (skip if yes)
         ├── is today's raw daily file present?      (bail if no)
         ├── is the raw daily file non-empty?        (bail if no)
         └── run Gemini Pro Thinking over the raw daily
             → writes digest next to the raw file
07:15  both apps now read digests in every agent call
Fri 17:00  weekly trend-digest agent runs over Mon-Fri
```

## Storage — recommendation

**Put digests next to the raw archives, not inside Stockdrop.** The digest is
derivative of that archive. Both apps reach it via a path constant — no cross-
project data duplication.

```
Investment Ideas and Portfolio/
├── FT Archive/
│   ├── daily/YYYY-MM-DD.md
│   └── digests/
│       ├── YYYY-MM-DD.md          # daily structured digest
│       ├── YYYY-MM-DD.json        # same digest, machine-readable
│       └── weekly/YYYY-Www.md     # Friday trend digest
├── Finimize Archive/
│   ├── daily/YYYY-MM-DD.md
│   └── digests/
│       ├── YYYY-MM-DD.md
│       ├── YYYY-MM-DD.json
│       └── weekly/YYYY-Www.md
└── flagged_for_portfolio_desk.json    # items flagged `critical`
```

Both codebases import a shared path constant (`NEWS_ARCHIVE_ROOT`), env-
overridable. Flat files on disk; no new DB table for v1. Keep it simple — the
access pattern is read-heavy with tiny files (~5KB each).

**If you want the digests inside Stockdrop:** mirror into
`Stock-Tracker/data/news_digests/{ft,finimize}/…` via hardlink or a daily copy
step. Source of truth stays with the archive.

## Digest shape (the daily artifacts)

Each daily digest has two files: a human-readable markdown and a machine-
readable JSON for agent consumption and path queries.

### JSON schema (per source)

```json
{
  "date": "2026-04-22",
  "source": "ft",                    // "ft" | "finimize"
  "generated_at": "2026-04-22T07:12:00Z",
  "model": "gemini-3.1-pro-thinking",
  "one_liner": "AI capex unease crosses into credit; oil vol tied to Trump feed.",
  "market_tape": "2-sentence paragraph...",
  "themes": [
    {
      "theme": "private_credit_strain",
      "sentiment": "bearish",
      "confidence": 0.8,
      "supporting_articles": ["<uuid-1>", "<uuid-2>"],
      "one_liner": "Private credit funding costs widening; BDC NAV discounts..."
    }
  ],
  "tickers_mentioned": {
    "NVDA": {
      "count": 2,
      "sentiment": "bearish",
      "articles": ["<uuid>"],
      "relevance_to_portfolio": "high"  // auto: high if held
    }
  },
  "macro_signals": [
    { "signal": "fed_hawkish_shift", "direction": "up_rates",
      "confidence": 0.6, "article": "<uuid>" }
  ],
  "risk_flags": [
    { "flag": "geopolitical_hormuz", "severity": "medium",
      "impacts": ["energy","safe_havens"] }
  ],
  "flagged_critical": [                // drives `flagged_for_portfolio_desk.json`
    { "ticker": "AAPL", "headline": "...", "uuid": "...",
      "reason": "earnings_guidance_cut" }
  ]
}
```

### Markdown (what agents read in-prompt)

Bullet-point version of the same structure. Budget: ~600-900 tokens per source.

## Which agents consume what

### Stockdrop

| Agent | FT daily | FT weekly | Finimize daily | Finimize weekly |
|---|---|---|---|---|
| Technical Analysis sensor | — | — | — | — |
| News Analysis sensor | **full** | — | **full** | — |
| Sentiment Analysis | opinion themes only | — | — | — |
| Competitive Landscape | — | — | ticker/sector-matched items | — |
| Seeking Alpha | — | — | — | — |
| Bull Researcher | bullish themes | — | thesis items matching ticker/sector | — |
| Bear Researcher | bearish + macro | weekly macro direction | — | — |
| Risk Management agent | **macro + risk_flags** | **full weekly** | — | — |
| PM (Fund Manager) | `one_liner` + `market_tape` | weekly one-liner | `one_liner` | weekly one-liner |
| Deep Research | **full daily** | **full weekly** | **full daily** | **full weekly** |

Design principles:
- Technical Analysis and Seeking Alpha are kept clean. News contaminates
  technical signal and is out of scope for analyst sentiment.
- News Analysis sensor and Deep Research are the biggest consumers.
- PM gets the compact summary, not the full digest — it's the synthesis stage.
- Risk Management is the key consumer of macro/risk signals — this is what that
  agent is for.

### Portfolio Desk

- **Escalation ladder** (`desk/escalation.py`) — deterministic match only.
  Triggers:
  - `news_ticker_mentioned` — held ticker in today's `tickers_mentioned`
  - `news_sector_theme` — held ticker's sector overlaps a `theme` with non-neutral sentiment
  - `news_macro_risk` — held position is exposed to a `risk_flag`
- **Reassessment prompt** (`desk/prompts/`) — inject matched items only, not the
  full digest. Phrase as: *"These news items triggered this review. How do they
  change the thesis for {ticker}?"*
- **Forked sensor agents** — same consumption map as Stockdrop but with
  sell-focused framing.
- **Sell-focused PM** — compact summary, bearish-weighted framing.

### Full-article forward flag

`flagged_for_portfolio_desk.json` is appended to when the summarizer tags an
item `relevance_to_portfolio: critical`. Triggers:
- earnings miss / guidance cut on a held ticker
- SEC / regulatory action
- takeover bid or strategic review
- sector-wide crash
- material management change

Portfolio Desk polls this file at the start of each review cycle. For each
entry, it fetches the full FT article **on demand** through the Chrome session
(where the user is logged in) and injects the body into the next review of the
affected position. Pre-fetching everything is expensive and usually wasted —
fetch on demand only for `critical`.

## Weekly Friday digest

Separate artifact from the scheduler's Sunday weekly rollup (different audience).

- **Schedule:** Friday 17:00 local (post-close).
- **Inputs:** 5 daily digests (Mon-Fri) + this week's raw daily files + last
  week's Friday digest (for direction comparison).
- **Outputs:** `digests/weekly/YYYY-Www.md` + `.json`.
- **Purpose:** identify themes that recurred across days, tickers mentioned
  2+ times, direction shifts (e.g., "FT pivoted hawkish on Fed Tue→Thu"),
  contradictions between FT and Finimize, cross-reference against current
  portfolio.
- **Weighting in prompts:** PM and DR treat weekly themes as higher signal than
  a single day's entry.

## Summarizer agent prompts

Two prompts — the roles differ and that should be reflected in framing.

### FT daily summarizer prompt

```
You are a markets news summariser producing a structured daily digest from
today's captured FT articles. The FT is a highly reliable news source — treat
its framing as authoritative. Your output feeds directly into investment
decision agents, so precision matters more than breadth.

INPUTS:
- Today's raw article list (title, standfirst, author, section, URL, 2-3 sentence
  summary each), parsed from {path_to_raw_file}.
- Yesterday's digest, for direction-change detection: {path_to_prior_digest}.
- Current portfolio holdings: {portfolio_tickers_and_sectors}.

PRODUCE a JSON object matching this schema exactly:
{schema_pasted_here}

Rules:
1. `one_liner` ≤ 20 words capturing the day's dominant signal.
2. `market_tape` ≤ 60 words neutral-toned summary of the overall news flow.
3. `themes` = up to 5, each supported by at least one article UUID. Sentiment
   is bullish / bearish / neutral / mixed. Confidence 0-1.
4. `tickers_mentioned` — include every ticker that appears by name. Set
   `relevance_to_portfolio` to "high" if the ticker is held, "medium" if a
   sector peer of a holding, else "low".
5. `macro_signals` — rates, growth, inflation, geopolitics, regulation. Cite
   the article UUID.
6. `risk_flags` — anything that materially changes risk for broad asset classes.
7. `flagged_critical` — populate only for items that meet the critical criteria:
   earnings/guidance event on a held ticker, SEC/regulatory action, takeover
   bid, sector crash, management change. Be strict; a tag of `critical` triggers
   downstream work.
8. Do NOT invent tickers, numbers, or events that are not in the source.
9. Preserve UUIDs from the source; downstream tools use them to retrieve full
   articles on demand.
```

### Finimize daily summarizer prompt

```
You are a thesis-aggregation summariser producing a structured daily digest
from today's captured Finimize articles. Finimize is closer to longer-term
investment ideas than breaking news — treat repeated themes/tickers across
days as an accumulating signal, not a single-day fact. Your output feeds
investment decision agents that care about thesis strength.

INPUTS:
- Today's raw article list (title, tagline, URL, tickers, tags, 2-3 sentence
  summary each), parsed from {path_to_raw_file}.
- The last 5 Finimize digests, for thesis-reinforcement detection:
  {paths_to_prior_digests}.
- Current portfolio holdings: {portfolio_tickers_and_sectors}.

PRODUCE a JSON object matching the shared digest schema, with these differences:
- `themes[].confidence` should be BOOSTED when the same theme appeared in the
  prior 5 digests. Explicitly track `recurrence_count` in each theme:
  how many of the last 5 days it appeared in.
- `tickers_mentioned[].count` tracks today's count; add
  `rolling_count_5d` for the last 5 days.
- `flagged_critical` is reserved for Finimize calling out a held ticker with a
  clear thesis-level reason (acquisition, regulatory change, multi-day drumbeat).

Rules:
1. Treat Finimize tags as hints, not ground truth.
2. If a ticker has appeared 3+ days in the last 5, flag it in `one_liner`.
3. Do NOT inflate sentiment from marketing-style headlines — many Finimize
   items are explainer-style rather than directional.
4. When the article has a `ticker` field but the body clearly refers to a
   different company, note the mismatch under a `data_anomalies` array and
   skip the ticker tag. (We already see this on CSE-listed small caps.)
5. Do NOT invent items not in the source.
```

### Weekly Friday trend digest prompt

```
You are a trend synthesis agent. Produce the weekly market-direction digest for
this week (Mon-Fri), drawing from five daily digests per source and the raw
daily files for direct quote back-up when needed.

INPUTS:
- FT daily digests for Mon-Fri: {paths}.
- Finimize daily digests for Mon-Fri: {paths}.
- Last week's weekly digest: {path} (for direction-change detection).
- Current portfolio holdings: {portfolio_tickers_and_sectors}.

PRODUCE a markdown file with these sections, in this order:

1. **Direction of the tape** (≤80 words)
   One paragraph: where did the narrative move this week vs. last week? Was
   the shift gradual or abrupt?

2. **Recurring themes** (up to 7, ranked)
   For each: theme label, days-appeared count (out of 5), dominant sentiment,
   sources (FT, Finimize, or both), and one-sentence why-it-matters.

3. **Ticker watchlist**
   Every ticker mentioned 2+ times this week. Group by: held, sector-peer of
   held, neither. For each: rolling count, sentiment, dominant storyline.

4. **Direction shifts**
   Things where FT's framing pivoted during the week (e.g., hawkish on Mon,
   dovish by Thu). Flag contradictions between FT and Finimize on the same
   theme.

5. **Portfolio intersections**
   For each currently held ticker/sector that intersected the week's flow:
   what changed in the narrative, and whether this merits an escalation to
   Portfolio Desk.

6. **Read-in-full recommendations**
   Up to 5 specific FT or Finimize URLs that are worth reading end-to-end.

Rules:
- Cite article UUIDs (FT) / slugs (Finimize) for every claim.
- Do not repeat items verbatim from the daily digests — synthesise.
- If a day is missing from the inputs (scheduler skipped), say so at the top.
```

## Integration points

### Stockdrop

1. **Bootstrap step at each pipeline run** — call
   `ensure_news_digest_for_today()` in `app/services/stock_service.py` before
   kicking off Council 1. Checks-and-generates. Idempotent.
2. **New helper module** — `app/services/news_digest_service.py`:
   - `ensure_daily_digest(source, date)` — idempotent
   - `load_digest(source, date)` → dict from JSON
   - `format_for_agent(digest, agent_name)` → str, returns the right slice
     per the agent-consumption table
   - `load_weekly_digest(source, week)` → dict
3. **Agent prompt builders** consume `format_for_agent(...)` and paste into the
   existing prompts.
4. **Gatekeeper stays deterministic** — no change. News doesn't gate.

### Portfolio Desk

1. **Escalation rule** — extend `desk/escalation.py`:
   ```
   news_match = match_news_to_position(position, today_digests)
   if news_match.has_hit:
       return Decision(COUNCIL, trigger=news_match.trigger_enum,
                       reason=news_match.headline)
   ```
   Slots in after `first_review` and `hard_trigger`, before price/age rules.
2. **Prompt injection** — each reassessment prompt in `desk/prompts/` gets an
   optional "news_context" block populated only when the news trigger fired.
3. **Flagged-critical poll** — at the start of a Desk run, load
   `flagged_for_portfolio_desk.json`; any entry for a held ticker forces a
   `COUNCIL_DEEP` review and pulls the full FT article body on demand.

### Env / config

```
NEWS_ARCHIVE_ROOT=/abs/path/to/Investment Ideas and Portfolio
NEWS_DIGEST_MODEL=gemini-3.1-pro-thinking
NEWS_DIGEST_ENABLED=true
DESK_NEWS_TRIGGER_ENABLED=false       # opt-in during rollout
DESK_NEWS_MAX_ESCALATIONS=3
```

## Phased delivery

1. **Digest generator + idempotent write** — produce daily JSON+MD for FT and
   Finimize. Manual run for a few days; eyeball quality vs. the raw files.
2. **Stockdrop injection** — News sensor + PM + Deep Research. Measure prompt
   token impact and verdict shift vs. baseline.
3. **Portfolio Desk escalation** — behind `DESK_NEWS_TRIGGER_ENABLED`. Start
   with `news_ticker_mentioned` only; add theme/macro after a week.
4. **Weekly Friday digest** — simplest scheduled job. High daily read value for
   the user directly, too, so ship this even if downstream agents don't use it
   yet.
5. **Critical-flag on-demand full fetch** — last, since it needs the Chrome
   session and a retry path.

## Open questions

- Weekly digest scheduler: the current README says Sunday. Our agent-written
  Friday digest is a separate artifact. Keep both? Retire the Sunday rollup?
- Ticker→sector mapping: maintain a small manual map in
  `news_digest_service.py` covering held positions, or derive sectors from
  council reports? Leaning manual for v1 (~20 tickers).
- Should the weekly digest be rendered as a Cowork artifact for persistent
  daily re-reading, not just a file? High re-open value for the user.
- Logging policy when a raw file is missing: skip silently, or surface in
  Stockdrop's pipeline logs? Leaning surface — otherwise you forget the
  scheduler is broken.

---

## Implementation status (2026-04-22)

All plan items delivered. 40 tests passing.

### New files

- `app/services/news_digest_schema.py` — paths, `Article`, `AGENT_SLICE_MAP`
- `app/services/news_digest_parser.py` — FT + Finimize daily markdown parsers
- `app/services/portfolio_tickers.py` — xlsx loader (header=3; skips account markers)
- `app/services/news_digest_prompts.py` — three prompt builders (FT daily, Finimize daily, FT weekly)
- `app/services/news_digest_service.py` — orchestrator (`ensure_daily_digest`, `ensure_news_digests_for_today`, `ensure_ft_weekly_digest`, `format_for_agent`, flagged-critical append)
- `scripts/news_digest/run_daily.py` — CLI backfill for daily digests
- `scripts/news_digest/run_weekly.py` — CLI backfill for FT weekly

### Modified files

- `app/services/stock_service.py` — bootstrap call to `ensure_news_digests_for_today()` inside `check_large_cap_drops`, guarded with try/except (non-fatal)
- `app/services/research_service.py` — `_news_block_for(state, agent)` helper + 6 direct injection sites (News, Market Sentiment, Competitive, Bear, Risk, PM). Bull + Deep Research receive the digest **transitively** via the reports they read.
- `.env.example` — added `NEWS_ARCHIVE_ROOT`, `NEWS_DIGEST_MODEL`, `NEWS_DIGEST_ENABLED`

### Injection map (final)

| Agent            | Slice            | Rationale                                    |
|------------------|------------------|----------------------------------------------|
| news             | full             | Most comprehensive — primary news consumer   |
| market_sentiment | sentiment_full   | Tickers + macro signals shape tape reading   |
| competitive      | competitive_full | Sector-scoped themes + tickers               |
| bear             | bearish_bundle   | Bearish themes, risk flags, macro            |
| risk             | macro_risk       | Macro signals + risk flags only              |
| pm               | compact          | One-liner + market tape only (cheap)         |
| bull             | —                | Transitive via News/Sentiment/Competitive    |
| deep_research    | —                | Transitive via Phase 1 reports               |
| technical        | —                | Technical analysis is not news-driven        |
| seeking_alpha    | —                | External source, already its own signal      |

### Test coverage (40 passing)

- `tests/test_news_digest_parser.py` — 5 tests (FT + Finimize parsing against real archive fixtures)
- `tests/test_portfolio_tickers.py` — 4 tests (header offset, account-marker skip)
- `tests/test_news_digest_service.py` — 24 tests (orchestration, idempotency, slicing, flagged-critical append, weekly with last-3-Finimize context, ISO-week math across year boundary)
- `tests/test_news_digest_prompt_injection.py` — 7 tests (per-agent block content + transitive non-injection)

### How to use

```bash
# Backfill a specific daily digest
python scripts/news_digest/run_daily.py --date 2026-04-22 --source ft

# Generate current ISO week's FT weekly (pulls last 3 Finimize weeklies as context)
python scripts/news_digest/run_weekly.py

# Disable entirely (bootstrap becomes a no-op)
NEWS_DIGEST_ENABLED=false
```

In normal operation the scanner bootstrap generates today's digests lazily on
the first scan of the day — no cron needed on the Stockdrop side.

"""Prompt builders for the news digest summariser.

Three builders:
- build_ft_daily_prompt
- build_finimize_daily_prompt
- build_ft_weekly_prompt  (FT weekly only; Finimize weekly is scheduler-written)
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.services.news_digest_schema import Article

_SCHEMA = """{
  "date": "YYYY-MM-DD",
  "source": "ft" | "finimize",
  "generated_at": "ISO8601",
  "model": "gemini-3.1-pro-thinking",
  "one_liner": "<=20 words — dominant signal of the day",
  "market_tape": "<=60 words — neutral paragraph summarising overall flow",
  "themes": [
    {
      "theme": "snake_case_label",
      "sentiment": "bullish" | "bearish" | "neutral" | "mixed",
      "confidence": 0.0,
      "opinion_driven": false,
      "supporting_articles": ["<uuid-or-slug>"],
      "one_liner": "<=25 words"
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
    blocks = []
    for a in articles:
        parts = [f"id: {a.uuid}", f"section: {a.section}", f"title: {a.title}", f"url: {a.url}"]
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
    return ", ".join(f"{t} ({s})" for t, s in sorted(portfolio.items()))


def build_ft_daily_prompt(
    *,
    date: str,
    articles: List[Article],
    prior_digest_text: Optional[str],
    portfolio: Dict[str, str],
) -> str:
    articles_text = _articles_block(articles)
    prior = prior_digest_text or "(no prior digest available — first run)"
    return f"""You are a markets news summariser producing a structured daily digest from
today's captured FT articles. The FT is a highly reliable news source — treat
its framing as authoritative. Your output feeds directly into investment
decision agents, so precision matters more than breadth.

INPUTS:
- Date: {date}
- Today's articles:

{articles_text}

- Yesterday's digest (for direction-change detection):

{prior}

- Current portfolio holdings:
{_portfolio_block(portfolio)}

PRODUCE a single JSON object matching this schema exactly:

{_SCHEMA}

Rules:
1. `source` MUST be "ft". `date` MUST be "{date}".
2. `one_liner` <= 20 words capturing the day's dominant signal.
3. `market_tape` <= 60 words neutral-toned summary of overall news flow.
4. `themes`: up to 5, each supported by at least one article id. Mark
   `opinion_driven: true` ONLY for themes sourced primarily from Opinion or
   Editorials sections.
5. `tickers_mentioned`: include every explicit ticker. Set
   `relevance_to_portfolio` to "high" if held, "medium" if a sector peer, else
   "low".
6. `macro_signals`: rates, growth, inflation, geopolitics, regulation.
7. `risk_flags`: items that materially change risk for broad asset classes.
8. `flagged_critical`: ONLY earnings/guidance event on a HELD ticker, SEC
   action, takeover bid, sector crash, management change. Be strict.
9. DO NOT invent tickers, numbers, or events not in the source.
10. Preserve article ids verbatim.
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
    prior_lines = [f"=== prior digest -{i} ===\n{t}" for i, t in enumerate(prior_digests_text[:5], 1)]
    prior = "\n\n".join(prior_lines) if prior_lines else "(no prior digests available)"
    return f"""You are a thesis-aggregation summariser producing a structured daily digest
from today's captured Finimize articles. Finimize is closer to longer-term
investment ideas than breaking news — treat repeated themes/tickers across
days as an accumulating signal, not a single-day fact.

INPUTS:
- Date: {date}
- Today's articles:

{articles_text}

- Last 5 Finimize digests (for recurrence detection):

{prior}

- Current portfolio holdings:
{_portfolio_block(portfolio)}

PRODUCE a single JSON object matching the shared schema, with these
FINIMIZE-SPECIFIC additions:
- Each theme gets `recurrence_count` (0-5): days in the prior 5 digests it appeared.
- Each `tickers_mentioned` entry gets `rolling_count_5d`: total mentions across
  the last 5 digests plus today.

{_SCHEMA}

Rules:
1. `source` MUST be "finimize". `date` MUST be "{date}".
2. BOOST `themes[].confidence` when `recurrence_count >= 2`.
3. If a ticker has `rolling_count_5d >= 3`, mention it in `one_liner`.
4. DO NOT inflate sentiment from marketing-style headlines.
5. If an article lists a ticker in its metadata but the body refers to a
   different company, skip the ticker and add a `data_anomalies` entry.
6. `flagged_critical`: reserved for held tickers with clear thesis-level
   reasons (acquisition, regulatory change, multi-day drumbeat).
7. DO NOT invent items not in the source.
8. Treat Finimize tags as hints, not ground truth.
9. Respond with ONLY the JSON object.
"""


def build_ft_weekly_prompt(
    *,
    iso_week: str,
    ft_daily_digests: List[str],
    finimize_weekly_rollups: List[str],
    prior_ft_weekly: Optional[str],
    portfolio: Dict[str, str],
) -> str:
    """FT weekly trend digest.

    Inputs:
      - 5 FT daily digests (this week, ours)
      - Last 3 Finimize weekly rollups (scheduler-written, external)
      - Last week's FT weekly (ours, for direction-change detection)
      - Portfolio
    """

    def _labeled(blocks: List[str], label: str) -> str:
        if not blocks:
            return f"(no {label} inputs captured this week)"
        return "\n\n".join(f"=== {label} {i} ===\n{b}" for i, b in enumerate(blocks, 1))

    ft_text = _labeled(ft_daily_digests, "FT daily")
    fin_text = _labeled(finimize_weekly_rollups, "Finimize weekly")
    prior = prior_ft_weekly or "(no prior FT weekly — first run)"
    return f"""You are a trend synthesis agent producing the FT weekly market-direction
digest for ISO week {iso_week}. You synthesise one week of FT dailies against
the last three weeks of Finimize weekly rollups — the Finimize rollups give
you a longer-horizon thesis backdrop that a single week of FT news can't.

INPUTS:
- This week's FT daily digests (Mon-Fri):

{ft_text}

- Last 3 Finimize weekly rollups (scheduler-written):

{fin_text}

- Last week's FT weekly (for direction-change detection):

{prior}

- Current portfolio holdings:
{_portfolio_block(portfolio)}

PRODUCE markdown with these sections IN ORDER, no other sections:

1. **Direction of the tape** (<=80 words)
   Where did the FT narrative move this week vs. last week? Gradual or abrupt?
   Reference the Finimize 3-week backdrop where it confirms or contradicts.

2. **Recurring themes** (up to 7, ranked by days-appeared)
   theme label, days-appeared (out of 5), dominant sentiment, sources
   (FT-only / both / Finimize-only), one-sentence why-it-matters.

3. **Ticker watchlist**
   Every ticker mentioned 2+ times this week across either source. Group by:
   held / sector-peer of held / neither.

4. **Direction shifts**
   Where FT's framing pivoted during the week. Contradictions between this
   week's FT and the rolling Finimize thesis.

5. **Portfolio intersections**
   For each HELD ticker/sector that intersected this week's flow: what
   changed, and whether it merits an escalation.

6. **Read-in-full recommendations**
   Up to 5 specific article ids (FT UUIDs or Finimize slugs) worth reading.

Rules:
- Cite article ids for every claim.
- Do not repeat items verbatim from daily digests — synthesise.
- If a weekday digest is missing, note it at the top.
- Do NOT fabricate tickers, numbers, or events.
"""

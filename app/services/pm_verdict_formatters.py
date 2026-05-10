"""Console-block formatters for the PM verdict.

Two helpers:
  - format_rr_block(upside, downside, rr)   -> bordered Risk/Reward highlight
  - format_ratings_block(ratings)           -> bordered External Ratings (or fallback line)

Pure string builders, no I/O. Tested in tests/test_pm_verdict_formatters.py.
"""
from __future__ import annotations

from typing import Optional


def rating_label(score: Optional[float]) -> str:
    """Map a 1.0-5.0 numeric score to SA's qualitative band."""
    if score is None:
        return "n/a"
    if score >= 4.5:
        return "Strong Buy"
    if score >= 3.5:
        return "Buy"
    if score >= 2.5:
        return "Hold"
    if score >= 1.5:
        return "Sell"
    return "Strong Sell"


def _rr_glyph(rr: Optional[float]) -> str:
    if rr is None:
        return ""
    if rr >= 2.0:
        return "✅"
    if rr >= 1.5:
        return "⚠️"
    return "❌"


def _fmt_pct(v: Optional[float], sign: str) -> str:
    if v is None:
        return "n/a"
    return f"{sign}{v:.1f}%"


def format_rr_block(upside: Optional[float], downside: Optional[float], rr: Optional[float]) -> str:
    """Bordered Risk/Reward highlight block.

    Layout:
        ┌─ RISK / REWARD ──────────────────────────────┐
        │  R/R: 2.0x  ✅                                │
        │  Upside  +12.5%   ↑ to TP1                   │
        │  Downside −6.4%   ↓ to Stop                  │
        └──────────────────────────────────────────────┘
    """
    glyph = _rr_glyph(rr)
    rr_str = "n/a" if rr is None else f"{rr:.1f}x"
    rr_line = f"R/R: {rr_str}" + (f"  {glyph}" if glyph else "")
    up_line = f"Upside   {_fmt_pct(upside, '+')}   ↑ to TP1"
    # Downside uses unicode minus for visual weight; tolerate negative input as magnitude.
    dn_val = abs(downside) if downside is not None else None
    dn_line = f"Downside {_fmt_pct(dn_val, '−')}   ↓ to Stop"

    width = 48
    top = "┌─ RISK / REWARD " + "─" * (width - len("┌─ RISK / REWARD ") - 1) + "┐"
    bot = "└" + "─" * (width - 2) + "┘"

    def row(text: str) -> str:
        # Pad inside the borders. Account for emoji visual width naively (✅/⚠️/❌ render as ~2 cells).
        return f"│  {text}".ljust(width - 1) + "│"

    return "\n".join([top, row(rr_line), row(up_line), row(dn_line), bot])


def format_ratings_block(ratings: dict) -> str:
    """Bordered External Ratings block, or a single fallback line.

    Three modes:
      1. CSV unavailable           -> "External Ratings: unavailable (snapshot CSV missing or unreadable)"
      2. Ticker not in CSV         -> "External Ratings: n/a (ticker not in SA_Quant_Ranked_Clean.csv)"
      3. Hit                       -> bordered block with all four ratings
    """
    if not ratings.get("available", False):
        return "External Ratings: unavailable (snapshot CSV missing or unreadable)"

    quant = ratings.get("sa_quant_rating")
    authors = ratings.get("sa_authors_rating")
    ws = ratings.get("wall_street_rating")
    rank = ratings.get("sa_rank")
    total = ratings.get("total_ranked")

    if quant is None and authors is None and ws is None and rank is None:
        return "External Ratings: n/a (ticker not in SA_Quant_Ranked_Clean.csv)"

    width = 60
    title = " EXTERNAL RATINGS (informational, not seen by agents) "
    top = "┌─" + title + "─" * (width - len(title) - 3) + "┐"
    bot = "└" + "─" * (width - 2) + "┘"

    def row(text: str) -> str:
        return f"│  {text}".ljust(width - 1) + "│"

    def fmt_score(s):
        return f"{s:.2f}  ({rating_label(s)})" if s is not None else "n/a"

    rank_line = (
        f"#{rank} / {total:,}" if rank is not None and total else
        f"#{rank}" if rank is not None else "n/a"
    )

    return "\n".join([
        top,
        row(f"SA Quant Rating:    {fmt_score(quant)}"),
        row(f"SA Analyst Rating:  {fmt_score(authors)}"),
        row(f"Wall Street Rating: {fmt_score(ws)}"),
        row(f"SA Rank:            {rank_line}"),
        bot,
    ])

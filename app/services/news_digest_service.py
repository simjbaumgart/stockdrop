"""News digest orchestrator.

Public entry points:
    ensure_daily_digest(source, date)       — idempotent generate-if-missing
    ensure_news_digests_for_today()         — both sources, today's date
    ensure_ft_weekly_digest(iso_week)       — FT weekly (pulls last 3 Finimize weeklies)
    load_digest(source, date)               — pure read
    load_ft_weekly(iso_week)                — pure read
    load_finimize_weekly(iso_week)          — pure read of scheduler-written file
    format_for_agent(agent, date, ticker, sector) — slice for prompt injection
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app.services.news_digest_parser import parse_finimize_daily, parse_ft_daily
from app.services.news_digest_prompts import (
    build_finimize_daily_prompt,
    build_ft_daily_prompt,
    build_ft_weekly_prompt,
)
from app.services.news_digest_schema import (
    AGENT_SLICE_MAP,
    SOURCES,
    Article,
    digest_enabled,
    digest_json_path,
    digest_md_path,
    digest_model,
    finimize_weekly_scheduler_path,
    flagged_critical_path,
    ft_weekly_digest_json_path,
    ft_weekly_digest_md_path,
    raw_daily_path,
)
from app.services.portfolio_tickers import load_portfolio_tickers

logger = logging.getLogger(__name__)

_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# LLM call (patchable)
# ---------------------------------------------------------------------------


def _call_thinking_model(prompt: str) -> str:
    """Single-shot call to the configured thinking model."""
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
    ol = (digest.get("one_liner") or "").strip()
    if ol:
        lines.append(f"_{ol}_")
        lines.append("")
    tape = (digest.get("market_tape") or "").strip()
    if tape:
        lines.append("## Market tape")
        lines.append(tape)
        lines.append("")
    themes = digest.get("themes") or []
    if themes:
        lines.append("## Themes")
        for t in themes:
            extras = []
            if "recurrence_count" in t:
                extras.append(f"rec:{t['recurrence_count']}/5")
            if t.get("opinion_driven"):
                extras.append("opinion")
            ext = f" ({', '.join(extras)})" if extras else ""
            lines.append(
                f"- **{t.get('theme')}** [{t.get('sentiment')}, conf {t.get('confidence')}]"
                f"{ext}: {t.get('one_liner','')}"
            )
        lines.append("")
    tickers = digest.get("tickers_mentioned") or {}
    if tickers:
        lines.append("## Tickers mentioned")
        for tkr, meta in tickers.items():
            extra = ""
            if "rolling_count_5d" in meta:
                extra = f", 5d:{meta['rolling_count_5d']}"
            lines.append(
                f"- **{tkr}** (x{meta.get('count', 0)}, {meta.get('sentiment')}, "
                f"rel:{meta.get('relevance_to_portfolio', 'low')}{extra})"
            )
        lines.append("")
    macro = digest.get("macro_signals") or []
    if macro:
        lines.append("## Macro signals")
        for m in macro:
            lines.append(f"- {m.get('signal')} -> {m.get('direction')} (conf {m.get('confidence')})")
        lines.append("")
    risks = digest.get("risk_flags") or []
    if risks:
        lines.append("## Risk flags")
        for r in risks:
            lines.append(
                f"- **{r.get('flag')}** [{r.get('severity')}] — impacts {', '.join(r.get('impacts', []))}"
            )
        lines.append("")
    critical = digest.get("flagged_critical") or []
    if critical:
        lines.append("## Flagged critical (for Portfolio Desk)")
        for c in critical:
            lines.append(f"- **{c.get('ticker')}** — {c.get('reason')}: {c.get('headline')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# flagged_for_portfolio_desk.json
# ---------------------------------------------------------------------------


def _append_flagged_critical(digest: dict) -> None:
    critical = digest.get("flagged_critical") or []
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
            {**entry, "source": digest.get("source"), "date": digest.get("date"), "flagged_at": now}
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


def load_ft_weekly(iso_week: str) -> Optional[str]:
    path = ft_weekly_digest_md_path(iso_week)
    return path.read_text(encoding="utf-8") if path.exists() else None


def load_finimize_weekly(iso_week: str) -> Optional[str]:
    """Read the scheduler-written Finimize weekly rollup."""
    path = finimize_weekly_scheduler_path(iso_week)
    return path.read_text(encoding="utf-8") if path.exists() else None


# ---------------------------------------------------------------------------
# Daily generation
# ---------------------------------------------------------------------------


def _parse_raw(source: str, date: str) -> List[Article]:
    path = raw_daily_path(source, date)
    return parse_ft_daily(path) if source == "ft" else parse_finimize_daily(path)


def _prior_digest_text(source: str, date: str, days_back: int) -> List[str]:
    out: List[str] = []
    d = datetime.strptime(date, "%Y-%m-%d")
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
            date=date, articles=articles, prior_digests_text=prior, portfolio=portfolio
        )

    try:
        raw = _call_thinking_model(prompt)
        digest = _parse_json_or_raise(raw)
    except Exception as e:
        logger.exception("[news-digest] generation failed for %s %s: %s", source, date, e)
        return None

    digest.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
    digest.setdefault("model", digest_model())
    digest["source"] = source
    digest["date"] = date

    json_path = digest_json_path(source, date)
    md_path = digest_md_path(source, date)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(digest), encoding="utf-8")
    logger.info("[news-digest] wrote %s and %s", json_path, md_path)

    _append_flagged_critical(digest)
    return digest


def ensure_news_digests_for_today(date: Optional[str] = None) -> None:
    d = date or datetime.now().strftime("%Y-%m-%d")
    for source in SOURCES:
        try:
            ensure_daily_digest(source, d)
        except Exception as e:
            logger.exception("[news-digest] ensure_daily_digest(%s, %s) raised: %s", source, d, e)


# ---------------------------------------------------------------------------
# Weekly (FT only — Finimize weekly is scheduler-written)
# ---------------------------------------------------------------------------


def _iso_week_for(date: str) -> str:
    d = datetime.strptime(date, "%Y-%m-%d")
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _weekday_dates(iso_week: str) -> List[str]:
    """Mon-Fri YYYY-MM-DD for the given ISO week."""
    year, week = iso_week.split("-W")
    monday = datetime.strptime(f"{year}-W{int(week):02d}-1", "%G-W%V-%u")
    return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]


def _prior_iso_weeks(iso_week: str, n: int) -> List[str]:
    """Return the n prior ISO weeks, most recent first."""
    year, week = iso_week.split("-W")
    monday = datetime.strptime(f"{year}-W{int(week):02d}-1", "%G-W%V-%u")
    out: List[str] = []
    for i in range(1, n + 1):
        prev = monday - timedelta(days=7 * i)
        y, w, _ = prev.isocalendar()
        out.append(f"{y}-W{w:02d}")
    return out


def ensure_ft_weekly_digest(iso_week: str) -> Optional[dict]:
    """Generate the FT weekly digest.

    Inputs:
      - 5 FT daily digests from this ISO week (ours)
      - Last 3 Finimize weekly rollups (scheduler-written, external)
      - Last week's FT weekly (ours)
      - Portfolio
    """
    if not digest_enabled():
        return None

    md_path = ft_weekly_digest_md_path(iso_week)
    if md_path.exists():
        logger.info("[news-digest] FT weekly %s already exists — skipping", iso_week)
        return {"iso_week": iso_week, "skipped": True, "path": str(md_path)}

    ft_dailies = [m for d in _weekday_dates(iso_week) if (m := _load_digest_md("ft", d))]
    if not ft_dailies:
        logger.info("[news-digest] no FT daily digests for week %s — skipping", iso_week)
        return None

    prior_weeks = _prior_iso_weeks(iso_week, 3)
    finimize_weeklies = [w for pw in prior_weeks if (w := load_finimize_weekly(pw))]

    prior_ft = load_ft_weekly(_prior_iso_weeks(iso_week, 1)[0])
    portfolio = load_portfolio_tickers()

    prompt = build_ft_weekly_prompt(
        iso_week=iso_week,
        ft_daily_digests=ft_dailies,
        finimize_weekly_rollups=finimize_weeklies,
        prior_ft_weekly=prior_ft,
        portfolio=portfolio,
    )

    try:
        md = _call_thinking_model(prompt).strip()
    except Exception as e:
        logger.exception("[news-digest] FT weekly generation failed for %s: %s", iso_week, e)
        return None

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md + "\n", encoding="utf-8")
    meta = {
        "iso_week": iso_week,
        "source": "ft",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": digest_model(),
        "inputs": {
            "ft_daily_count": len(ft_dailies),
            "finimize_weekly_count": len(finimize_weeklies),
            "finimize_weeks_used": [
                pw for pw in prior_weeks if load_finimize_weekly(pw) is not None
            ],
            "has_prior_ft_weekly": prior_ft is not None,
        },
    }
    ft_weekly_digest_json_path(iso_week).write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    logger.info("[news-digest] wrote FT weekly %s (used %d Finimize rollups)", md_path, len(finimize_weeklies))
    return meta


# ---------------------------------------------------------------------------
# format_for_agent — slicing per AGENT_SLICE_MAP
# ---------------------------------------------------------------------------


def _oneliner_block(digest: dict) -> str:
    ol = (digest.get("one_liner") or "").strip()
    mt = (digest.get("market_tape") or "").strip()
    return " ".join(p for p in (ol, mt) if p).strip()


def _themes_block(digest: dict, filt: str) -> str:
    out: List[str] = []
    for t in digest.get("themes") or []:
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


def _tickers_sector_block(digest: dict, ticker: str) -> str:
    out: List[str] = []
    target = (ticker or "").upper()
    for tkr, meta in (digest.get("tickers_mentioned") or {}).items():
        tkr_u = tkr.upper()
        is_direct = tkr_u == target
        rel = meta.get("relevance_to_portfolio", "low")
        if is_direct or rel in ("high", "medium"):
            tag = "direct" if is_direct else f"rel:{rel}"
            out.append(
                f"- **{tkr_u}** ({tag}): x{meta.get('count')}, sent:{meta.get('sentiment')}"
            )
    return "\n".join(out)


def _macro_risk_block(digest: dict) -> str:
    lines: List[str] = []
    for m in digest.get("macro_signals") or []:
        lines.append(
            f"- macro:{m.get('signal')} -> {m.get('direction')} (conf:{m.get('confidence')})"
        )
    for r in digest.get("risk_flags") or []:
        lines.append(
            f"- risk:{r.get('flag')} [{r.get('severity')}] impacts:{','.join(r.get('impacts', []))}"
        )
    return "\n".join(lines)


def _sentiment_full_block(digest: dict) -> str:
    parts: List[str] = []
    tape = (digest.get("market_tape") or "").strip()
    if tape:
        parts.append(f"MARKET TAPE: {tape}")
    themes = _themes_block(digest, "all")
    if themes:
        parts.append("THEMES:\n" + themes)
    macro_lines = [
        f"- {m.get('signal')} -> {m.get('direction')} (conf:{m.get('confidence')})"
        for m in digest.get("macro_signals") or []
    ]
    if macro_lines:
        parts.append("MACRO SIGNALS:\n" + "\n".join(macro_lines))
    return "\n\n".join(parts)


def _competitive_full_block(digest: dict, ticker: str) -> str:
    parts: List[str] = []
    themes = _themes_block(digest, "all")
    if themes:
        parts.append("THEMES:\n" + themes)
    tickers = _tickers_sector_block(digest, ticker)
    if tickers:
        parts.append("TICKERS (direct / sector-peer):\n" + tickers)
    risk_lines = [
        f"- {r.get('flag')} [{r.get('severity')}] impacts:{','.join(r.get('impacts', []))}"
        for r in digest.get("risk_flags") or []
    ]
    if risk_lines:
        parts.append("RISK FLAGS:\n" + "\n".join(risk_lines))
    return "\n\n".join(parts)


def _bearish_bundle_block(digest: dict) -> str:
    parts: List[str] = []
    ol = _oneliner_block(digest)
    if ol:
        parts.append(ol)
    bearish = _themes_block(digest, "bearish")
    if bearish:
        parts.append("BEARISH THEMES:\n" + bearish)
    macro = _macro_risk_block(digest)
    if macro:
        parts.append("MACRO / RISK:\n" + macro)
    return "\n\n".join(parts)


def _daily_slice(
    digest: Optional[dict], md: str, slice_name: str, ticker: str
) -> str:
    if digest is None or slice_name == "none":
        return ""
    if slice_name == "full":
        return md
    if slice_name == "compact":
        return _oneliner_block(digest)
    if slice_name == "sentiment_full":
        return _sentiment_full_block(digest)
    if slice_name == "competitive_full":
        return _competitive_full_block(digest, ticker)
    if slice_name == "bearish_bundle":
        return _bearish_bundle_block(digest)
    if slice_name == "macro_risk":
        return _macro_risk_block(digest)
    return ""


def _weekly_slice(md: Optional[str], slice_name: str) -> str:
    if not md or slice_name == "none":
        return ""
    if slice_name == "weekly_full":
        return md
    if slice_name == "weekly_macro":
        return md
    if slice_name == "weekly_oneliner":
        for line in md.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("**"):
                return s
        return md[:400]
    return ""


def format_for_agent(
    agent_name: str, date: str, ticker: str, sector: Optional[str] = None
) -> str:
    slices = AGENT_SLICE_MAP.get(agent_name)
    if not slices:
        return ""

    iso_week = _iso_week_for(date)

    ft_daily = load_digest("ft", date)
    ft_daily_md = _load_digest_md("ft", date)
    fin_daily = load_digest("finimize", date)
    fin_daily_md = _load_digest_md("finimize", date)

    ft_weekly_md = load_ft_weekly(iso_week)
    fin_weekly_md = load_finimize_weekly(iso_week)

    ft_block = _daily_slice(ft_daily, ft_daily_md, slices["ft_daily"], ticker)
    fin_block = _daily_slice(fin_daily, fin_daily_md, slices["finimize_daily"], ticker)
    ft_w_block = _weekly_slice(ft_weekly_md, slices["ft_weekly"])
    fin_w_block = _weekly_slice(fin_weekly_md, slices["finimize_weekly"])

    sections: List[str] = []
    if ft_block.strip():
        sections.append(f"--- FT daily digest ({date}) ---\n{ft_block.strip()}")
    if fin_block.strip():
        sections.append(f"--- Finimize daily digest ({date}) ---\n{fin_block.strip()}")
    if ft_w_block.strip():
        sections.append(f"--- FT weekly digest ({iso_week}) ---\n{ft_w_block.strip()}")
    if fin_w_block.strip():
        sections.append(f"--- Finimize weekly rollup ({iso_week}) ---\n{fin_w_block.strip()}")

    return "\n\n".join(sections) if sections else ""

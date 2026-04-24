"""Constants, dataclasses, and consumption map for the news-digest subsystem."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

_DEFAULT_ROOT = (
    "/Users/simonbaumgart/Documents/Claude/Projects/Investment Ideas and Portfolio"
)


def news_archive_root() -> Path:
    return Path(os.getenv("NEWS_ARCHIVE_ROOT", _DEFAULT_ROOT))


def digest_enabled() -> bool:
    return os.getenv("NEWS_DIGEST_ENABLED", "true").lower() in ("1", "true", "yes")


def digest_model() -> str:
    return os.getenv("NEWS_DIGEST_MODEL", "gemini-3.1-pro-preview")


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


def ft_weekly_digest_md_path(iso_week: str) -> Path:
    """Ours — FT weekly digest written by this codebase."""
    return digest_dir("ft") / "weekly" / f"{iso_week}.md"


def ft_weekly_digest_json_path(iso_week: str) -> Path:
    return digest_dir("ft") / "weekly" / f"{iso_week}.json"


def finimize_weekly_scheduler_path(iso_week: str) -> Path:
    """Theirs — Finimize weekly written by the Cowork scheduler."""
    return archive_dir("finimize") / "weekly" / f"{iso_week}.md"


def flagged_critical_path() -> Path:
    return news_archive_root() / "flagged_for_portfolio_desk.json"


@dataclass
class Article:
    uuid: str
    title: str
    section: str
    url: str
    summary: str
    published: str = ""
    tags: List[str] = field(default_factory=list)
    tickers: List[str] = field(default_factory=list)
    byline: str = ""


# See plan doc for rationale. Six direct consumers; Bull + DR + Technical + SA inherit transitively.
AGENT_SLICE_MAP: Dict[str, Dict[str, str]] = {
    "technical":        {"ft_daily": "none",             "finimize_daily": "none",             "ft_weekly": "none",            "finimize_weekly": "none"},
    "seeking_alpha":    {"ft_daily": "none",             "finimize_daily": "none",             "ft_weekly": "none",            "finimize_weekly": "none"},
    "bull":             {"ft_daily": "none",             "finimize_daily": "none",             "ft_weekly": "none",            "finimize_weekly": "none"},
    "deep_research":    {"ft_daily": "none",             "finimize_daily": "none",             "ft_weekly": "none",            "finimize_weekly": "none"},
    "news":             {"ft_daily": "full",             "finimize_daily": "full",             "ft_weekly": "none",            "finimize_weekly": "none"},
    "market_sentiment": {"ft_daily": "sentiment_full",   "finimize_daily": "sentiment_full",   "ft_weekly": "none",            "finimize_weekly": "none"},
    "competitive":      {"ft_daily": "competitive_full", "finimize_daily": "competitive_full", "ft_weekly": "none",            "finimize_weekly": "none"},
    "bear":             {"ft_daily": "bearish_bundle",   "finimize_daily": "none",             "ft_weekly": "weekly_macro",    "finimize_weekly": "none"},
    "risk":             {"ft_daily": "macro_risk",       "finimize_daily": "none",             "ft_weekly": "weekly_full",     "finimize_weekly": "none"},
    "pm":               {"ft_daily": "compact",          "finimize_daily": "compact",          "ft_weekly": "weekly_oneliner", "finimize_weekly": "none"},
}

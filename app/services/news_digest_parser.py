"""Pure parser: raw daily markdown file -> list of Article dataclasses."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from app.services.news_digest_schema import Article

_FT_URL_RE = re.compile(r"https://www\.ft\.com/content/([0-9a-f-]+)", re.IGNORECASE)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_BULLET_URL_RE = re.compile(r"^-\s+\*\*URL:\*\*\s+(\S+)", re.IGNORECASE)
_BULLET_PUB_RE = re.compile(r"^-\s+\*\*Published:\*\*\s+(.+?)\s*$", re.IGNORECASE)
_BULLET_TAGS_RE = re.compile(r"^-\s+\*\*Tags:\*\*\s+(.+?)\s*$", re.IGNORECASE)
_BULLET_TKR_RE = re.compile(r"^-\s+\*\*Tickers:\*\*\s+(.+?)\s*$", re.IGNORECASE)
_BULLET_BYLINE_RE = re.compile(r"^-\s+\*\*Byline:\*\*\s+(.+?)\s*$", re.IGNORECASE)
_SUMMARY_INLINE_RE = re.compile(r"^\*\*Summary:\*\*\s+(.+)$", re.IGNORECASE)


def _split_csv(val: str) -> List[str]:
    return [p.strip() for p in val.split(",") if p.strip()]


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else url


def _finalize(current: dict, summary_buf: List[str]) -> Optional[Article]:
    if not current.get("title") or not current.get("url"):
        return None
    summary = " ".join(s.strip() for s in summary_buf if s.strip())
    uuid = current.get("uuid") or _slug_from_url(current["url"])
    return Article(
        uuid=uuid,
        title=current["title"],
        section=current.get("section", "unknown"),
        url=current["url"],
        summary=summary.strip(),
        published=current.get("published", ""),
        tags=current.get("tags", []),
        tickers=current.get("tickers", []),
        byline=current.get("byline", ""),
    )


def _parse(path: Path, *, ft_mode: bool) -> List[Article]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()

    articles: List[Article] = []
    current: dict = {}
    summary_buf: List[str] = []
    section = "unknown" if ft_mode else "news"
    mode = "scan"

    def flush():
        nonlocal current, summary_buf
        art = _finalize(current, summary_buf) if current else None
        if art is not None:
            articles.append(art)
        current = {}
        summary_buf = []

    def start_article(title: str, sect: str):
        nonlocal current, summary_buf, mode
        flush()
        current = {"title": title, "section": sect}
        summary_buf = []
        mode = "meta"

    for raw in lines:
        line = raw.rstrip()

        if ft_mode:
            m = _H2_RE.match(line)
            if m:
                # FT uses ## as section header
                flush()
                section = m.group(1).strip().lower()
                mode = "scan"
                continue
            m = _H3_RE.match(line)
            if m:
                start_article(m.group(1).strip(), section)
                continue
        else:
            # Finimize: ## = article title, ### Summary = summary block
            m = _H1_RE.match(line)
            if m:
                # Top-level "# Finimize News — DATE" — ignore
                continue
            m = _H2_RE.match(line)
            if m:
                start_article(m.group(1).strip(), "news")
                continue
            m = _H3_RE.match(line)
            if m:
                if m.group(1).strip().lower() == "summary":
                    mode = "summary"
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
            if m := _BULLET_PUB_RE.match(line):
                current["published"] = m.group(1)
                continue
            if m := _BULLET_TAGS_RE.match(line):
                current["tags"] = _split_csv(m.group(1))
                continue
            if m := _BULLET_TKR_RE.match(line):
                current["tickers"] = _split_csv(m.group(1))
                continue
            if m := _BULLET_BYLINE_RE.match(line):
                current["byline"] = m.group(1)
                continue
            if m := _SUMMARY_INLINE_RE.match(line):
                mode = "summary"
                summary_buf.append(m.group(1))
                continue
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
                # Heading ends summary; back-process via main loop by re-reading this line.
                # Cheap re-entry: flush, return mode to scan, fall through in a follow-up pass.
                # Simpler: flush here and handle the heading via a recursive single-line call is overkill.
                # Instead: simulate by handling inline.
                flush()
                mode = "scan"
                if ft_mode:
                    m2 = _H2_RE.match(line)
                    if m2:
                        section = m2.group(1).strip().lower()
                        continue
                    m3 = _H3_RE.match(line)
                    if m3:
                        start_article(m3.group(1).strip(), section)
                        continue
                else:
                    m2 = _H2_RE.match(line)
                    if m2:
                        start_article(m2.group(1).strip(), "news")
                        continue
                    m3 = _H3_RE.match(line)
                    if m3 and m3.group(1).strip().lower() == "summary":
                        mode = "summary"
                        continue
                continue
            summary_buf.append(line)

    flush()
    return articles


def parse_ft_daily(path) -> List[Article]:
    return _parse(Path(path), ft_mode=True)


def parse_finimize_daily(path) -> List[Article]:
    return _parse(Path(path), ft_mode=False)


def parse_wsj_daily(path) -> List[Article]:
    """WSJ shares FT's H2/H3/bullet structure but tacks ' · Author' onto the
    Published line. Reuse the FT parse path then split byline into its own field.
    """
    articles = _parse(Path(path), ft_mode=True)
    out: List[Article] = []
    for a in articles:
        published = a.published
        byline = a.byline
        if " · " in published:
            pub, _, by = published.partition(" · ")
            published = pub.strip()
            byline = byline or by.strip()
        out.append(
            Article(
                uuid=a.uuid,
                title=a.title,
                section=a.section,
                url=a.url,
                summary=a.summary,
                published=published,
                tags=a.tags,
                tickers=a.tickers,
                byline=byline,
            )
        )
    return out

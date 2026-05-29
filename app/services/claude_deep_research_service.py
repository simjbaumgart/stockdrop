"""Claude Opus 4.8 Deep Research provider (live pipeline only).

Mirrors app/services/deep_research_service.py's execute_* interface and
returns the same result-dict shape. Two-phase flow:
  1. Research loop with server-side web_search + web_fetch (multi-hop,
     pause_turn continuations) — grounds real source URLs.
  2. Synthesis call (no tools) with output_config.format to emit the
     exact result JSON, fed the collected URLs.

NOTE: provider routing is via DEEP_RESEARCH_PROVIDER=claude. Backfill is
out of scope — see memory/no-backfilling-deep-research.md.
"""
import os
import re
import json
import time
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

from app.services.deep_research_schemas import (
    INDIVIDUAL_SCHEMA, SELL_SCHEMA, BATCH_SCHEMA,
)

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-8"
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}
WEB_FETCH_TOOL = {"type": "web_fetch_20260209", "name": "web_fetch"}
MAX_CONTINUATIONS = 12   # outer pause_turn resumes; hard backstop on the multi-hop loop
RESEARCH_MAX_TOKENS = 32000
SYNTHESIS_MAX_TOKENS = 16000

_GOOGLE_RE = re.compile(r"Google Search", re.IGNORECASE)


def _deglooglify(prompt: str) -> str:
    """Swap Gemini-era 'Google Search' references for provider-neutral 'web search'."""
    return _GOOGLE_RE.sub("web search", prompt)


def _collect_source_urls(blocks: List[Any]) -> List[str]:
    """Extract de-duplicated http(s) URLs from a Claude response's content blocks:
    web_search_tool_result result lists and text-block citations."""
    seen: List[str] = []

    def _add(url: Optional[str]):
        if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
            seen.append(url)

    for b in blocks or []:
        btype = getattr(b, "type", None)
        if btype == "web_search_tool_result":
            for r in getattr(b, "content", None) or []:
                _add(getattr(r, "url", None))
        for cit in getattr(b, "citations", None) or []:
            _add(getattr(cit, "url", None))
    return seen

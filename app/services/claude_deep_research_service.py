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
CODE_EXEC_TOOL = {"type": "code_execution_20260120", "name": "code_execution"}
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


class ClaudeDeepResearchService:
    """Claude provider. Reuses the Gemini service's prompt builders verbatim
    (then de-Google'd) so prompt wording stays in one place."""

    def __init__(self):
        self.api_key = os.getenv("CLAUDE_API_KEY")
        self._client = None
        if not self.api_key:
            logger.warning("CLAUDE_API_KEY not set — ClaudeDeepResearchService disabled.")

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    # ---- Phase 1: multi-hop research loop ----------------------------------
    def _run_research(self, prompt: str) -> Dict[str, Any]:
        """Run the agentic web-research loop. Returns:
        {transcript_text, source_urls, thinking, usage, search_count, latency_s}.
        Claude searches -> reads -> fetches -> re-plans across hops; the server-side
        tool loop pauses (pause_turn) and we resume up to MAX_CONTINUATIONS times."""
        messages = [{
            "role": "user",
            "content": [{
                "type": "text",
                "text": prompt,
                "cache_control": {"type": "ephemeral"},
            }],
        }]
        # code_execution MUST be present for web_search_20260209 dynamic filtering.
        tools = [WEB_SEARCH_TOOL, WEB_FETCH_TOOL, CODE_EXEC_TOOL]

        all_blocks: List[Any] = []
        thinking_chunks: List[str] = []
        usage_in = usage_out = cache_read = cache_write = 0
        search_count = 0
        start = time.time()

        for _ in range(MAX_CONTINUATIONS):
            with self.client.messages.stream(
                model=MODEL,
                max_tokens=RESEARCH_MAX_TOKENS,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": "high"},
                tools=tools,
                messages=messages,
            ) as stream:
                resp = stream.get_final_message()

            u = resp.usage
            usage_in += getattr(u, "input_tokens", 0) or 0
            usage_out += getattr(u, "output_tokens", 0) or 0
            cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
            cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0

            for b in resp.content:
                all_blocks.append(b)
                if getattr(b, "type", None) == "thinking":
                    thinking_chunks.append(getattr(b, "thinking", "") or "")
                if getattr(b, "type", None) == "server_tool_use" and getattr(b, "name", "") == "web_search":
                    search_count += 1

            if resp.stop_reason == "pause_turn":
                # Resume the server-side tool loop: re-send the assistant turn
                # verbatim. Do NOT append a user "Continue." message — the API
                # auto-resumes from the trailing server_tool_use block.
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break  # end_turn / max_tokens / refusal

        if getattr(resp, "stop_reason", None) == "pause_turn":
            logger.warning(
                "[Claude DR] research hit MAX_CONTINUATIONS (%d) still paused — "
                "transcript may be truncated.", MAX_CONTINUATIONS)

        transcript = "\n".join(
            b.text for b in all_blocks if getattr(b, "type", None) == "text" and getattr(b, "text", None)
        )
        return {
            "transcript_text": transcript,
            "source_urls": _collect_source_urls(all_blocks),
            "thinking": "\n".join(thinking_chunks),
            "usage": {"in": usage_in, "out": usage_out,
                      "cache_read": cache_read, "cache_write": cache_write},
            "search_count": search_count,
            "latency_s": round(time.time() - start, 1),
        }

    # ---- Phase 2: structured synthesis (no tools) --------------------------
    def _synthesize(self, transcript: str, source_urls: List[str], schema: dict) -> Optional[Dict]:
        url_block = "\n".join(f"- {u}" for u in source_urls) or "(no sources fetched)"
        synth_prompt = (
            "Below is your own research transcript on this stock. Convert your "
            "findings into the required JSON. For every entry in "
            "verification_results, the source_url MUST be copied verbatim from "
            "the AVAILABLE SOURCES list — do not invent URLs; omit any claim you "
            "cannot ground in that list.\n\n"
            f"AVAILABLE SOURCES:\n{url_block}\n\n"
            f"RESEARCH TRANSCRIPT:\n{transcript[:120000]}"
        )
        with self.client.messages.stream(
            model=MODEL,
            max_tokens=SYNTHESIS_MAX_TOKENS,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": synth_prompt}],
        ) as stream:
            resp = stream.get_final_message()
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error("[Claude DR] synthesis JSON parse failed: %.300s", text)
            return None

    def _record_cost(self, decision_id, symbol, stage, research, synth_usage):
        if decision_id is None:
            return
        try:
            from app.services.token_tracker import record_llm_call
            tokens_in = research["usage"]["in"] + synth_usage.get("in", 0)
            tokens_out = research["usage"]["out"] + synth_usage.get("out", 0)
            record_llm_call(
                decision_id=decision_id, ticker=symbol,
                run_date=datetime.now().strftime("%Y-%m-%d"),
                stage=stage, agent_name="claude_deep_research", model=MODEL,
                tokens_in=tokens_in, tokens_out=tokens_out,
            )
        except Exception as e:
            logger.warning("[Claude DR] cost record failed for %s: %s", symbol, e)

    # ---- Public interface (mirrors DeepResearchService) --------------------
    def execute_deep_research(self, symbol: str, context: dict,
                              decision_id: int = None) -> Optional[Dict]:
        from app.services.deep_research_service import deep_research_service
        try:
            prompt = _deglooglify(deep_research_service._construct_prompt(symbol, context))
            research = self._run_research(prompt)
            if not research["transcript_text"].strip():
                logger.warning("[Claude DR] empty research transcript for %s — skipping synthesis.", symbol)
                return None
            result = self._synthesize(research["transcript_text"],
                                      research["source_urls"], INDIVIDUAL_SCHEMA)
            if result is None:
                return None
            self._record_cost(decision_id, symbol, "deep_research", research, {})
            result["_claude_research_meta"] = {
                "source_urls": research["source_urls"],
                "search_count": research["search_count"],
                "latency_s": research["latency_s"],
                "thinking": research["thinking"],
                "usage": research["usage"],
            }
            return result
        except Exception as e:
            logger.error("[Claude DR] execute_deep_research failed for %s: %s", symbol, e)
            return None

    def execute_sell_reassessment(self, symbol: str, context: dict,
                                  decision_id: int = None) -> Optional[Dict]:
        from app.services.deep_research_service import deep_research_service
        try:
            prompt = _deglooglify(
                deep_research_service._construct_sell_reassessment_prompt(symbol, context))
            research = self._run_research(prompt)
            if not research["transcript_text"].strip():
                logger.warning("[Claude DR] empty sell-research transcript for %s — skipping synthesis.", symbol)
                return None
            result = self._synthesize(research["transcript_text"],
                                      research["source_urls"], SELL_SCHEMA)
            if result is None:
                return None
            self._record_cost(decision_id, symbol, "sell_reassessment", research, {})
            return result
        except Exception as e:
            logger.error("[Claude DR] execute_sell_reassessment failed for %s: %s", symbol, e)
            return None

    def execute_batch_comparison(self, candidates: List[Dict], batch_id=None):
        # Batch comparison stays on Gemini for now (low priority, no trading-level
        # overrides). Routed to Claude only if explicitly enabled later.
        raise NotImplementedError("Claude batch comparison not enabled; use Gemini.")


claude_deep_research_service = ClaudeDeepResearchService()

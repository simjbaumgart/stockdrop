# Claude Opus 4.8 Deep Research Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Claude Opus 4.8 (with an autonomous multi-hop web-research loop) as an opt-in alternative provider for Deep Research, run live-pipeline only, default to Gemini, and validate it via a verbose shadow comparison over the 15 most recent decisions.

**Architecture:** A new `ClaudeDeepResearchService` exposes the same three `execute_*` methods as the existing Gemini `DeepResearchService` and returns the **identical result-dict shape**, so the queue/worker/dedup/DB/PDF machinery is untouched. It runs a two-phase flow: (1) an agentic **research loop** using Anthropic's server-side `web_search_20260209` + `web_fetch_20260209` tools — Claude searches, reads, fetches full pages, and re-plans across many hops (server-side `pause_turn` continuations + an outer loop), accumulating real source URLs; (2) a **synthesis call** with no tools using `output_config.format` to emit the exact result JSON, fed the real URLs so `verification_results` are grounded. The live worker routes to Claude when `DEEP_RESEARCH_PROVIDER=claude`; default stays Gemini. The backfill path is explicitly out of scope (see `memory/no-backfilling-deep-research.md`).

**Tech Stack:** Python 3.9, `anthropic` SDK (model `claude-opus-4-8`), adaptive thinking, server-side web tools, structured outputs, prompt caching; SQLite; pytest + pytest-asyncio.

---

## Key API facts (from claude-api skill, cached 2026-05-26)

- Client: `anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))` — SDK default env is `ANTHROPIC_API_KEY`, so pass the key explicitly.
- Model: `claude-opus-4-8`. Thinking: `thinking={"type":"adaptive","display":"summarized"}` (summarized so we can capture reasoning in the shadow). Effort: `output_config={"effort":"high"}`.
- Web tools: `{"type":"web_search_20260209","name":"web_search"}` and `{"type":"web_fetch_20260209","name":"web_fetch"}` — server-side, dynamic filtering built in, **no beta header**.
- Server tool loop hits its cap → `stop_reason:"pause_turn"`; **re-send** `user` + `assistant(response.content)` to resume (do NOT inject a "continue" message). Cap continuations.
- Structured output is **incompatible with citations** → cannot be combined with web search in one call. Use a separate tool-free synthesis call with `output_config={"format":{"type":"json_schema","schema":{...}}}`.
- Large `max_tokens` (>~16K) **must stream**: use `client.messages.stream(...)` + `stream.get_final_message()`.
- Usage: `resp.usage.input_tokens / output_tokens / cache_read_input_tokens / cache_creation_input_tokens`. Server tool use (search count) appears as `server_tool_use` blocks / usage — capture for cost.
- Prompt caching: put `cache_control:{"type":"ephemeral"}` on the large static context block; min cacheable prefix on Opus 4.8 is 4096 tokens.

---

## File Structure

- **Create** `app/services/claude_deep_research_service.py` — `ClaudeDeepResearchService` + module singleton `claude_deep_research_service`. Owns the research loop, synthesis, schema, URL collection, cost recording. Returns the same result dicts as the Gemini service.
- **Create** `app/services/deep_research_schemas.py` — the JSON Schemas for the individual / sell / batch synthesis outputs (shared, single source of truth).
- **Modify** `app/services/deep_research_service.py` — top-of-method provider routing in `execute_deep_research`, `execute_sell_reassessment`, `execute_batch_comparison`.
- **Modify** `app/services/token_pricing.py` — add `CLAUDE_PRICING` and make `compute_cost` consult it.
- **Modify** `requirements.txt` — add `anthropic`.
- **Modify** `.env.example` — add `CLAUDE_API_KEY=` and `DEEP_RESEARCH_PROVIDER=gemini`.
- **Create** `scripts/analysis/claude_deep_research_shadow.py` — verbose 15-decision shadow eval (one-off, not operational backfill).
- **Create** `tests/test_claude_deep_research_service.py` — unit tests (pure helpers) + opt-in live integration test.
- **Create** `tests/test_deep_research_provider_routing.py` — routing unit tests.

---

## Task 1: Dependency, key, and config

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`

- [ ] **Step 1: Add the SDK to requirements**

Append to `requirements.txt`:

```
anthropic==0.92.0
```

- [ ] **Step 2: Verify install**

Run: `pip install -r requirements.txt`
Expected: `anthropic` installs; `python -c "import anthropic; print(anthropic.__version__)"` prints `0.92.0` or newer.

- [ ] **Step 3: Document env vars**

In `.env.example`, under the Gemini block, add:

```
# Anthropic Claude (alternative Deep Research provider)
CLAUDE_API_KEY=
# Deep Research provider for the LIVE pipeline: "gemini" (default) or "claude"
DEEP_RESEARCH_PROVIDER=gemini
```

(The real `CLAUDE_API_KEY` is already set in the user's `.env`.)

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .env.example
git commit -m "feat(deep-research): add anthropic dep + CLAUDE_API_KEY/DEEP_RESEARCH_PROVIDER config"
```

---

## Task 2: Synthesis JSON Schemas

**Files:**
- Create: `app/services/deep_research_schemas.py`
- Test: `tests/test_claude_deep_research_service.py`

The schema must mirror the keys the Gemini path returns (so `_handle_completion` works unchanged). Structured-output JSON Schema constraints: every object needs `additionalProperties: false`, no `minimum`/`maximum`/`minLength`, no recursion.

- [ ] **Step 1: Write the failing test**

Create `tests/test_claude_deep_research_service.py`:

```python
from app.services.deep_research_schemas import INDIVIDUAL_SCHEMA, SELL_SCHEMA, BATCH_SCHEMA


def test_individual_schema_has_required_result_keys():
    props = INDIVIDUAL_SCHEMA["properties"]
    for key in (
        "review_verdict", "action", "conviction", "entry_price_low",
        "entry_price_high", "stop_loss", "verification_results",
        "swot_analysis", "knife_catch_warning", "reason",
        "sell_price_low", "sell_price_high", "ceiling_exit", "exit_trigger",
    ):
        assert key in props, f"missing {key}"
    assert INDIVIDUAL_SCHEMA["additionalProperties"] is False


def test_verification_results_items_carry_source_url():
    item = INDIVIDUAL_SCHEMA["properties"]["verification_results"]["items"]
    assert set(item["required"]) >= {"claim", "verdict", "source_url"}
    assert item["additionalProperties"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_claude_deep_research_service.py -k schema -v`
Expected: FAIL with `ModuleNotFoundError: app.services.deep_research_schemas`

- [ ] **Step 3: Write the schemas**

Create `app/services/deep_research_schemas.py`:

```python
"""JSON Schemas for Claude structured-output synthesis of Deep Research results.

Keys mirror what the Gemini DeepResearchService returns so the shared
_handle_completion / DB-write path works unchanged.
"""

_STR = {"type": "string"}
_NUM = {"type": "number"}
_NUM_OR_NULL = {"type": ["number", "null"]}

_SWOT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "strengths": {"type": "array", "items": _STR},
        "weaknesses": {"type": "array", "items": _STR},
        "opportunities": {"type": "array", "items": _STR},
        "threats": {"type": "array", "items": _STR},
    },
    "required": ["strengths", "weaknesses", "opportunities", "threats"],
}

_VERIFICATION_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claim": _STR,
        "verdict": {"type": "string", "enum": ["VERIFIED", "DISPUTED"]},
        "source_url": _STR,
    },
    "required": ["claim", "verdict", "source_url"],
}

INDIVIDUAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "review_verdict": {"type": "string",
                           "enum": ["CONFIRMED", "UPGRADED", "ADJUSTED", "OVERRIDDEN"]},
        "action": {"type": "string", "enum": ["BUY", "BUY_LIMIT", "WATCH", "AVOID"]},
        "conviction": {"type": "string", "enum": ["HIGH", "MODERATE", "LOW"]},
        "drop_type": _STR,
        "risk_level": {"type": "string", "enum": ["Low", "Medium", "High", "Extreme"]},
        "catalyst_type": {"type": "string", "enum": ["Structural", "Temporary", "Noise"]},
        "entry_price_low": _NUM,
        "entry_price_high": _NUM,
        "stop_loss": _NUM,
        "take_profit_1": _NUM,
        "take_profit_2": _NUM_OR_NULL,
        "upside_percent": _NUM,
        "downside_risk_percent": _NUM,
        "risk_reward_ratio": _NUM,
        "pre_drop_price": _NUM,
        "entry_trigger": _STR,
        "reassess_in_days": _NUM,
        "sell_price_low": _NUM,
        "sell_price_high": _NUM,
        "ceiling_exit": _NUM,
        "exit_trigger": _STR,
        "global_market_analysis": _STR,
        "local_market_analysis": _STR,
        "swot_analysis": _SWOT,
        "verification_results": {"type": "array", "items": _VERIFICATION_ITEM},
        "council_blindspots": {"type": "array", "items": _STR},
        "knife_catch_warning": {"type": "boolean"},
        "reason": _STR,
    },
    "required": [
        "review_verdict", "action", "conviction", "drop_type", "risk_level",
        "catalyst_type", "entry_price_low", "entry_price_high", "stop_loss",
        "take_profit_1", "upside_percent", "downside_risk_percent",
        "risk_reward_ratio", "entry_trigger", "reassess_in_days",
        "sell_price_low", "sell_price_high", "ceiling_exit", "exit_trigger",
        "global_market_analysis", "local_market_analysis", "swot_analysis",
        "verification_results", "council_blindspots", "knife_catch_warning", "reason",
    ],
}

SELL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "thesis_status": {"type": "string", "enum": ["INTACT", "WEAKENING", "BROKEN"]},
        "sell_action": {"type": "string",
                        "enum": ["HOLD", "SELL_PARTIAL", "SELL_FULL", "TIGHTEN_STOP"]},
        "updated_sell_price_low": _NUM,
        "updated_sell_price_high": _NUM,
        "updated_ceiling_exit": _NUM,
        "updated_stop_loss": _NUM_OR_NULL,
        "exit_trigger": _STR,
        "next_reassess_in_days": _NUM,
        "thesis_reasoning": _STR,
        "action_reasoning": _STR,
        "key_observations": {"type": "array", "items": _STR},
    },
    "required": [
        "thesis_status", "sell_action", "updated_sell_price_low",
        "updated_sell_price_high", "updated_ceiling_exit", "exit_trigger",
        "next_reassess_in_days", "thesis_reasoning", "action_reasoning",
        "key_observations",
    ],
}

BATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "winner_symbol": _STR,
        "rationale": _STR,
        "projected_timeline": _STR,
        "ranking": {"type": "array", "items": _STR},
    },
    "required": ["winner_symbol", "rationale", "projected_timeline", "ranking"],
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_claude_deep_research_service.py -k schema -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/deep_research_schemas.py tests/test_claude_deep_research_service.py
git commit -m "feat(deep-research): add Claude synthesis JSON schemas"
```

---

## Task 3: ClaudeDeepResearchService — pure helpers (TDD)

Build the testable, API-free helpers first: prompt de-Google'ing, source-URL collection from a response, and cost computation.

**Files:**
- Create: `app/services/claude_deep_research_service.py`
- Test: `tests/test_claude_deep_research_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_deep_research_service.py`:

```python
from app.services.claude_deep_research_service import (
    _deglooglify, _collect_source_urls,
)


def test_deglooglify_replaces_google_search_references():
    src = "Verify their key claims using fresh Google Search data."
    out = _deglooglify(src)
    assert "Google Search" not in out
    assert "web search" in out


class _FakeBlock:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.type = kw.get("type")


def test_collect_source_urls_pulls_from_web_tool_results_and_citations():
    # web_search result block carrying a list of results with urls
    search_block = _FakeBlock(
        type="web_search_tool_result",
        content=[_FakeBlock(type="web_search_result",
                            url="https://example.com/a", title="A")],
    )
    # a text block carrying a citation with a url
    cited = _FakeBlock(
        type="text", text="x",
        citations=[_FakeBlock(type="web_search_result_location",
                              url="https://example.com/b")],
    )
    urls = _collect_source_urls([search_block, cited])
    assert urls == ["https://example.com/a", "https://example.com/b"]


def test_collect_source_urls_dedupes_and_skips_nonhttp():
    blocks = [
        _FakeBlock(type="web_search_tool_result",
                   content=[_FakeBlock(type="web_search_result", url="https://x.com/1")]),
        _FakeBlock(type="web_search_tool_result",
                   content=[_FakeBlock(type="web_search_result", url="https://x.com/1")]),
        _FakeBlock(type="web_search_tool_result",
                   content=[_FakeBlock(type="web_search_result", url="ftp://nope")]),
    ]
    assert _collect_source_urls(blocks) == ["https://x.com/1"]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_claude_deep_research_service.py -k "deglooglify or collect_source" -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError`.

- [ ] **Step 3: Implement the module skeleton + helpers**

Create `app/services/claude_deep_research_service.py`:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_claude_deep_research_service.py -k "deglooglify or collect_source" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/claude_deep_research_service.py tests/test_claude_deep_research_service.py
git commit -m "feat(deep-research): Claude service skeleton + pure helpers (TDD)"
```

---

## Task 4: Cost recording for Claude

**Files:**
- Modify: `app/services/token_pricing.py`
- Test: `tests/test_token_pricing_claude.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_token_pricing_claude.py`:

```python
from app.services.token_pricing import compute_cost


def test_claude_opus_cost_known_rates():
    # $5/1M in, $25/1M out
    cost = compute_cost("claude-opus-4-8", 1_000_000, 1_000_000)
    assert abs(cost - 30.0) < 1e-6


def test_unknown_model_returns_none():
    assert compute_cost("nope-model", 10, 10) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_token_pricing_claude.py -v`
Expected: FAIL — `compute_cost("claude-opus-4-8", ...)` returns `None` (not yet in any table).

- [ ] **Step 3: Add the Claude table and consult it**

In `app/services/token_pricing.py`, after the `GEMINI_PRICING` dict add:

```python
# USD per 1M tokens. Opus 4.8 published rate (claude-api skill, cached 2026-05-26).
CLAUDE_PRICING = {
    "claude-opus-4-8":  {"in": 5.0, "out": 25.0},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0},
}
# Web search billed separately, per 1,000 searches. VERIFY against current rate card.
CLAUDE_WEB_SEARCH_USD_PER_1K = 10.0  # TODO verify
```

Then change the lookup in `compute_cost` from:

```python
    rates = GEMINI_PRICING.get(model)
```

to:

```python
    rates = GEMINI_PRICING.get(model) or CLAUDE_PRICING.get(model)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_token_pricing_claude.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/token_pricing.py tests/test_token_pricing_claude.py
git commit -m "feat(deep-research): add Claude pricing to compute_cost"
```

---

## Task 5: The research loop + synthesis (core)

**Files:**
- Modify: `app/services/claude_deep_research_service.py`
- Test: `tests/test_claude_deep_research_service.py` (live, opt-in)

Implement the `ClaudeDeepResearchService` class with the multi-hop research loop, the synthesis call, and the three `execute_*` methods returning the Gemini-compatible result dict.

- [ ] **Step 1: Implement the class**

Append to `app/services/claude_deep_research_service.py`:

```python
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
        Claude searches → reads → fetches → re-plans across hops; the server-side
        tool loop pauses (pause_turn) and we resume up to MAX_CONTINUATIONS times."""
        import anthropic  # noqa

        messages = [{
            "role": "user",
            "content": [{
                "type": "text",
                "text": prompt,
                "cache_control": {"type": "ephemeral"},
            }],
        }]
        tools = [WEB_SEARCH_TOOL, WEB_FETCH_TOOL]

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
                # Resume the server-side tool loop: re-send assistant turn verbatim.
                messages.append({"role": "assistant", "content": resp.content})
                messages.append({"role": "user", "content": "Continue."})
                continue
            break  # end_turn / max_tokens / refusal

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
        prompt = _deglooglify(deep_research_service._construct_prompt(symbol, context))
        research = self._run_research(prompt)
        result = self._synthesize(research["transcript_text"],
                                  research["source_urls"], INDIVIDUAL_SCHEMA)
        if result is None:
            return None
        synth_usage = {}  # _synthesize usage folded into research cost approximation
        self._record_cost(decision_id, symbol, "deep_research", research, synth_usage)
        result["_claude_research_meta"] = {
            "source_urls": research["source_urls"],
            "search_count": research["search_count"],
            "latency_s": research["latency_s"],
            "thinking": research["thinking"],
            "usage": research["usage"],
        }
        return result

    def execute_sell_reassessment(self, symbol: str, context: dict,
                                  decision_id: int = None) -> Optional[Dict]:
        from app.services.deep_research_service import deep_research_service
        prompt = _deglooglify(
            deep_research_service._construct_sell_reassessment_prompt(symbol, context))
        research = self._run_research(prompt)
        result = self._synthesize(research["transcript_text"],
                                  research["source_urls"], SELL_SCHEMA)
        if result is None:
            return None
        self._record_cost(decision_id, symbol, "sell_reassessment", research, {})
        return result

    def execute_batch_comparison(self, candidates: List[Dict], batch_id=None):
        # Batch comparison delegates to the Gemini path for now (low priority,
        # no trading-level overrides). Routed only if explicitly enabled later.
        raise NotImplementedError("Claude batch comparison not enabled; use Gemini.")


claude_deep_research_service = ClaudeDeepResearchService()
```

> **Note on `_synthesize` usage:** the synthesis call's tokens are not separately threaded into `_record_cost` in this minimal version (folded as 0). If precise accounting matters, return `resp.usage` from `_synthesize` and add it. Flagged, not blocking.

- [ ] **Step 2: Write the opt-in live integration test**

Per CLAUDE.md ("integration tests should hit real APIs where feasible"), gate on the key + an explicit env flag so CI without credentials skips it.

Append to `tests/test_claude_deep_research_service.py`:

```python
import os
import pytest
from app.services.claude_deep_research_service import claude_deep_research_service

requires_live = pytest.mark.skipif(
    not (os.getenv("CLAUDE_API_KEY") and os.getenv("RUN_CLAUDE_LIVE_TESTS")),
    reason="set CLAUDE_API_KEY and RUN_CLAUDE_LIVE_TESTS=1 to run live Claude test",
)


@requires_live
def test_live_individual_research_returns_grounded_result():
    context = {
        "pm_decision": {"verdict": "BUY_LIMIT", "reason": "test"},
        "bull_case": "Test bull case.",
        "bear_case": "Test bear case.",
        "technical_data": {"rsi": 28, "price": 100.0},
        "drop_percent": -7.5,
        "raw_news": [],
    }
    result = claude_deep_research_service.execute_deep_research("AAPL", context)
    assert result is not None
    assert result["review_verdict"] in ("CONFIRMED", "UPGRADED", "ADJUSTED", "OVERRIDDEN")
    meta = result["_claude_research_meta"]
    assert meta["search_count"] >= 1, "expected at least one web search hop"
    for v in result["verification_results"]:
        if v.get("verdict") in ("VERIFIED", "DISPUTED"):
            assert v["source_url"].startswith("http")
```

- [ ] **Step 3: Run unit tests; run the live test manually**

Run (unit, always): `pytest tests/test_claude_deep_research_service.py -k "not live" -v`
Expected: PASS.

Run (live, manual): `RUN_CLAUDE_LIVE_TESTS=1 pytest tests/test_claude_deep_research_service.py -k live -v -s`
Expected: PASS; observe `search_count >= 1` and grounded `source_url`s. (Costs real money; minutes of latency.)

- [ ] **Step 4: Commit**

```bash
git add app/services/claude_deep_research_service.py tests/test_claude_deep_research_service.py
git commit -m "feat(deep-research): Claude multi-hop research loop + structured synthesis"
```

---

## Task 6: Live provider routing (env-var gated)

**Files:**
- Modify: `app/services/deep_research_service.py`
- Test: `tests/test_deep_research_provider_routing.py`

Route at the top of the two live `execute_*` methods. The worker/queue/dedup/`_handle_completion` are unchanged — they just receive a result dict.

- [ ] **Step 1: Write the failing test**

Create `tests/test_deep_research_provider_routing.py`:

```python
import os
from unittest import mock
from app.services.deep_research_service import deep_research_service


def test_routes_to_claude_when_provider_env_set():
    sentinel = {"review_verdict": "CONFIRMED", "action": "BUY"}
    with mock.patch.dict(os.environ, {"DEEP_RESEARCH_PROVIDER": "claude"}):
        with mock.patch(
            "app.services.claude_deep_research_service.claude_deep_research_service.execute_deep_research",
            return_value=sentinel,
        ) as claude_exec:
            out = deep_research_service.execute_deep_research("AAPL", {"drop_percent": -6}, 1)
    claude_exec.assert_called_once()
    assert out is sentinel


def test_defaults_to_gemini(monkeypatch):
    monkeypatch.delenv("DEEP_RESEARCH_PROVIDER", raising=False)
    # Force the Gemini HTTP path to short-circuit so we don't hit the network:
    with mock.patch("app.services.deep_research_service.requests.post",
                    side_effect=AssertionError("gemini path attempted (expected)")):
        try:
            deep_research_service.execute_deep_research("AAPL", {"drop_percent": -6}, 1)
        except AssertionError as e:
            assert "gemini path attempted" in str(e)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_deep_research_provider_routing.py -v`
Expected: FAIL — first test fails because no routing exists (Gemini path runs / returns None).

- [ ] **Step 3: Add routing**

In `app/services/deep_research_service.py`, add a helper near the top of the class (after `__init__`):

```python
    @staticmethod
    def _provider() -> str:
        return os.getenv("DEEP_RESEARCH_PROVIDER", "gemini").strip().lower()
```

At the **first line** of `execute_deep_research(self, symbol, context, decision_id=None)`:

```python
        if self._provider() == "claude":
            from app.services.claude_deep_research_service import claude_deep_research_service
            logger.info("[Deep Research] Routing %s to Claude provider.", symbol)
            return claude_deep_research_service.execute_deep_research(symbol, context, decision_id)
```

At the **first line** of `execute_sell_reassessment(self, symbol, context, decision_id=None)` (after the docstring):

```python
        if self._provider() == "claude":
            from app.services.claude_deep_research_service import claude_deep_research_service
            logger.info("[Deep Research Sell] Routing %s to Claude provider.", symbol)
            return claude_deep_research_service.execute_sell_reassessment(symbol, context, decision_id)
```

Leave `execute_batch_comparison` on Gemini (Claude batch is `NotImplementedError`).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_deep_research_provider_routing.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/deep_research_service.py tests/test_deep_research_provider_routing.py
git commit -m "feat(deep-research): env-gated live routing to Claude provider"
```

---

## Task 7: Verbose 15-decision shadow eval

**Files:**
- Create: `scripts/analysis/claude_deep_research_shadow.py`

A one-off comparison (NOT operational backfill): pull the 15 most recent `decision_points` that have a Gemini DR verdict, rebuild each context, run the Claude path, and dump everything — Gemini result, Claude result, full reasoning, every search query / source URL, token usage, estimated cost, and wall-clock latency.

- [ ] **Step 1: Confirm the columns/keys available**

Run: `python -c "import sqlite3,os; c=sqlite3.connect(os.getenv('DB_PATH','subscribers.db')); print([r[1] for r in c.execute('PRAGMA table_info(decision_points)')])"`
Expected: a column list including `deep_research_review_verdict`, `deep_research_action`, `deep_research_score`, `deep_research_reason`, and the trading-level columns. Confirm the exact name of the report/context JSON column (used to rebuild context).

- [ ] **Step 2: Write the script**

Create `scripts/analysis/claude_deep_research_shadow.py`:

```python
"""Verbose shadow: run the 15 most recent Gemini-DR'd decisions through the
Claude provider and dump a full side-by-side (reasoning, sources, cost, latency).

One-off eval — NOT operational backfill. Writes to data/claude_shadow/.

Usage:
    CLAUDE_API_KEY=... python scripts/analysis/claude_deep_research_shadow.py [--limit 15]
"""
import os
import sys
import json
import time
import argparse
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.claude_deep_research_service import claude_deep_research_service
from app.services.token_pricing import (
    compute_cost, CLAUDE_WEB_SEARCH_USD_PER_1K,
)

OUT_DIR = "data/claude_shadow"


def _recent_decisions(limit: int):
    """15 most recent decision_points carrying a Gemini DR verdict."""
    conn = sqlite3.connect(os.getenv("DB_PATH", "subscribers.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM decision_points
        WHERE deep_research_review_verdict IS NOT NULL
          AND deep_research_review_verdict != ''
        ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _rebuild_context(row: dict) -> dict:
    """Reconstruct the deep-research context from a stored decision row.

    Reuses StockService._build_deep_research_context where possible. If the
    stored report/raw JSON columns are present, parse and pass them; otherwise
    fall back to a minimal context from the row's own columns.
    """
    from app.services.stock_service import StockService
    svc = StockService.__new__(StockService)  # no full init needed for the builder
    # NOTE: adjust the column names below to the actual JSON columns confirmed
    # in Step 1 (e.g. 'report_data_json' / 'raw_data_json').
    report_data = json.loads(row.get("report_data_json") or "{}")
    raw_data = json.loads(row.get("raw_data_json") or "{}")
    if report_data:
        try:
            return svc._build_deep_research_context(report_data, raw_data)
        except Exception:
            pass
    return {
        "pm_decision": {"verdict": row.get("verdict"), "reason": row.get("reason", "")},
        "bull_case": row.get("bull_case", "Not available"),
        "bear_case": row.get("bear_case", "Not available"),
        "technical_data": {},
        "drop_percent": row.get("drop_percent", 0) or 0,
        "raw_news": [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15)
    args = ap.parse_args()

    if not os.getenv("CLAUDE_API_KEY"):
        print("CLAUDE_API_KEY not set."); sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    decisions = _recent_decisions(args.limit)
    print(f"Shadowing {len(decisions)} decisions through Claude...\n")

    summary = []
    for i, row in enumerate(decisions, 1):
        symbol = row["symbol"]
        print(f"[{i}/{len(decisions)}] {symbol} (decision {row['id']})...")
        context = _rebuild_context(row)
        t0 = time.time()
        try:
            # decision_id=None so the shadow never writes cost rows into prod tables
            claude = claude_deep_research_service.execute_deep_research(symbol, context, None)
        except Exception as e:
            print(f"  ERROR: {e}")
            claude = {"_error": str(e)}
        latency = round(time.time() - t0, 1)

        meta = (claude or {}).get("_claude_research_meta", {})
        usage = meta.get("usage", {})
        token_cost = compute_cost(MODEL_FALLBACK := "claude-opus-4-8",
                                  usage.get("in", 0), usage.get("out", 0)) or 0.0
        search_cost = (meta.get("search_count", 0) / 1000.0) * CLAUDE_WEB_SEARCH_USD_PER_1K
        est_cost = round(token_cost + search_cost, 4)

        record = {
            "decision_id": row["id"],
            "symbol": symbol,
            "gemini": {
                "review_verdict": row.get("deep_research_review_verdict"),
                "action": row.get("deep_research_action"),
                "score": row.get("deep_research_score"),
                "reason": row.get("deep_research_reason"),
                "entry_low": row.get("entry_price_low"),
                "entry_high": row.get("entry_price_high"),
                "stop_loss": row.get("stop_loss"),
            },
            "claude": {k: v for k, v in (claude or {}).items() if k != "_claude_research_meta"},
            "claude_research": {
                "source_urls": meta.get("source_urls", []),
                "search_count": meta.get("search_count"),
                "thinking": meta.get("thinking", ""),
                "usage": usage,
            },
            "cost_usd_est": est_cost,
            "latency_s": latency,
            "agree_verdict": (row.get("deep_research_review_verdict")
                              == (claude or {}).get("review_verdict")),
            "agree_action": (row.get("deep_research_action")
                             == (claude or {}).get("action")),
        }
        fname = os.path.join(OUT_DIR, f"shadow_{symbol}_{row['id']}.json")
        with open(fname, "w") as f:
            json.dump(record, f, indent=2)
        summary.append({k: record[k] for k in
                        ("decision_id", "symbol", "agree_verdict", "agree_action",
                         "cost_usd_est", "latency_s")})
        print(f"  verdict gemini={record['gemini']['review_verdict']} "
              f"claude={record['claude'].get('review_verdict')} "
              f"agree={record['agree_verdict']} "
              f"cost=${est_cost} {latency}s sources={len(meta.get('source_urls', []))}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(OUT_DIR, f"_summary_{stamp}.json"), "w") as f:
        json.dump(summary, f, indent=2)

    n = len(summary)
    v_agree = sum(1 for s in summary if s["agree_verdict"])
    a_agree = sum(1 for s in summary if s["agree_action"])
    total_cost = round(sum(s["cost_usd_est"] for s in summary), 2)
    print(f"\n=== SHADOW SUMMARY ({n} decisions) ===")
    print(f"Verdict agreement: {v_agree}/{n}   Action agreement: {a_agree}/{n}")
    print(f"Total est cost: ${total_cost}   Avg latency: "
          f"{round(sum(s['latency_s'] for s in summary)/max(n,1),1)}s")
    print(f"Per-decision JSON + summary in {OUT_DIR}/")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Wire the confirmed context columns**

Using the column names confirmed in Step 1, edit `_rebuild_context` so `report_data` / `raw_data` read the **actual** JSON columns. If those columns don't exist, leave the minimal fallback (the shadow still runs, with thinner context).

- [ ] **Step 4: Dry-run the selection (no API calls)**

Run: `python -c "from scripts.analysis.claude_deep_research_shadow import _recent_decisions; print([(d['id'],d['symbol']) for d in _recent_decisions(15)])"`
Expected: 15 `(id, symbol)` pairs, newest first.

- [ ] **Step 5: Run the shadow**

Run: `CLAUDE_API_KEY=$CLAUDE_API_KEY python scripts/analysis/claude_deep_research_shadow.py --limit 15`
Expected: 15 per-decision JSONs + a summary in `data/claude_shadow/`, and a printed agreement/cost/latency summary. (Real cost, ~minutes each.)

- [ ] **Step 6: Commit**

```bash
git add scripts/analysis/claude_deep_research_shadow.py
git commit -m "feat(deep-research): verbose 15-decision Claude shadow eval"
```

---

## Task 8: Manual live smoke test of the routed pipeline

**Files:** none (operational verification)

- [ ] **Step 1: Enable Claude for one live run**

Set in the environment (not committed): `DEEP_RESEARCH_PROVIDER=claude`, with `CLAUDE_API_KEY` already present.

- [ ] **Step 2: Trigger one individual analysis through the normal path**

Use the project's standard manual-validation route (CLAUDE.md: "run the pipeline against a known recent drop and verify the report structure"). Confirm in logs:
- `[Deep Research] Routing <SYM> to Claude provider.`
- The DR result reaches `_handle_completion`, writes `decision_points` (review_verdict/action/levels), and `_apply_trading_level_overrides` runs.
- `verification_results` contain `http` source URLs (not UNVERIFIED-only).

- [ ] **Step 3: Revert provider to default**

Unset `DEEP_RESEARCH_PROVIDER` (or set `gemini`). Confirm the next run logs the Gemini path.

- [ ] **Step 4: Final review checkpoint**

Use superpowers:requesting-code-review on the full diff before any merge.

---

## Self-Review

**Spec coverage:**
- "Explain the true autonomous multi-hop loop" → realized as the Task 5 research loop (server-side web tools + `pause_turn` continuations + `MAX_CONTINUATIONS`). ✅
- "From now on, no backfilling" → backfill explicitly out of scope; live routing is env-var only; recorded in memory. ✅
- "Do a shadow on 15 decisions" → Task 7, 15 most recent Gemini-DR'd decisions. ✅
- "Cost is ok" → cost captured (Task 4 pricing + shadow cost column) but not gated. ✅
- Verbose capture (reasoning, verdict, sources, cost, latency) → `_claude_research_meta` + shadow record. ✅
- Gemini stays default → routing defaults to gemini (Task 6). ✅
- API key via `.env` as `CLAUDE_API_KEY` → read explicitly (Task 1, Task 5). ✅

**Open items flagged (not blocking):**
- `CLAUDE_WEB_SEARCH_USD_PER_1K` value is a TODO — verify against current rate card before trusting shadow cost totals.
- `_synthesize` token usage is not folded into `_record_cost` (counts research-phase tokens only) — refine if precise accounting is needed.
- `_rebuild_context` JSON column names must be confirmed against the live schema in Task 7 Step 1.
- Claude batch comparison intentionally `NotImplementedError` — batch stays on Gemini.

**Type consistency:** `execute_deep_research` / `execute_sell_reassessment` signatures match the Gemini service; result dicts use the same keys `_handle_completion` reads; `_collect_source_urls` / `_deglooglify` names are consistent across module and tests.

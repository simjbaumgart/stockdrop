"""LLM judge for the News Agent shadow comparison.

Compares the production and shadow News Agent reports for each pair across the
accuracy dimensions that cannot be measured deterministically: source
classification, hard-event detection, and narrative coherence.
"""
import json
import os
import re
import time
from typing import Any, Dict, List

JUDGE_MODEL = "gemini-3.1-pro-preview"

JUDGE_INSTRUCTIONS = """You are a neutral evaluator comparing two News Agent reports for the same stock, same news data, same time window. Only the underlying model differs. Report A is the production model; Report B is the shadow model.

Evaluate across these dimensions:
1. source_classification — which report more accurately classifies cited articles as official / wire / analyst / opinion. Answer one of: "production_better", "shadow_better", "tie".
2. hard_event_detection — which report more correctly identifies event-driven catalysts (earnings, guidance cuts, M&A, legal/regulatory) versus rumor/sentiment moves. Answer one of: "production_better", "shadow_better", "tie".
3. production_coherence — does Report A's synthesis hang together without hallucinating connections unsupported by cited articles. Answer one of: "high", "medium", "low".
4. shadow_coherence — same judgement for Report B. Answer one of: "high", "medium", "low".
5. disagreements — a short string describing any specific discrepancy worth manual review (especially source misclassification or a missed/invented hard event). Empty string if none.

Respond ONLY with a JSON object with exactly these keys: source_classification, hard_event_detection, production_coherence, shadow_coherence, disagreements."""


def build_judge_prompt(production_report: str, shadow_report: str) -> str:
    return (
        JUDGE_INSTRUCTIONS
        + "\n\n=== REPORT A (production) ===\n"
        + (production_report or "")
        + "\n\n=== REPORT B (shadow) ===\n"
        + (shadow_report or "")
        + "\n\nReturn the JSON object now."
    )


def parse_judge_response(raw: str) -> Dict[str, Any]:
    """Extract the JSON object from the judge response, tolerant of code fences."""
    fallback = {
        "source_classification": "parse_error",
        "hard_event_detection": "parse_error",
        "production_coherence": "parse_error",
        "shadow_coherence": "parse_error",
        "disagreements": "judge response could not be parsed",
    }
    if not raw:
        return fallback
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return fallback
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return fallback
    for key in fallback:
        data.setdefault(key, "n/a")
    return data


def _make_client():
    """Create a Google GenAI client. Kept separate so tests can skip it."""
    from google import genai as new_genai
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    return new_genai.Client(api_key=api_key)


def judge_all_pairs(rows: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Judge every completed pair. Returns a dict keyed by row id."""
    client = _make_client()
    results: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        if not r.get("shadow_report") or r.get("shadow_error"):
            continue
        prompt = build_judge_prompt(r.get("production_report", ""),
                                    r.get("shadow_report", ""))
        try:
            response = client.models.generate_content(
                model=JUDGE_MODEL, contents=prompt
            )
            results[r["id"]] = parse_judge_response(getattr(response, "text", ""))
        except Exception as e:
            results[r["id"]] = parse_judge_response("")
            results[r["id"]]["disagreements"] = f"judge call failed: {e}"
        time.sleep(1)
    return results

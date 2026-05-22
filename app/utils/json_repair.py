"""Gemini Flash JSON-repair helper.

When an agent (Deep Research, Fund Manager) returns a malformed or truncated
report instead of clean JSON, this helper asks Gemini Flash to re-extract the
content into a schema-conformant JSON object. Returns the parsed dict, or
None on any failure so callers can fall back to their own abort path.
"""

import json
import logging
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

# 90s: repair-via-Flash needs more headroom than a normal Flash call — the
# prompt carries the full truncated report plus a schema, and a 30s cap was
# timing out in production (see 04-22 ADBE incident).
_REPAIR_TIMEOUT_SECONDS = 90
_REPAIR_MODEL_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)


def repair_json_via_flash(
    raw_text: str,
    schema_def: str,
    api_key: Optional[str],
    log_prefix: str = "[JSON Repair]",
    label: str = "output",
) -> Optional[Dict]:
    """Extract a schema-conformant JSON object from a malformed report.

    Args:
        raw_text: The malformed/truncated report content.
        schema_def: A JSON schema string the repaired output must match.
        api_key: Gemini API key. None disables repair (returns None).
        log_prefix: Caller tag for log lines (e.g. "[Fund Manager]").
        label: Short descriptor of what is being repaired, for logs.

    Returns:
        The parsed JSON dict, or None on any failure.
    """
    if not api_key:
        logger.warning("%s Repair skipped: no API key.", log_prefix)
        return None
    if not raw_text:
        return None

    try:
        logger.info(
            "%s Attempting to repair output (%s) using Gemini Flash...",
            log_prefix, label,
        )
        prompt = f"""
You are a data extraction assistant. I have a stock analysis report that is not in the required JSON format.
Please extract the relevant information and format it EXACTLY as this JSON object.
Do not include markdown formatting or code blocks around the JSON. Just return the raw JSON string.

SCHEMA:
{schema_def}

REPORT CONTENT:
{raw_text}
"""
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"},
        }
        response = requests.post(
            _REPAIR_MODEL_URL, headers=headers, json=payload,
            timeout=_REPAIR_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.error("%s Repair API Error: %s", log_prefix, response.text)
            return None

        data = response.json()
        if data.get("candidates"):
            repair_text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(repair_text)
        return None

    except Exception as e:
        logger.error("%s Repair failed: %s", log_prefix, e)
        return None

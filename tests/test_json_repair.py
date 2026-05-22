"""Unit tests for the Gemini Flash JSON-repair helper."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

from app.utils.json_repair import repair_json_via_flash

SCHEMA = '{"action": "BUY", "conviction": "HIGH"}'


def _mock_response(status=200, text='{"action": "BUY", "conviction": "HIGH"}'):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": text}]}}]
    }
    return resp


def test_returns_parsed_dict_on_success():
    with patch("app.utils.json_repair.requests.post", return_value=_mock_response()):
        out = repair_json_via_flash("truncated junk", SCHEMA, "fake-key")
    assert out == {"action": "BUY", "conviction": "HIGH"}


def test_returns_none_without_api_key():
    out = repair_json_via_flash("junk", SCHEMA, None)
    assert out is None


def test_returns_none_on_empty_text():
    out = repair_json_via_flash("", SCHEMA, "fake-key")
    assert out is None


def test_returns_none_on_http_error():
    with patch("app.utils.json_repair.requests.post", return_value=_mock_response(status=500)):
        out = repair_json_via_flash("junk", SCHEMA, "fake-key")
    assert out is None


def test_returns_none_on_exception():
    with patch("app.utils.json_repair.requests.post", side_effect=RuntimeError("boom")):
        out = repair_json_via_flash("junk", SCHEMA, "fake-key")
    assert out is None

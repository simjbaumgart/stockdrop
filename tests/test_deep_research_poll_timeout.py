"""Poll-loop resilience for execute_deep_research (Gemini provider).

Regression: 2026-05 AVGO individual DR polled toward the full 90-minute
ceiling (max_retries=360 x 15s) without ever reaching a terminal status,
monopolising the single DR worker and starving FIVE/FN/PUK behind it. Two
root causes:
  1. requests.post / requests.get had no `timeout=`, so a hung HTTP
     connection could block the worker thread indefinitely.
  2. The poll budget was an iteration count (360) with no wall-clock cap
     well below 90 min, so a task stuck in a non-terminal status occupied
     the worker for the full ceiling.

These tests pin: every HTTP call passes a timeout, the loop is bounded by a
short configurable per-task budget, and a single transient GET timeout is
retried (not fatal).
"""
import json

import pytest
import requests

from app.services.deep_research_service import DeepResearchService

VALID = {"review_verdict": "BUY", "action": "BUY", "conviction": "HIGH"}
VALID_JSON = json.dumps(VALID)


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _svc():
    svc = DeepResearchService.__new__(DeepResearchService)  # bypass __init__/network
    svc.api_key = "test-key"
    svc.base_url = "https://example.invalid/interactions"
    return svc


def _patch_common(monkeypatch):
    monkeypatch.setattr(
        "app.services.deep_research_service.time.sleep", lambda *_a, **_k: None
    )
    monkeypatch.setenv("DEEP_RESEARCH_PROVIDER", "gemini")


def test_never_terminal_task_is_abandoned_and_bounded(monkeypatch):
    """A task that never reaches a terminal status must return None after a
    short bounded number of polls (not 360), and every HTTP call must pass a
    timeout."""
    _patch_common(monkeypatch)
    monkeypatch.setenv("DR_TASK_TIMEOUT_SECONDS", "30")  # -> ~2 polls at 15s

    calls = {"post_timeout": "MISSING", "get_timeouts": []}

    def fake_post(url, headers=None, json=None, timeout="MISSING"):
        calls["post_timeout"] = timeout
        return _Resp({"id": "interaction-123"})

    def fake_get(url, headers=None, timeout="MISSING"):
        calls["get_timeouts"].append(timeout)
        return _Resp({"status": "in_progress"})

    monkeypatch.setattr("app.services.deep_research_service.requests.post", fake_post)
    monkeypatch.setattr("app.services.deep_research_service.requests.get", fake_get)

    svc = _svc()
    result = svc.execute_deep_research("AVGO", {}, decision_id=None)

    assert result is None, "non-terminal task must be abandoned, not return a result"
    assert calls["post_timeout"] != "MISSING", "POST must pass a timeout="
    assert calls["get_timeouts"], "GET must have been polled at least once"
    assert all(
        t != "MISSING" for t in calls["get_timeouts"]
    ), "every GET poll must pass a timeout="
    # Bounded by the short per-task budget, NOT the old 360-iteration ceiling.
    assert len(calls["get_timeouts"]) <= 5, (
        f"expected a handful of polls for a 30s budget, got {len(calls['get_timeouts'])}"
    )


def test_transient_get_timeout_is_retried_not_fatal(monkeypatch):
    """A single GET that raises Timeout must not abort the task — the loop
    should keep polling within budget and still capture a later completion."""
    _patch_common(monkeypatch)
    monkeypatch.setenv("DR_TASK_TIMEOUT_SECONDS", "120")

    state = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp({"id": "interaction-123"})

    def fake_get(url, headers=None, timeout=None):
        state["n"] += 1
        if state["n"] == 1:
            raise requests.exceptions.Timeout("slow poll")
        return _Resp({"status": "completed", "outputs": [{"text": VALID_JSON}]})

    monkeypatch.setattr("app.services.deep_research_service.requests.post", fake_post)
    monkeypatch.setattr("app.services.deep_research_service.requests.get", fake_get)

    svc = _svc()
    result = svc.execute_deep_research("AVGO", {}, decision_id=None)

    assert result == VALID, "task should recover after a transient poll timeout"
    assert state["n"] >= 2, "the second poll should have run after the timeout"

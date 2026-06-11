"""v0.8.2-288 review #5 (IT): vertexaisearch.cloud.google.com
grounding-api-redirect URLs were persisted to JSON/DB — they expire and are
unauditable. Resolve to the final URL before saving; keep the original on
any failure."""

import os
# KEEP this import-time redirect: importing deep_research_service below runs
# the singleton's batch-winner sync, which WRITES to the DB at import time —
# before the conftest autouse guard can intervene.
os.environ.setdefault("DB_PATH", "test_dr_redirect_resolution.db")

from types import SimpleNamespace

import app.services.deep_research_service as drs
from app.services.deep_research_service import resolve_redirect_urls

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc123"


def _entry(url):
    return {"claim": "c", "verdict": "VERIFIED", "source_url": url}


def test_redirect_resolved_and_original_kept(monkeypatch):
    def fake_get(url, allow_redirects, timeout, stream):
        assert url == REDIRECT
        return SimpleNamespace(url="https://www.reuters.com/article/real", close=lambda: None)

    monkeypatch.setattr(drs.requests, "get", fake_get)
    out = resolve_redirect_urls([_entry(REDIRECT)])
    assert out[0]["source_url"] == "https://www.reuters.com/article/real"
    assert out[0]["grounding_redirect"] == REDIRECT


def test_non_redirect_urls_untouched(monkeypatch):
    def fake_get(*a, **k):
        raise AssertionError("must not fetch non-redirect URLs")

    monkeypatch.setattr(drs.requests, "get", fake_get)
    out = resolve_redirect_urls([_entry("https://example.com/x")])
    assert out[0]["source_url"] == "https://example.com/x"
    assert "grounding_redirect" not in out[0]


def test_failure_keeps_original(monkeypatch):
    def fake_get(*a, **k):
        raise drs.requests.RequestException("boom")

    monkeypatch.setattr(drs.requests, "get", fake_get)
    out = resolve_redirect_urls([_entry(REDIRECT)])
    assert out[0]["source_url"] == REDIRECT


def test_lookup_budget_capped(monkeypatch):
    calls = []

    def fake_get(url, **k):
        calls.append(url)
        return SimpleNamespace(url="https://resolved.example/x", close=lambda: None)

    monkeypatch.setattr(drs.requests, "get", fake_get)
    entries = [_entry(REDIRECT) for _ in range(20)]
    resolve_redirect_urls(entries, max_lookups=5)
    assert len(calls) == 5  # the rest keep the redirect URL, bounded wall-clock


def teardown_module(module):
    try:
        os.remove("test_dr_redirect_resolution.db")
    except OSError:
        pass

"""Tests for SeekingAlpha _call_endpoint resilience.

Production behavior (post-fix):
  - Adds a 10s timeout to every request.
  - Retries once with 2s backoff on Timeout, ConnectionError, 429, and 5xx.
  - Does NOT retry on success-with-empty-body — that's a real "no data"
    signal and retrying would burn quota.
  - Logs the cause distinctly (timeout / 429 / 5xx / empty / 4xx) so we
    can see in logs whether RapidAPI is rate-limiting us or genuinely
    has no data.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("RAPIDAPI_KEY_SEEKING_ALPHA", "fake-key")
    from app.services.seeking_alpha_service import SeekingAlphaService
    return SeekingAlphaService()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("app.services.seeking_alpha_service.time.sleep", lambda *a, **kw: None)


def _ok_response(payload):
    r = MagicMock()
    r.status_code = 200
    r.text = '{"data": []}'
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def _empty_response(status=200):
    r = MagicMock()
    r.status_code = status
    r.text = ""
    r.json.return_value = None
    r.raise_for_status = MagicMock()
    return r


def _http_error_response(status):
    r = MagicMock()
    r.status_code = status
    r.text = "{}"
    err = requests.HTTPError(f"HTTP {status}")
    err.response = r
    r.raise_for_status.side_effect = err
    return r


class TestCallEndpointRetries:
    def test_returns_payload_on_first_success(self, svc):
        with patch("app.services.seeking_alpha_service.requests.get",
                   return_value=_ok_response({"data": [1]})) as g:
            result = svc._call_endpoint("foo", {"id": "AAPL"})
        assert result == {"data": [1]}
        assert g.call_count == 1

    def test_request_uses_explicit_timeout(self, svc):
        with patch("app.services.seeking_alpha_service.requests.get",
                   return_value=_ok_response({"data": []})) as g:
            svc._call_endpoint("foo")
        _, kwargs = g.call_args
        assert kwargs.get("timeout"), "request must specify a timeout"

    def test_retries_once_on_timeout_then_succeeds(self, svc):
        with patch("app.services.seeking_alpha_service.requests.get",
                   side_effect=[requests.Timeout("slow"),
                                _ok_response({"data": [1]})]) as g:
            result = svc._call_endpoint("foo")
        assert result == {"data": [1]}
        assert g.call_count == 2

    def test_retries_once_on_429_then_succeeds(self, svc):
        with patch("app.services.seeking_alpha_service.requests.get",
                   side_effect=[_http_error_response(429),
                                _ok_response({"data": [1]})]) as g:
            result = svc._call_endpoint("foo")
        assert result == {"data": [1]}
        assert g.call_count == 2

    def test_retries_once_on_503_then_succeeds(self, svc):
        with patch("app.services.seeking_alpha_service.requests.get",
                   side_effect=[_http_error_response(503),
                                _ok_response({"data": [1]})]) as g:
            result = svc._call_endpoint("foo")
        assert result == {"data": [1]}
        assert g.call_count == 2

    def test_does_not_retry_on_401(self, svc):
        with patch("app.services.seeking_alpha_service.requests.get",
                   return_value=_http_error_response(401)) as g:
            result = svc._call_endpoint("foo")
        assert result is None
        assert g.call_count == 1

    def test_does_not_retry_on_404(self, svc):
        with patch("app.services.seeking_alpha_service.requests.get",
                   return_value=_http_error_response(404)) as g:
            result = svc._call_endpoint("foo")
        assert result is None
        assert g.call_count == 1

    def test_does_not_retry_on_empty_body(self, svc):
        """Empty body with 200 OK is a real 'no data' answer — retrying burns quota."""
        with patch("app.services.seeking_alpha_service.requests.get",
                   return_value=_empty_response(200)) as g:
            result = svc._call_endpoint("foo")
        assert result is None
        assert g.call_count == 1

    def test_returns_none_after_two_failed_attempts(self, svc):
        with patch("app.services.seeking_alpha_service.requests.get",
                   side_effect=requests.Timeout("slow")) as g:
            result = svc._call_endpoint("foo")
        assert result is None
        assert g.call_count == 2

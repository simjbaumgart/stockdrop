import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.services.fred_service import FredService


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test_key")
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test_av")
    s = FredService()
    s._cache.clear()
    return s


def _obs(value="4.25", date="2026-04-20"):
    return {"observations": [{"value": value, "date": date}]}


class TestFredCache:
    def test_fresh_fetch_caches(self, svc):
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status = MagicMock()
            mget.return_value.json = MagicMock(return_value=_obs("4.25"))
            v, d = svc._fetch_latest_observation("DGS10")
            assert v == "4.25"
            assert "DGS10" in svc._cache

    def test_cache_hit_skips_http(self, svc):
        svc._cache["DGS10"] = {
            "value": "4.30",
            "date": "2026-04-20",
            "fetched_at": datetime.datetime.utcnow(),
            "stale": False,
        }
        with patch("app.services.fred_service.requests.get") as mget:
            v, d = svc._fetch_latest_observation("DGS10")
            assert v == "4.30"
            mget.assert_not_called()

    def test_500_serves_stale(self, svc):
        svc._cache["DGS10"] = {
            "value": "4.10",
            "date": "2026-04-19",
            "fetched_at": datetime.datetime.utcnow() - datetime.timedelta(hours=25),
            "stale": False,
        }
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status.side_effect = Exception("500")
            v, d = svc._fetch_latest_observation("DGS10")
            assert v == "4.10"
            assert svc._cache["DGS10"]["stale"] is True

    def test_500_no_cache_falls_back_to_alpha_vantage_for_yields(self, svc):
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status.side_effect = Exception("500")
            with patch.object(svc, "_fetch_av_treasury_yield", return_value=("4.27", "2026-04-20")) as fav:
                v, d = svc._fetch_latest_observation("DGS10")
                assert v == "4.27"
                fav.assert_called_once_with("10year")

    def test_500_no_cache_no_fallback_for_non_yield_series(self, svc):
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status.side_effect = Exception("500")
            v, d = svc._fetch_latest_observation("GDP")
            assert v == "N/A"
            assert d == "N/A"

    def test_staleness_flag_in_get_macro_data(self, svc):
        svc._cache["DGS10"] = {
            "value": "4.10",
            "date": "2026-04-19",
            "fetched_at": datetime.datetime.utcnow() - datetime.timedelta(hours=25),
            "stale": True,
        }
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status.side_effect = Exception("500")
            data = svc.get_macro_data()
            assert data["10Y Treasury Yield"].get("stale") is True


class TestFredPersistentSnapshot:
    """The in-memory cache evaporates on restart. A JSON snapshot on disk lets a
    fresh process serve last-known-good values when FRED is unreachable."""

    def test_successful_fetch_writes_snapshot(self, svc, tmp_path, monkeypatch):
        snap = tmp_path / "fred_snapshot.json"
        monkeypatch.setattr("app.services.fred_service._SNAPSHOT_PATH", str(snap))
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status = MagicMock()
            mget.return_value.json = MagicMock(return_value=_obs("4.25", "2026-04-20"))
            svc._fetch_latest_observation("UNRATE")
        assert snap.exists()
        import json
        body = json.loads(snap.read_text())
        assert body["UNRATE"]["value"] == "4.25"
        assert body["UNRATE"]["date"] == "2026-04-20"

    def test_snapshot_loaded_on_init_as_stale(self, tmp_path, monkeypatch):
        import json
        snap = tmp_path / "fred_snapshot.json"
        snap.write_text(json.dumps({
            "UNRATE": {"value": "4.20", "date": "2026-04-15"},
            "GDP": {"value": "30000", "date": "2026-03-31"},
        }))
        monkeypatch.setenv("FRED_API_KEY", "test_key")
        monkeypatch.setattr("app.services.fred_service._SNAPSHOT_PATH", str(snap))
        s = FredService()
        assert "UNRATE" in s._cache
        assert s._cache["UNRATE"]["value"] == "4.20"
        # Loaded entries are stale by default — they came from a prior process.
        assert s._cache["UNRATE"]["stale"] is True

    def test_persistent_fallback_serves_stale_after_failed_first_fetch(
        self, tmp_path, monkeypatch
    ):
        """Fresh process + FRED down + snapshot present → returns stale snapshot value."""
        import json
        snap = tmp_path / "fred_snapshot.json"
        snap.write_text(json.dumps({
            "UNRATE": {"value": "4.20", "date": "2026-04-15"},
        }))
        monkeypatch.setenv("FRED_API_KEY", "test_key")
        monkeypatch.setattr("app.services.fred_service._SNAPSHOT_PATH", str(snap))
        s = FredService()
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status.side_effect = Exception("500")
            v, d = s._fetch_latest_observation("UNRATE")
        assert v == "4.20"
        assert d == "2026-04-15"

    def test_missing_snapshot_does_not_crash(self, tmp_path, monkeypatch):
        snap = tmp_path / "does_not_exist.json"
        monkeypatch.setenv("FRED_API_KEY", "test_key")
        monkeypatch.setattr("app.services.fred_service._SNAPSHOT_PATH", str(snap))
        s = FredService()  # must not raise
        assert s._cache == {}

    def test_corrupt_snapshot_does_not_crash(self, tmp_path, monkeypatch):
        snap = tmp_path / "snapshot.json"
        snap.write_text("{not json")
        monkeypatch.setenv("FRED_API_KEY", "test_key")
        monkeypatch.setattr("app.services.fred_service._SNAPSHOT_PATH", str(snap))
        s = FredService()  # must not raise
        assert s._cache == {}

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

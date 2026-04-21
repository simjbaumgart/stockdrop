import os
import datetime
import logging
import requests
from typing import Dict, Any, Optional, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FredService:
    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
    AV_URL = "https://www.alphavantage.co/query"

    CACHE_TTL = datetime.timedelta(hours=24)

    _AV_YIELD_MAP = {
        "DGS10": "10year",
        "DGS2": "2year",
    }

    def __init__(self):
        self.api_key = os.getenv("FRED_API_KEY")
        self.av_api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get_macro_data(self) -> Dict[str, Any]:
        if not self.api_key:
            logger.warning("FRED_API_KEY not found. Returning empty macro data.")
            return {}

        indicators = {
            "Unemployment Rate": "UNRATE",
            "CPI (Inflation)": "CPIAUCSL",
            "Fed Funds Rate": "FEDFUNDS",
            "GDP": "GDP",
            "10Y Treasury Yield": "DGS10",
            "2Y Treasury Yield": "DGS2",
        }

        data: Dict[str, Any] = {}
        for name, series_id in indicators.items():
            try:
                value, date = self._fetch_latest_observation(series_id)
                entry: Dict[str, Any] = {"value": value, "date": date}
                cached = self._cache.get(series_id)
                if cached and cached.get("stale"):
                    entry["stale"] = True
                data[name] = entry
            except Exception as e:
                logger.error(f"Error fetching {name} ({series_id}): {e}")
                data[name] = {"value": "N/A", "date": "N/A"}

        try:
            ten_y = float(data["10Y Treasury Yield"]["value"])
            two_y = float(data["2Y Treasury Yield"]["value"])
            data["10Y-2Y Spread"] = {
                "value": f"{ten_y - two_y:.2f}",
                "date": data["10Y Treasury Yield"]["date"],
            }
        except (ValueError, KeyError):
            data["10Y-2Y Spread"] = {"value": "N/A", "date": "N/A"}

        return data

    def _fetch_latest_observation(self, series_id: str) -> Tuple[str, str]:
        cached = self._cache.get(series_id)
        now = datetime.datetime.utcnow()
        if cached and not cached.get("stale"):
            age = now - cached["fetched_at"]
            if age < self.CACHE_TTL:
                return cached["value"], cached["date"]

        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1,
        }

        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            json_data = response.json()
            observations = json_data.get("observations", [])
            if observations:
                latest = observations[0]
                value = latest.get("value", "N/A")
                date = latest.get("date", "N/A")
                self._cache[series_id] = {
                    "value": value,
                    "date": date,
                    "fetched_at": now,
                    "stale": False,
                }
                return value, date
            return "N/A", "N/A"

        except Exception as e:
            logger.warning(f"FRED fetch failed for {series_id}: {e}")
            if cached:
                cached["stale"] = True
                self._cache[series_id] = cached
                logger.info(f"Serving stale value for {series_id} (age: {now - cached['fetched_at']})")
                return cached["value"], cached["date"]
            if series_id in self._AV_YIELD_MAP and self.av_api_key:
                av = self._fetch_av_treasury_yield(self._AV_YIELD_MAP[series_id])
                if av:
                    value, date = av
                    self._cache[series_id] = {
                        "value": value,
                        "date": date,
                        "fetched_at": now,
                        "stale": False,
                    }
                    return value, date
            return "N/A", "N/A"

    def _fetch_av_treasury_yield(self, maturity: str) -> Optional[Tuple[str, str]]:
        try:
            params = {
                "function": "TREASURY_YIELD",
                "interval": "daily",
                "maturity": maturity,
                "apikey": self.av_api_key,
            }
            r = requests.get(self.AV_URL, params=params, timeout=10)
            r.raise_for_status()
            data = r.json().get("data", [])
            if data:
                return data[0]["value"], data[0]["date"]
        except Exception as e:
            logger.warning(f"Alpha Vantage treasury fallback failed for {maturity}: {e}")
        return None


fred_service = FredService()

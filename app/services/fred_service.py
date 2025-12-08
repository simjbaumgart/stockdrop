import requests
import os
import logging
from typing import Dict, Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FredService:
    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
    
    def __init__(self):
        self.api_key = os.getenv("FRED_API_KEY")
        
    def get_macro_data(self) -> Dict[str, Any]:
        """
        Fetches key macroeconomic indicators from FRED.
        """
        if not self.api_key:
            logger.warning("FRED_API_KEY not found. Returning empty macro data.")
            return {}
            
        indicators = {
            "Unemployment Rate": "UNRATE",
            "CPI (Inflation)": "CPIAUCSL", 
            "Fed Funds Rate": "FEDFUNDS",
            "GDP": "GDP",
            "10Y Treasury Yield": "DGS10",
            "2Y Treasury Yield": "DGS2"
        }
        
        data = {}
        for name, series_id in indicators.items():
            try:
                value, date = self._fetch_latest_observation(series_id)
                data[name] = {"value": value, "date": date}
            except Exception as e:
                logger.error(f"Error fetching {name} ({series_id}): {e}")
                data[name] = {"value": "N/A", "date": "N/A"}
                
        # Calculate Yield Curve Spread if possible
        try:
            ten_y = float(data["10Y Treasury Yield"]["value"])
            two_y = float(data["2Y Treasury Yield"]["value"])
            spread = ten_y - two_y
            data["10Y-2Y Spread"] = {"value": f"{spread:.2f}", "date": data["10Y Treasury Yield"]["date"]}
        except (ValueError, KeyError):
            data["10Y-2Y Spread"] = {"value": "N/A", "date": "N/A"}
            
        return data

    def _fetch_latest_observation(self, series_id: str) -> tuple[str, str]:
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1
        }
        
        response = requests.get(self.BASE_URL, params=params)
        response.raise_for_status()
        
        json_data = response.json()
        observations = json_data.get("observations", [])
        
        if observations:
            latest = observations[0]
            return latest.get("value", "N/A"), latest.get("date", "N/A")
            
        return "N/A", "N/A"

fred_service = FredService()

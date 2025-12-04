import os
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest, OptionChainRequest
from alpaca.data.timeframe import TimeFrame
from dotenv import load_dotenv

load_dotenv()

class AlpacaService:
    def __init__(self):
        self.api_key = os.getenv("ALPACA_API_KEY")
        self.secret_key = os.getenv("ALPACA_SECRET_KEY")
        
        if not self.api_key or not self.secret_key:
            print("Warning: Alpaca API keys not found in environment variables.")
            self.stock_client = None
            self.option_client = None
        else:
            self.stock_client = StockHistoricalDataClient(self.api_key, self.secret_key)
            self.option_client = OptionHistoricalDataClient(self.api_key, self.secret_key)

    def get_snapshots(self, symbols: List[str]) -> Dict:
        """
        Fetches snapshots for a list of symbols.
        Returns a dictionary where keys are symbols and values are snapshot objects (or dicts).
        """
        if not self.stock_client:
            return {}

        try:
            request_params = StockSnapshotRequest(symbol_or_symbols=symbols)
            snapshots = self.stock_client.get_stock_snapshot(request_params)
            return snapshots
        except Exception as e:
            print(f"Error fetching Alpaca snapshots: {e}")
            return {}

    def get_latest_price(self, symbol: str) -> Optional[float]:
        """
        Helper to get just the latest price for a single symbol.
        """
        snapshots = self.get_snapshots([symbol])
        if symbol in snapshots:
            return snapshots[symbol].latest_trade.price
        return None

    def get_option_chain(self, symbol: str) -> Dict:
        """
        Fetches the option chain for a given symbol.
        Note: Alpaca's Option Chain API might be different from yfinance.
        We might need to fetch all active options and filter/organize them.
        """
        if not self.option_client:
            return {"calls": [], "puts": []}

        try:
            # This is a simplified approach. Alpaca's option data structure is complex.
            # We might need to query for specific expiration dates or fetch all.
            # For now, let's try to get a snapshot of the chain or active contracts.
            # The alpaca-py SDK documentation suggests using OptionChainRequest if available,
            # or querying option contracts.
            
            # Since getting the *entire* chain might be heavy, we might need to be specific.
            # However, yfinance's option_chain(date) returns calls and puts for a specific date.
            
            # Let's return a placeholder or basic structure for now as we explore the specific
            # request needed for a full chain similar to yfinance.
            # Real implementation might require iterating over expirations.
            
            return {"calls": [], "puts": []} 
            
        except Exception as e:
            print(f"Error fetching Alpaca option chain: {e}")
            return {"calls": [], "puts": []}

alpaca_service = AlpacaService()

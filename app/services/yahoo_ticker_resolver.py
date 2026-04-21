import requests
from typing import Optional, Dict

class YahooTickerResolver:
    """
    Resolves TradingView symbols/exchanges to Yahoo Finance tickers.
    Uses a static map for common exchanges and falls back to Yahoo's Autocomplete API.
    """
    def __init__(self):
        # Static map: TradingView Exchange -> Yahoo Suffix (US only)
        self.suffix_map = {
            'NASDAQ': '',
            'NYSE': '',
            'AMEX': '',
            'OTC': '',
            'US': '',
        }

        # Region map (unused for US-only, kept for interface compatibility)
        self.region_map = {}

    def resolve(self, symbol: str, exchange: str = "", name: str = "", region: str = "") -> str:
        """
        Tries to find the correct Yahoo Ticker.
        Returns the best guess string.
        """
        # clean inputs
        symbol = symbol.strip() if symbol else ""
        exchange = exchange.strip().upper() if exchange else ""
        name = name.strip() if name else ""
        region = region.strip().lower() if region else ""

        # US exchanges need no suffix
        if exchange in self.suffix_map:
            return symbol

        # METHOD C: The "Search API" Fallback
        # If mapping didn't work, ask Yahoo.
        # Now allows searching by Symbol even if Name is missing.
        if name or (symbol and len(symbol) > 0 and symbol.isalpha()):
            print(f"🔍 Searching Yahoo for: {name or symbol} ({exchange or region})...")
            result = self._search_yahoo(name, symbol)
            if result:
                return result
        
        # Fallback: validation of just symbol
        return symbol

    def _search_yahoo(self, query_name: str, query_symbol: str) -> Optional[str]:
        """
        Hits the Yahoo Finance Autocomplete API to find the real ticker.
        """
        try:
            url = "https://query2.finance.yahoo.com/v1/finance/search"
            headers = {'User-Agent': 'Mozilla/5.0'}
            # Try searching by name first
            params = {
                'q': query_name if query_name else query_symbol,
                'quotesCount': 5,
                'newsCount': 0
            }
            
            r = requests.get(url, headers=headers, params=params, timeout=5)
            data = r.json()
            
            if 'quotes' in data and len(data['quotes']) > 0:
                # Look for the best match. 
                # Simple Strategy: Take the first result that is an EQUITY.
                for quote in data['quotes']:
                    if quote.get('quoteType') == 'EQUITY':
                        return quote['symbol']
                        
            # Backup: Try searching by Symbol if Name failed (and we didn't just try it)
            if query_name and query_symbol and query_symbol != query_name:
                params['q'] = query_symbol
                r = requests.get(url, headers=headers, params=params, timeout=5)
                data = r.json()
                if 'quotes' in data and len(data['quotes']) > 0:
                     return data['quotes'][0]['symbol']

        except Exception as e:
            print(f"Error searching Yahoo: {e}")
            
        return None # Could not find it

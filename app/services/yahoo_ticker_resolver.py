import requests
from typing import Optional, Dict

class YahooTickerResolver:
    """
    Resolves TradingView symbols/exchanges to Yahoo Finance tickers.
    Uses a static map for common exchanges and falls back to Yahoo's Autocomplete API.
    """
    def __init__(self):
        # 1. Define the STATIC MAP (TradingView Exchange -> Yahoo Suffix)
        # This covers 80% of cases instantly.
        self.suffix_map = {
            # Europe
            'LSE': '.L',      # London
            'SIX': '.SW',     # Switzerland
            'MIL': '.MI',     # Milan (Italy)
            'BME': '.MC',     # Madrid (Spain)
            'FSX': '.F',      # Frankfurt
            'XETR': '.DE',    # Xetra (Germany)
            'OMXSTO': '.ST',  # Stockholm (Sweden)
            'OMXHEX': '.HE',  # Helsinki (Finland)
            'OMXCOP': '.CO',  # Copenhagen (Denmark)
            'EURONEXT': '.PA', # Default to Paris (Risky, requires fallback often)
            
            # Asia
            'TSE': '.T',      # Tokyo
            'HKSE': '.HK',    # Hong Kong
            'SSE': '.SS',     # Shanghai
            'SZSE': '.SZ',    # Shenzhen
            'NSE': '.NS',     # India (NSE)
            'BSE': '.BO',     # India (BSE)
            'TWSE': '.TW',    # Taiwan
            
            # Americas
            'TSX': '.TO',     # Toronto
            'TSXV': '.V',     # TSX Venture
            'NASDAQ': '',     # US (No suffix)
            'NYSE': '',       # US (No suffix)
            'AMEX': '',       # US (No suffix)
            'OTC': '',        # OTC often has no suffix, or .OB
        }

    def resolve(self, symbol: str, exchange: str = "", name: str = "") -> str:
        """
        Tries to find the correct Yahoo Ticker.
        Returns the best guess string.
        
        Args:
            symbol: The stock symbol (e.g. "AAPL", "NESN")
            exchange: The exchange code from TradingView (e.g. "NASDAQ", "SIX")
            name: The company name (e.g. "Nestle S.A.")
        """
        # clean inputs
        symbol = symbol.strip() if symbol else ""
        exchange = exchange.strip().upper() if exchange else ""
        name = name.strip() if name else ""

        # METHOD A: Try Static Mapping
        # If we know the exchange perfectly, just add the suffix.
        if exchange in self.suffix_map:
            suffix = self.suffix_map[exchange]
            
            # Special logic for Euronext (Ambiguous) or German LS (not in map)
            # Euronext covers Paris, Amsterdam, Brussels, Lisbon.
            if exchange == 'EURONEXT':
                # We skip static mapping for Euronext to force a Search (Method B)
                # unless we assume Paris (.PA) which is in the map.
                # The prompt suggested skipping it. But let's check if we want to default to something?
                # The user code had: if exchange == 'EURONEXT': pass else: return ...
                pass 
            else:
                return f"{symbol}{suffix}"

        # METHOD B: The "Search API" Fallback
        # If mapping didn't work (or it's ambiguous like 'LS' or 'EURONEXT'),
        # we ask Yahoo: "What is the ticker for [Company Name]?"
        if name:
            print(f"🔍 Searching Yahoo for: {name} ({exchange})...")
            result = self._search_yahoo(name, symbol)
            if result:
                return result
        
        # Fallback: validation of just symbol (sometimes symbol is unique enough)
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
                'q': query_name,
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
                        
            # Backup: Try searching by Symbol if Name failed
            if query_symbol and query_symbol != query_name:
                params['q'] = query_symbol
                r = requests.get(url, headers=headers, params=params, timeout=5)
                data = r.json()
                if 'quotes' in data and len(data['quotes']) > 0:
                     return data['quotes'][0]['symbol']

        except Exception as e:
            print(f"Error searching Yahoo: {e}")
            
        return None # Could not find it

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
            'XETRA': '.DE',   # Alias
            'GER': '.DE',     # Alias
            'OMXSTO': '.ST',  # Stockholm (Sweden)
            'OMXHEX': '.HE',  # Helsinki (Finland)
            'OMXCOP': '.CO',  # Copenhagen (Denmark)
            'EURONEXT': '.PA', # Default to Paris (Risky, requires fallback often)
            'PAR': '.PA',      # Paris
            'AMS': '.AS',      # Amsterdam
            'BRU': '.BR',      # Brussels
            'LIS': '.LS',      # Lisbon
            
            # Asia
            'TSE': '.T',      # Tokyo
            'HKSE': '.HK',    # Hong Kong
            'SSE': '.SS',     # Shanghai
            'SZSE': '.SZ',    # Shenzhen
            'NSE': '.NS',     # India (NSE)
            'BSE': '.BO',     # India (BSE)
            'TWSE': '.TW',    # Taiwan
            'KSE': '.KS',     # Korea
            
            # Americas
            'TSX': '.TO',     # Toronto
            'TSXV': '.V',     # TSX Venture
            'TRT': '.TO',     # Toronto Alias
            'NASDAQ': '',     # US (No suffix)
            'NYSE': '',       # US (No suffix)
            'AMEX': '',       # US (No suffix)
            'OTC': '',        # OTC often has no suffix, or .OB
            'US': '',         # Generic US
        }

        # 2. Region Map (Fallback if Exchange missing)
        self.region_map = {
            'germany': '.DE',
            'uk': '.L',
            'britain': '.L',
            'france': '.PA',
            'italy': '.MI',
            'spain': '.MC',
            'switzerland': '.SW',
            'sweden': '.ST',
            'india': '.NS',
            'japan': '.T',
            'china': '.SS',
            'hong kong': '.HK',
            'canada': '.TO',
            'australia': '.AX',
            'brazil': '.SA'
        }

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
        
        # Handle dot replacement for TSX/CA symbols often (BBD.B -> BBD-B)
        if region in ['canada', 'ca'] or exchange in ['TSX', 'TSXV', 'TRT']:
            symbol = symbol.replace('.', '-')

        # METHOD A: Try Static Mapping (Exchange)
        if exchange in self.suffix_map:
            suffix = self.suffix_map[exchange]
            if suffixes := self.suffix_map.get(exchange): 
                 # Handle cases where suffix might be conditional (e.g. Euronext)
                 pass
            # Skip Euronext general key if we want search
            if exchange != 'EURONEXT':
                return f"{symbol}{suffix}"

        # METHOD B: Try Static Mapping (Region)
        if not exchange and region:
            for key, suff in self.region_map.items():
                if key in region:
                    return f"{symbol}{suff}"

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

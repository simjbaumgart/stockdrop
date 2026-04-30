import finnhub
import os
from dotenv import load_dotenv

load_dotenv()

class FinnhubService:
    def __init__(self):
        self.api_key = os.getenv("FINNHUB_API_KEY")
        if not self.api_key:
            print("WARNING: FINNHUB_API_KEY not found in environment variables.")
            self.client = None
        else:
            self.client = finnhub.Client(api_key=self.api_key)

    def get_filings(self, symbol: str, from_date: str = None, to_date: str = None):
        """
        Get filings for a specific symbol.
        
        Args:
            symbol: Stock symbol (e.g., AAPL)
            from_date: Start date YYYY-MM-DD
            to_date: End date YYYY-MM-DD
        """
        if not self.client:
            return []
        
        try:
            # The API method signature is filings(self, symbol='', cik='', access_number='', form='', _from='', to='')
            # We will use symbol and optionally dates.
            # Note: _from is the argument name in their python client to avoid keyword conflict
            kwargs = {'symbol': symbol}
            if from_date:
                kwargs['_from'] = from_date
            if to_date:
                kwargs['to'] = to_date
                
            return self.client.filings(**kwargs)
        except Exception as e:
            print(f"Error fetching filings from Finnhub for {symbol}: {e}")
            return []

    def get_company_news(self, symbol: str, from_date: str, to_date: str):
        """
        Get company news for a specific symbol.
        
        Args:
            symbol: Stock symbol (e.g., AAPL)
            from_date: Start date YYYY-MM-DD
            to_date: End date YYYY-MM-DD
        """
        if not self.client:
            return []
            
        try:
            return self.client.company_news(symbol, _from=from_date, to=to_date)
        except Exception as e:
            print(f"Error fetching company news from Finnhub for {symbol}: {e}")
            return []

    def extract_filing_text(self, url: str) -> str:
        """
        Downloads the SEC filing HTML from the given URL and extracts the text content.
        Uses BeautifulSoup to remove script/style tags and clean up whitespace.
        
        Args:
            url: The SEC filing URL (usually .htm or .xml)
            
        Returns:
            Extracted text as a string, or empty string on failure.
        """
        import requests
        from bs4 import BeautifulSoup

        if not url:
            return ""
            
        # SEC requires a user-agent to avoid 403s
        headers = {
            "User-Agent": "StockDropResearch bot@stockdrop.com",
            "Accept-Encoding": "gzip, deflate",
            "Host": "www.sec.gov"
        }
        
        try:
            print(f"Fetching SEC filing from: {url}")
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
                
            # Get text
            text = soup.get_text()
            
            # Basic cleanup: break into lines and remove leading/trailing space
            lines = (line.strip() for line in text.splitlines())
            # Break multi-headlines into a line each
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            # Drop blank lines
            clean_text = '\n'.join(chunk for chunk in chunks if chunk)
            
            return clean_text
            
        except Exception as e:
            print(f"Error extracting filing text from {url}: {e}")
            return ""

    def get_latest_reported_quarter(self, symbol: str) -> str | None:
        """Return the most recently reported fiscal quarter as 'YYYYQN' (e.g. '2026Q1'),
        or None on any failure / no data.

        Uses Finnhub's free /stock/earnings endpoint which already exposes a 'quarter'
        and 'year' field per row alongside the period date. We sort by period to be
        defensive (Finnhub usually returns newest-first, but we don't rely on that).
        """
        if not self.client:
            return None
        try:
            rows = self.client.company_earnings(symbol)
        except Exception as e:
            print(f"[FinnhubService] company_earnings failed for {symbol}: {e}")
            return None
        if not rows:
            return None
        latest = None
        try:
            latest = max(rows, key=lambda r: r.get("period", ""))
            year = latest.get("year")
            quarter = latest.get("quarter")
            if year is None or quarter is None:
                return None
            return f"{int(year)}Q{int(quarter)}"
        except (ValueError, TypeError) as e:
            print(f"[FinnhubService] could not derive quarter from {latest!r}: {e}")
            return None

    def get_insider_sentiment(self, symbol: str, from_date: str, to_date: str):
        """
        Get insider sentiment data for a specific symbol.
        """
        if not self.client:
            return {}
        try:
            return self.client.stock_insider_sentiment(symbol, _from=from_date, to=to_date)
        except Exception as e:
            print(f"Error fetching insider sentiment for {symbol}: {e}")
            return {}

finnhub_service = FinnhubService()

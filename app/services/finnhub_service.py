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

    def get_transcript_list(self, symbol: str):
        """
        Get a list of earnings call transcripts for a symbol.
        """
        if not self.client:
            return []
        try:
            return self.client.transcripts_list(symbol=symbol)
        except Exception as e:
            print(f"Error fetching transcript list for {symbol}: {e}")
            return []

    def get_transcript_content(self, transcript_id: str):
        """
        Get the full content of a specific transcript by ID.
        """
        if not self.client:
            return {}
        try:
            return self.client.transcripts(_id=transcript_id)
        except Exception as e:
            print(f"Error fetching transcript content for {transcript_id}: {e}")
            return {}

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

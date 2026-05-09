import os
import time
import logging

import finnhub
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# One retry with 2s backoff is enough to absorb the brief Finnhub
# blips we saw in the May-2026 sessions. Anything persistent is a real
# outage and the caller falls back to empty data.
_RETRY_BACKOFF_SEC = 2
_TRANSIENT_STATUS = {500, 502, 503, 504}


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, finnhub.FinnhubAPIException):
        status = getattr(exc, "status_code", None)
        if status is None:
            # Older SDK builds attach the response object instead of a flat code.
            resp = getattr(exc, "response", None)
            status = getattr(resp, "status_code", None)
        return status in _TRANSIENT_STATUS
    return False


def _call_with_retry(method, *args, **kwargs):
    """Call a Finnhub SDK method with one retry on transient errors.

    Re-raises the original exception after the second failure so callers'
    existing try/except can degrade gracefully (return [] or {}).
    """
    try:
        return method(*args, **kwargs)
    except Exception as e:
        if not _is_transient(e):
            raise
        name = getattr(method, "__name__", repr(method))
        logger.info(f"[Finnhub] transient error on {name}: {e}; retrying in {_RETRY_BACKOFF_SEC}s")
        time.sleep(_RETRY_BACKOFF_SEC)
        return method(*args, **kwargs)


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

            return _call_with_retry(self.client.filings, **kwargs)
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
            return _call_with_retry(self.client.company_news, symbol, _from=from_date, to=to_date)
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
            rows = _call_with_retry(self.client.company_earnings, symbol)
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

    def get_earnings_facts(self, symbol: str) -> dict | None:
        """Return the latest reported quarter's EPS facts as a structured dict,
        or None if data is missing/unavailable.

        Returned shape:
            {
                "reported_eps": float,
                "consensus_eps": float,
                "surprise_pct": float,         # signed; positive = beat
                "fiscal_quarter": "YYYYQN",
                "period": "YYYY-MM-DD",
                "source": "finnhub",
                "fetched_at": "<ISO 8601 UTC>",
            }
        """
        from datetime import datetime, timezone

        if not self.client:
            return None
        try:
            rows = _call_with_retry(self.client.company_earnings, symbol)
        except Exception as e:
            print(f"[FinnhubService] company_earnings failed for {symbol}: {e}")
            return None
        if not rows:
            return None

        try:
            latest = max(rows, key=lambda r: r.get("period", ""))
        except (ValueError, TypeError):
            return None

        actual = latest.get("actual")
        estimate = latest.get("estimate")
        if actual is None or estimate is None:
            return None

        # Finnhub may already populate surprisePercent. Fall back to computing
        # it ourselves if missing or zero against a non-zero estimate.
        surprise_pct = latest.get("surprisePercent")
        if surprise_pct is None and estimate not in (0, None):
            try:
                surprise_pct = round((float(actual) - float(estimate)) / float(estimate) * 100.0, 2)
            except (TypeError, ValueError):
                surprise_pct = None

        year = latest.get("year")
        quarter = latest.get("quarter")
        fq = None
        if year is not None and quarter is not None:
            try:
                fq = f"{int(year)}Q{int(quarter)}"
            except (TypeError, ValueError):
                fq = None

        return {
            "reported_eps": float(actual),
            "consensus_eps": float(estimate),
            "surprise_pct": float(surprise_pct) if surprise_pct is not None else None,
            "fiscal_quarter": fq,
            "period": latest.get("period"),
            "source": "finnhub",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def get_insider_sentiment(self, symbol: str, from_date: str, to_date: str):
        """
        Get insider sentiment data for a specific symbol.
        """
        if not self.client:
            return {}
        try:
            return _call_with_retry(self.client.stock_insider_sentiment, symbol, _from=from_date, to=to_date)
        except Exception as e:
            print(f"Error fetching insider sentiment for {symbol}: {e}")
            return {}

finnhub_service = FinnhubService()

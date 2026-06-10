import requests
import os
import time
import logging
from dotenv import load_dotenv, find_dotenv
from datetime import datetime
from typing import List, Dict, Optional

load_dotenv(find_dotenv())

logger = logging.getLogger(__name__)


class BenzingaService:
    """
    Service to fetch Benzinga news via the Massive Data API (powered by Polygon.io).

    Resilience:
      * Circuit breaker — after N consecutive request failures (timeouts / non-200),
        calls short-circuit to [] for a cooldown window instead of stalling every
        candidate on the same dead endpoint (~10s timeout * 4 calls/stock otherwise).
      * Market-news cache — SPY/DIA/QQQ market context is identical across all
        candidates in a scan, so it is fetched at most once per TTL window rather
        than 3x per stock.
    """

    BASE_URL = "https://api.polygon.io/v2/reference/news"
    REQUEST_TIMEOUT = 10  # seconds

    # Circuit breaker: trip after this many consecutive failures, stay open for
    # the cooldown. Kept short (vs Drive's 24h) because news is per-scan and a
    # transient outage should self-heal by the next 20-minute scan.
    FAILURES_TO_TRIP = 3
    COOLDOWN_SECONDS = 600  # 10 minutes

    # Market news is the same for every candidate; cache it per scan.
    MARKET_NEWS_TTL_SECONDS = 900  # 15 minutes
    MARKET_TICKERS = ["SPY", "DIA", "QQQ"]

    def __init__(self):
        # We try to load here, but we will also check in the method call for robustness
        self.api_key = os.getenv("BENZINGA_API_KEY")
        self.base_url = self.BASE_URL

        # Circuit breaker state (in-memory; this is a long-lived singleton in the
        # web process, so per-process state is sufficient).
        self._consecutive_failures = 0
        self._disabled_until: float = 0.0

        # Market-news cache: (fetched_at_epoch, result_list)
        self._market_news_cache: Optional[tuple] = None

        if self.api_key:
            masked = f"{self.api_key[:4]}...{self.api_key[-4:]}"
            logger.info(f"BenzingaService initialized with key {masked}")
        else:
            logger.warning("BenzingaService initialized with NO KEY.")

    # ------------------------------------------------------------------ breaker
    def _breaker_open(self) -> bool:
        """True while the breaker is tripped (requests should be skipped)."""
        if self._disabled_until == 0.0:
            return False
        if time.time() >= self._disabled_until:
            # Cooldown elapsed — half-open: allow the next request to probe.
            self._consecutive_failures = 0
            self._disabled_until = 0.0
            return False
        return True

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.FAILURES_TO_TRIP and self._disabled_until == 0.0:
            self._disabled_until = time.time() + self.COOLDOWN_SECONDS
            logger.warning(
                "Benzinga/Polygon news circuit breaker tripped after %d consecutive "
                "failures. Skipping news fetches for %d seconds.",
                self._consecutive_failures,
                self.COOLDOWN_SECONDS,
            )

    def _record_success(self) -> None:
        if self._consecutive_failures:
            self._consecutive_failures = 0
        self._disabled_until = 0.0

    # -------------------------------------------------------------- company news
    def get_company_news(self, symbol: str) -> List[Dict]:
        """
        Fetches news from the Massive Data stream (Polygon.io).
        Returns [] on any failure (and short-circuits while the breaker is open).
        """
        # LAZY LOAD / RELOAD if missing
        if not self.api_key:
            logger.debug("Benzinga key missing in instance. Attempting re-load from env...")
            load_dotenv(find_dotenv(), override=True)
            self.api_key = os.getenv("BENZINGA_API_KEY")

        if not self.api_key:
            logger.warning("BENZINGA_API_KEY not found in env even after reload.")
            return []

        if self._breaker_open():
            logger.debug("Benzinga breaker open — skipping news fetch for %s.", symbol)
            return []

        try:
            params = {
                "ticker": symbol,
                "limit": 20,
                "sort": "published_utc",
                "order": "desc",
            }
            headers = {"Authorization": f"Bearer {self.api_key}"}

            response = requests.get(
                self.base_url, params=params, headers=headers, timeout=self.REQUEST_TIMEOUT
            )

            if response.status_code == 200:
                self._record_success()
                results = response.json().get("results", [])
                return self._process_news(results)

            # Non-200 — count as a failure for the breaker. Log status only (the
            # URL carries the API key as a query param; never log response.url).
            self._record_failure()
            if response.status_code == 401:
                logger.error("Benzinga/Polygon news: 401 Unauthorized. Check BENZINGA_API_KEY.")
            else:
                logger.warning("Benzinga/Polygon news error: status %s", response.status_code)
            return []

        except Exception as e:
            self._record_failure()
            logger.warning("Error fetching Benzinga news for %s: %s", symbol, type(e).__name__)
            return []

    # --------------------------------------------------------------- market news
    def get_market_news(self, limit: int = 10) -> List[Dict]:
        """
        Fetches broad market news using ETF proxies (SPY, DIA, QQQ).
        Cached per TTL window — identical across all candidates in a scan, so this
        avoids re-running the 3x ETF fan-out for every stock.
        """
        now = time.time()
        if self._market_news_cache is not None:
            fetched_at, cached = self._market_news_cache
            if now - fetched_at < self.MARKET_NEWS_TTL_SECONDS:
                return cached[:limit]

        all_news: List[Dict] = []
        logger.debug("Fetching market news for %s...", self.MARKET_TICKERS)

        for ticker in self.MARKET_TICKERS:
            news = self.get_company_news(ticker)
            if news:
                all_news.extend(news)

        # Deduplicate by headline
        unique_news: Dict[str, Dict] = {}
        for item in all_news:
            headline = item.get("headline")
            if headline and headline not in unique_news:
                unique_news[headline] = item

        final_list = list(unique_news.values())
        final_list.sort(key=lambda x: x.get("datetime", 0), reverse=True)

        # Cache the full deduped list; callers slice with their own limit.
        self._market_news_cache = (now, final_list)
        return final_list[:limit]

    # ------------------------------------------------------------------ helpers
    def _process_news(self, articles) -> List[Dict]:
        """
        Normalizes Polygon/Massive news objects to our standard format.
        """
        processed = []
        for item in articles:
            try:
                published_utc = item.get("published_utc", "")

                ts = 0
                date_str = ""
                if published_utc:
                    try:
                        # ISO parsing compatible with Python 3.9+
                        dt = datetime.fromisoformat(published_utc.replace("Z", "+00:00"))
                        ts = int(dt.timestamp())
                        date_str = dt.strftime("%Y-%m-%d")
                    except Exception:
                        pass

                publisher = item.get("publisher", {}).get("name", "Unknown")
                image_url = item.get("image_url", "")

                # Extract Insights and Keywords to augment content
                insights = item.get("insights", [])
                keywords = item.get("keywords", [])

                content_parts = []

                description = item.get("description", "")
                if description:
                    content_parts.append(f"Summary: {description}")

                if insights:
                    content_parts.append("\nInsight Analysis:")
                    for insight in insights:
                        ins_ticker = insight.get("ticker", "N/A")
                        sentiment = insight.get("sentiment", "unknown")
                        reasoning = insight.get("sentiment_reasoning", "")
                        content_parts.append(f"- [{ins_ticker}] {sentiment.upper()}: {reasoning}")

                if keywords:
                    content_parts.append(f"\nKeywords: {', '.join(keywords)}")

                full_content = "\n".join(content_parts)

                processed.append({
                    "source": publisher,  # e.g. "Benzinga", "The Motley Fool"
                    "headline": item.get("title", ""),
                    "summary": description,
                    "content": full_content,  # Augmented with insights and keywords
                    "url": item.get("article_url", ""),
                    "datetime": ts,
                    "datetime_str": date_str,
                    "image": image_url,
                })
            except Exception as e:
                logger.warning("Error processing Polygon news item: %s", type(e).__name__)
                continue

        return processed


benzinga_service = BenzingaService()

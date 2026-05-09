import pandas as pd
import os
import sqlite3
import json
import requests
import yfinance as yf
from typing import List, Dict, Set, Any, Optional
from datetime import datetime, timedelta
import pytz
from app.services.email_service import email_service
from app.services.research_service import research_service
from app.services.alpaca_service import alpaca_service
from app.services.tradingview_service import tradingview_service
from app.database import add_decision_point, get_today_decision_symbols, update_decision_point, get_decision_points, get_cached_transcript, save_cached_transcript
from app.services.storage_service import storage_service
from app.services.gatekeeper_service import gatekeeper_service
from app.utils import get_git_version
from app.services.finnhub_service import finnhub_service
from app.services.alpha_vantage_service import alpha_vantage_service
from app.services.drive_service import drive_service
from app.services.benzinga_service import benzinga_service
from app.services.yahoo_ticker_resolver import YahooTickerResolver
from app.services.deep_research_service import deep_research_service
from app.services.seeking_alpha_service import seeking_alpha_service
from app.services.news_digest_service import ensure_news_digests_for_today

# --- DefeatBeta SSL bypass (pinned to 0.0.29; pandas-2.x compatible) ---
# DefeatBeta lazy-downloads NLTK 'punkt_tab' on import; macOS Python often
# can't find the system CA bundle, so we provide an unverified context just
# for that one download. This is intentionally narrow.
import ssl as _ssl
try:
    _ssl._create_default_https_context = _ssl._create_unverified_context
except AttributeError:
    pass

try:
    from defeatbeta_api.data.ticker import Ticker as _DBTicker
except ImportError:
    _DBTicker = None  # gracefully degrade if package missing

# Age threshold (in days) above which a DefeatBeta transcript is considered stale
# and triggers an Alpha Vantage fallback fetch.
STALE_TRANSCRIPT_DAYS = 75

# Minimum average daily volume (shares) for a ticker to be considered tradeable.
# Paired with the gatekeeper's $5 price floor to catch above-$5 tickers that
# still have no realistic liquidity.
MIN_AVG_VOLUME = 100_000


class StockService:
    def __init__(self):
        # Indices tickers: CSI 300 (000300.SS), S&P 500 (^GSPC), STOXX 600 (^STOXX)
        # Indices tickers: CSI 300 (000300.SS), S&P 500 (^GSPC), STOXX 600 (^STOXX)
        # Indices tickers: S&P 500, STOXX 600, China (CSI 300), India (Nifty 50)
        # Indices tickers configuration for TradingView
        # Format: "Name": {"symbol": "...", "screener": "...", "exchange": "..."}
        self.indices_config = {
            "S&P 500": {"symbol": "SPX", "screener": "america", "exchange": "CBOE"},
        }

        # Keep old tickers for fallback or reference if needed
        self.indices_tickers = {
            "S&P 500": "^GSPC",
        }

        # Sector tickers (US ETFs as proxies)
        self.sector_tickers = {
            "Technology": "XLK",
            "Financials": "XLF",
            "Healthcare": "XLV",
            "Consumer Discretionary": "XLY",
            "Consumer Staples": "XLP",
            "Energy": "XLE",
            "Industrials": "XLI",
            "Communication Services": "XLC",
            "Utilities": "XLU",
            "Materials": "XLB",
            "Real Estate": "XLRE"
        }
        
        # Metadata mapping for normalization
        self.stock_metadata = {
            # US Tech / Comm
            "AAPL": {"region": "US", "sector": "Technology"},
            "MSFT": {"region": "US", "sector": "Technology"},
            "GOOGL": {"region": "US", "sector": "Communication Services"},
            "AMZN": {"region": "US", "sector": "Consumer Discretionary"},
            "TSLA": {"region": "US", "sector": "Consumer Discretionary"},
            "NVDA": {"region": "US", "sector": "Technology"},
            "META": {"region": "US", "sector": "Communication Services"},
            "NFLX": {"region": "US", "sector": "Communication Services"},
            "ADBE": {"region": "US", "sector": "Technology"},
            "CRM": {"region": "US", "sector": "Technology"},
            "AMD": {"region": "US", "sector": "Technology"},
            "INTC": {"region": "US", "sector": "Technology"},
            "CSCO": {"region": "US", "sector": "Technology"},
            "ORCL": {"region": "US", "sector": "Technology"},
            # US Finance
            "BRK-B": {"region": "US", "sector": "Financials"},
            "JPM": {"region": "US", "sector": "Financials"},
            "V": {"region": "US", "sector": "Financials"},
            "MA": {"region": "US", "sector": "Financials"},
            "BAC": {"region": "US", "sector": "Financials"},
            "WFC": {"region": "US", "sector": "Financials"},
            "MS": {"region": "US", "sector": "Financials"},
            "GS": {"region": "US", "sector": "Financials"},
            "AXP": {"region": "US", "sector": "Financials"},
            "BLK": {"region": "US", "sector": "Financials"},
            # US Healthcare
            "LLY": {"region": "US", "sector": "Healthcare"},
            "JNJ": {"region": "US", "sector": "Healthcare"},
            "UNH": {"region": "US", "sector": "Healthcare"},
            "MRK": {"region": "US", "sector": "Healthcare"},
            "ABBV": {"region": "US", "sector": "Healthcare"},
            "PFE": {"region": "US", "sector": "Healthcare"},
            "TMO": {"region": "US", "sector": "Healthcare"},
            "DHR": {"region": "US", "sector": "Healthcare"},
            "ABT": {"region": "US", "sector": "Healthcare"},
            "BMY": {"region": "US", "sector": "Healthcare"},
            # US Consumer
            "WMT": {"region": "US", "sector": "Consumer Staples"},
            "PG": {"region": "US", "sector": "Consumer Staples"},
            "HD": {"region": "US", "sector": "Consumer Discretionary"},
            "KO": {"region": "US", "sector": "Consumer Staples"},
            "PEP": {"region": "US", "sector": "Consumer Staples"},
            "COST": {"region": "US", "sector": "Consumer Staples"},
            "MCD": {"region": "US", "sector": "Consumer Discretionary"},
            "NKE": {"region": "US", "sector": "Consumer Discretionary"},
            "DIS": {"region": "US", "sector": "Communication Services"},
            "SBUX": {"region": "US", "sector": "Consumer Discretionary"},
            # US Energy / Industrial
            "XOM": {"region": "US", "sector": "Energy"},
            "CVX": {"region": "US", "sector": "Energy"},
            "COP": {"region": "US", "sector": "Energy"},
            "SLB": {"region": "US", "sector": "Energy"},
            "GE": {"region": "US", "sector": "Industrials"},
            "CAT": {"region": "US", "sector": "Industrials"},
            "UPS": {"region": "US", "sector": "Industrials"},
            "HON": {"region": "US", "sector": "Industrials"},
            "LMT": {"region": "US", "sector": "Industrials"},
            "RTX": {"region": "US", "sector": "Industrials"},
        }
        
        # A curated list of major stocks to track for "Biggest Movers"
        # Expanded list to include more large cap stocks across sectors
        self.stock_tickers = list(self.stock_metadata.keys())
        
        # Cache to store sent notifications: Set[(symbol, date_str)]
        self.sent_notifications: Set[tuple] = set()
        
        # Store research reports: Dict[symbol, report_text]
        self.research_reports: Dict[str, str] = {}
        
        # Simple data cache: Dict[key, (data, timestamp)]
        self.cache: Dict[str, tuple] = {}
        self.cache_ttl = 3600 # 1 hour

        # Initialize Yahoo Ticker Resolver
        self.resolver = YahooTickerResolver()

        # Service singletons used by transcript orchestration
        self.alpha_vantage_service = alpha_vantage_service
        self._finnhub_service = finnhub_service

        # Batch Candidates for Deep Research (Deprecated)
        # self.deep_research_candidates = []


    def get_indices(self) -> Dict:
        # Try fetching from TradingView first
        try:
            data = tradingview_service.get_indices_data(self.indices_config)
            
            # Check if we have valid data, or if we need fallback
            expected_indices = ["S&P 500"]

            for name in expected_indices:
                if name not in data or data[name]["price"] == 0.0:
                    fallback_data = self._get_index_fallback(name)
                    data[name] = fallback_data
            return data
        except Exception as e:
            print(f"Error in get_indices (TradingView): {e}")
            # Fallback mechanism removed
            return self._get_indices_fallback_all()

    def _get_index_fallback(self, name: str) -> Dict:
        # Fallback mechanism using yfinance is removed.
        # Returning empty data if TradingView fails.
        return {"price": 0.0, "change": 0.0, "change_percent": 0.0}

    def _get_indices_fallback_all(self) -> Dict:
        # Fallback mechanism using yfinance is removed.
        return {}

    def get_top_movers(self) -> List[Dict]:
        return self._fetch_and_sort_stocks(limit=10)

    def get_large_cap_movers(self, processed_symbols: Set[str] = None) -> List[Dict]:
        """
        Fetches large cap stocks that have dropped significantly using TradingView Screener.
        """
        # Use TradingView Screener with global markets
        # Default filters: Market Cap > $5B (approx), Change < -5%, Volume > 50k
        return tradingview_service.get_top_movers(
            min_market_cap_usd=5_000_000_000, 
            max_change_percent=-5.0,
            min_volume=50_000,
            processed_symbols=processed_symbols
        )

    def get_stock_details(self, symbol: str) -> Dict:
        try:
            # Use Alpaca for details
            # We need to fetch snapshot to get price, open, high, low, volume
            snapshots = alpaca_service.get_snapshots([symbol])
            if symbol not in snapshots:
                return {}
                
            snapshot = snapshots[symbol]
            
            # Extract data
            price = snapshot.latest_trade.price if snapshot.latest_trade else 0.0
            # Fallback to daily bar if latest trade is missing
            if price == 0.0 and snapshot.daily_bar:
                price = snapshot.daily_bar.close
                
            # Safer access for prev_daily_bar which might be previous_daily_bar or missing
            prev_bar = getattr(snapshot, 'prev_daily_bar', None) or getattr(snapshot, 'previous_daily_bar', None)
            prev_close = prev_bar.close if prev_bar else 0.0
            open_price = snapshot.daily_bar.open if snapshot.daily_bar else 0.0
            day_high = snapshot.daily_bar.high if snapshot.daily_bar else 0.0
            day_low = snapshot.daily_bar.low if snapshot.daily_bar else 0.0
            volume = snapshot.daily_bar.volume if snapshot.daily_bar else 0
            
            # Static metadata
            metadata = self.stock_metadata.get(symbol, {})
            name = metadata.get("name", symbol) # We don't have name in metadata currently, maybe add it or use symbol
            # Actually metadata only has region and sector. 
            # We can't easily get longName from Alpaca snapshot.
            # We will use symbol as name or generic.
            
            return {
                "symbol": symbol,
                "name": symbol, # Placeholder as we don't have longName
                "price": price,
                "previous_close": prev_close,
                "open": open_price,
                "day_high": day_high,
                "day_low": day_low,
                "volume": volume,
                "market_cap": 0, # Not available
                "pre_market_price": 0.0, # Not available
                "currency": "USD"
            }
        except Exception as e:
            print(f"Error fetching details for {symbol}: {e}")
            return {}

    def get_options_dates(self, symbol: str) -> List[str]:
        # Alpaca / TradingView doesn't easily provide option dates list like yfinance
        return []

    def get_option_chain(self, symbol: str, date: str) -> Dict:
        return alpaca_service.get_option_chain(symbol)

        return context_data

    def _fetch_market_context(self) -> Dict[str, float]:
        """
        Fetches the current percentage change for all tracked indices and sectors.
        Uses TradingView for both.
        """
        context_data = {}
        
        # 1. Fetch Indices from TradingView
        try:
            indices_data = tradingview_service.get_indices_data(self.indices_config)
            for name, data in indices_data.items():
                context_data[name] = data.get("change_percent", 0.0)
        except Exception as e:
            print(f"Error fetching indices context: {e}")
            
        # 2. Fetch Sectors via TradingViewService
        try:
            sector_data = tradingview_service.get_sector_performance(self.sector_tickers)
            context_data.update(sector_data)
        except Exception as e:
             print(f"Error fetching sector context: {e}")
             
        return context_data

    def check_large_cap_drops(self):
        """
        Checks for large cap stocks that have dropped more than 6% (normalized)
        and sends an email notification if not already sent today.
        """
        version = get_git_version()
        print(f"\n{'='*50}")
        print(f"  StockDrop {version}")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*50}\n")
        print("Checking for large cap drops...")

        # News digest bootstrap — idempotent. Generates FT + Finimize daily
        # digests if upstream Cowork scheduler has written today's raw files
        # and our digest hasn't run yet. Silent bail if disabled/missing.
        try:
            ensure_news_digests_for_today()
        except Exception as e:
            print(f"  > [News Digest] Bootstrap raised (non-fatal): {e}")

        # self.deep_research_candidates = [] # Removed batch queue
        potential_batch_candidates = [] # For Deep Research Comparison
        
        # --- GATEKEEPER PHASE 1: Global Market Regime ---
        regime_info = gatekeeper_service.check_market_regime()
        # NOTE: Bear regime no longer halts screening — we always check for strong dips.
        if regime_info['regime'] == 'BEAR':
            print("GATEKEEPER: Market is in BEAR regime. Continuing screening (bear halt disabled).")

        # 1. Fetch Market Context
        market_context = self._fetch_market_context()
        print(f"Market Context: {market_context}")
        
        # 2. Load already processed symbols/companies to prevent duplicates
        today_date = datetime.now().date()
        today_str = today_date.strftime("%Y-%m-%d")
        
        # Calculate Previous Trading Day
        prev_trading_day = self._get_previous_trading_day(today_date)
        prev_date_str = prev_trading_day.strftime("%Y-%m-%d")
        
        print(f"Deduplication Window: Since {prev_date_str} (Previous Trading Day)")
        
        # Fetch symbols processed TODAY (absolute duplicate check)
        processed_symbols = set(storage_service.get_today_decisions())
        from app.database import get_today_decision_symbols, get_analyzed_companies_since
        db_processed_symbols = get_today_decision_symbols()
        processed_symbols.update(db_processed_symbols)
        
        for symbol in processed_symbols:
            self.sent_notifications.add((symbol, today_str))
            
        # Fetch Companies analyzed since Previous Trading Day
        # This prevents re-analyzing "Nvidia" on Monday if it was done on Friday.
        recent_companies = set(get_analyzed_companies_since(prev_date_str))
        print(f"Recently analyzed companies (Blacklist): {len(recent_companies)}")

        print(f"Already processed symbols today: {processed_symbols}")

        # 3. Fetch Large Cap Movers (passing processed symbols for logging)
        print("--- SCREENER: Fetching stocks from TradingView across all markets ---")
        large_cap_movers = self.get_large_cap_movers(processed_symbols)
        print(f"--- SCREENER: {len(large_cap_movers)} total stocks returned from screener ---")

        # Print screener overview
        print("=" * 50)
        print(f"  Screener Results: {len(large_cap_movers)} stocks pulled from markets")
        print("=" * 50)
        if large_cap_movers:
            print(f"  {'Symbol':<10} {'Price':>10} {'Drop %':>10}    {'Exchange'}")
            print(f"  {'─' * 45}")
            for s in large_cap_movers:
                symbol = s.get("symbol", "?")
                price = s.get("price", 0)
                change = s.get("change_percent", 0)
                exchange = s.get("exchange", "?")
                print(f"  {symbol:<10} ${price:>9.2f} {change:>9.2f}%    {exchange}")
            print("=" * 50)

        # Save found list to CSV with timestamp
        try:
            timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            save_dir = "data/found_stocks"
            os.makedirs(save_dir, exist_ok=True)
            filename = f"{save_dir}/found_stocks_{timestamp_str}.csv"
            
            if large_cap_movers:
                df = pd.DataFrame(large_cap_movers)
                df.to_csv(filename, index=False)
                print(f"Saved found stocks to {filename}")
            else:
                print("No stocks found to save.")
        except Exception as e:
            print(f"Error saving found stocks to CSV: {e}")

        # Identify and print stocks that still need processing
        deferred_tasks = [] # Initialize queue
        pending_processing = []
        for stock in large_cap_movers:
            if stock["change_percent"] <= -5.0:
                if (stock["symbol"], today_str) not in self.sent_notifications:
                    pending_processing.append(stock["symbol"])
        
        print(f"Pending processing: {pending_processing}")
        
        # --- Market Priority Sorting ---
        # 1. US (Open)
        # 2. EU (Open)
        # 3. China (Open)
        # 4. Rest (or Closed)
        # Map region names to simple keys
        # Regions: America (US), Europe, China, India, Australia, etc.
        
        def get_priority_score(stock):
            is_open = self._is_market_open()
            score = 60  # US base score
            if is_open:
                score += 40
            return score
        # Deduplicate stocks by symbol, keeping the one with the largest drop
        unique_stocks = {}
        for s in large_cap_movers:
            sym = s["symbol"]
            if sym not in unique_stocks:
                unique_stocks[sym] = s
            else:
                # Keep the one with more negative change (larger drop)
                if s["change_percent"] < unique_stocks[sym]["change_percent"]:
                    unique_stocks[sym] = s
        
        large_cap_movers = list(unique_stocks.values())
        print(f"--- SCREENER: {len(large_cap_movers)} unique stocks after deduplication ---")

        # First sort by symbol alphabetic
        large_cap_movers.sort(key=lambda x: x["symbol"])

        # Then sort by priority score descending
        large_cap_movers.sort(key=get_priority_score, reverse=True)

        # Count candidates that qualify for processing (>= 5% drop, not already processed)
        qualifying_count = sum(
            1 for s in large_cap_movers
            if s["change_percent"] <= -5.0 and (s["symbol"], today_str) not in self.sent_notifications
        )
        print(f"--- SCREENER: {qualifying_count} stocks qualify for processing (>= 5% drop, not yet processed) ---")

        print("Processing Queue (Top 5):")
        for s in large_cap_movers[:5]:
            p_score = get_priority_score(s)
            print(f"  - {s['symbol']} ({s.get('region')}) [Score: {p_score}]")
        
        for stock in large_cap_movers:
            symbol = stock["symbol"]
            change_percent = stock["change_percent"]
            price = stock["price"]
            
            # Check if drop is more than 7% (absolute)
            # User requested to ignore market context normalization for the trigger
            if change_percent <= -5.0:
                notification_key = (symbol, today_str)
                
                if notification_key not in self.sent_notifications:
                    # Get company name EARLY for deduplication
                    company_name = stock.get("description", stock.get("name", symbol))
                    
                    # --- DEDUPLICATION CHECK ---
                    # Check if company name is in the recent blacklist (case-insensitive)
                    # We expect company_name to be set.
                    if company_name and company_name.upper() in recent_companies:
                         print(f"Skipping {symbol} ({company_name}): Company analyzed recently (since {prev_date_str}).")
                         continue
                         
                    exchange = stock.get("exchange")
                    
                    print(f"Processing candidate {symbol} ({company_name}) [{change_percent:.2f}%]")
                    
                    # --- Active Trading Check ---
                    if not self._is_actively_traded(
                        symbol, 
                        region=stock.get("region", "US"), 
                        volume=stock.get("volume", 0),
                        exchange=exchange,
                        name=company_name
                    ):
                        continue

                    # --- WORKFLOW: 1. Technical Qualification ---
                    # First, we analyze the stock on the technical side (Gatekeeper).
                    # If it qualifies (passes filters), we proceed.
                    # --------------------------------------------
                    print(f"GATEKEEPER: Checking technical filters for {symbol}...")
                    region = stock.get("region", "US") 
                    screener = stock.get("screener")
                    cached_indicators = stock.get("cached_indicators")
                    
                    is_valid, reasons = gatekeeper_service.check_technical_filters(
                        symbol,
                        region=region,
                        exchange=exchange,
                        screener=screener,
                        cached_indicators=cached_indicators,
                        drop_pct=change_percent,
                    )
                    
                    if not is_valid:
                        print(f"GATEKEEPER: {symbol} REJECTED.")

                        # Primary Reason: liquidity takes priority over BB
                        liquidity_status = reasons.get('liquidity_status', '')
                        if liquidity_status and '<' in str(liquidity_status):
                            print(f"  [PRIMARY REASON] {liquidity_status}")
                        elif 'bb_status' in reasons:
                            print(f"  [PRIMARY REASON] {reasons['bb_status']}")
                        elif liquidity_status:
                            print(f"  [PRIMARY REASON] {liquidity_status}")

                        # Context Data
                        print("  [CONTEXT]")
                        for key, value in reasons.items():
                            if key in ('bb_status', 'liquidity_status'):
                                continue
                            try:
                                val_to_print = f"{float(value):.2f}"
                            except (ValueError, TypeError):
                                val_to_print = value
                            print(f"    {key}: {val_to_print}")

                        # Optionally log this rejection to DB or file?
                        continue
                        
                    tier = reasons.get("tier", "UNKNOWN")
                    print(f"GATEKEEPER: {symbol} APPROVED [tier={tier}]. Reasons: {reasons}")
                    
                    print(f"Triggering notification for {symbol} ({change_percent:.2f}%)")
                    
                    # Fetch Technical Analysis (MOVED UP)
                    print(f"Fetching technical analysis for {symbol}...")
                    import time
                    time.sleep(2) # Avoid 429 from Gatekeeper call
                    technical_analysis = tradingview_service.get_technical_analysis(
                        symbol,
                        region=stock.get("region", "US"),
                        exchange=exchange,
                        screener=stock.get("screener"),
                    )
                    technical_analysis["gatekeeper_findings"] = reasons # Add early

                    # Check for earnings proximity (Restored)
                    is_earnings, earnings_date_str = self._check_earnings_proximity(symbol)

                    # Fetch News Headlines (Moved Up for Deferral Check)
                    print(f"Fetching news for {symbol}...")
                    news_data = self.get_aggregated_news(
                        symbol, 
                        region=stock.get("region", "US"),
                        exchange=exchange,
                        company_name=company_name
                    )
                    print(f"  > Fetched {len(news_data)} news articles.")

                    current_version = get_git_version()

                    # Check Deferral
                    if len(news_data) == 0:
                        print(f"  > [DEFERRED] {symbol} has 0 news items. Adding to deferred queue.")
                        deferred_tasks.append({
                            "symbol": symbol, "price": price, "change_percent": change_percent, 
                            "stock": stock, "company_name": company_name, "exchange": exchange, 
                            "reasons": reasons, "market_context": market_context, 
                            "news_data": news_data, "is_earnings": is_earnings, 
                            "earnings_date_str": earnings_date_str, "current_version": current_version,
                            "technical_analysis": technical_analysis 
                        })
                        continue

                    # Run Deep Analysis Immediately
                    res = self._run_deep_analysis(
                        symbol, price, change_percent, stock, company_name, exchange, 
                        reasons, market_context, news_data, is_earnings, earnings_date_str, current_version,
                        technical_analysis
                    )
                    
                    if res:
                        rec = res.get('recommendation', 'HOLD')
                        if "BUY" in rec.upper():
                            print(f"[Batch Comparison] Adding {symbol} to candidate list (Rec: {rec})")
                            potential_batch_candidates.append(res)
                    
        # Process Deferred Tasks
        if deferred_tasks:
            print(f"\nProcessing {len(deferred_tasks)} deferred stocks (0 news)...")
            for task in deferred_tasks:
                print(f"Processing deferred: {task['symbol']}...")
                res = self._run_deep_analysis(
                     task['symbol'], task['price'], task['change_percent'], task['stock'], 
                     task['company_name'], task['exchange'], task['reasons'], 
                     task['market_context'], task['news_data'], task['is_earnings'], 
                     task['earnings_date_str'], task['current_version'],
                     task['technical_analysis']
                )
                

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Cycle completed. Large cap drops check finished.")
        
        # --- DEEP RESEARCH BACKFILL (User Requested) ---
        # Run backfill for any high-scoring stocks from today that are missing a verdict
        self._process_deep_research_backfill(today_str)

    def _should_trigger_deep_research(self, report_data: dict) -> bool:
        """
        Trigger deep research for buy decisions:
        - BUY: always trigger (any conviction, any R/R)
        - BUY_LIMIT: trigger when R/R > 1.25 (any conviction)
        """
        action = report_data.get("recommendation", "AVOID").upper()
        risk_reward = report_data.get("risk_reward_ratio", 0)

        # BUY: always trigger
        if action == "BUY":
            return True

        # BUY_LIMIT: trigger when R/R > 1.0. The Pending DR Review status
        # gate uses the same threshold; raising it would strand rows.
        if action == "BUY_LIMIT":
            try:
                if float(risk_reward) > 1.0:
                    return True
            except (TypeError, ValueError):
                return False

        return False

    def _build_deep_research_context(self, report_data: dict, raw_data: dict) -> dict:
        """
        Builds a condensed but complete context package for deep research.
        Passes SYNTHESIZED council output, not raw data.
        """
        return {
            # PM Decision (new structured output)
            "pm_decision": {
                "action": report_data.get("recommendation"),
                "conviction": report_data.get("conviction"),
                "drop_type": report_data.get("drop_type"),
                "entry_price_low": report_data.get("entry_price_low"),
                "entry_price_high": report_data.get("entry_price_high"),
                "stop_loss": report_data.get("stop_loss"),
                "take_profit_1": report_data.get("take_profit_1"),
                "take_profit_2": report_data.get("take_profit_2"),
                "upside_percent": report_data.get("upside_percent"),
                "downside_risk_percent": report_data.get("downside_risk_percent"),
                "risk_reward_ratio": report_data.get("risk_reward_ratio"),
                "pre_drop_price": report_data.get("pre_drop_price"),
                "entry_trigger": report_data.get("entry_trigger"),
                "reason": report_data.get("executive_summary"),
                "key_factors": report_data.get("key_factors", []),
            },
            # Synthesized agent reports (not raw data)
            "bull_case": report_data.get("bull_report", ""),
            "bear_case": report_data.get("bear_report", ""),
            # Technical indicators (compact — already processed)
            "technical_data": raw_data.get("indicators", {}),
            # Drop context
            "drop_percent": raw_data.get("change_percent", 0),
            # Paywalled news — deep research can't access via Google Search
            "raw_news": raw_data.get("news_items", []),
            # Transcript summary instead of full raw transcript (~95% cheaper)
            "transcript_summary": self._extract_transcript_summary(report_data),
            "transcript_date": raw_data.get("transcript_date"),
            # Evidence quality
            "data_depth": report_data.get("data_depth", {}),
        }

    def _extract_transcript_summary(self, report_data: dict) -> str:
        """
        Extracts the 'Extended Transcript Summary' section from the News Agent's output.
        Falls back to a short message if the summary can't be found.
        """
        news_report = report_data.get("macro_report", "")

        marker = "Extended Transcript Summary"
        if marker in news_report:
            start = news_report.index(marker)
            rest = news_report[start + len(marker):]
            end_markers = ["## Key Drivers", "### Key Drivers", "## Narrative Check",
                           "### Narrative Check", "## Top 5 Sources", "### Top 5 Sources",
                           "## MACRO CHECK", "NEEDS_ECONOMICS"]
            end_pos = len(rest)
            for em in end_markers:
                if em in rest:
                    pos = rest.index(em)
                    end_pos = min(end_pos, pos)

            summary = rest[:end_pos].strip()
            if len(summary) > 100:
                return summary

        return "No transcript summary available from council."

    def _process_deep_research_backfill(self, date_str: str):
        """
        Checks for stocks analyzed TODAY (or date_str) that have qualifying recommendations
        but are missing a Deep Research Verdict. Triggers Deep Research using saved council context.
        """
        print("\n[Backfill] Checking for outstanding Deep Research candidates...")
        try:
            conn = sqlite3.connect(os.getenv("DB_PATH", "subscribers.db"))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Query candidates: Same Date, BUY/BUY_LIMIT with MODERATE+ conviction and R/R >= 1.5
            query = """
                SELECT * FROM decision_points 
                WHERE date(timestamp) = ? 
                AND recommendation IN ('BUY', 'BUY_LIMIT')
                AND conviction IN ('MODERATE', 'HIGH')
                AND risk_reward_ratio >= 1.5
                AND (deep_research_verdict IS NULL OR deep_research_verdict = '' OR deep_research_verdict = '-' OR deep_research_verdict LIKE 'UNKNOWN%' OR deep_research_verdict = 'ERROR_PARSING' OR deep_research_verdict = 'PENDING_REVIEW')
            """
            cursor.execute(query, (date_str,))
            rows = cursor.fetchall()
            conn.close()
            
            candidates = [dict(row) for row in rows]
            
            if not candidates:
                print("[Backfill] No outstanding candidates found.")
                return

            print(f"[Backfill] Found {len(candidates)} candidates needing Deep Research: {[c['symbol'] for c in candidates]}")
            
            for c in candidates:
                symbol = c['symbol']
                decision_id = c['id']
                drop_percent = c.get('drop_percent', 0.0)
                
                # Try to load council reports from file
                # council2 (Phase 1+2: includes bull/bear/risk) preferred, fallback to council1
                council_dir = "data/council_reports"
                from app.utils.ticker_paths import safe_ticker_path
                _safe = safe_ticker_path(symbol)
                council1_path = f"{council_dir}/{_safe}_{date_str}_council1.json"
                council2_path = f"{council_dir}/{_safe}_{date_str}_council2.json"
                council_reports = {}
                
                if os.path.exists(council1_path):
                    try:
                        with open(council1_path, 'r') as f:
                            council_reports = json.loads(f.read())
                            print(f"  > Loaded council1 report from file: {council1_path}")
                    except Exception as e:
                        print(f"  > Error reading council1 report file: {e}")
                
                if os.path.exists(council2_path):
                    try:
                        with open(council2_path, 'r') as f:
                            council2_data = json.loads(f.read())
                            council_reports.update(council2_data)
                            print(f"  > Loaded council2 report (bull/bear/risk) from file: {council2_path}")
                    except Exception as e:
                        print(f"  > Error reading council2 report file: {e}")
                
                # Build context from DB row + file data
                context = {
                    "pm_decision": {
                        "action": c.get("recommendation"),
                        "conviction": c.get("conviction"),
                        "drop_type": c.get("drop_type"),
                        "entry_price_low": c.get("entry_price_low"),
                        "entry_price_high": c.get("entry_price_high"),
                        "stop_loss": c.get("stop_loss"),
                        "take_profit_1": c.get("take_profit_1"),
                        "take_profit_2": c.get("take_profit_2"),
                        "upside_percent": c.get("upside_percent"),
                        "downside_risk_percent": c.get("downside_risk_percent"),
                        "risk_reward_ratio": c.get("risk_reward_ratio"),
                        "pre_drop_price": c.get("pre_drop_price"),
                        "entry_trigger": c.get("entry_trigger"),
                        "reason": c.get("reasoning", "")[:500],
                        "key_factors": [],
                    },
                    "bull_case": council_reports.get("bull", "Not available from backfill."),
                    "bear_case": council_reports.get("bear", "Not available from backfill."),
                    "technical_data": council_reports.get("technical", {}),
                    "drop_percent": drop_percent,
                    "raw_news": [],  # Not available in inline backfill
                    "transcript_summary": "No transcript summary available from backfill.",
                    "transcript_date": None,
                    "data_depth": {},
                }
                
                print(f"  > Triggering Backfill for {symbol} (Conviction: {c.get('conviction')}, R/R: {c.get('risk_reward_ratio')})...")
                queued = deep_research_service.queue_research_task(
                    symbol=symbol,
                    context=context,
                    decision_id=decision_id
                )
                if queued:
                    print(f"  > Queued backfill task for {symbol}")
                else:
                    print(f"  > Skipped {symbol}: already in-flight (live trigger beat the backfill)")
                
        except Exception as e:
            print(f"[Backfill] Error processing backfill: {e}")

    def _is_actively_traded(self, symbol: str, region: str = "US", volume: float = 0, exchange: str = "", name: str = "") -> bool:
        """
        Checks if the stock is actively traded to avoid illiquid tickers.
        Criteria: Avg volume > MIN_AVG_VOLUME over last 5 days.
        """
        # 1. Faster Check: Use volume from Screener if available
        if volume > MIN_AVG_VOLUME:
            return True

        # 2. Fallback: Check yfinance (historical volume)
        try:
            # Suffix mapping for yfinance
            yf_symbol = self._resolve_yfinance_ticker(symbol, region, exchange, name)

            # Use yfinance for quick volume check with shared session
            ticker = yf.Ticker(yf_symbol)
            hist = ticker.history(period="5d")

            if hist.empty:
                print(f"  > [Active Check] No history found for {yf_symbol}. Assuming inactive (or suffix mismatch).")
                # If mapped symbol failed, maybe try original?
                if yf_symbol != symbol:
                    print(f"  > [Active Check] Retrying with original symbol {symbol}...")
                    hist = yf.Ticker(symbol).history(period="5d")
                    if hist.empty:
                        return False
                else:
                    return False

            avg_vol = hist['Volume'].mean()
            if avg_vol < MIN_AVG_VOLUME:
                print(f"  > [Active Check] {yf_symbol} Volume Low ({int(avg_vol)} < {MIN_AVG_VOLUME:,}). Skipping.")
                return False

            return True
        except Exception as e:
            print(f"  > [Active Check] Error checking {symbol}: {e}. Skipping to be safe.")
            return False

    def _check_earnings_proximity(self, symbol: str) -> tuple[bool, str]:
        """
        Checks if the current date is close to an earnings date using TradingView.
        Returns (is_earnings_drop, earnings_date_str).
        """
        try:
            # We don't have region easily available here, assuming US or using helper
            # If we want accuracy, we should store region in stock object passed around.
            # But currently we only have symbol.
            # We can try to use 'US' or iterate. But let's try 'US' first.
            # The symbol is usually unique enough or we found it in a specific region earlier.
            
            # Use TradingView service
            earnings_ts = tradingview_service.get_earnings_date(symbol, region="US")
            
            if earnings_ts is None:
                return False, None
                
            # Convert timestamp to date
            earnings_date = datetime.fromtimestamp(earnings_ts).date()
            today = datetime.now().date()
            
            # Check range [-1, +2] days
            delta = (today - earnings_date).days
            
            if -1 <= delta <= 2:
                return True, earnings_date.strftime("%Y-%m-%d")
                    
            return False, None
            
        except Exception as e:
            print(f"Error checking earnings for {symbol}: {e}")
            return False, None

    def get_daily_movers(self, threshold: float = 5.0) -> List[Dict]:
        """
        Fetches all stocks from the curated list that have moved more than 'threshold' percent.
        """
        movers = []
        movers = []
        try:
            snapshots = alpaca_service.get_snapshots(self.stock_tickers)
            
            for symbol, snapshot in snapshots.items():
                try:
                    price = snapshot.latest_trade.price if snapshot.latest_trade else (snapshot.daily_bar.close if snapshot.daily_bar else 0.0)
                    prev_close = snapshot.prev_daily_bar.close if snapshot.prev_daily_bar else 0.0
                    
                    if price and prev_close:
                        change = price - prev_close
                        change_percent = (change / prev_close) * 100
                        
                        if abs(change_percent) >= threshold:
                            movers.append({
                                "symbol": symbol,
                                "price": price,
                                "change_percent": change_percent,
                                "sector": self.stock_metadata.get(symbol, {}).get("sector", "Unknown")
                            })
                except Exception:
                    continue
        except Exception as e:
            print(f"Error fetching daily movers: {e}")
            
        return sorted(movers, key=lambda x: abs(x["change_percent"]), reverse=True)

    def _fetch_news_headlines(self, symbol: str) -> str:
        """Fetches top 5 news headlines for the symbol using yfinance."""
        try:
            # Handle special tickers if needed, but usually passed symbol matches yfinance format
            # or is close enough.
            ticker = yf.Ticker(symbol)
            news = ticker.news
            if not news:
                return "No specific news headlines found."
            
            headlines = []
            for n in news[:5]:
                title = n.get('title', 'No Title')
                # providerPublishTime is unix timestamp
                pub_time = n.get('providerPublishTime', 0)
                pub_date = datetime.fromtimestamp(pub_time).strftime('%Y-%m-%d')
                headlines.append(f"- {pub_date}: {title}")
                
            return "\n".join(headlines)
        except Exception as e:
            print(f"Error fetching news for {symbol}: {e}")
            return "Error fetching news (API issue)."

    def _resolve_yfinance_ticker(self, symbol: str, region: str, exchange: str = "", name: str = "") -> str:
        """
        Resolves the correct yfinance ticker using YahooTickerResolver.
        """
        # Use simple region fallback if exchange not provided, to mimic old behavior if needed.
        # But resolver is robust.
        # We can pass what we have.
        try:
            return self.resolver.resolve(symbol, exchange, name, region)
        except Exception as e:
            print(f"Resolver Error: {e}. Falling back to symbol {symbol}")
            return symbol

    # --- Source Type Classification ---
    # Maps known publisher names to source types for news quality assessment.
    SOURCE_TYPE_OFFICIAL_KEYWORDS = [
        "SEC", "PR Newswire", "GlobeNewsWire", "Globe Newswire",
        "Business Wire", "AccessWire", "EIN Presswire",
    ]
    SOURCE_TYPE_ANALYST_KEYWORDS = [
        "Seeking Alpha", "Motley Fool", "InvestorPlace", "Zacks",
    ]

    @staticmethod
    def _classify_source_type(provider: str, source: str) -> str:
        """
        Classify a news item into one of four source types based on provider and
        original publisher name:
          WIRE           — factual reporting (Benzinga, Reuters, Finnhub, etc.)
          ANALYST        — opinion / thesis (Seeking Alpha, Motley Fool, etc.)
          OFFICIAL       — press releases, SEC filings (primary source)
          MARKET_CONTEXT — broad market news (Wall Street Breakfast, SPY/DIA/QQQ)
        """
        if provider == "Market News (Benzinga)":
            return "MARKET_CONTEXT"
        source_lower = source.lower()
        for kw in StockService.SOURCE_TYPE_OFFICIAL_KEYWORDS:
            if kw.lower() in source_lower:
                return "OFFICIAL"
        for kw in StockService.SOURCE_TYPE_ANALYST_KEYWORDS:
            if kw.lower() in source_lower:
                return "ANALYST"
        return "WIRE"

    def get_aggregated_news(self, symbol: str, region: str = "US", exchange: str = "", company_name: str = "") -> List[Dict]:
        """
        Fetches and aggregates news from Benzinga (Primary), Alpha Vantage, Finnhub, and yfinance.
        Returns a standardised list of news items.
        
        Standard Object:
        {
            "source": str,
            "provider": str,
            "source_type": str,  # WIRE | ANALYST | OFFICIAL | MARKET_CONTEXT
            "headline": str,
            "summary": str,
            "content": str, # Full article body (optional)
            "url": str,
            "datetime": int, # Unix timestamp
            "datetime_str": str, # YYYY-MM-DD
            "image": str
        }
        """
        # --- SEEKING ALPHA (Local Context) ---
        try:
            sa_counts = seeking_alpha_service.get_counts(symbol)
            print(f"  > Seeking Alpha (Local): {sa_counts['total']} articles (Analysis: {sa_counts['analysis']}, News: {sa_counts['news']}, PR: {sa_counts['pr']}, WSB: {sa_counts['wsb']} [{sa_counts['wsb_date']}])")
        except Exception as e:
            print(f"  > Seeking Alpha (Local): Error getting counts ({e})")

        news_items = []
        massive_items = []
        other_items = []
        
        # 1. Massive/Benzinga News (Primary - Full Content)
        # User requested to try for ALL executing, removing region lock.
        if True:
            try:
                # Calculate 3 months ago (approx 90 days)
                three_months_ago = int((datetime.now() - timedelta(days=90)).timestamp())
                
                bz_news = benzinga_service.get_company_news(symbol)
                
                # Filter: Max 3 months old
                bz_filtered = [n for n in bz_news if n.get('datetime', 0) >= three_months_ago]
                
                # Sort by date desc
                bz_filtered.sort(key=lambda x: x.get('datetime', 0), reverse=True)
                
                # Take Top 10
                bz_final = bz_filtered[:10]
                
                for item in bz_final:
                    # User requested Massive priority. We tag it as Massive but keep the original publisher info.
                    original_source = item.get('source', 'Benzinga')
                    massive_items.append({
                        "source": f"Massive ({original_source})",
                        "provider": "Benzinga/Massive",
                        "source_type": self._classify_source_type("Benzinga/Massive", original_source),
                        "headline": item.get('headline'),
                        "summary": item.get('summary'),
                        "content": item.get('content'), # Full HTML body
                        "url": item.get('url'),
                        "datetime": item.get('datetime'),
                        "datetime_str": item.get('datetime_str'),
                        "image": item.get('image')
                    })
                    
                print(f"  > Fetched {len(bz_final)} Massive/Benzinga articles (Top 10, <90 days).")
                
            except Exception as e:
                print(f"Error fetching Benzinga news: {e}")
        else:
             print(f"  > Skipping Massive/Benzinga for non-US region: {region}")
             
        # --- MARKET NEWS INTEGRATION (US ONLY) ---
        if region == "US" or region == "America":
             try:
                 market_news = benzinga_service.get_market_news(limit=5)
                 if market_news:
                     print(f"  > Fetched {len(market_news)} Market Context articles (SPY/DIA/QQQ).")
                     for item in market_news:
                         # Tag as Market News so ResearchService knows
                         item['provider'] = 'Market News (Benzinga)'
                         item['source_type'] = 'MARKET_CONTEXT'
                         massive_items.append(item)
             except Exception as e:
                 print(f"Error fetching market news: {e}")


        # Resolve yfinance ticker for news ensuring we don't pick up colliding US stocks
        # ... (rest of function)
        # Resolve yfinance ticker for news ensuring we don't pick up colliding US stocks
        # ... (rest of function)
        yf_symbol = self._resolve_yfinance_ticker(symbol, region, exchange, company_name)
        
        # 1. Alpha Vantage News (Primary Source)
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            
            # Alpha Vantage uses the raw symbol mostly, or might need suffix?
            # Usually Alpha Vantage matches the query flexibility. 
            # If we pass EVT.DE to AV? Let's assume symbol is fine or we should pass yf_symbol?
            # The stock_service uses 'symbol' everywhere else. 
            # Let's stick to 'symbol' for AV unless we see issues. 
            # AV often needs ticker translation for non-US? 
            # For now, keeping 'symbol' for AV as tested before (presumably).
            
            av_news = alpha_vantage_service.get_company_news(symbol, start_date=week_ago, end_date=today)
            if av_news:
                print(f"  > Alpha Vantage: {len(av_news)} articles")
                for item in av_news:
                    item['provider'] = 'Alpha Vantage'
                    item['source_type'] = self._classify_source_type('Alpha Vantage', item.get('source', ''))
                other_items.extend(av_news)
            else:
                print(f"  > Alpha Vantage: 0 articles")
        except Exception as e:
            print(f"Error fetching Alpha Vantage news for {symbol}: {e}")
            
        # 2. Finnhub News (Secondary Source) - ALWAYS RUN
        try:
             fh_news = finnhub_service.get_company_news(symbol, from_date=week_ago, to_date=today)
             if fh_news:
                 print(f"  > Finnhub: {len(fh_news)} articles")
                 
                 for item in fh_news:
                    try:
                        ts = item.get('datetime', 0)
                        dt_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                        # Simple duplicate check by headline (against both massive and others)
                        if any(n['headline'] == item.get('headline') for n in massive_items + other_items):
                            continue
                            
                        fh_source = item.get('source', 'Finnhub')
                        other_items.append({
                            "source": fh_source,
                            "provider": "Finnhub",
                            "source_type": self._classify_source_type("Finnhub", fh_source),
                            "headline": item.get('headline', 'No Title'),
                            "summary": item.get('summary'),
                            "url": item.get('url'),
                            "datetime": ts,
                            "datetime_str": dt_str,
                            "image": item.get('image', '')
                        })
                    except Exception:
                        continue
             else:
                 print(f"  > Finnhub: 0 articles")
        except Exception as e:
            print(f"Error fetching Finnhub news for {symbol}: {e}")

        # 3. yfinance News (Secondary Source) - ALWAYS RUN
        try:
            # Use resolved symbol (e.g. EVT.DE) to avoid US collision for German stocks
            ticker = yf.Ticker(yf_symbol)
            yf_news = ticker.news
            if yf_news:
                print(f"  > yfinance: {len(yf_news)} articles ({yf_symbol})")
            else:
                print(f"  > yfinance: 0 articles ({yf_symbol})")
                
            for item in yf_news:
                try:
                    # Handle new YF structure (nested content)
                    content = item.get('content', item) # Fallback to item if flat
                    
                    # Try to find date
                    ts = 0
                    if 'providerPublishTime' in content:
                        ts = content['providerPublishTime']
                    elif 'pubDate' in content:
                        # Parse ISO string "2025-12-09T16:00:00Z"
                        try:
                            dt = datetime.fromisoformat(content['pubDate'].replace('Z', '+00:00'))
                            ts = int(dt.timestamp())
                        except:
                            pass
                    
                    if ts == 0:
                        continue
                        
                    dt_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    
                    title = content.get('title', 'No Title')
                    # Avoid duplicates from Finnhub or Massive
                    if any(n['headline'] == title for n in massive_items + other_items):
                        continue
                    
                    # Extract thumbnail if available
                    image = ""
                    if 'thumbnail' in content and 'resolutions' in content['thumbnail']:
                         res = content['thumbnail']['resolutions']
                         if res:
                             image = res[0].get('url', '')
                             
                    url = (content.get('clickThroughUrl') or {}).get('url', '') if content.get('clickThroughUrl') else (content.get('link', '') or '')
                    
                    yf_source = content.get('provider', {}).get('displayName', 'Yahoo Finance')
                    other_items.append({
                        "source": yf_source,
                        "provider": "Yahoo Finance",
                        "source_type": self._classify_source_type("Yahoo Finance", yf_source),
                        "headline": title,
                        "summary": content.get('summary', ''), # Often empty in simple list
                        "url": url,
                        "datetime": ts,
                        "datetime_str": dt_str,
                        "image": image
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"Error fetching yfinance news for {symbol} ({yf_symbol}): {e}")

        # 4. TradingView Scraper (Tertiary Source)
        try:
             # Lazy import to avoid circular dependencies or issues if not installed
            from tradingview_scraper.symbols.news import NewsScraper
            scraper = NewsScraper()
            
            exchange = "NASDAQ"
            headers = scraper.scrape_headlines(symbol=symbol, exchange=exchange)
            
            for item in headers:
                try:
                    title = item.get('title', 'No Title')
                    # Deduplicate
                    if any(n['headline'] == title for n in massive_items + other_items):
                        continue
                        
                    ts = item.get('published', 0)
                    dt_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    
                    tv_source = item.get('source', 'TradingView')
                    other_items.append({
                        "source": tv_source,
                        "provider": "TradingView",
                        "source_type": self._classify_source_type("TradingView", tv_source),
                        "headline": title,
                        "summary": item.get('description', ''), # Description often empty in headlines, checks details?
                        "url": f"https://www.tradingview.com{item.get('storyPath', '')}",
                        "datetime": ts,
                        "datetime_str": dt_str,
                        "image": "" # No image in headlines usually
                    })
                except Exception:
                    continue
            
            print(f"  > TradingView: {len(headers)} articles")
                    
        except ImportError:
            # print("tradingview-scraper not installed.") # Silencing noise
            pass
        except Exception as e:
            print(f"Error fetching TradingView news for {symbol}: {e}")
            
        # PRIORITY MERGE:
        # 1. Massive items take precedence (all of them).
        # 2. Others fill the remainder up to 30.
        
        limit = 30
        remaining = limit - len(massive_items)
        if remaining < 0: remaining = 0
        
        # Sort others by newest first
        other_items.sort(key=lambda x: x['datetime'], reverse=True)
        
        # Merge
        final_list = massive_items + other_items[:remaining]
        
        # Final Sort for Agent Context (Chronological)
        # Note: We prioritized INCLUSION of Massive. 
        # Now we sort by time so the timeline makes sense to the AI.
        final_list.sort(key=lambda x: x['datetime'], reverse=True)
        
        return final_list

    def get_latest_filing_text(self, symbol: str) -> str:
        """
        Fetches the text of the most recent significant filing (8-K, 10-Q, 10-K).
        """
        try:
            # Look back 30 days for 8-K, or 90 days for 10-Q/10-K
            # Simplification: Look back 3 months (90 days)
            from_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            to_date = datetime.now().strftime("%Y-%m-%d")
            
            filings = finnhub_service.get_filings(symbol, from_date=from_date, to_date=to_date)
            
            target_filing = None
            # Prioritize 8-K (breaking news) then 10-Q/10-K
            for f in filings:
                if f['form'] == '8-K':
                    target_filing = f
                    break
            
            if not target_filing:
                 for f in filings:
                    if f['form'] in ['10-Q', '10-K']:
                        target_filing = f
                        break
            
            if target_filing and 'reportUrl' in target_filing:
                url = target_filing['reportUrl']
                print(f"Fetching filing text from: {url}")
                text = finnhub_service.extract_filing_text(url)
                # Truncate to avoid exploding context window (e.g. 20k chars)
                if len(text) > 20000:
                    text = text[:20000] + "\n[TRUNCATED...]"
                return text
                
            return ""
            
        except Exception as e:
            print(f"Error fetching filing for {symbol}: {e}")
            return ""

    def _finnhub_latest_quarter_for(self, symbol: str) -> str | None:
        """Indirection so tests can patch quarter discovery without touching FinnhubService.

        Returns 'YYYYQN' (e.g. '2026Q1') for the most recently reported quarter, or None.
        """
        return self._finnhub_service.get_latest_reported_quarter(symbol)

    @staticmethod
    def _transcript_matches_company(transcript_text: str, expected_company: str) -> bool:
        """
        Defensive match: does the transcript text reference the expected company?

        DefeatBeta's HuggingFace dataset has known ticker-collision bugs (e.g.
        'L' returns Loblaw instead of Loews). We verify the first 1500 chars
        of the transcript mention either the full expected name or its first
        significant token (modulo case and common corporate suffixes).

        Returns True if no expected_company was provided (backward compat).
        """
        if not expected_company:
            return True
        if not transcript_text:
            return False

        head = transcript_text[:1500].lower()
        expected_lower = expected_company.lower()
        # Strip common corporate suffixes that may not appear in the call.
        for suffix in (
            " corporation", " corp", " incorporated", " inc.", " inc",
            " plc", " ltd", " limited", " companies", " company", " co.",
            " holdings", " group",
        ):
            if expected_lower.endswith(suffix):
                expected_lower = expected_lower[: -len(suffix)]
        expected_lower = expected_lower.strip(" ,.")

        if not expected_lower:
            return True  # nothing meaningful left to match — accept

        # Match either the full stripped name or its first significant token.
        first_token = expected_lower.split()[0]
        return expected_lower in head or (len(first_token) >= 3 and first_token in head)

    def get_latest_transcript(self, symbol: str, company_name: str = "") -> dict:
        """Fetch the most recent earnings-call transcript with Alpha Vantage fallback.

        Source priority:
            1. DefeatBeta (HuggingFace parquet) — primary source, fast and free.
               If fresh (<= STALE_TRANSCRIPT_DAYS days old) return immediately.
            2. SQLite cache, keyed by (symbol, fiscal_quarter) — checked when DefeatBeta
               is empty or stale, before any AV call.
            3. Alpha Vantage EARNINGS_CALL_TRANSCRIPT — fires only when both DefeatBeta
               is stale/empty AND the cache has nothing for the latest reported quarter.

        Returns a dict with shape: {"text": str, "date": str | None, "warning": str}
        On any failure, returns {"text": "", "date": None, "warning": ""} so callers
        degrade gracefully.

        AV calls are bounded by an in-memory daily counter (see AlphaVantageService).
        """
        empty = {"text": "", "date": None, "warning": ""}

        # Step 1: Try DefeatBeta first (no quarter parameter needed; fast & free).
        db_text = ""
        db_date_str = None
        db_age_days = None
        if _DBTicker is not None:
            try:
                df = _DBTicker(symbol).earning_call_transcripts().get_transcripts_list()
                if df is not None and not df.empty:
                    df = df.sort_values("report_date", ascending=False)
                    row = df.iloc[0]
                    db_date_str = str(row.get("report_date", "")).split(" ")[0]
                    paragraphs = row.get("transcripts")
                    if hasattr(paragraphs, "tolist"):
                        paragraphs = paragraphs.tolist()
                    if isinstance(paragraphs, list):
                        db_text = "\n".join(
                            p.get("content", "")
                            for p in paragraphs
                            if isinstance(p, dict) and p.get("content")
                        )
                    # Defensive: DefeatBeta returns wrong-company transcripts for
                    # some ambiguous tickers (e.g. 'L' → Loblaw). Reject if the
                    # transcript text doesn't reference the expected company.
                    if db_text and not self._transcript_matches_company(db_text, company_name):
                        print(
                            f"[StockService] DefeatBeta company mismatch for {symbol}: "
                            f"expected '{company_name}', transcript head did not match. "
                            f"Discarding and falling through to AV."
                        )
                        db_text = ""
                        db_date_str = None
                        db_age_days = None
                    if db_date_str:
                        try:
                            db_dt = datetime.strptime(db_date_str, "%Y-%m-%d").date()
                            db_age_days = (datetime.utcnow().date() - db_dt).days
                        except ValueError:
                            db_age_days = None
            except Exception as e:
                print(f"[StockService] DefeatBeta transcript fetch failed for {symbol}: {e}")

        # Step 2: If DefeatBeta gave us fresh data, we're done — no Finnhub, no cache, no AV.
        needs_fallback = (not db_text) or (db_age_days is not None and db_age_days > STALE_TRANSCRIPT_DAYS)
        if not needs_fallback:
            return {"text": db_text, "date": db_date_str, "warning": ""}

        # Step 3: Resolve the quarter. Without it we cannot use the cache or call AV.
        quarter = self._finnhub_latest_quarter_for(symbol)
        if not quarter:
            if db_text:
                return {"text": db_text, "date": db_date_str, "warning": ""}
            return empty

        # Step 4: Cache lookup before any AV call.
        cached = get_cached_transcript(symbol, quarter)
        if cached and cached.get("text"):
            return {
                "text": cached["text"],
                "date": cached.get("report_date"),
                "warning": "",
            }

        # Step 5: Call Alpha Vantage.
        av = self.alpha_vantage_service.get_earnings_call_transcript(symbol, quarter)
        av_text = av.get("text", "")

        if av_text:
            # Persist for future scans (immutable per quarter — first-write-wins).
            save_cached_transcript(
                symbol=symbol,
                fiscal_quarter=quarter,
                source="alpha_vantage",
                text=av_text,
                report_date=av.get("report_date"),
            )
            return {"text": av_text, "date": av.get("report_date"), "warning": ""}

        # Step 6: AV gave us nothing — fall back to whatever DefeatBeta had (even if stale).
        if db_text:
            return {"text": db_text, "date": db_date_str, "warning": ""}
        return empty

    def _is_market_open(self, region: str = "US") -> bool:
        """
        Check if the US market is currently open.
        Uses approximate UTC hours.
        """
        now_utc = datetime.now(pytz.utc)
        if now_utc.weekday() >= 5:
            return False

        hour = now_utc.hour + now_utc.minute / 60.0
        # NYSE/NASDAQ: 14:30 - 21:00 UTC (approx)
        return 14.5 <= hour <= 21.0

    def _run_deep_analysis(self, symbol, price, change_percent, stock, company_name, exchange, reasons, market_context, news_data, is_earnings, earnings_date_str, current_version, technical_analysis):
        """
        Runs the deep analysis pipeline for a stock:
        1. DB Pending
        2. Technical Analysis (TradingView) - Now passed in
        3. Filings/Transcripts
        4. Research Agents
        5. DB Final Update
        6. Notifications
        """
        # 1. Add partial decision point to DB (Status: Pending)
        from app.database import add_decision_point, update_decision_point
        
        print(f"Adding pending decision for {symbol}...")
        decision_id = add_decision_point(
            symbol=symbol, 
            price=price, 
            drop_percent=change_percent, 
            recommendation="PENDING", 
            reasoning="Analyzing...", 
            status="Pending",
            company_name=company_name,
            pe_ratio=stock.get("pe_ratio"),
            market_cap=stock.get("market_cap"),
            sector=stock.get("sector", self.stock_metadata.get(symbol, {}).get("sector")),
            region=stock.get("region", self.stock_metadata.get(symbol, {}).get("region")),
            is_earnings_drop=is_earnings,
            earnings_date=earnings_date_str,
            git_version=current_version,
            gatekeeper_tier=reasons.get("tier"),
        )

        # Generate research report
        print(f"Generating research report for {symbol}...")
        
        # Technical Analysis is already fetched and passed in
        # Add Gatekeeper findings to technical analysis passed to agents (if not already done)
        if "gatekeeper_findings" not in technical_analysis:
             technical_analysis["gatekeeper_findings"] = reasons

        # Fetch Filings & Transcript
        print(f"Fetching filings/transcript for {symbol}...")
        filings_text = self.get_latest_filing_text(symbol)
        transcript_data = self.get_latest_transcript(symbol, company_name=company_name)
        transcript_text = ""
        transcript_date = None
        transcript_warning = ""

        if isinstance(transcript_data, dict):
             transcript_text = transcript_data.get("text", "")
             transcript_date = transcript_data.get("date")
             transcript_warning = transcript_data.get("warning")
        else:
             transcript_text = transcript_data or ""

        
        # Use technical analysis data for indicators
        ta_data = technical_analysis 
        indicators = ta_data.get('indicators', {})
        
        # Add cached Gatekeeper indicators if missing
        cached_indicators = stock.get("cached_indicators")
        if cached_indicators:
            for k, v in cached_indicators.items():
                if k not in indicators:
                    indicators[k] = v

        # Pre-fetch structured EPS facts so the PM sees a canonical earnings
        # dict instead of relying on the News Agent to summarize from news
        # articles (different articles cite different consensus numbers).
        try:
            from app.services.finnhub_service import finnhub_service
            earnings_facts = finnhub_service.get_earnings_facts(symbol)
        except Exception as e:
            print(f"[Earnings Facts] Failed to fetch for {symbol}: {e}")
            earnings_facts = None

        # Prepare Raw Data dictionary
        raw_data = {
            "metrics": {
                "pe_ratio": stock.get("pe_ratio"),
                "price_to_book": stock.get("pb_ratio"),
                "peg_ratio": stock.get("peg_ratio"),
                "debt_to_equity": stock.get("debt_to_equity"),
                "profit_margin": stock.get("net_margin")
            },
            "indicators": indicators,
            "news_items": news_data,
            "transcript_text": transcript_text or "", 
            "transcript_date": transcript_date,
            "transcript_warning": transcript_warning,
            "market_context": market_context,
            "change_percent": stock.get("change_percent", 0.0),
            "gatekeeper_tier": reasons.get("tier"),
            "earnings_facts": earnings_facts,
        }

        # Pass raw_data to research service
        report_data = research_service.analyze_stock(symbol, raw_data)
        
        recommendation = report_data.get("recommendation", "HOLD")
        
        # --- EVIDENCE CHECKLIST ---
        checklist = report_data.get("checklist", {})
        news_count = len(news_data)
        latest_news_date = news_data[0]['datetime_str'] if news_count > 0 else "N/A"
        has_transcript = "Yes" if transcript_text and len(transcript_text) > 100 else "No"
        drop_reason_found = "Yes" if checklist.get("drop_reason_identified", False) else "No"
        fed_considered = "Yes" if checklist.get("economics_run", False) else "No"
        
        print(f"\n{'='*40}")
        print(f"*** EVIDENCE CHECKLIST FOR {symbol} ***")
        print(f"  - News Articles: {news_count}")
        print(f"  - Latest News: {latest_news_date}")
        print(f"  - Earnings Call Transcript: {has_transcript}")
        print(f"  - Reason for Drop Identified: {drop_reason_found}")
        print(f"  - FED Report Considered: {fed_considered}")
        print(f"{'='*40}\n")
        
        print(f"*** DECISION FOR {symbol}: {recommendation} ***")
        print(f"{'='*40}\n")
        
        print(f"--- NEWS AGENT SUMMARY for {symbol} ---")
        print(report_data.get('macro_report', 'No News Agent Report available.'))
        print(f"---------------------------------------\n")

        # Construct Reasoning
        reasoning_parts = [
            f"*** EXECUTIVE SUMMARY ***\n{report_data.get('executive_summary', 'N/A')}\n",
            f"*** TECHNICIAN'S REPORT ***\n{report_data.get('technician_report', 'N/A')}\n",
            f"*** RATIONAL BULL CASE ***\n{report_data.get('bull_report', 'N/A')}\n",
            f"*** BEAR'S PRE-MORTEM ***\n{report_data.get('bear_report', 'N/A')}\n",
            f"*** CONTEXTUAL ANALYSIS ***\n{report_data.get('macro_report', 'N/A')}\n",
            f"*** JUDGE'S SYNTHESIS ***\n{report_data.get('detailed_report', 'N/A')}"
        ]
        reasoning = "\n".join(reasoning_parts)
        
        self.research_reports[symbol] = reasoning
        
        # 3-state position lifecycle: BUY/BUY_LIMIT with R/R > 1.0 advances
        # to 'Pending DR Review' until deep research finalizes the verdict.
        # _should_trigger_deep_research mirrors this threshold so rows are
        # never stranded in the pending state.
        rec_upper = recommendation.upper()
        try:
            rr_value = float(report_data.get("risk_reward_ratio") or 0.0)
        except (TypeError, ValueError):
            rr_value = 0.0
        will_trigger_dr = self._should_trigger_deep_research(report_data)
        if rec_upper in ("BUY", "BUY_LIMIT", "STRONG BUY", "STRONG_BUY", "SPECULATIVE BUY", "SPECULATIVE_BUY") and will_trigger_dr and rr_value > 1.0:
            status = "Pending DR Review"
        elif "BUY" in rec_upper and rr_value > 1.0:
            # BUY-flavored verdict that won't trigger DR (edge case): leave
            # as Pending too so it surfaces in the UI as awaiting review.
            status = "Pending DR Review"
        else:
            status = "Not Owned"

        if decision_id:
            import json
            data_depth_str = json.dumps(report_data.get('data_depth', {}))
            
            update_decision_point(
                decision_id, 
                recommendation, 
                reasoning, 
                status, 
                data_depth=data_depth_str,
                # PM trading-level fields (v0.9)
                conviction=report_data.get("conviction"),
                drop_type=report_data.get("drop_type"),
                entry_price_low=report_data.get("entry_price_low"),
                entry_price_high=report_data.get("entry_price_high"),
                stop_loss=report_data.get("stop_loss"),
                take_profit_1=report_data.get("take_profit_1"),
                take_profit_2=report_data.get("take_profit_2"),
                pre_drop_price=report_data.get("pre_drop_price"),
                upside_percent=report_data.get("upside_percent"),
                downside_risk_percent=report_data.get("downside_risk_percent"),
                risk_reward_ratio=report_data.get("risk_reward_ratio"),
                entry_trigger=report_data.get("entry_trigger"),
                reassess_in_days=report_data.get("reassess_in_days"),
                sell_price_low=report_data.get("sell_price_low"),
                sell_price_high=report_data.get("sell_price_high"),
                ceiling_exit=report_data.get("ceiling_exit"),
                exit_trigger=report_data.get("exit_trigger"),
                # Pre-fetched EPS facts (canonical, from Finnhub)
                reported_eps=(earnings_facts or {}).get("reported_eps"),
                consensus_eps=(earnings_facts or {}).get("consensus_eps"),
                surprise_pct=(earnings_facts or {}).get("surprise_pct"),
                earnings_fiscal_quarter=(earnings_facts or {}).get("fiscal_quarter"),
                earnings_narrative_flag=report_data.get("earnings_narrative_flag"),
            )
            print(f"Updated decision point for {symbol}: {recommendation} -> {status} (Conviction: {report_data.get('conviction', 'N/A')})")
            print(f"  > Saved trading levels and Data Depth metrics to DB.")
            print(f"  > Sell Zone: ${report_data.get('sell_price_low', 'N/A')} - ${report_data.get('sell_price_high', 'N/A')} | Ceiling: ${report_data.get('ceiling_exit', 'N/A')}")
            
            # Print Fund Manager Key Factors
            key_factors = report_data.get("key_factors", [])
            if key_factors:
                print("Fund Manager Key Factors:")
                for factor in key_factors:
                     print(f" - {factor}")
            print("") # Newline for spacing

        try:
            # --- DEEP RESEARCH TRIGGER ---
            # Multi-condition gate: only high-probability candidates
            should_trigger = self._should_trigger_deep_research(report_data)
            
            if should_trigger:
                print(f"[StockService] Deep Research Triggered: {recommendation} (Conviction: {report_data.get('conviction')}, R/R: {report_data.get('risk_reward_ratio')})")
                print(f"[StockService] Queuing Deep Research for {symbol}...")
                
                context = self._build_deep_research_context(report_data, raw_data)
                deep_research_service.queue_research_task(
                    symbol=symbol,
                    context=context,
                    decision_id=decision_id
                )
            else:
                print(f"[StockService] Deep Research NOT triggered for {symbol} (Action: {recommendation}, Conviction: {report_data.get('conviction')}, R/R: {report_data.get('risk_reward_ratio')})")
                
        except Exception as e:
            print(f"Error checking Deep Research trigger: {e}")
        
        # Save detailed decision data locally and to Cloud
        decision_data = {
            "symbol": symbol,
            "company_name": company_name,
            "price": price,
            "change_percent": change_percent,
            "recommendation": recommendation,
            "reasoning": reasoning,
            "pe_ratio": stock.get("pe_ratio", 0.0),
            "market_cap": stock.get("market_cap", 0.0),
            "sector": stock.get("sector", self.stock_metadata.get(symbol, {}).get("sector", "Unknown")),
            "region": stock.get("region", self.stock_metadata.get(symbol, {}).get("region", "Unknown")),
            "is_earnings_drop": is_earnings,
            "earnings_date": earnings_date_str,
            "news": news_data,
            "git_version": current_version
        }
        
        storage_service.save_decision_locally(decision_data)
        drive_service.save_decision(decision_data)
        
        # Prepare context for this specific stock
        stock_context = {}
        # Add Indices
        for name, ticker in self.indices_tickers.items():
            stock_context[name] = market_context.get(ticker, 0.0)
        
        # Add Sector if available
        metadata = self.stock_metadata.get(symbol, {})
        sector = metadata.get("sector")
        if sector and sector in self.sector_tickers:
            sector_ticker = self.sector_tickers[sector]
            stock_context[f"Sector ({sector})"] = market_context.get(sector_ticker, 0.0)

        # Conditional Email Notification — trigger on BUY (immediate entry signal)
        if recommendation.upper() == "BUY":
            print(f"Verdict is {recommendation}. Sending email notification.")
            email_service.send_notification(symbol, change_percent, price, report_data, stock_context)
        else:
            print(f"Verdict is {recommendation}. Skipping email notification (Logic: BUY only).")
            
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.sent_notifications.add((symbol, today_str))

        return decision_data


    def _get_previous_trading_day(self, date_obj: datetime.date) -> datetime.date:
        """
        Calculates the previous trading day.
        Monday (0) -> Friday ( -3 days)
        Sunday (6) -> Friday ( -2 days)
        Saturday (5) -> Friday ( -1 day)
        Others -> Yesterday ( -1 day)
        """
        weekday = date_obj.weekday()
        if weekday == 0: # Monday
            return date_obj - timedelta(days=3)
        elif weekday == 6: # Sunday
            return date_obj - timedelta(days=2)
        elif weekday == 5: # Saturday
            return date_obj - timedelta(days=1)
        else:
            return date_obj - timedelta(days=1)



stock_service = StockService()

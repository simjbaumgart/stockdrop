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
from app.database import add_decision_point, get_today_decision_symbols, update_decision_point
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

# FIX: Bypass SSL verification for NLTK download inside DefeatBeta (Global Fix)
import ssl
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

try:
    from defeatbeta_api.data.ticker import Ticker
except ImportError:
    print("DefeatBeta not installed or import failed.")
    Ticker = None



class StockService:
    def __init__(self):
        # Indices tickers: CSI 300 (000300.SS), S&P 500 (^GSPC), STOXX 600 (^STOXX)
        # Indices tickers: CSI 300 (000300.SS), S&P 500 (^GSPC), STOXX 600 (^STOXX)
        # Indices tickers: S&P 500, STOXX 600, China (CSI 300), India (Nifty 50)
        # Indices tickers configuration for TradingView
        # Format: "Name": {"symbol": "...", "screener": "...", "exchange": "..."}
        self.indices_config = {
            "S&P 500": {"symbol": "SPX", "screener": "america", "exchange": "CBOE"},
            # STOXX 600 removed from TradingView config due to API issues. Will be fetched via fallback.
            "India": {"symbol": "NIFTY", "screener": "india", "exchange": "NSE"}
        }
        
        # Keep old tickers for fallback or reference if needed
        self.indices_tickers = {
            "S&P 500": "^GSPC",
            "STOXX 600": "^STOXX",
            "India": "^NSEI"
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
            # Europe (Using STOXX 600 as region proxy, sectors mapped to US ETFs for simplicity or generic)
            "ASML": {"region": "EU", "sector": "Technology"},
            "MC.PA": {"region": "EU", "sector": "Consumer Discretionary"},
            "NESN.SW": {"region": "EU", "sector": "Consumer Staples"},
            "NOVN.SW": {"region": "EU", "sector": "Healthcare"},
            "ROG.SW": {"region": "EU", "sector": "Healthcare"},
            "SAP": {"region": "EU", "sector": "Technology"},
            "AZN": {"region": "EU", "sector": "Healthcare"},
            "SHEL": {"region": "EU", "sector": "Energy"},
            "LIN": {"region": "EU", "sector": "Materials"},
            "OR.PA": {"region": "EU", "sector": "Consumer Staples"},
            "SIE.DE": {"region": "EU", "sector": "Industrials"},
            "TTE.PA": {"region": "EU", "sector": "Energy"},
            "HSBC": {"region": "EU", "sector": "Financials"},
            "ULVR.L": {"region": "EU", "sector": "Consumer Staples"},
            "BP.L": {"region": "EU", "sector": "Energy"},
            "DTE.DE": {"region": "EU", "sector": "Communication Services"},
            "AIR.PA": {"region": "EU", "sector": "Industrials"},
            "EL.PA": {"region": "EU", "sector": "Industrials"},
            # China
            "600519.SS": {"region": "CN", "sector": "Consumer Staples"},
            "300750.SZ": {"region": "CN", "sector": "Industrials"},
            "601318.SS": {"region": "CN", "sector": "Financials"},
            "600036.SS": {"region": "CN", "sector": "Financials"},
            "002594.SZ": {"region": "CN", "sector": "Consumer Discretionary"},
            "BABA": {"region": "CN", "sector": "Consumer Discretionary"},
            "JD": {"region": "CN", "sector": "Consumer Discretionary"},
            "PDD": {"region": "CN", "sector": "Consumer Discretionary"},
            "BIDU": {"region": "CN", "sector": "Communication Services"}
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
        
        # Batch Candidates for Deep Research (Deprecated)
        # self.deep_research_candidates = []


    def get_indices(self) -> Dict:
        # Try fetching from TradingView first
        try:
            data = tradingview_service.get_indices_data(self.indices_config)
            
            # Check if we have valid data for all, or if we need fallback
            # Also check for indices that were not in TradingView config (like STOXX 600)
            expected_indices = ["S&P 500", "STOXX 600", "China", "India"]
            
            for name in expected_indices:
                if name not in data or data[name]["price"] == 0.0:
                    # print(f"Fetching {name} via fallback...") # Reduce noise
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
        print("Checking for large cap drops...")
        # self.deep_research_candidates = [] # Removed batch queue
        potential_batch_candidates = [] # For Deep Research Comparison
        
        # --- GATEKEEPER PHASE 1: Global Market Regime ---
        regime_info = gatekeeper_service.check_market_regime()
        # print(f"Market Regime: {regime_info['regime']} ({regime_info['details']})") # Suppress global noise
        
        if regime_info['regime'] == 'BEAR':
            print("GATEKEEPER: Market is in BEAR regime. Halting long-biased dip buying.")
            return

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
        large_cap_movers = self.get_large_cap_movers(processed_symbols)

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
            region_str = stock.get("region", "Other")
            # Ensure "Germany" is detected if it comes as "Europe (Germany)" or similar
            is_germany = "Germany" in region_str
            is_open = self._is_market_open(region_str)
            
            # Priority Hierarchy:
            # 1. US (Open) -> 100
            # 2. Germany (Open) -> 95
            # 3. EU (Open) -> 90
            # 4. China (Open) -> 80
            # 5. Rest (Open) -> 70
            # 6. US (Closed) -> 60
            # 7. EU (Closed) -> 50
            # ...
            
            # Base Score
            score = 0
            if is_open:
                score += 40  # Open base
            
            # Region Score
            if region_str in ["America", "US"] or "America" in region_str:
                score += 60
            elif is_germany:
                 score += 55 # Higher than standard EU
            elif "Europe" in region_str or region_str == "EU":
                score += 50
            elif "China" in region_str or region_str == "CN":
                score += 40
            else:
                score += 30
                
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

        # First sort by symbol alphabetic
        large_cap_movers.sort(key=lambda x: x["symbol"])
        
        # Then sort by priority score descending
        large_cap_movers.sort(key=get_priority_score, reverse=True)
        
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
                        cached_indicators=cached_indicators
                    )
                    
                    if not is_valid:
                        print(f"GATEKEEPER: {symbol} REJECTED.")
                        
                        # Primary Reason
                        if 'bb_status' in reasons:
                            print(f"  [PRIMARY REASON] {reasons['bb_status']}")
                            
                        # Context Data
                        print("  [CONTEXT]")
                        for key, value in reasons.items():
                            if key == 'bb_status': continue
                            try:
                                val_to_print = f"{float(value):.2f}"
                            except (ValueError, TypeError):
                                val_to_print = value
                            print(f"    {key}: {val_to_print}")
                            
                        # Optionally log this rejection to DB or file?
                        continue
                        
                    print(f"GATEKEEPER: {symbol} APPROVED. Reasons: {reasons}")
                    
                    print(f"Triggering notification for {symbol} ({change_percent:.2f}%)")
                    
                    # Fetch Technical Analysis (MOVED UP)
                    print(f"Fetching technical analysis for {symbol}...")
                    import time
                    time.sleep(2) # Avoid 429 from Gatekeeper call
                    technical_analysis = tradingview_service.get_technical_analysis(symbol, region=stock.get("region", "US"))
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
                        score = res.get('ai_score', 0)
                        if "STRONG BUY" in rec.upper() or ("BUY" in rec.upper() and score >= 75):
                            print(f"[Batch Comparison] Adding {symbol} to candidate list (Score: {score})")
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

    def _process_deep_research_backfill(self, date_str: str):
        """
        Checks for stocks analyzed TODAY (or date_str) that have a high AI score (>=70)
        but are missing a Deep Research Verdict. Triggers Deep Research using the Summary Report.
        """
        print("\n[Backfill] Checking for outstanding Deep Research candidates...")
        try:
            conn = sqlite3.connect("subscribers.db")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Query candidates: Same Date, Score >= 70, Verdict is NULL/Empty/Unknown/Dash
            query = """
                SELECT * FROM decision_points 
                WHERE date(timestamp) = ? 
                AND ai_score >= 70 
                AND (recommendation LIKE '%BUY%' OR recommendation LIKE '%STRONG BUY%')
                AND (deep_research_verdict IS NULL OR deep_research_verdict = '' OR deep_research_verdict = '-' OR deep_research_verdict LIKE 'UNKNOWN%')
            """
            cursor.execute(query, (date_str,))
            rows = cursor.fetchall()
            conn.close()
            
            candidates = [dict(row) for row in rows]
            
            if not candidates:
                print("[Backfill] No outstanding candidates found.")
                return

            print(f"[Backfill] Found {len(candidates)} candidates needing Deep Research: {[c['symbol'] for c in candidates]}")
            
            # Use DeepResearchService to queue them
            # We use the existing service instance
            
            for c in candidates:
                symbol = c['symbol']
                decision_id = c['id']
                detailed_report = c.get('detailed_report', '')
                drop_percent = c.get('drop_percent', 0.0)
                
                # Try to load from file first (Preferred source)
                # Format: data/council_reports/{ticker}_{date}_council1.json
                report_file_path = f"data/council_reports/{symbol}_{date_str}_council1.json"
                file_report_content = None
                
                if os.path.exists(report_file_path):
                    try:
                        with open(report_file_path, 'r') as f:
                            file_content = f.read()
                            # Check length
                            if len(file_content) > 100:
                                file_report_content = file_content
                                print(f"  > Loaded council report from file: {report_file_path} (Length: {len(file_content)})")
                            else:
                                print(f"  > Council report file found but too short ({len(file_content)} chars). Skipping file.")
                    except Exception as e:
                        print(f"  > Error reading council report file: {e}")
                
                # Use file content if available, otherwise DB
                summary_report = file_report_content if file_report_content else detailed_report
                
                if not summary_report:
                    print(f"  > Skipping {symbol}: No detailed_report available (File or DB).")
                    continue
                
                # Final length check safety
                if len(str(summary_report)) < 50:
                     print(f"  > Skipping {symbol}: Report content too short.")
                     continue
                    
                print(f"  > Triggering Backfill for {symbol} (Score: {c['ai_score']})...")
                
                # We need to construct the payload for the queue.
                # Since 'summary_report' is a new concept in execute_deep_research, 
                # we need to make sure the worker handles it.
                # However, the worker calls 'execute_deep_research' using kwargs unpacked from payload.
                # We updated 'execute_deep_research' signature, but we also need to update
                # 'queue_research_task' or manually put into queue if we want it async.
                # Or we can just call execute_deep_research SYNCHRONOUSLY here if we want to ensure it runs now?
                # User said: "run the deep research reports, once a cycle is complete".
                # If we queue them, the single worker will pick them up. This is safer for rate limits.
                
                # Let's extend 'queue_research_task' or generic 'queue' put.
                # Since we modified the signature of execute_deep_research, we can add 'summary_report' field to payload.
                
                payload = {
                    'symbol': symbol,
                    'raw_news': None, # Not available
                    'technical_data': None, # Not available
                    'drop_percent': drop_percent,
                    'decision_id': decision_id,
                    'transcript_text': "",
                    'transcript_date': None,
                    'transcript_warning': None,
                    'summary_report': summary_report # This needs to be passed to execute_deep_research
                }
                
                # We need to ensure _process_individual_task passes 'summary_report' to execute_deep_research
                # The worker code:
                # self._process_individual_task(task_payload)
                #   -> execute_deep_research(..., payload['transcript_warning'])
                # It likely doesn't pass 'summary_report' yet. We need to update that too.
                # But first let's queue it.
                
                deep_research_service.individual_queue.put({'type': 'individual', 'payload': payload})
                print(f"  > Queued backfill task for {symbol}")
                
        except Exception as e:
            print(f"[Backfill] Error processing backfill: {e}")

    def _is_actively_traded(self, symbol: str, region: str = "US", volume: float = 0, exchange: str = "", name: str = "") -> bool:
        """
        Checks if the stock is actively traded to avoid illiquid tickers.
        Criteria: Avg volume > 50k over last 5 days.
        """
        # 1. Faster Check: Use volume from Screener if available
        if volume > 50000:
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
            if avg_vol < 50000:
                print(f"  > [Active Check] {yf_symbol} Volume Low ({int(avg_vol)} < 50k). Skipping.")
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

    def get_aggregated_news(self, symbol: str, region: str = "US", exchange: str = "", company_name: str = "") -> List[Dict]:
        """
        Fetches and aggregates news from Benzinga (Primary), Alpha Vantage, Finnhub, and yfinance.
        Returns a standardised list of news items.
        
        Standard Object:
        {
            "source": str,
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
                            
                        other_items.append({
                            "source": item.get('source', 'Finnhub'),
                            "provider": "Finnhub",
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
                    
                    other_items.append({
                        "source": content.get('provider', {}).get('displayName', 'Yahoo Finance'),
                        "provider": "Yahoo Finance",
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
            
            # Determine exchange (defaults to NASDAQ if not found, or use a map)
            exchange = "NASDAQ" # Default
            if symbol in self.stock_metadata:
                region = self.stock_metadata[symbol].get("region", "US")
                if region == "EU": exchange = "XETR" # Germany
                elif region == "CN": exchange = "SSE"
                elif region == "IN": exchange = "NSE"
                elif region == "AU": exchange = "ASX"
                
            headers = scraper.scrape_headlines(symbol=symbol, exchange=exchange)
            
            for item in headers:
                try:
                    title = item.get('title', 'No Title')
                    # Deduplicate
                    if any(n['headline'] == title for n in massive_items + other_items):
                        continue
                        
                    ts = item.get('published', 0)
                    dt_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    
                    other_items.append({
                        "source": item.get('source', 'TradingView'),
                        "provider": "TradingView",
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

    def get_latest_transcript(self, symbol: str) -> str:
        """
        Fetches the text of the most recent earnings call transcript.
        """
        try:

            # 1. Try DefeatBeta First
            print(f"[StockService] Fetching transcript for {symbol} from DefeatBeta...")
            try:
                if Ticker is None:
                    raise ImportError("DefeatBeta Ticker not available")
                
                db_ticker = Ticker(symbol)
                transcripts = db_ticker.earning_call_transcripts()
                df = transcripts.get_transcripts_list()
                
                # df is a pandas DataFrame with columns like 'transcripts', 'report_date', etc.
                if not df.empty:
                    # Sort by date descending if possible, or take first
                    if 'report_date' in df.columns:
                        df = df.sort_values('report_date', ascending=False)
                    
                    latest_row = df.iloc[0]
                    
                    # Get date from DefeatBeta
                    db_date_str = str(latest_row.get('report_date', '')).split(' ')[0]
                    
                    # 'transcripts' column contains the data (numpy array of dicts)
                    transcripts_data = latest_row.get('transcripts', None)
                    
                    # Handle numpy array if needed
                    import numpy as np
                    if isinstance(transcripts_data, np.ndarray):
                        transcripts_data = transcripts_data.tolist()
                        
                    content = ""
                    if isinstance(transcripts_data, list):
                         for part in transcripts_data: 
                             if isinstance(part, dict) and 'content' in part:
                                 content += part['content'] + "\n"
                    
                    if content:
                        print(f"[StockService] Successfully fetched transcript from DefeatBeta for {symbol} (Len: {len(content)}).")
                        
                        recency_check_db = self._check_transcript_recency(symbol, db_date_str)
                        return {
                            "text": content,
                            "date": db_date_str,
                            "is_outdated": recency_check_db["is_outdated"],
                            "warning": recency_check_db["message"]
                        }
            except Exception as e:
                print(f"[StockService] DefeatBeta failed: {e}")

            # 2. Fallback to Finnhub
            print(f"[StockService] DefeatBeta transcript empty or failed for {symbol}. Trying Finnhub...")
            
            transcripts = finnhub_service.get_transcript_list(symbol)
            if not transcripts:
                return ""
                
            latest_id = transcripts[0]['id']
            content = finnhub_service.get_transcript_content(latest_id)
            
            # Parse content (list of speech objects)
            full_text = []
            if 'transcript' in content:
                for chunk in content['transcript']:
                    speaker = chunk.get('name', 'Unknown')
                    speech = chunk.get('speech', [])
                    # Join list of speech strings
                    speech_text = " ".join(speech) if isinstance(speech, list) else str(speech)
                    full_text.append(f"{speaker}: {speech_text}")
            
            text = "\n".join(full_text)
            
            # Extract date from Finnhub metadata if available
            transcript_date_str = None
            if transcripts and len(transcripts) > 0:
                 # Finnhub usually provides 'time' as "2024-10-25 10:00:00" or similar
                 transcript_date_str = transcripts[0].get('time', '').split(' ')[0]

            # Check recency
            recency_check = self._check_transcript_recency(symbol, transcript_date_str)
            
            if text:
                 return {
                     "text": text,
                     "date": transcript_date_str,
                     "is_outdated": recency_check["is_outdated"],
                     "warning": recency_check["message"]
                 }
                 
            return {"text": "", "date": None, "is_outdated": False, "warning": ""}

        except Exception as e:
            print(f"[StockService] Error in get_latest_transcript for {symbol}: {e}")
            return {"text": "", "date": None, "is_outdated": False, "warning": ""}

    def _check_transcript_recency(self, symbol: str, transcript_date_str: str) -> Dict[str, Any]:
        """
        Checks if the provided transcript date is reasonably close to the most recent earnings date provided by Yahoo Finance.
        Returns a dict with 'is_outdated' (bool) and 'message' (str).
        """
        result = {"is_outdated": False, "message": ""}
        if not transcript_date_str:
            return result
        
        try:
            # Parse transcript date
            # Try a few formats or just isoformat
            try:
                transcript_date = datetime.strptime(transcript_date_str, "%Y-%m-%d").date()
            except ValueError:
                return result # Cannot parse, assume fine

            ticker = yf.Ticker(symbol)
            # earnings_dates index is datetime (tz-aware usually)
            earnings = ticker.earnings_dates
            
            if earnings is None or earnings.empty:
                return result

            # Filter for Past earnings only (handle timezones safely by converting to date)
            now_date = datetime.now().date()
            
            # Sort descending just in case
            earnings = earnings.sort_index(ascending=False)
            
            past_earnings = []
            for dt in earnings.index:
                # Convert to simple date
                e_date = dt.date()
                if e_date <= now_date:
                    past_earnings.append(e_date)
            
            if not past_earnings:
                return result
                
            last_earnings_date = past_earnings[0]
            
            # Check difference
            # Allow 10 days buffer? Sometimes transcripts are delayed or date mismatch
            delta = abs((last_earnings_date - transcript_date).days)
            
            # If the last confirmed earnings date is significantly newer (e.g. > 14 days) than our transcript 
            # OR if our transcript is significantly OLDER than the last earnings date.
            
            # Case: Transcript is from May, Last Earnings was August -> Outdated.
            days_since_last_earnings = (last_earnings_date - transcript_date).days
            
            if days_since_last_earnings > 15: # More than 2 weeks lag
                result["is_outdated"] = True
                result["message"] = f"WARNING: The available earnings transcript is dated {transcript_date_str}, but Yahoo Finance indicates a more recent earnings call occurred on {last_earnings_date}. The transcript may be outdated."
                print(f"[StockService] Recency Warning for {symbol}: {result['message']}")
            
            return result

        except Exception as e:
            print(f"[StockService] Error checking recency for {symbol}: {e}")
            return result
            
        except Exception as e:
            # Likely 403 Forbidden for free tier
            # print(f"Error fetching transcript for {symbol}: {e}") 
            # Silently fail or log debug
            return ""

    def _is_market_open(self, region: str) -> bool:
        """
        Heuristic to check if a market region is currently open.
        Uses approximate UTC hours.
        """
        now_utc = datetime.now(pytz.utc)
        # Weekday check (0=Mon, 6=Sun)
        if now_utc.weekday() >= 5:
            return False
            
        hour = now_utc.hour + now_utc.minute / 60.0
        
        if "America" in region or region == "US":
            # NYSE/NASDAQ: 14:30 - 21:00 UTC (approx)
            return 14.5 <= hour <= 21.0
        elif "Europe" in region or region == "EU":
            # London/Frankfurt: 08:00 - 16:30 UTC (approx)
            return 8.0 <= hour <= 16.5
        elif "China" in region or region == "CN":
            # Shanghai: 01:30 - 07:00 UTC (approx, ignoring lunch break)
            return 1.5 <= hour <= 7.0
        
        return False

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
            git_version=current_version
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
        transcript_data = self.get_latest_transcript(symbol)
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
            "change_percent": stock.get("change_percent", 0.0)
        }

        # Pass raw_data to research service
        report_data = research_service.analyze_stock(symbol, raw_data)
        
        recommendation = report_data.get("recommendation", "HOLD")
        score = report_data.get("score", "N/A")
        
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
        
        print(f"*** DECISION FOR {symbol}: {recommendation} (Score: {score}/100) ***")
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
        
        # Update Status
        status = "Owned" if recommendation == "BUY" else "Not Owned"
        try:
            float_score = float(score)
            
            if float_score >= 7.0:
                status = "Owned"
            else:
                status = "Not Owned"
        except:
            if recommendation == "BUY":
                status = "Owned"
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
                ai_score=float(score) if isinstance(score, (int, float)) else None,
                data_depth=data_depth_str
            )
            print(f"Updated decision point for {symbol}: {recommendation} -> {status} (Score: {score})")
            print(f"  > Saved Data Depth metrics to DB.")
            
            # Print Fund Manager Rationale
            key_points = report_data.get("key_decision_points", [])
            if key_points:
                print("Fund Manager Rationale:")
                for point in key_points:
                     print(f" - {point}")
            print("") # Newline for spacing

        try:
            float_score = float(score)
            
            # --- DEEP RESEARCH TRIGGER ---
            # Criteria: 
            # 1. Recommendation is BUY with Score >= 70
            # 2. OR Recommendation is STRONG BUY (No score cutoff)
            
            is_buy = "BUY" == recommendation.upper()
            is_strong_buy = "STRONG BUY" in recommendation.upper() # Covers STRONG BUY
            
            should_trigger = False
            
            if is_strong_buy:
                should_trigger = True
                print(f"[StockService] Deep Research Triggered: STRONG BUY detected.")
            elif is_buy and float_score >= 70.0:
                should_trigger = True
                print(f"[StockService] Deep Research Triggered: BUY with Score {float_score} >= 70.")
            
            if should_trigger:
                print(f"[StockService] Queuing Deep Research for {symbol}...")
                
                # Extract additional reports
                market_sentiment_report = report_data.get('market_sentiment_report', '')
                competitive_report = report_data.get('competitive_report', '')
                
                deep_research_service.queue_research_task(
                    symbol=symbol,
                    raw_news=news_data,
                    technical_data=technical_analysis,
                    drop_percent=change_percent,
                    decision_id=decision_id,
                    transcript_text=transcript_text or "",
                    transcript_date=transcript_date,
                    transcript_warning=transcript_warning,
                    market_sentiment_report=market_sentiment_report,
                    competitive_report=competitive_report
                )
                
        except Exception as e:
            print(f"Error checking Deep Research trigger: {e}")
                        
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
            "ai_score": float(score) if isinstance(score, (int, float, str)) and str(score).replace('.', '', 1).isdigit() else 0,
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

        # Conditional Email Notification
        if "STRONG BUY" in recommendation.upper():
            print(f"Verdict is {recommendation}. Sending email notification.")
            email_service.send_notification(symbol, change_percent, price, report_data, stock_context)
        else:
            print(f"Verdict is {recommendation}. Skipping email notification (Logic: Strong Buy only).")
            
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

import yfinance as yf
import pandas as pd
import os
from typing import List, Dict, Set
from datetime import datetime
from app.services.email_service import email_service
from app.services.research_service import research_service
from app.services.alpaca_service import alpaca_service
from app.services.tradingview_service import tradingview_service
from app.services.drive_service import drive_service
from app.services.storage_service import storage_service

class StockService:
    def __init__(self):
        # Indices tickers: CSI 300 (000300.SS), S&P 500 (^GSPC), STOXX 600 (^STOXX)
        # Indices tickers: CSI 300 (000300.SS), S&P 500 (^GSPC), STOXX 600 (^STOXX)
        # Indices tickers: S&P 500, STOXX 600, China (CSI 300), India (Nifty 50), Australia (ASX 200)
        # Indices tickers configuration for TradingView
        # Format: "Name": {"symbol": "...", "screener": "...", "exchange": "..."}
        self.indices_config = {
            "S&P 500": {"symbol": "SPX", "screener": "america", "exchange": "CBOE"},
            # STOXX 600 removed from TradingView config due to API issues. Will be fetched via fallback.
            "China": {"symbol": "000300", "screener": "china", "exchange": "SSE"},
            "India": {"symbol": "NIFTY", "screener": "india", "exchange": "NSE"},
            "Australia": {"symbol": "XJO", "screener": "australia", "exchange": "ASX"}
        }
        
        # Keep old tickers for fallback or reference if needed
        self.indices_tickers = {
            "S&P 500": "^GSPC",
            "STOXX 600": "^STOXX",
            "China": "000300.SS",
            "India": "^NSEI",
            "Australia": "^AXJO"
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

    def get_indices(self) -> Dict:
        # Try fetching from TradingView first
        try:
            data = tradingview_service.get_indices_data(self.indices_config)
            
            # Check if we have valid data for all, or if we need fallback
            # Also check for indices that were not in TradingView config (like STOXX 600)
            expected_indices = ["S&P 500", "STOXX 600", "China", "India", "Australia"]
            
            for name in expected_indices:
                if name not in data or data[name]["price"] == 0.0:
                    # print(f"Fetching {name} via fallback...") # Reduce noise
                    fallback_data = self._get_index_fallback(name)
                    data[name] = fallback_data
            return data
        except Exception as e:
            print(f"Error in get_indices (TradingView): {e}")
            # Fallback to yfinance for all
            return self._get_indices_fallback_all()

    def _get_index_fallback(self, name: str) -> Dict:
        ticker = self.indices_tickers.get(name)
        if not ticker:
            return {"price": 0.0, "change": 0.0, "change_percent": 0.0}
            
        try:
            ticker_obj = yf.Ticker(ticker)
            price = ticker_obj.fast_info.last_price
            prev_close = ticker_obj.fast_info.previous_close
            
            if price and prev_close:
                change = price - prev_close
                change_percent = (change / prev_close) * 100
                return {
                    "price": price,
                    "change": change,
                    "change_percent": change_percent
                }
            else:
                hist = ticker_obj.history(period="1d")
                if not hist.empty:
                    price = hist["Close"].iloc[-1]
                    return {"price": price, "change": 0.0, "change_percent": 0.0}
        except Exception as e:
            print(f"Fallback error for {name}: {e}")
            
        return {"price": 0.0, "change": 0.0, "change_percent": 0.0}

    def _get_indices_fallback_all(self) -> Dict:
        data = {}
        for name in self.indices_tickers:
            data[name] = self._get_index_fallback(name)
        return data

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
                
            prev_close = snapshot.prev_daily_bar.close if snapshot.prev_daily_bar else 0.0
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
        # Alpaca doesn't provide easy option dates list like yfinance
        # Returning empty for now
        return []

    def get_option_chain(self, symbol: str, date: str) -> Dict:
        return alpaca_service.get_option_chain(symbol)

        return context_data

    def _fetch_market_context(self) -> Dict[str, float]:
        """
        Fetches the current percentage change for all tracked indices and sectors.
        Uses TradingView for Indices (more reliable for global) and yfinance for Sectors.
        """
        context_data = {}
        
        # 1. Fetch Indices from TradingView
        try:
            indices_data = tradingview_service.get_indices_data(self.indices_config)
            for name, data in indices_data.items():
                context_data[name] = data.get("change_percent", 0.0)
        except Exception as e:
            print(f"Error fetching indices context: {e}")
            
        # 2. Fetch Sectors from yfinance (US ETFs)
        try:
            tickers_str = " ".join(self.sector_tickers.values())
            tickers = yf.Tickers(tickers_str)
            for name, symbol in self.sector_tickers.items():
                try:
                    ticker = tickers.tickers[symbol]
                    # Use history for sectors as fast_info might be delayed/empty
                    hist = ticker.history(period="1d")
                    if not hist.empty:
                        # Calculate change from open or prev close? 
                        # yfinance history 'Close' is current price if market open?
                        # Let's use fast_info if available, else history
                        price = ticker.fast_info.last_price
                        prev_close = ticker.fast_info.previous_close
                        
                        if price and prev_close:
                            change = price - prev_close
                            change_percent = (change / prev_close) * 100
                            context_data[symbol] = change_percent
                        else:
                             context_data[symbol] = 0.0
                    else:
                        context_data[symbol] = 0.0
                except Exception:
                    context_data[symbol] = 0.0
        except Exception as e:
             print(f"Error fetching sector context: {e}")
             
        return context_data

    def check_large_cap_drops(self):
        """
        Checks for large cap stocks that have dropped more than 6% (normalized)
        and sends an email notification if not already sent today.
        """
        print("Checking for large cap drops...")
        
        # 1. Fetch Market Context
        market_context = self._fetch_market_context()
        print(f"Market Context: {market_context}")
        
        # 2. Load already processed symbols to prevent duplicates and for logging
        today_str = datetime.now().strftime("%Y-%m-%d")
        processed_symbols = set(storage_service.get_today_decisions())
        
        # Also check database for robust deduplication
        from app.database import get_today_decision_symbols
        db_processed_symbols = get_today_decision_symbols()
        processed_symbols.update(db_processed_symbols)
        
        for symbol in processed_symbols:
            self.sent_notifications.add((symbol, today_str))
            
        print(f"Already processed today: {processed_symbols}")

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
        pending_processing = []
        for stock in large_cap_movers:
            if stock["change_percent"] <= -5.0:
                if (stock["symbol"], today_str) not in self.sent_notifications:
                    pending_processing.append(stock["symbol"])
        
        print(f"Pending processing: {pending_processing}")
        
        for stock in large_cap_movers:
            symbol = stock["symbol"]
            change_percent = stock["change_percent"]
            price = stock["price"]
            
            # Check if drop is more than 7% (absolute)
            # User requested to ignore market context normalization for the trigger
            if change_percent <= -5.0:
                notification_key = (symbol, today_str)
                
                if notification_key not in self.sent_notifications:
                    print(f"Triggering notification for {symbol} ({change_percent:.2f}%)")
                    
                    # Get company name
                    company_name = stock.get("description", stock.get("name", symbol))

                    # Check for earnings proximity
                    is_earnings, earnings_date = self._check_earnings_proximity(symbol)
                    
                    # 1. Save initial "Pending" state to DB immediately
                    from app.database import add_decision_point, update_decision_point
                    
                    print(f"Adding pending decision for {symbol}...")
                    decision_id = add_decision_point(
                        symbol, 
                        price, 
                        change_percent, 
                        "ANALYZING", # Recommendation
                        "Gemini is analyzing this stock...", # Reasoning
                        "Pending", # Status
                        company_name=company_name,
                        pe_ratio=stock.get("pe_ratio", 0.0),
                        market_cap=stock.get("market_cap", 0.0),
                        sector=stock.get("sector", self.stock_metadata.get(symbol, {}).get("sector", "Unknown")),
                        region=stock.get("region", self.stock_metadata.get(symbol, {}).get("region", "Unknown")),
                        is_earnings_drop=is_earnings,
                        earnings_date=earnings_date
                    )

                    # Generate research report
                    print(f"Generating research report for {symbol}...")
                    
                    # Pass company name to research service
                    report_data = research_service.analyze_stock(symbol, company_name, price, change_percent)
                    
                    recommendation = report_data.get("recommendation", "HOLD")
                    # Use executive summary as the main reasoning text for DB/Display
                    reasoning = report_data.get("executive_summary", report_data.get("full_text", ""))
                    
                    self.research_reports[symbol] = reasoning
                    
                    # 2. Update decision point with final result
                    status = "Owned" if recommendation == "BUY" else "Not Owned"
                    if decision_id:
                        update_decision_point(decision_id, recommendation, reasoning, status)
                        print(f"Updated decision point for {symbol}: {recommendation} -> {status}")
                    
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
                        "earnings_date": earnings_date
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

                    email_service.send_notification(symbol, change_percent, price, report_data, stock_context)
                    self.sent_notifications.add(notification_key)

    def _check_earnings_proximity(self, symbol: str) -> tuple[bool, str]:
        """
        Checks if the current date is close to an earnings date (within -2 to +1 days).
        Returns (is_earnings_drop, earnings_date_str).
        """
        try:
            ticker = yf.Ticker(symbol)
            # Try to get earnings dates history
            earnings_dates = ticker.earnings_dates
            
            if earnings_dates is None or earnings_dates.empty:
                return False, None
                
            # Convert index to date objects (index is usually datetime)
            dates = earnings_dates.index.date
            today = datetime.now().date()
            
            for date in dates:
                # Check if today is within range [earnings_date - 1 day, earnings_date + 2 days]
                # Usually drops happen immediately after earnings (next day) or same day if after hours
                delta = (today - date).days
                
                # If delta is 0 (same day), 1 (day after), or -1 (day before - anticipation?)
                # Let's say -1 to +2 to be safe
                if -1 <= delta <= 2:
                    return True, date.strftime("%Y-%m-%d")
                    
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

stock_service = StockService()

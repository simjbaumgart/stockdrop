import yfinance as yf
from typing import List, Dict, Set
from datetime import datetime
from app.services.email_service import email_service
from app.services.research_service import research_service

class StockService:
    def __init__(self):
        # Indices tickers: CSI 300 (000300.SS), S&P 500 (^GSPC), STOXX 600 (^STOXX)
        # Indices tickers: CSI 300 (000300.SS), S&P 500 (^GSPC), STOXX 600 (^STOXX)
        self.indices_tickers = {
            "CSI 300": "000300.SS",
            "S&P 500": "^GSPC",
            "STOXX 600": "^STOXX"
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
        
        # A curated list of major stocks to track for "Biggest Movers"
        # Expanded list to include more large cap stocks across sectors
        self.stock_tickers = [
            # Tech / Communication
            "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "NFLX", "ADBE", "CRM", "AMD", "INTC", "CSCO", "ORCL",
            # Finance
            "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "MS", "GS", "AXP", "BLK",
            # Healthcare
            "LLY", "JNJ", "UNH", "MRK", "ABBV", "PFE", "TMO", "DHR", "ABT", "BMY",
            # Consumer
            "WMT", "PG", "HD", "KO", "PEP", "COST", "MCD", "NKE", "DIS", "SBUX",
            # Energy / Industrial
            "XOM", "CVX", "COP", "SLB", "GE", "CAT", "UPS", "HON", "LMT", "RTX",
            # Europe
            "ASML", "MC.PA", "NESN.SW", "NOVN.SW", "ROG.SW", "SAP", "AZN", "SHEL", "LIN", "OR.PA",
            "SIE.DE", "TTE.PA", "HSBC", "ULVR.L", "BP.L", "DTE.DE", "AIR.PA", "EL.PA",
            # China (Major ones available on Yahoo)
            "600519.SS", "300750.SZ", "601318.SS", "600036.SS", "002594.SZ", "BABA", "JD", "PDD", "BIDU"
        ]

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
        
        # Cache to store sent notifications: Set[(symbol, date_str)]
        self.sent_notifications: Set[tuple] = set()
        
        # Store research reports: Dict[symbol, report_text]
        self.research_reports: Dict[str, str] = {}

    def get_indices(self) -> Dict:
        data = {}
        for name, ticker in self.indices_tickers.items():
            try:
                ticker_obj = yf.Ticker(ticker)
                price = ticker_obj.fast_info.last_price
                prev_close = ticker_obj.fast_info.previous_close
                
                if price and prev_close:
                    change = price - prev_close
                    change_percent = (change / prev_close) * 100
                    
                    data[name] = {
                        "price": price,
                        "change": change,
                        "change_percent": change_percent
                    }
                else:
                    hist = ticker_obj.history(period="1d")
                    if not hist.empty:
                        price = hist["Close"].iloc[-1]
                        data[name] = {"price": price, "change": 0.0, "change_percent": 0.0}
            except Exception as e:
                print(f"Error fetching {name}: {e}")
                data[name] = {"price": 0.0, "change": 0.0, "change_percent": 0.0}
        return data

    def get_top_movers(self) -> List[Dict]:
        return self._fetch_and_sort_stocks(limit=10)

    def get_large_cap_movers(self) -> List[Dict]:
        # Filter for Market Cap > 500 Million USD
        # Note: 500 Million is 500,000,000
        return self._fetch_and_sort_stocks(limit=10, min_market_cap=500_000_000)

    def _fetch_and_sort_stocks(self, limit: int, min_market_cap: float = 0) -> List[Dict]:
        stocks_data = []
        tickers_str = " ".join(self.stock_tickers)
        try:
            tickers = yf.Tickers(tickers_str)
            
            for symbol in self.stock_tickers:
                try:
                    ticker = tickers.tickers[symbol]
                    
                    # Check market cap if filter is applied
                    if min_market_cap > 0:
                        market_cap = ticker.fast_info.market_cap
                        if not market_cap or market_cap < min_market_cap:
                            continue

                    price = ticker.fast_info.last_price
                    prev_close = ticker.fast_info.previous_close
                    
                    if price and prev_close:
                        change = price - prev_close
                        change_percent = (change / prev_close) * 100
                        
                        stocks_data.append({
                            "symbol": symbol,
                            "name": symbol, # Using symbol as name for performance
                            "price": price,
                            "change": change,
                            "change_percent": change_percent
                        })
                except Exception:
                    continue
                    
            sorted_stocks = sorted(stocks_data, key=lambda x: abs(x["change_percent"]), reverse=True)
            return sorted_stocks[:limit]
            
        except Exception as e:
            print(f"Error fetching stocks: {e}")
            return []

    def get_stock_details(self, symbol: str) -> Dict:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            
            # Try to get pre-market data from 'info' dict (slower but has more data)
            # or fallback to fast_info if possible (fast_info usually doesn't have pre-market)
            # We will use .info for details page as speed is less critical than dashboard
            full_info = ticker.info
            
            return {
                "symbol": symbol,
                "name": full_info.get("longName", symbol),
                "price": info.last_price,
                "previous_close": info.previous_close,
                "open": info.open,
                "day_high": info.day_high,
                "day_low": info.day_low,
                "volume": info.last_volume,
                "market_cap": info.market_cap,
                "pre_market_price": full_info.get("preMarketPrice"),
                "currency": info.currency
            }
        except Exception as e:
            print(f"Error fetching details for {symbol}: {e}")
            return {}

    def get_options_dates(self, symbol: str) -> List[str]:
        try:
            ticker = yf.Ticker(symbol)
            return list(ticker.options)
        except Exception as e:
            print(f"Error fetching options dates for {symbol}: {e}")
            return []

    def get_option_chain(self, symbol: str, date: str) -> Dict:
        try:
            ticker = yf.Ticker(symbol)
            chain = ticker.option_chain(date)
            
            # Convert DataFrames to list of dicts
            calls = chain.calls.fillna(0).to_dict(orient="records")
            puts = chain.puts.fillna(0).to_dict(orient="records")
            
            return {
                "calls": calls,
                "puts": puts
            }
        except Exception as e:
            print(f"Error fetching option chain for {symbol} on {date}: {e}")
            return {"calls": [], "puts": []}

    def _fetch_market_context(self) -> Dict[str, float]:
        """
        Fetches the current percentage change for all tracked indices and sectors.
        Returns a dictionary mapping ticker symbol (or name) to percentage change.
        """
        context_data = {}
        
        # Combine all tickers to fetch: Indices + Sectors
        all_tickers = list(self.indices_tickers.values()) + list(self.sector_tickers.values())
        tickers_str = " ".join(all_tickers)
        
        try:
            tickers = yf.Tickers(tickers_str)
            
            for symbol in all_tickers:
                try:
                    ticker = tickers.tickers[symbol]
                    price = ticker.fast_info.last_price
                    prev_close = ticker.fast_info.previous_close
                    
                    if price and prev_close:
                        change = price - prev_close
                        change_percent = (change / prev_close) * 100
                        context_data[symbol] = change_percent
                    else:
                        context_data[symbol] = 0.0
                except Exception:
                    context_data[symbol] = 0.0
                    
        except Exception as e:
            print(f"Error fetching market context: {e}")
            
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
        
        # 2. Fetch Large Cap Movers
        large_cap_movers = self.get_large_cap_movers()
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        for stock in large_cap_movers:
            symbol = stock["symbol"]
            change_percent = stock["change_percent"]
            price = stock["price"]
            
            # 3. Calculate Normalized Change
            metadata = self.stock_metadata.get(symbol, {})
            region = metadata.get("region", "US")
            sector = metadata.get("sector")
            
            reference_change = 0.0
            reference_source = "None"
            
            # Determine reference index/sector
            if region == "US":
                # Try to use Sector ETF first
                if sector and sector in self.sector_tickers:
                    sector_ticker = self.sector_tickers[sector]
                    reference_change = market_context.get(sector_ticker, 0.0)
                    reference_source = f"Sector ({sector})"
                else:
                    # Fallback to S&P 500
                    reference_change = market_context.get(self.indices_tickers["S&P 500"], 0.0)
                    reference_source = "S&P 500"
            elif region == "EU":
                reference_change = market_context.get(self.indices_tickers["STOXX 600"], 0.0)
                reference_source = "STOXX 600"
            elif region == "CN":
                reference_change = market_context.get(self.indices_tickers["CSI 300"], 0.0)
                reference_source = "CSI 300"
                
            normalized_change = change_percent - reference_change
            
            print(f"Stock: {symbol}, Change: {change_percent:.2f}%, Ref ({reference_source}): {reference_change:.2f}%, Norm: {normalized_change:.2f}%")
            
            # Check if NORMALIZED drop is more than 6% (i.e. <= -6.0)
            # We use the normalized change to filter out market-wide drops
            if normalized_change <= -6.0:
                notification_key = (symbol, today_str)
                
                if notification_key not in self.sent_notifications:
                    print(f"Triggering notification for {symbol} (Norm: {normalized_change:.2f}%)")
                    
                    # Generate research report
                    print(f"Generating research report for {symbol}...")
                    # Pass the normalized change context to the research service if needed, 
                    # but for now we just pass the raw change as that's what the user sees on the ticker
                    report = research_service.analyze_stock(symbol, price, change_percent)
                    self.research_reports[symbol] = report
                    
                    # We might want to mention the normalization in the email?
                    # For now, keeping the email format simple.
                    email_service.send_notification(symbol, change_percent, price, report)
                    self.sent_notifications.add(notification_key)

stock_service = StockService()

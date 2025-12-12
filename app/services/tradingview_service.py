from tradingview_ta import TA_Handler, Interval, Exchange
from tradingview_screener import Query, Column
from typing import Dict, List, Optional, Set
import concurrent.futures

class TradingViewService:
    def __init__(self):
        pass

    def get_analysis(self, symbol: str, screener: str, exchange: str) -> Optional[Dict]:
        """
        Fetches technical analysis for a single symbol.
        """
        try:
            handler = TA_Handler(
                symbol=symbol,
                screener=screener,
                exchange=exchange,
                interval=Interval.INTERVAL_1_DAY
            )
            analysis = handler.get_analysis()
            return analysis
        except Exception as e:
            print(f"Error fetching TradingView analysis for {symbol}: {e}")
            return None

    def get_indices_data(self, indices_config: Dict[str, Dict]) -> Dict[str, Dict]:
        """
        Fetches data for multiple indices in parallel.
        
        Args:
            indices_config: Dict mapping index name to config dict 
                            {"symbol": "...", "screener": "...", "exchange": "..."}
                            
        Returns:
            Dict mapping index name to data dict {"price": float, "change": float, "change_percent": float}
        """
        results = {}
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_name = {
                executor.submit(self._fetch_single_index, name, config): name
                for name, config in indices_config.items()
            }
            
            for future in concurrent.futures.as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    data = future.result()
                    results[name] = data
                except Exception as e:
                    print(f"Error processing future for {name}: {e}")
                    results[name] = {"price": 0.0, "change": 0.0, "change_percent": 0.0}
                    
        return results

    def _fetch_single_index(self, name: str, config: Dict) -> Dict:
        """
        Helper to fetch a single index.
        """
        try:
            analysis = self.get_analysis(
                symbol=config["symbol"],
                screener=config["screener"],
                exchange=config["exchange"]
            )
            
            if analysis:
                price = analysis.indicators["close"]
                open_price = analysis.indicators["open"]
                # TradingView TA doesn't always give 'change' directly in indicators, 
                # but we can calculate it from open/close or use 'change' if available.
                # Actually, indicators usually have 'change' and 'change|1d' etc depending on setup,
                # but standard indicators might just be OHLC + technicals.
                # Let's check what we get. 'close' is current price.
                # We might need to rely on 'change' indicator if it exists, or calculate from previous close.
                # 'close[1]' is previous close usually available in some libraries, but here we get indicators.
                
                # Let's try to find 'change' or calculate from open (which is day open, not prev close).
                # Ideally we want prev close.
                # analysis.indicators keys usually include: 'open', 'close', 'high', 'low', 'volume', 'RSI', etc.
                
                # If we can't get change directly, we might return 0.0 or try to approximate.
                # However, for a screener, we really want the change.
                # Let's see if 'change' is in indicators.
                
                change = analysis.indicators.get("change", 0.0)
                if change == 0.0 and "open" in analysis.indicators:
                     # Fallback: change from open (intraday)
                     change = price - analysis.indicators["open"]
                
                # Calculate percent
                # We need prev close to be accurate. 
                # If we only have change, we can infer prev close = price - change
                prev_close = price - change
                change_percent = (change / prev_close * 100) if prev_close else 0.0
                
                return {
                    "price": price,
                    "change": change,
                    "change_percent": change_percent,
                    "recommendation": analysis.summary.get("RECOMMENDATION", "NEUTRAL")
                }
            else:
                return {"price": 0.0, "change": 0.0, "change_percent": 0.0}
                
        except Exception as e:
            print(f"Error fetching {name} from TradingView: {e}")
            return {"price": 0.0, "change": 0.0, "change_percent": 0.0}

    def get_top_movers(self, min_market_cap_usd: int = 5_000_000_000, max_change_percent: float = -4.0, min_volume: int = 50_000, processed_symbols: Set[str] = None) -> List[Dict]:
        """
        Fetches top movers from multiple global markets using TradingView Screener.
        
        Markets:
        - America (USD)
        - Europe (Germany, UK, France, Spain, Italy, Netherlands) (EUR/GBP)
        - China (CNY)
        - India (INR)
        - Australia (AUD)
        
        Filters:
        - Market Cap > $5B USD (converted to local currency)
        - Change < -4%
        - Volume > 50,000
        """
        
        # Market Configuration with approximate exchange rates (as of late 2024/2025)
        # We use a safe buffer or direct conversion.
        # USD Base: 5,000,000,000 (Default)
        
        
        # User requested specific lower threshold for Europe (500M)
        eu_min_cap_usd = 500_000_000
        
        # Market Cap Adjustments: Increase non-US thresholds by 10%
        # min_market_cap_usd is the base user input (e.g. 5B)
        
        # Adjust base for Non-US 
        non_us_min_cap_usd = min_market_cap_usd * 1.10
        non_us_eu_min_cap_usd = eu_min_cap_usd * 1.10

        market_configs = [
            {
                "region": "America",
                "markets": ["america"],
                "currency_threshold": min_market_cap_usd # USD (No increase for US)
            },
            {
                "region": "Europe (Germany)",
                "markets": ["germany"],
                "currency_threshold": non_us_eu_min_cap_usd * 0.95 # EUR ~0.95 USD
            },
             {
                "region": "Europe (UK)",
                "markets": ["uk"],
                "currency_threshold": non_us_eu_min_cap_usd * 0.80 # GBP ~0.80 USD
            },
            {
                "region": "Europe (Eurozone)",
                "markets": ["france", "spain", "italy", "netherlands", "belgium", "portugal", "finland", "ireland", "austria"],
                "currency_threshold": non_us_eu_min_cap_usd * 0.95 # EUR
            },
            {
                "region": "Europe (Switzerland)",
                "markets": ["switzerland"],
                "currency_threshold": non_us_eu_min_cap_usd * 0.88 # CHF ~0.88 USD
            },
            {
                "region": "Europe (Sweden)",
                "markets": ["sweden"],
                "currency_threshold": non_us_eu_min_cap_usd * 10.5 # SEK ~10.5 USD
            },
            {
                "region": "Europe (Denmark)",
                "markets": ["denmark"],
                "currency_threshold": non_us_eu_min_cap_usd * 7.0 # DKK ~7.0 USD
            },
            {
                "region": "Japan",
                "markets": ["japan"],
                "currency_threshold": non_us_min_cap_usd * 150 # JPY ~150 USD
            },
            # {
            #     "region": "Canada",
            #     "markets": ["canada"],
            #     "currency_threshold": non_us_min_cap_usd * 1.40 # CAD ~1.40 USD
            # },
            {
                "region": "South Korea",
                "markets": ["korea"],
                "currency_threshold": non_us_min_cap_usd * 1400 # KRW ~1400 USD
            },
            {
                "region": "Taiwan",
                "markets": ["taiwan"],
                "currency_threshold": non_us_min_cap_usd * 32 # TWD ~32 USD
            },
            {
                "region": "Brazil",
                "markets": ["brazil"],
                "currency_threshold": non_us_min_cap_usd * 6.0 # BRL ~6.0 USD
            },
            {
                "region": "China",
                "markets": ["china"],
                "currency_threshold": non_us_min_cap_usd * 7.2 # CNY ~7.2 USD
            },
            {
                "region": "India",
                "markets": ["india"],
                "currency_threshold": non_us_min_cap_usd * 84.0 # INR ~84 USD
            },
            {
                "region": "Australia",
                "markets": ["australia"],
                "currency_threshold": non_us_min_cap_usd * 1.55 # AUD ~1.55 USD
            }
        ]
        
        all_movers = []
        
        # We can run these in parallel
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_config = {
                executor.submit(self._fetch_market_movers, config, max_change_percent, min_volume): config
                for config in market_configs
            }
            
            for future in concurrent.futures.as_completed(future_to_config):
                config = future_to_config[future]
                try:
                    movers = future.result()
                    
                    # Log stats
                    if processed_symbols is None:
                        processed_symbols = set()
                        
                    found_count = len(movers)
                    new_count = sum(1 for m in movers if m['symbol'] not in processed_symbols)
                    already_processed_count = found_count - new_count
                    
                    print(f"  > Found {found_count} stocks for {config['region']}. (New: {new_count}, Already Processed: {already_processed_count})")
                    
                    for m in movers:
                        status = "Already Processed" if m['symbol'] in processed_symbols else "New"
                        print(f"    - {m['symbol']} ({m['change_percent']:.2f}%) [{status}]")
                    
                    all_movers.extend(movers)
                except Exception as e:
                    print(f"Error processing market config for {config['region']}: {e}")

        # Deduplicate by symbol (just in case) and sort
        # Use a dict to deduplicate
        unique_movers = {m['symbol']: m for m in all_movers}.values()
        
        # Sort by change percent (ascending, biggest drop first)
        sorted_movers = sorted(unique_movers, key=lambda x: x['change_percent'])
        
        return sorted_movers # Return all global losers matching criteria

    def _fetch_market_movers(self, config: Dict, max_change_percent: float, min_volume: int) -> List[Dict]:
        """
        Helper to fetch movers for a specific market configuration.
        """
        movers = []
        try:
            # Initialize query
            # We pass the list of markets to set_markets
            q = Query().set_markets(*config["markets"]).select(
                'name', 
                'description', 
                'change', 
                'close', 
                'change_abs', 
                'market_cap_basic', 
                'volume', 
                'currency',
                'exchange',
                # Price Action
                'open', 'high', 'low',
                # Technicals
                'RSI',
                'SMA50', 
                'SMA200',
                'BB.lower',
                'BB.upper',
                'MACD.macd',
                'MACD.signal',
                'MACD.hist',
                'Mom',
                'Stoch.K',
                'Stoch.D',
                'ADX',
                'CCI20',
                'VWMA',
                'ATR',
                'relative_volume_10d_calc',
                'beta_1_year',
                'price_52_week_high', 
                'price_52_week_low',
                'Recommend.All',
                'Recommend.MA',
                # Performance
                'Perf.W',
                'Perf.YTD',
                # Financials - Valuation
                'price_book_fq', 'price_earnings_ttm', 'enterprise_value_ebitda_ttm', 'price_free_cash_flow_ttm', 'dividend_yield_recent',
                # Financials - Income
                'total_revenue_ttm', 'total_revenue_yoy_growth_ttm', 'gross_margin_ttm', 'operating_margin_ttm', 'net_income_ttm', 'earnings_per_share_basic_ttm',
                # Financials - Balance Sheet
                'total_assets_fq', 'total_liabilities_fq', 'total_debt_fq', 'cash_n_equivalents_fq', 'current_ratio_fq', 'debt_to_equity_fq',
                # Financials - Cash Flow
                'free_cash_flow_ttm'
            )

            # Apply Filters
            q = q.where(
                Column('market_cap_basic') > config["currency_threshold"],
                Column('change') < max_change_percent,
                Column('volume') > min_volume
            )

            # Fetch Data
            count, df = q.get_scanner_data()
            
            if not df.empty:
                # Sort locally
                df = df.sort_values(by='change', ascending=True)
                df = df.head(20) # Top 20 per region
                
                for index, row in df.iterrows():
                    symbol = row['name']
                    
                    # Add region info to name or symbol if needed to distinguish?
                    # For now just keep symbol.
                    
                    movers.append({
                        "symbol": symbol,
                        "name": row['description'], # Use description as name (Company Name)
                        "price": row['close'],
                        "change": 0.0,
                        "change_percent": row['change'],
                        "market_cap": row['market_cap_basic'],
                        "volume": row['volume'],
                        "pe_ratio": row['price_earnings_ttm'],
                        "debt_to_equity": row['debt_to_equity_fq'],
                        "currency": row['currency'],
                    "region": config["region"],
                    "exchange": row['exchange'],
                    "screener": config["markets"][0],
                    "cached_indicators": {
                        "rsi": row['RSI'],
                        "sma200": row['SMA200'],
                        "bb_lower": row['BB.lower'],
                        "bb_upper": row['BB.upper'],
                        "close": row['close'],
                        "open": row['open'],
                        "high": row['high'],
                        "low": row['low'],
                        "volume": row['volume'],
                        "perf_w": row['Perf.W'],
                        "perf_ytd": row['Perf.YTD'],
                        "recommend_all": row['Recommend.All'],
                        "recommend_ma": row['Recommend.MA'],
                        "macd": row['MACD.macd'],
                        "macd_signal": row['MACD.signal'],
                        "macd_hist": row['MACD.hist'],
                        "mom": row['Mom'],
                        "stoch_k": row['Stoch.K'],
                        "stoch_d": row['Stoch.D'],
                        "adx": row['ADX'],
                        "cci": row['CCI20'],
                        "vwma": row['VWMA'],
                        "atr": row['ATR'],
                        "sma50": row['SMA50'],
                        "rvol": row['relative_volume_10d_calc'],
                        "beta": row['beta_1_year'],
                        "high52": row['price_52_week_high'],
                        "low52": row['price_52_week_low'],
                        # Financials
                        "pe_ratio": row['price_earnings_ttm'],
                        "pb_ratio": row['price_book_fq'],
                        "ev_ebitda": row['enterprise_value_ebitda_ttm'],
                        "p_fcf": row['price_free_cash_flow_ttm'],
                        "div_yield": row['dividend_yield_recent'],
                        "revenue": row['total_revenue_ttm'],
                        "rev_growth": row['total_revenue_yoy_growth_ttm'],
                        "gross_margin": row['gross_margin_ttm'],
                        "op_margin": row['operating_margin_ttm'],
                        "net_income": row['net_income_ttm'],
                        "eps": row['earnings_per_share_basic_ttm'],
                        "total_assets": row['total_assets_fq'],
                        "total_liabilities": row['total_liabilities_fq'],
                        "total_debt": row['total_debt_fq'],
                        "cash": row['cash_n_equivalents_fq'],
                        "current_ratio": row['current_ratio_fq'],
                        "debt_to_equity": row['debt_to_equity_fq'],
                        "fcf": row['free_cash_flow_ttm']
                    }
                })
        except Exception as e:
            print(f"Error fetching movers for {config['region']}: {e}")
            
        return movers

    def get_latest_price(self, symbol: str, region: str = "US") -> float:
        """
        Fetches the latest price for a symbol using TradingView Screener.
        Tries to map the region to the appropriate market.
        """
        # Map region to markets
        region_map = {
            "US": ["america"],
            "EU": ["germany", "uk", "france", "spain", "italy", "netherlands", "europe", "switzerland", "sweden", "denmark"],
            "CN": ["china"],
            "IN": ["india"],
        }
        
        markets = region_map.get(region, ["america"]) # Default to US
        
        try:
            q = Query().set_markets(*markets).select('close').where(
                Column('name') == symbol
            )
            count, df = q.get_scanner_data()
            
            if not df.empty:
                return df.iloc[0]['close']
                
        except Exception as e:
            print(f"Error fetching price for {symbol} in {region}: {e}")
            
        return 0.0

    def get_technical_analysis(self, symbol: str, region: str = "US") -> Dict:
        """
        Fetches technical analysis summary for a symbol.
        """
        # Map region to markets/exchanges if needed
        # For TA_Handler, we need screener and exchange
        # This is a best-effort mapping.
        
        screener_map = {
            "US": "america",
            "EU": "germany", # Default to germany for EU if unknown
            "CN": "china",
            "IN": "india",
        }
        
        exchange_map = {
            "US": "NASDAQ", # Default, might need to try NYSE if fails or use generic
            "EU": "XETR",
        }
        
        screener = screener_map.get(region, "america")
        exchange = exchange_map.get(region, "NASDAQ")
        
        try:
            # First try with default exchange
            handler = TA_Handler(
                symbol=symbol,
                screener=screener,
                exchange=exchange,
                interval=Interval.INTERVAL_1_DAY
            )
            analysis = handler.get_analysis()
            
            # If successful, extract key indicators
            if analysis:
                return {
                    "summary": analysis.summary,
                    "oscillators": analysis.oscillators,
                    "moving_averages": analysis.moving_averages,
                    "indicators": analysis.indicators
                }
                
        except Exception as e:
            print(f"Error fetching TA for {symbol} ({region}): {e}")
            
            # Fallback: Try without specific exchange if possible or different exchange?
            # TradingView TA requires exchange.
            pass
            
        return {}

    def get_technical_indicators(self, symbol: str, region: str = "US", exchange: str = None, screener: str = None) -> Dict:
        """
        Fetches specific technical indicators (SMA200, RSI, BB, Volume) for Gatekeeper.
        If exchange/screener are provided, they are used directly. Otherwise, inferred from region.
        """
        # Map region to markets/exchanges if explict ones not provided
        screener_map = {
            "US": "america", "America": "america",
            "EU": "germany", "Europe (Germany)": "germany", "Europe (UK)": "uk", 
            "CN": "china", "China": "china",
            "IN": "india", "India": "india",
        }
        
        # Default exchange map (best guess)
        exchange_map = {
            "US": "NASDAQ", "America": "NASDAQ",
            "EU": "XETR", "Europe (Germany)": "XETR",
            "CN": "SSE", "China": "SSE",
            "IN": "NSE", "India": "NSE",
        }
        
        if not screener:
            screener = screener_map.get(region, "america")
            # Try to start lower case config region match or substring
            if not screener and "Europe" in region: screener = "germany" # Fallback
            
        if not exchange:
            exchange = exchange_map.get(region, "NASDAQ")


        # Overrides for specific known tickers (like Indices/ETFs on AMEX/ARCA)
        # SPY, XLK, etc are often on AMEX
        if symbol in ["SPY", "XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLC", "XLU", "XLB", "XLRE"]:
            exchange = "AMEX"
        
        try:
            handler = TA_Handler(
                symbol=symbol,
                screener=screener,
                exchange=exchange,
                interval=Interval.INTERVAL_1_DAY
            )
            
            # Simple retry logic for 429
            import time
            max_retries = 3
            for i in range(max_retries):
                try:
                    analysis = handler.get_analysis()
                    break
                except Exception as e:
                    if "429" in str(e):
                        if i < max_retries - 1:
                            wait_time = (i + 1) * 2 # 2s, 4s, 6s...
                            print(f"429 Limit hit for {symbol}. Retrying in {wait_time}s...")
                            time.sleep(wait_time)
                            continue
                    raise e
            
            if analysis:
                inds = analysis.indicators
                return {
                    "close": inds.get("close", 0.0),
                    "sma200": inds.get("SMA200", 0.0),
                    "rsi": inds.get("RSI", 50.0), 
                    "bb_lower": inds.get("BB.lower", 0.0),
                    "bb_upper": inds.get("BB.upper", 0.0),
                    "volume": inds.get("volume", 0),
                }
        except Exception as e:
            print(f"Error fetching indicators for {symbol}: {e}")
            
        return {}

    def get_earnings_date(self, symbol: str, region: str = "US") -> Optional[int]:
        """
        Fetches the next earnings release date timestamp.
        """
        region_map = {
            "US": ["america"],
            "EU": ["germany", "europe"],
            "CN": ["china"],
        }
        markets = region_map.get(region, ["america"])
        
        try:
            q = Query().set_markets(*markets).select('earnings_release_date').where(
                Column('name') == symbol
            )
            count, df = q.get_scanner_data()
            
            if not df.empty:
                val = df.iloc[0]['earnings_release_date']
                if val:
                    return int(val)
        except Exception as e:
            print(f"Error fetching earnings date for {symbol}: {e}")
            
        return None

    def get_sector_performance(self, sector_tickers: Dict[str, str]) -> Dict[str, float]:
        """
        Fetches performance (change %) for a map of Sector Name -> Ticker.
        Iterates through tickers using Screener to avoid Exchange guessing issues
        or limitations of 'is_in'.
        """
        results = {}
        
        # We can run in parallel
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_name = {
                executor.submit(self._fetch_single_sector, name, ticker): name
                for name, ticker in sector_tickers.items()
            }
            
            for future in concurrent.futures.as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    change = future.result()
                    results[name] = change
                except Exception as e:
                    print(f"Error processing sector {name}: {e}")
                    results[name] = 0.0
                    
        return results

    def _fetch_single_sector(self, name: str, ticker: str) -> float:
        """
        Helper to fetch change % for a single sector ticker using valid Screener query.
        """
        try:
            # Assumes America/US for sectors (ETFs)
            q = Query().set_markets('america').select('change').where(
                Column('name') == ticker
            )
            count, df = q.get_scanner_data()
            
            if not df.empty:
                return float(df.iloc[0]['change'])
        except Exception:
            pass
        return 0.0

tradingview_service = TradingViewService()


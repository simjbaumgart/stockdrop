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
        Fetches top movers from the US market using TradingView Screener.

        Filters:
        - Market Cap > $5B USD
        - Change < -4%
        - Volume > 50,000
        """
        market_configs = [
            {
                "region": "America",
                "markets": ["america"],
                "currency_threshold": min_market_cap_usd
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
                'Perf.1M',
                'Perf.3M',
                'Perf.6M',
                'Perf.Y',
                'Perf.5Y',
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
                        "perf_1m": row['Perf.1M'],
                        "perf_3m": row['Perf.3M'],
                        "perf_6m": row['Perf.6M'],
                        "perf_1y": row['Perf.Y'],
                        "perf_5y": row['Perf.5Y'],
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
        Fetches the latest price for a symbol using TradingView Screener (US market only).
        """
        markets = ["america"]
        
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
        Fetches technical analysis summary for a symbol (US market only).
        """
        screener = "america"
        exchange = "NASDAQ"
        
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
        If exchange/screener are provided, they are used directly. Otherwise, defaults to US.
        """
        if not screener:
            screener = "america"

        if not exchange:
            exchange = "NASDAQ"


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
        Fetches the next earnings release date timestamp (US market only).
        """
        markets = ["america"]
        
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


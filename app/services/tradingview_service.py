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
        # USD Base: 5,000,000,000
        
        market_configs = [
            {
                "region": "America",
                "markets": ["america"],
                "currency_threshold": min_market_cap_usd # USD
            },
            {
                "region": "Europe (Germany)",
                "markets": ["germany"],
                "currency_threshold": min_market_cap_usd * 0.95 # EUR ~0.95 USD (approx, let's say 1 EUR = 1.05 USD -> 5B USD = 4.76B EUR)
                                                                # Let's use 4.5B EUR to be safe
            },
             {
                "region": "Europe (UK)",
                "markets": ["uk"],
                "currency_threshold": min_market_cap_usd * 0.80 # GBP ~0.80 USD (approx, 1 GBP = 1.25 USD -> 5B USD = 4B GBP)
            },
            {
                "region": "Europe (Others)",
                "markets": ["france", "spain", "italy", "netherlands"],
                "currency_threshold": min_market_cap_usd * 0.95 # EUR
            },
            {
                "region": "China",
                "markets": ["china"],
                "currency_threshold": min_market_cap_usd * 7.2 # CNY ~7.2 USD
            },
            {
                "region": "India",
                "markets": ["india"],
                "currency_threshold": min_market_cap_usd * 84.0 # INR ~84 USD
            },
            {
                "region": "Australia",
                "markets": ["australia"],
                "currency_threshold": min_market_cap_usd * 1.55 # AUD ~1.55 USD
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
        
        return sorted_movers[:50] # Return top 50 global losers

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
                'close',  
                'change', 
                'market_cap_basic', 
                'volume', 
                'price_earnings_ttm', 
                'debt_to_equity_fq',
                'currency'
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
                        "region": config["region"]
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
            "EU": ["germany", "uk", "france", "spain", "italy", "netherlands", "europe"],
            "CN": ["china"],
            "IN": ["india"],
            "AU": ["australia"]
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

tradingview_service = TradingViewService()


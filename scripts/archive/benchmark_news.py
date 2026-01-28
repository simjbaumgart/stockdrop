
import sys
import os
import pandas as pd
import time
from datetime import datetime, timedelta

# Add parent dir to path to import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.alpha_vantage_service import alpha_vantage_service
from app.services.finnhub_service import finnhub_service
from app.services.benzinga_service import benzinga_service
from app.services.polygon_service import polygon_service
from app.services.stock_news_api_service import stock_news_api_service
import yfinance as yf

def get_yfinance_news(symbol: str) -> list:
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        parsed = []
        for n in news:
            # Handle new YF structure (nested content)
            content = n.get('content', n) # Fallback to n if flat
            
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

            parsed.append({
                'datetime': ts,
                'datetime_str': datetime.fromtimestamp(ts).strftime('%Y-%m-%d'),
                'has_full_text': False 
            })
        return parsed
    except:
        return []

def benchmark_provider(name, func, symbol):
    start_time = datetime.now()
    try:
        # Normalize symbol for YF/Benzinga/Polygon if needed usually handled in service or passed direct
        news = func(symbol)
    except Exception as e:
        print(f"Error {name}: {e}")
        news = []
        
    duration = (datetime.now() - start_time).total_seconds()
    
    # Filter last 7 days count
    seven_days_ago = datetime.now() - timedelta(days=7)
    recent_count = 0
    latest_date = "N/A"
    full_text_count = 0
    
    if news:
        # Sort by date descending
        news.sort(key=lambda x: x.get('datetime', 0), reverse=True)
        latest_date = news[0].get('datetime_str', 'N/A')
        
        for n in news:
            ts = n.get('datetime', 0)
            if ts:
                dt = datetime.fromtimestamp(ts)
                if dt >= seven_days_ago:
                    recent_count += 1
            
            if n.get('has_full_text', False):
                full_text_count += 1

    return {
        "Provider": name,
        "Symbol": symbol,
        "Count (7d)": recent_count,
        "Latest": latest_date,
        "FullText": full_text_count,
        "Time(s)": round(duration, 2)
    }

def run_benchmark():
    tickers = {
        "US Major": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "WMT"],
        "US Mid": ["AOS", "BWA", "SEE", "ZBRA", "IPG"],
        "EU DAX": ["SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "BMW.DE", "AIR.DE", "BAYN.DE", "BAS.DE", "ADS.DE", "IFX.DE"],
        "EU DAX Raw": ["SAP", "SIE", "ALV", "DTE", "BMW", "AIR", "BAYN", "BAS", "ADS", "IFX"]
    }
    
    # Alpha Vantage wrapper (different signature)
    def av_wrapper(sym):
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        return alpha_vantage_service.get_company_news(sym, start_date=week_ago, end_date=today) or []

    # Finnhub wrapper
    def fh_wrapper(sym):
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        raw = finnhub_service.get_company_news(sym, from_date=week_ago, to_date=today) or []
        # Standardize for metric counting
        parsed = []
        for r in raw:
            parsed.append({
                'datetime': r.get('datetime', 0),
                'datetime_str': datetime.fromtimestamp(r.get('datetime', 0)).strftime('%Y-%m-%d'),
                'has_full_text': len(r.get('summary', '')) > 500
            })
        return parsed

    providers = [
        ("AlphaVantage", av_wrapper),
        ("Finnhub", fh_wrapper),
        ("YFinance", get_yfinance_news),
        ("Benzinga", lambda s: benzinga_service.get_company_news(s)),
        ("Polygon", lambda s: polygon_service.get_company_news(s)),
        ("StockNewsAPI", lambda s: stock_news_api_service.get_company_news(s))
    ]
    
    all_results = []
    
    print(f"{'='*80}")
    print(f"STARTING NEWS PROVIDER BENCHMARK")
    print(f"{'='*80}\n")

    for region, symbol_list in tickers.items():
        print(f"--- Processing {region} ---")
        for sym in symbol_list:
            print(f"  > Benchmarking {sym}...")
            for name, func in providers:
                if name == "Polygon":
                    time.sleep(15) # Rate limit avoidance for Free Tier
                    
                # Handle region suffix removal for some US-centric APIs if needed?
                # Most APIs take 'SAP.DE' fine mostly or might fail. 
                # AV needs 'SAP.DE' usually. YF needs it. 
                # Benzinga/Polygon might prefer ISIN or different format but let's try raw.
                res = benchmark_provider(name, func, sym)
                res['Region'] = region
                all_results.append(res)
    
    df = pd.DataFrame(all_results)
    
    print("\n" + "="*80)
    print("BENCHMARK RESULTS (Aggregated by Provider & Region)")
    print("="*80)
    
    # Pivot to show Avg Count and Avg Latency per Region
    summary = df.pivot_table(
        index="Provider", 
        columns="Region", 
        values=["Count (7d)", "FullText"], 
        aggfunc="mean"
    )
    print(summary.round(1))
    
    print("\n" + "="*80)
    print("DETAILED SAMPLE (First 5 Rows)")
    print("="*80)
    print(df.head().to_string())
    
    # Find overall winner
    print("\n" + "="*80)
    print("OVERALL WINNER (Highest Total Articles)")
    print("="*80)
    total_arts = df.groupby("Provider")["Count (7d)"].sum().sort_values(ascending=False)
    print(total_arts)

    # Save to CSV
    df.to_csv("news_benchmark_results.csv", index=False)
    print(f"\nSaved detailed results to news_benchmark_results.csv")

if __name__ == "__main__":
    run_benchmark()

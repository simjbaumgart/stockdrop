
import sys
import os
import pandas as pd
from datetime import datetime, timedelta
import yfinance as yf

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.alpha_vantage_service import alpha_vantage_service
from app.services.finnhub_service import finnhub_service
from app.services.benzinga_service import benzinga_service
from app.services.polygon_service import polygon_service
from app.services.stock_news_api_service import stock_news_api_service

def get_yfinance_content(symbol):
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        parsed = []
        for n in news:
            content = n.get('content', n)
            
            # Extract date
            date_str = "N/A"
            ts = 0
            if 'providerPublishTime' in content:
                ts = content['providerPublishTime']
            elif 'pubDate' in content:
                 try:
                    dt = datetime.fromisoformat(content['pubDate'].replace('Z', '+00:00'))
                    ts = int(dt.timestamp())
                 except: pass
            
            if ts:
                date_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
            
            parsed.append({
                "Source": "YFinance",
                "Headline": content.get('title', 'No Title'),
                "Date": date_str,
                "Summary": content.get('summary', ''),
                "Full_Text": "N/A (YF usually links only)",
                "URL": (content.get('clickThroughUrl') or {}).get('url', '') if content.get('clickThroughUrl') else (content.get('link', '') or '')
            })
        return parsed
    except Exception as e:
        print(f"YF Error: {e}")
        return []

def run_analysis():
    symbol = "WRB"
    print(f"Fetching news content for {symbol}...")
    
    all_articles = []
    
    # 1. Alpha Vantage
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        av_news = alpha_vantage_service.get_company_news(symbol, start_date=week_ago, end_date=today) or []
        for n in av_news[:5]:
            all_articles.append({
                "Source": "AlphaVantage",
                "Headline": n.get('headline'),
                "Date": n.get('datetime_str'),
                "Summary": n.get('summary', ''),
                "Full_Text": "N/A",
                "URL": n.get('url')
            })
    except Exception as e: print(f"AV Error: {e}")

    # 2. Finnhub
    try:
        fh_news = finnhub_service.get_company_news(symbol, from_date=week_ago, to_date=today) or []
        for n in fh_news[:5]:
            all_articles.append({
                "Source": "Finnhub",
                "Headline": n.get('headline'),
                "Date": datetime.fromtimestamp(n.get('datetime', 0)).strftime('%Y-%m-%d'),
                "Summary": n.get('summary', ''),
                "Full_Text": "N/A",
                "URL": n.get('url')
            })
    except Exception as e: print(f"FH Error: {e}")

    # 3. YFinance
    yf_news = get_yfinance_content(symbol)
    all_articles.extend(yf_news[:5])

    # 4. Benzinga
    try:
        bz_news = benzinga_service.get_company_news(symbol) or []
        for n in bz_news[:5]:
            all_articles.append({
                "Source": "Benzinga",
                "Headline": n.get('headline'),
                "Date": n.get('datetime_str'),
                "Summary": n.get('summary', ''),
                "Full_Text": n.get('body', 'N/A'), # Capture full body
                "URL": n.get('url')
            })
    except Exception as e: print(f"BZ Error: {e}")

    # 5. Polygon
    try:
        poly_news = polygon_service.get_company_news(symbol, limit=10) or [] 
        for n in poly_news[:5]:
            all_articles.append({
                "Source": "Polygon",
                "Headline": n.get('headline'),
                "Date": n.get('datetime_str'),
                "Summary": n.get('summary', ''), # Description in Polygon
                "Full_Text": "N/A",
                "URL": n.get('url')
            })
    except Exception as e: print(f"Poly Error: {e}")

    # 6. StockNewsAPI
    try:
        sna_news = stock_news_api_service.get_company_news(symbol, items=5) or []
        for n in sna_news[:5]:
            all_articles.append({
                "Source": "StockNewsAPI",
                "Headline": n.get('headline'),
                "Date": n.get('datetime_str'),
                "Summary": n.get('summary', ''), # Text field in SNA
                "Full_Text": n.get('text', 'N/A'), # Capture full text
                "URL": n.get('url')
            })
    except Exception as e: print(f"SNA Error: {e}")

    # Save to CSV
    df = pd.DataFrame(all_articles)
    filename = "wrb_content_depth.csv"
    df.to_csv(filename, index=False)
    print(f"Saved content analysis to {filename}")
    print(df.head(10).to_string())

if __name__ == "__main__":
    run_analysis()


try:
    from tradingview_scraper.symbols.news import NewsScraper
    print("Successfully imported NewsScraper")
    
    scraper = NewsScraper()
    # Test for AAPL first as a sanity check
    print("Fetching news for AAPL...")
    news = scraper.get_news(symbol="AAPL", exchange="NASDAQ")
    # print(news) # Output might be large
    print(f"Found {len(news)} news items")
    if len(news) > 0:
        print("First item keys:", news[0].keys())

except ImportError:
    print("Error: tradingview-scraper not found or import failed.")
except Exception as e:
    print(f"Error running scraper: {e}")

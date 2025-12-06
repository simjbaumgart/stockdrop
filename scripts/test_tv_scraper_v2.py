
try:
    from tradingview_scraper.symbols.news import NewsScraper
    print("Successfully imported NewsScraper")
    
    scraper = NewsScraper()
    
    # Try scrape_headlines
    print("Calling scrape_headlines for AAPL...")
    try:
        # Guessing arguments based on context
        news = scraper.scrape_headlines(symbol="AAPL", exchange="NASDAQ")
        print(f"Found {len(news)} headlines")
        if len(news) > 0:
            print("First item:", news[0])
            print("First item keys:", news[0].keys())
    except TypeError as e:
        print(f"TypeError calling scrape_headlines: {e}")
        # Inspect signature if possible?
        import inspect
        print("Signature:", inspect.signature(scraper.scrape_headlines))

except Exception as e:
    print(f"Error running scraper test v2: {e}")

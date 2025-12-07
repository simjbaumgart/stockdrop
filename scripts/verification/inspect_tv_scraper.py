
try:
    from tradingview_scraper.symbols.news import NewsScraper
    print("Successfully imported NewsScraper")
    
    scraper = NewsScraper()
    print("Available methods:", dir(scraper))
    
except ImportError:
    print("Error: tradingview-scraper not found or import failed.")
except Exception as e:
    print(f"Error inspecting scraper: {e}")

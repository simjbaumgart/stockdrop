from app.services.stock_service import stock_service
import yfinance as yf

symbol = "SEIT"
print(f"--- Diagnosing {symbol} ---")

# 1. Active Trading Check
print("\n1. Testing Active Trading Check (_is_actively_traded):")
try:
    is_active = stock_service._is_actively_traded(symbol)
    print(f"Result: {is_active}")
    
    # Detail
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period="5d")
    print("History tail:\n", hist.tail())
    if not hist.empty:
        print(f"Avg Volume: {hist['Volume'].mean()}")
except Exception as e:
    print(f"Error checking active trading: {e}")

# 2. News Check
print("\n2. Testing News Count (get_aggregated_news):")
try:
    news = stock_service.get_aggregated_news(symbol)
    print(f"News Count: {len(news)}")
    for n in news[:3]:
        print(f" - {n['headline']} ({n['source']})")
except Exception as e:
    print(f"Error checking news: {e}")

import asyncio
from app.services.stock_service import stock_service

def test_stock_service_indices():
    print("Testing StockService.get_indices() with TradingView integration...")
    try:
        # This is a synchronous method now (internally uses threads if needed, but the method itself is sync)
        # Wait, get_indices is sync in stock_service.py
        data = stock_service.get_indices()
        
        print("\nIndices Data:")
        for name, info in data.items():
            print(f"{name}: {info}")
            
        # Basic validation
        assert "S&P 500" in data
        assert "STOXX 600" in data
        assert "China" in data
        
        print("\nVerification Successful!")
        
    except Exception as e:
        print(f"\nVerification Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_stock_service_indices()


import sys
import os

# Add app to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.stock_service import StockService

def verify_indices():
    print("Verifying StockService Indices...")
    service = StockService()
    
    indices = service.get_indices()
    print("\nFetched Indices:")
    for name, data in indices.items():
        price = data.get('price', 0.0)
        change = data.get('change_percent', 0.0)
        print(f"  - {name}: {price} ({change:.2f}%)")
        
    # Validation logic
    india_data = indices.get("India")
    if india_data and india_data.get('price', 0.0) > 0:
        print("\nSUCCESS: India index fetched successfully.")
    else:
        print("\nFAILED: India index missing or empty.")
        exit(1)

if __name__ == "__main__":
    verify_indices()

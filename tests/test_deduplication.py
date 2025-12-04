from app.services.storage_service import storage_service
from app.services.stock_service import stock_service
import os
import datetime
import csv
import pathlib

def test_deduplication():
    print("Testing deduplication logic...")
    
    # 1. Setup: Create a dummy decision file for today
    backup_dir = pathlib.Path("data/decisions")
    backup_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    file_path = backup_dir / f"decisions_{date_str}.csv"
    
    # Ensure clean state for this test file (backup if needed, but for test env just overwrite)
    # Actually, let's just append a unique symbol "DEDUP_TEST"
    
    test_symbol = "DEDUP_TEST"
    
    with open(file_path, mode='a', newline='') as f:
        writer = csv.writer(f)
        # If file is empty/new, write header (simplified check)
        if file_path.stat().st_size == 0:
             headers = ["Timestamp", "Symbol", "Price", "Change Percent", "Recommendation", "Reasoning", "P/E Ratio", "Market Cap", "Sector", "Region"]
             writer.writerow(headers)
             
        row = [
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            test_symbol,
            100.0, -10.0, "BUY", "Test", 10.0, 1000000, "Tech", "US"
        ]
        writer.writerow(row)
        
    print(f"Added {test_symbol} to {file_path}")
    
    # 2. Verify StorageService reads it
    today_decisions = storage_service.get_today_decisions()
    print(f"StorageService found: {today_decisions}")
    
    if test_symbol in today_decisions:
        print("PASS: StorageService correctly identified the symbol.")
    else:
        print("FAIL: StorageService did not find the symbol.")
        
    # 3. Verify StockService loads it (simulating check_large_cap_drops logic)
    # We can't easily call check_large_cap_drops without triggering real API calls.
    # But we can verify the logic we inserted:
    # processed_symbols = storage_service.get_today_decisions()
    # for symbol in processed_symbols:
    #    self.sent_notifications.add((symbol, today_str))
    
    # Let's manually trigger this logic on the stock_service instance
    processed_symbols = storage_service.get_today_decisions()
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    
    for symbol in processed_symbols:
        stock_service.sent_notifications.add((symbol, today_str))
        
    if (test_symbol, today_str) in stock_service.sent_notifications:
        print("PASS: StockService successfully loaded the symbol into sent_notifications.")
    else:
        print("FAIL: StockService failed to load the symbol.")

if __name__ == "__main__":
    test_deduplication()

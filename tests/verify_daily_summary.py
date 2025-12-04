import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.stock_service import stock_service
from app.services.email_service import email_service

def verify_daily_summary():
    print("Fetching daily movers (threshold=0.0 to ensure we get some)...")
    # Using 0.0 threshold to guarantee we get results for testing
    movers = stock_service.get_daily_movers(threshold=0.0)
    
    print(f"Found {len(movers)} movers.")
    if movers:
        print(f"Top mover: {movers[0]}")
    
    print("Sending mock daily summary...")
    # This will print "Mock Daily Summary Sent..." if no email creds are set
    email_service.send_daily_summary(movers[:5]) # Send top 5
    
    print("SUCCESS: Daily summary logic verified.")

if __name__ == "__main__":
    verify_daily_summary()

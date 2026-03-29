import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from app.services.stock_service import stock_service
from app.services.deep_research_service import deep_research_service

# Mock email service to avoid spamming if needed, but for now let's see if it prints to stdout
# stock_service.email_service.send_notification = lambda *args: print(f"MOCK EMAIL: {args}")

print("Triggering check_large_cap_drops...")
stock_service.check_large_cap_drops()
print("Check complete.")
print(f"Research Reports: {len(stock_service.research_reports)}")
print(f"Sent Notifications: {len(stock_service.sent_notifications)}")

deep_research_service.wait_for_completion()

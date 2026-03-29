import os
from dotenv import load_dotenv
load_dotenv()

from app.services.deep_research_service import deep_research_service
import time

print("API Key loaded:", bool(os.getenv("GEMINI_API_KEY")))
print("Service running:", deep_research_service.is_running)

context = {
    "pm_decision": {"action": "BUY"},
    "bull_case": "Test",
    "bear_case": "Test",
    "technical_data": {},
    "drop_percent": -5.0,
    "raw_news": [],
    "transcript_summary": "",
    "transcript_date": "N/A",
    "data_depth": {}
}

print("Queueing task...")
deep_research_service.queue_research_task("TEST", context, 1)

print("Waiting for task to process...")
time.sleep(5)
print("Queue size:", deep_research_service.individual_queue.qsize())
print("Active tasks:", deep_research_service.active_tasks_count)

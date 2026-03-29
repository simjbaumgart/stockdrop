# test_wait_for_completion.py
import asyncio
import sys
import os
import time
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from app.services.deep_research_service import deep_research_service

def test_wait():
    print("Initial queue size:", deep_research_service.individual_queue.qsize())
    print("Queueing dummy task...")
    
    deep_research_service.queue_research_task("TEST_TICKER", {"test": "context"}, decision_id=999999)
    
    print("Waiting for completion...")
    start_time = time.time()
    deep_research_service.wait_for_completion()
    duration = time.time() - start_time
    print(f"Wait completed in {duration:.2f} seconds.")

if __name__ == "__main__":
    test_wait()

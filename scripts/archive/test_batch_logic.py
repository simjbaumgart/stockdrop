
import sys
import os
import time
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.database import init_db
from app.services.deep_research_service import deep_research_service

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def verify_batch_trigger():
    print("--- Verifying Batch Trigger Logic ---")
    
    # 1. Initialize DB (Applies migrations)
    print("1. Initializing Database (Applying Migrations)...")
    init_db()
    
    # 2. Trigger Scan Manually (bypassing the 5 min loop for testing)
    print("2. Triggering Scannner...")
    deep_research_service._scan_for_batches()
    
    # 3. Check Queue
    print("3. Checking Queue...")
    q_size = deep_research_service.batch_queue.qsize()
    print(f"Batch Queue Size: {q_size}")
    
    if q_size > 0:
        print("SUCCESS: Batch task was queued!")
        # Optional: Inspect task
        task = deep_research_service.batch_queue.queue[0]
        print(f"Task Candidates: {[c['symbol'] for c in task['payload']['candidates']]}")
    else:
        print("WARNING: No batch task queued. Check database state or logs.")

if __name__ == "__main__":
    verify_batch_trigger()

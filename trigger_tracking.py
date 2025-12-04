import sys
import os

# Add the project root to the python path
sys.path.append(os.getcwd())

from app.services.performance_service import performance_service

def main():
    print("Triggering daily performance recording...")
    count = performance_service.record_daily_performance()
    print(f"Recorded {count} snapshots.")

if __name__ == "__main__":
    main()

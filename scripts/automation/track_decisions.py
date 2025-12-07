import sys
import os

# Add the project root to the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.services.tracking_service import tracking_service

def main():
    print("Starting decision tracking...")
    tracking_service.update_tracked_stocks()
    print("Tracking complete.")

if __name__ == "__main__":
    main()

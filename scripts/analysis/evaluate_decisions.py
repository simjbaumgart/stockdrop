import sys
import os

# Add the project root to the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.services.performance_service import performance_service
from app.database import get_tracking_history

def main():
    print("Fetching decisions and calculating performance...")
    results = performance_service.evaluate_decisions()
    
    if not results:
        print("No decisions found.")
        return

    # Filter out SKIP recommendations
    results = [r for r in results if r['recommendation'] != 'SKIP']
    
    if not results:
        print("No decisions found (after filtering SKIP).")
        return

    # Header
    header = f"{'Symbol':<8} | {'Date':<20} | {'Rec':<6} | {'Start $':<10} | {'Curr $':<10} | {'Perf %':<10} | {'Outcome':<8} | {'Tracked':<8}"
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    
    for r in results:
        symbol = r['symbol']
        date = str(r['timestamp'])[:19]
        rec = r['recommendation']
        start = f"${r['start_price']:.2f}"
        curr = f"${r['current_price']:.2f}"
        perf = f"{r['performance_percent']:+.2f}%"
        outcome = r['outcome']
        
        # Get tracking count
        history = get_tracking_history(r['id'])
        tracked_count = len(history)
        
        # Color coding (using ANSI escape codes)
        RESET = "\033[0m"
        RED = "\033[91m"
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        
        color = RESET
        if outcome in ["PROFIT", "SAVED"]:
            color = GREEN
        elif outcome in ["LOSS", "MISSED"]:
            color = RED
        elif outcome == "NEUTRAL":
            color = YELLOW
            
        print(f"{color}{symbol:<8} | {date:<20} | {rec:<6} | {start:<10} | {curr:<10} | {perf:<10} | {outcome:<8} | {tracked_count:<8}{RESET}")

    print("-" * len(header))

if __name__ == "__main__":
    main()

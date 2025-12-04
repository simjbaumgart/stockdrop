import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, add_decision_point, get_decision_points
import sqlite3

def verify_decision_tracking():
    print("Initializing DB...")
    init_db()
    
    print("Adding mock decision point...")
    symbol = "MOCK_TEST"
    price = 100.0
    drop_percent = -10.0
    recommendation = "BUY"
    reasoning = "Test reasoning"
    status = "Owned"
    
    success = add_decision_point(symbol, price, drop_percent, recommendation, reasoning, status)
    if not success:
        print("FAILED: Could not add decision point")
        return
    
    print("Fetching decision points...")
    points = get_decision_points()
    
    found = False
    for p in points:
        if p["symbol"] == symbol:
            found = True
            print(f"Found decision point: {p}")
            assert p["price_at_decision"] == price
            assert p["recommendation"] == recommendation
            assert p["status"] == status
            break
            
    if found:
        print("SUCCESS: Decision point verified")
    else:
        print("FAILED: Decision point not found")

if __name__ == "__main__":
    verify_decision_tracking()

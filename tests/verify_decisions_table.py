from app.database import init_db, add_decision_point, get_decision_points
import os

def test_decisions_table():
    print("Testing decisions table schema and data...")
    
    # 1. Initialize DB (should trigger migration if needed)
    init_db()
    print("DB Initialized.")
    
    # 2. Add a decision point with new fields
    symbol = "TEST_DECISION"
    company_name = "Test Decision Corp"
    
    success = add_decision_point(
        symbol=symbol,
        price=150.0,
        drop_percent=-12.5,
        recommendation="BUY",
        reasoning="Test reasoning for decision table.",
        status="Owned",
        company_name=company_name,
        pe_ratio=25.5,
        market_cap=5000000000.0,
        sector="Technology",
        region="US"
    )
    
    if success:
        print("Decision point added successfully.")
    else:
        print("Failed to add decision point.")
        return

    # 3. Retrieve and verify
    points = get_decision_points()
    found = False
    for p in points:
        if p["symbol"] == symbol:
            found = True
            print("Found test decision point:")
            print(f"  Company Name: {p['company_name']}")
            print(f"  P/E Ratio: {p['pe_ratio']}")
            print(f"  Market Cap: {p['market_cap']}")
            print(f"  Sector: {p['sector']}")
            print(f"  Region: {p['region']}")
            
            if p["company_name"] == company_name and p["pe_ratio"] == 25.5:
                print("PASS: Data verification successful.")
            else:
                print("FAIL: Data mismatch.")
            break
            
    if not found:
        print("FAIL: Test decision point not found.")

if __name__ == "__main__":
    test_decisions_table()

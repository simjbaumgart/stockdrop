import os
import sqlite3
from app.database import init_db, add_decision_point, update_deep_research_data, get_decision_point

# Override DB Path for testing
os.environ["DB_PATH"] = "test_deep_research.db"
# We need to reload the module or just rely on the fact that init_db uses the env var when called?
# app.database defines DB_NAME at module level. We need to re-import or patch it.
import app.database
app.database.DB_NAME = "test_deep_research.db"

def test_deep_research_flow():
    print("=== Testing Deep Research Integration ===")
    
    # Clean up previous test
    if os.path.exists("test_deep_research.db"):
        os.remove("test_deep_research.db")
        
    # 1. Init DB (triggers migration)
    init_db()
    
    # 2. Add Decision Point
    print("Adding decision point...")
    decision_id = add_decision_point(
        symbol="AI_TEST",
        price=100.0,
        drop_percent=-5.0,
        recommendation="BUY",
        reasoning="Initial check",
        status="Pending"
    )
    print(f"Decision ID: {decision_id}")
    
    if not decision_id:
        print("FAIL: Could not add decision point")
        return

    # 3. Simulate Deep Research Update
    print("Updating with Deep Research results...")
    
    # Scenario: Strong Buy
    success = update_deep_research_data(
        decision_id=decision_id,
        verdict="STRONG_BUY",
        risk="Low",
        catalyst="Earnings Beat",
        knife_catch="False",
        score=90
    )
    
    if success:
        print("Update successful.")
    else:
        print("FAIL: Update failed.")
        return
        
    # 4. Verify Data
    dp = get_decision_point(decision_id)
    print("Retrieved Decision Point:")
    print(f"  Verdict: {dp['deep_research_verdict']}")
    print(f"  Risk: {dp['deep_research_risk']}")
    print(f"  Score: {dp['deep_research_score']}")
    
    if dp['deep_research_verdict'] == "STRONG_BUY" and dp['deep_research_score'] == 90:
        print("PASS: Data matches expected values.")
    else:
        print("FAIL: Data mismatch.")

    # 4b. Verify List Retrieval (Datatable Data Source)
    print("Verifying List Retrieval (Datatable Source)...")
    from app.database import get_decision_points
    all_points = get_decision_points()
    if all_points and 'deep_research_verdict' in all_points[0]:
         print("PASS: get_decision_points() returns new columns.")
    else:
         print("FAIL: get_decision_points() missing new columns.")

    # Clean up
    if os.path.exists("test_deep_research.db"):
        os.remove("test_deep_research.db")

if __name__ == "__main__":
    test_deep_research_flow()

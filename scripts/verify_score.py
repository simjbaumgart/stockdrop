import sqlite3
import os
from app.database import init_db, add_decision_point, update_decision_point, get_decision_point, DB_NAME

def verify_score_storage():
    print("--- Verifying AI Score Storage ---")
    
    # 1. Trigger Migration
    print("Initializing DB (Check for migration logs)...")
    init_db()
    
    # 2. Check Schema
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(decision_points)")
    columns = [row[1] for row in cursor.fetchall()]
    conn.close()
    
    if "ai_score" not in columns:
        print("FAIL: 'ai_score' column missing from decision_points table.")
        return
    print("PASS: 'ai_score' column exists.")
    
    # 3. Test Insert
    print("Testing Insert with Score...")
    score = 88.5
    symbol = "TEST_SCORE"
    
    # We added ai_score directly to add_decision_point signature
    d_id = add_decision_point(
        symbol, 100.0, -5.0, "BUY", "Testing score", 
        ai_score=score
    )
    
    retrieved = get_decision_point(d_id)
    if retrieved['ai_score'] == score:
        print(f"PASS: Score retrieved correctly: {retrieved['ai_score']}")
    else:
        print(f"FAIL: Score mismatch. Expected {score}, got {retrieved.get('ai_score')}")
        
    # 4. Test Update
    print("Testing Update with Score...")
    new_score = 92.1
    update_decision_point(d_id, "STRONG BUY", "Updated reasoning", "Owned", ai_score=new_score)
    
    retrieved_updated = get_decision_point(d_id)
    if retrieved_updated['ai_score'] == new_score:
        print(f"PASS: Updated score retrieved correctly: {retrieved_updated['ai_score']}")
    else:
        print(f"FAIL: Updated score mismatch. Expected {new_score}, got {retrieved_updated.get('ai_score')}")

if __name__ == "__main__":
    verify_score_storage()

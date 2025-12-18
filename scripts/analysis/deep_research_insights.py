import sqlite3
import json
import os
import sys

# Add parent directory to path to allow imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.database import DB_NAME, get_decision_points

def main():
    print(f"Connecting to database: {DB_NAME}")
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Check if columns exist (simple check by selecting one)
    try:
        cursor.execute("SELECT deep_research_swot FROM decision_points LIMIT 1")
    except sqlite3.OperationalError:
        print("Columns 'deep_research_swot' etc. NOT FOUND. Running migration by importing database.py...")
        from app.database import init_db
        init_db()
        print("Migration triggered.")

    cursor.execute("""
        SELECT symbol, timestamp, deep_research_verdict, deep_research_score, 
               deep_research_swot, deep_research_global_analysis, deep_research_local_analysis,
               deep_research_risk, deep_research_catalyst
        FROM decision_points 
        WHERE deep_research_verdict IS NOT NULL 
        ORDER BY timestamp DESC 
        LIMIT 10
    """)
    
    rows = cursor.fetchall()
    
    print(f"\nFound {len(rows)} Deep Research records:\n")
    
    for row in rows:
        print(f"=== {row['symbol']} ({row['timestamp']}) ===")
        print(f"Verdict: {row['deep_research_verdict']} (Score: {row['deep_research_score']})")
        print(f"Risk: {row['deep_research_risk']} | Catalyst: {row['deep_research_catalyst']}")
        
        if row['deep_research_global_analysis']:
            print(f"\n[Global Market Analysis]:\n{row['deep_research_global_analysis'][:200]}...")
            
        if row['deep_research_local_analysis']:
            print(f"\n[Local Market Analysis]:\n{row['deep_research_local_analysis'][:200]}...")
            
        if row['deep_research_swot']:
            try:
                swot = json.loads(row['deep_research_swot'])
                print("\n[SWOT Analysis]:")
                for k, v in swot.items():
                    print(f"  {k.upper()}: {v}")
            except:
                print(f"\n[SWOT Analysis (Raw)]: {row['deep_research_swot']}")
        
        print("\n" + "-"*50 + "\n")

    conn.close()

if __name__ == "__main__":
    main()

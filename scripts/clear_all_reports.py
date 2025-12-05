import sqlite3
import os
import pathlib
import glob

DB_NAME = "subscribers.db"

def clear_all_reports():
    print("WARNING: Deleting ALL reports and decision history.")
    
    # 1. Clear Database
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        print("Truncating decision_points table...")
        cursor.execute("DELETE FROM decision_points")
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        print(f"Deleted {deleted_count} records from decision_points table.")
    except Exception as e:
        print(f"Error clearing database: {e}")

    # 2. Clear CSV Files
    try:
        decisions_dir = pathlib.Path("data/decisions")
        files = glob.glob(str(decisions_dir / "*.csv"))
        
        for f in files:
            try:
                os.remove(f)
                print(f"Deleted {f}")
            except Exception as e:
                print(f"Error deleting {f}: {e}")
                
        print(f"Cleared {len(files)} CSV report files.")
            
    except Exception as e:
        print(f"Error clearing CSV files: {e}")

if __name__ == "__main__":
    clear_all_reports()

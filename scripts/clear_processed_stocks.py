
import sqlite3
import os

DB_NAME = os.getenv("DB_PATH", "subscribers.db")

def clear_processed_stocks():
    symbols_to_clear = ["ORCL", "HOOD", "RIVN", "CRK", "SFTBY", "DELHY", "ALBHF", "TTD"]
    
    print(f"Opening database: {DB_NAME}")
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # We need to find the specific decision points to clear.
        # But wait, if we delete the decision point, it will be treated as 'New' today since get_today_decision_symbols() checks timestamp.
        
        # Check if they exist first
        placeholders = ', '.join(['?'] * len(symbols_to_clear))
        cursor.execute(f"SELECT id, symbol, timestamp FROM decision_points WHERE symbol IN ({placeholders}) AND date(timestamp) = date('now')", symbols_to_clear)
        rows = cursor.fetchall()
        
        print(f"Found {len(rows)} entries for today to delete:")
        for row in rows:
            print(f" - ID: {row[0]}, Symbol: {row[1]}, Time: {row[2]}")
            
        if len(rows) > 0:
            cursor.execute(f"DELETE FROM decision_points WHERE symbol IN ({placeholders}) AND date(timestamp) = date('now')", symbols_to_clear)
            conn.commit()
            print("Successfully deleted entries. These stocks will now appear as [New] on next run.")
        else:
            print("No entries found for today (or they were already cleared).")
            
        conn.close()
        
    except Exception as e:
        print(f"Error clearing DB: {e}")

if __name__ == "__main__":
    clear_processed_stocks()

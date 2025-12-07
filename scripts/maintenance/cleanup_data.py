import sqlite3
import os

DB_NAME = "subscribers.db"

def cleanup_db():
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Symbols to remove
        symbols_to_remove = ['AIR.PA', 'BAYN.DE', 'GOOGL', 'MOCK_TEST']
        
        print(f"Removing records for: {symbols_to_remove}")
        
        # Get IDs to remove first (for logging)
        placeholders = ','.join(['?'] * len(symbols_to_remove))
        cursor.execute(f"SELECT id, symbol FROM decision_points WHERE symbol IN ({placeholders})", symbols_to_remove)
        rows = cursor.fetchall()
        
        if not rows:
            print("No records found to remove.")
            conn.close()
            return

        ids_to_remove = [row[0] for row in rows]
        print(f"Found {len(ids_to_remove)} records to remove.")
        
        # Remove from decision_tracking first (foreign key)
        placeholders_ids = ','.join(['?'] * len(ids_to_remove))
        cursor.execute(f"DELETE FROM decision_tracking WHERE decision_id IN ({placeholders_ids})", ids_to_remove)
        print(f"Removed tracking data for {cursor.rowcount} records.")
        
        # Remove from decision_points
        cursor.execute(f"DELETE FROM decision_points WHERE id IN ({placeholders_ids})", ids_to_remove)
        print(f"Removed {cursor.rowcount} decision points.")
        
        conn.commit()
        conn.close()
        print("Cleanup complete.")
        
    except Exception as e:
        print(f"Error during cleanup: {e}")

if __name__ == "__main__":
    cleanup_db()

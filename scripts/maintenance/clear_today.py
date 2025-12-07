import sqlite3
import os
import datetime
import pathlib

DB_NAME = "subscribers.db"

def clear_database():
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        print("Clearing today's decisions from database...")
        cursor.execute("DELETE FROM decision_points WHERE date(timestamp) = date('now')")
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        print(f"Deleted {deleted_count} records from decision_points.")
    except Exception as e:
        print(f"Error clearing database: {e}")

def clear_csv_files():
    try:
        backup_dir = pathlib.Path("data/decisions")
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        file_path = backup_dir / f"decisions_{date_str}.csv"
        
        if file_path.exists():
            os.remove(file_path)
            print(f"Deleted {file_path}")
        else:
            print(f"No CSV file found at {file_path}")
            
    except Exception as e:
        print(f"Error clearing CSV files: {e}")

if __name__ == "__main__":
    clear_database()
    clear_csv_files()
    print("Done. Please restart the application or trigger the check again.")

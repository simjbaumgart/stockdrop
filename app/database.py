import sqlite3
from typing import List

import os

DB_NAME = os.getenv("DB_PATH", "subscribers.db")

def init_db():
    """Initialize the database with the subscribers and decision_points tables."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS decision_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            price_at_decision REAL NOT NULL,
            drop_percent REAL NOT NULL,
            recommendation TEXT NOT NULL,
            reasoning TEXT,
            status TEXT DEFAULT 'Ignored',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            company_name TEXT,
            pe_ratio REAL,
            market_cap REAL,
            sector TEXT,
            region TEXT,
            is_earnings_drop BOOLEAN DEFAULT 0,
            earnings_date TEXT,
            ai_score REAL,
            deep_research_verdict TEXT,
            deep_research_risk TEXT,
            deep_research_catalyst TEXT,
            deep_research_knife_catch TEXT,
            deep_research_score INTEGER,
            deep_research_swot TEXT,
            deep_research_global_analysis TEXT,
            deep_research_local_analysis TEXT
        )
    ''')
    
    # Migration: Check for new columns and add them if missing
    try:
        cursor.execute("PRAGMA table_info(decision_points)")
        columns = [info[1] for info in cursor.fetchall()]
        
        new_columns = {
            "company_name": "TEXT",
            "pe_ratio": "REAL",
            "market_cap": "REAL",
            "sector": "TEXT",
            "region": "TEXT",
            "is_earnings_drop": "BOOLEAN DEFAULT 0",
            "earnings_date": "TEXT",
            "ai_score": "REAL",
            "git_version": "TEXT",
            "deep_research_score": "INTEGER",
            "deep_research_swot": "TEXT",
            "deep_research_global_analysis": "TEXT",
            "deep_research_local_analysis": "TEXT",
            "deep_research_verdict": "TEXT",
            "deep_research_risk": "TEXT",
            "deep_research_catalyst": "TEXT",
            "deep_research_knife_catch": "TEXT"
        }
        
        for col_name, col_type in new_columns.items():
            if col_name not in columns:
                print(f"Migrating database: Adding column {col_name} to decision_points")
                cursor.execute(f"ALTER TABLE decision_points ADD COLUMN {col_name} {col_type}")
                
    except Exception as e:
        print(f"Error during database migration: {e}")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS decision_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER NOT NULL,
            price REAL NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (decision_id) REFERENCES decision_points (id)
        )
    ''')
    conn.commit()
    conn.close()

def add_subscriber(email: str) -> bool:
    """Add a new subscriber. Returns True if added, False if already exists."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO subscribers (email) VALUES (?)", (email,))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        # Email already exists
        return False
    except Exception as e:
        print(f"Error adding subscriber: {e}")
        return False

def get_all_subscribers() -> List[str]:
    """Get a list of all subscriber emails."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM subscribers")
        emails = [row[0] for row in cursor.fetchall()]
        conn.close()
        return emails
    except Exception as e:
        print(f"Error fetching subscribers: {e}")
        return []

def add_decision_point(symbol: str, price: float, drop_percent: float, recommendation: str, reasoning: str, status: str = "Ignored", 
                      company_name: str = None, pe_ratio: float = None, market_cap: float = None, sector: str = None, region: str = None,
                      is_earnings_drop: bool = False, earnings_date: str = None, ai_score: float = None, git_version: str = None) -> int:
    """Add a new decision point."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO decision_points (symbol, price_at_decision, drop_percent, recommendation, reasoning, status, company_name, pe_ratio, market_cap, sector, region, is_earnings_drop, earnings_date, ai_score, git_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (symbol, price, drop_percent, recommendation, reasoning, status, company_name, pe_ratio, market_cap, sector, region, is_earnings_drop, earnings_date, ai_score, git_version))
        conn.commit()
        last_id = cursor.lastrowid
        conn.close()
        return last_id
    except Exception as e:
        print(f"Error adding decision point: {e}")
        return None

def update_decision_point(decision_id: int, recommendation: str, reasoning: str, status: str, ai_score: float = None) -> bool:
    """Update an existing decision point."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        if ai_score is not None:
            cursor.execute('''
                UPDATE decision_points 
                SET recommendation = ?, reasoning = ?, status = ?, ai_score = ?
                WHERE id = ?
            ''', (recommendation, reasoning, status, ai_score, decision_id))
        else:
            cursor.execute('''
                UPDATE decision_points 
                SET recommendation = ?, reasoning = ?, status = ?
                WHERE id = ?
            ''', (recommendation, reasoning, status, decision_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error updating decision point: {e}")
        return False

def get_decision_points() -> List[dict]:
    """Get all decision points."""
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM decision_points ORDER BY timestamp DESC")
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error fetching decision points: {e}")
        return []

def get_decision_point(decision_id: int) -> dict:
    """Get a single decision point by ID."""
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM decision_points WHERE id = ?", (decision_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"Error fetching decision point {decision_id}: {e}")
        return None

def add_tracking_point(decision_id: int, price: float) -> bool:
    """Add a new tracking point for a decision."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO decision_tracking (decision_id, price)
            VALUES (?, ?)
        ''', (decision_id, price))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error adding tracking point: {e}")
        return False

def get_tracking_history(decision_id: int) -> List[dict]:
    """Get tracking history for a decision."""
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM decision_tracking WHERE decision_id = ? ORDER BY timestamp ASC", (decision_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error fetching tracking history: {e}")
        return []

def get_today_decision_symbols() -> List[str]:
    """Get a list of symbols that have been processed today."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        # SQLite 'date' function returns YYYY-MM-DD
        cursor.execute("SELECT symbol FROM decision_points WHERE date(timestamp) = date('now')")
        symbols = [row[0] for row in cursor.fetchall()]
        conn.close()
        return symbols
    except Exception as e:
        print(f"Error fetching today's decision symbols: {e}")
        return []

def get_analyzed_companies_since(date_str: str) -> List[str]:
    """
    Get a list of company names analyzed on or after the specific date.
    Returns a list of uppercase company names.
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        # Filter where company_name is not null and date >= date_str
        cursor.execute("SELECT DISTINCT company_name FROM decision_points WHERE company_name IS NOT NULL AND date(timestamp) >= date(?)", (date_str,))
        rows = cursor.fetchall()
        
        companies = []
        for row in rows:
            if row[0]:
                companies.append(row[0].upper())
        conn.close()
        return companies
    except Exception as e:
        print(f"Error fetching analyzed companies: {e}")
        return []

def update_deep_research_data(decision_id: int, verdict: str, risk: str, catalyst: str, knife_catch: str, score: int = 0, swot: str = None, global_analysis: str = None, local_analysis: str = None) -> bool:
    """Update deep research fields for a decision point."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE decision_points 
            SET deep_research_verdict = ?, 
                deep_research_risk = ?, 
                deep_research_catalyst = ?, 
                deep_research_knife_catch = ?,
                deep_research_score = ?,
                deep_research_swot = ?,
                deep_research_global_analysis = ?,
                deep_research_local_analysis = ?
            WHERE id = ?
        ''', (verdict, risk, catalyst, knife_catch, score, swot, global_analysis, local_analysis, decision_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error updating deep research data: {e}")
        return False

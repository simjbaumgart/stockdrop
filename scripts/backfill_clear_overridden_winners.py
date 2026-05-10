"""One-shot fix: clear batch_winner=1 on rows where DR overrode to AVOID.
The selection logic was buggy when these were named winners; the dashboard
should not display them as 🏆."""
import os
import sqlite3

DB = os.getenv("DB_PATH", "subscribers.db")


def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE decision_points
        SET batch_winner = 0
        WHERE batch_winner = 1
          AND (deep_research_review_verdict = 'OVERRIDDEN'
               OR deep_research_action = 'AVOID'
               OR deep_research_verdict = 'AVOID')
        """
    )
    print(f"[Backfill] Cleared {cur.rowcount} stale batch_winner flags.")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()

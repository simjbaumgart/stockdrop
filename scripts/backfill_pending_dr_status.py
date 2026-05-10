"""One-shot fix: rows that were marked 'Owned' before DR completed and are
still BUY/BUY_LIMIT but DR ultimately overrode to AVOID should be demoted
to 'Not Owned'. Mirrors the new 3-state machine retroactively."""
import os
import sqlite3

DB = os.getenv("DB_PATH", "subscribers.db")


def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE decision_points
        SET status = 'Not Owned'
        WHERE status = 'Owned'
          AND (deep_research_review_verdict = 'OVERRIDDEN'
               OR deep_research_action IN ('AVOID', 'WATCH', 'HOLD', 'SELL'))
        """
    )
    print(f"[Backfill] Demoted {cur.rowcount} stale 'Owned' rows to 'Not Owned'.")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()

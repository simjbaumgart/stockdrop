"""Export the SQLite database to CSV files — safe to run while the FastAPI
app is reading/writing it.

Uses SQLite's read-only URI mode (`file:...?mode=ro`) which guarantees we
will not acquire any write lock or block the running process. Also sets
`PRAGMA query_only = ON` and a short busy_timeout so the export fails
fast rather than blocking if a writer is mid-transaction.

Outputs
-------
  docs/performance/<date>-package/database_export/
    decision_points.csv
    decision_tracking.csv
    batch_comparisons.csv
    subscribers.csv             (email column omitted for privacy by default)
    transcript_cache.csv
    schema.sql                  (CREATE TABLE statements for each table)
    README.md                   (row counts + export timestamp)

Usage
-----
  ./venv/bin/python scripts/analysis/export_database.py
  ./venv/bin/python scripts/analysis/export_database.py --out path/to/dir
  ./venv/bin/python scripts/analysis/export_database.py --include-subscribers-emails
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("db_export")


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open SQLite in URI read-only mode + query_only PRAGMA.

    Will not acquire any write lock. Times out fast (2s) if a writer is
    blocking, rather than hanging.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}")
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only = ON;")
    conn.execute("PRAGMA busy_timeout = 2000;")  # fail fast if locked
    return conn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.getenv("DB_PATH", "subscribers.db"))
    today = datetime.now().strftime("%Y-%m-%d")
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "docs" / "performance" / f"{today}-package" / "database_export"),
    )
    parser.add_argument(
        "--include-subscribers-emails", action="store_true",
        help="Include the email column in subscribers.csv (default: omit for privacy)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Opening %s in read-only mode...", db_path)
    conn = open_readonly(db_path)
    try:
        # Discover all tables (skip sqlite_* internals)
        tables = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name",
            conn,
        )["name"].tolist()
        logger.info("Found %d tables: %s", len(tables), ", ".join(tables))

        # Dump each table to CSV
        manifest_rows = []
        for tbl in tables:
            try:
                df = pd.read_sql_query(f"SELECT * FROM '{tbl}'", conn)
            except Exception as e:
                logger.warning("Failed to read %s: %s", tbl, e)
                continue

            # Privacy: scrub the subscribers table by default
            if tbl == "subscribers" and not args.include_subscribers_emails:
                if "email" in df.columns:
                    df = df.drop(columns=["email"])
                    logger.info("Dropped email column from subscribers (use "
                                "--include-subscribers-emails to keep)")

            out_path = out_dir / f"{tbl}.csv"
            df.to_csv(out_path, index=False)
            logger.info("  %s: %d rows -> %s", tbl, len(df), out_path.name)
            manifest_rows.append({
                "table": tbl,
                "row_count": len(df),
                "columns": len(df.columns),
                "file": out_path.name,
            })

        # Dump the schema (CREATE TABLE statements) so the CSVs can be
        # round-tripped back into a fresh SQLite DB if needed.
        schema_path = out_dir / "schema.sql"
        with open(schema_path, "w") as f:
            f.write("-- Schema for subscribers.db\n")
            f.write(f"-- Exported {datetime.now().isoformat()}\n\n")
            for row in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ):
                if row[0]:
                    f.write(row[0] + ";\n\n")
        logger.info("Wrote schema to %s", schema_path.name)

        # Manifest
        manifest = pd.DataFrame(manifest_rows)
        manifest_path = out_dir / "manifest.csv"
        manifest.to_csv(manifest_path, index=False)

        readme = out_dir / "README.md"
        readme.write_text(
            f"# Database export — {today}\n\n"
            f"Exported from `{db_path}` in read-only mode at "
            f"{datetime.now().isoformat()}.\n\n"
            f"The running FastAPI service was not interrupted; SQLite's URI "
            f"read-only mode (`?mode=ro`) does not acquire write locks.\n\n"
            "## Tables\n\n"
            + manifest.to_markdown(index=False)
            + "\n\n## Files\n\n"
            + "\n".join(f"- `{r['file']}` — {r['row_count']} rows × {r['columns']} cols"
                       for r in manifest_rows)
            + "\n- `schema.sql` — CREATE TABLE statements\n"
            + "- `manifest.csv` — this table\n"
        )
        logger.info("Wrote README to %s", readme.name)

    finally:
        conn.close()

    logger.info("Done. Package at %s", out_dir)


if __name__ == "__main__":
    main()

"""
Cleanup script for international (non-US) decision points.

Reports and optionally deletes decision_points rows that have international
stock symbols (identified by exchange suffixes like .DE, .L, .T, .SS, etc.)
or NaN prices.

Usage:
    python scripts/maintenance/cleanup_international_decisions.py          # Dry run (report only)
    python scripts/maintenance/cleanup_international_decisions.py --delete  # Actually delete rows
"""

import sqlite3
import os
import sys
import math

DB_PATH = os.getenv("DB_PATH", "subscribers.db")

INTERNATIONAL_SUFFIXES = [
    ".DE", ".PA", ".SW", ".L", ".AS", ".BR", ".LS",  # Europe
    ".MI", ".MC", ".F", ".ST", ".HE", ".CO",          # More Europe
    ".T", ".SS", ".SZ", ".HK", ".NS", ".BO",          # Asia
    ".TW", ".KS", ".AX", ".SA", ".TO", ".V",          # Other
]


def main():
    delete_mode = "--delete" in sys.argv

    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Find international symbols (have a dot-suffix that matches known intl exchanges)
    cursor.execute("SELECT id, symbol, price_at_decision, region, timestamp, recommendation FROM decision_points")
    rows = cursor.fetchall()

    international_rows = []
    nan_price_rows = []
    us_nan_rows = []

    for row in rows:
        symbol = row["symbol"] or ""
        price = row["price_at_decision"]
        is_intl = any(symbol.upper().endswith(suffix) for suffix in INTERNATIONAL_SUFFIXES)
        is_nan = price is None or (isinstance(price, float) and math.isnan(price))

        if is_intl:
            international_rows.append(row)
        elif is_nan:
            us_nan_rows.append(row)

        if is_nan:
            nan_price_rows.append(row)

    # Report
    print(f"Database: {DB_PATH}")
    print(f"Total decision points: {len(rows)}")
    print(f"International symbols: {len(international_rows)}")
    print(f"NaN/NULL prices (all): {len(nan_price_rows)}")
    print(f"NaN/NULL prices (US only): {len(us_nan_rows)}")
    print()

    # Breakdown by suffix
    suffix_counts = {}
    for row in international_rows:
        symbol = row["symbol"]
        suffix = "." + symbol.split(".")[-1] if "." in symbol else "none"
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1

    if suffix_counts:
        print("International breakdown by exchange suffix:")
        for suffix, count in sorted(suffix_counts.items(), key=lambda x: -x[1]):
            print(f"  {suffix}: {count}")
        print()

    # Breakdown by region
    region_counts = {}
    for row in international_rows:
        region = row["region"] or "unknown"
        region_counts[region] = region_counts.get(region, 0) + 1

    if region_counts:
        print("International breakdown by region:")
        for region, count in sorted(region_counts.items(), key=lambda x: -x[1]):
            print(f"  {region}: {count}")
        print()

    # Sample rows
    if international_rows:
        print("Sample international rows (first 10):")
        for row in international_rows[:10]:
            print(f"  id={row['id']} symbol={row['symbol']} price={row['price_at_decision']} "
                  f"region={row['region']} rec={row['recommendation']} date={row['timestamp']}")
        print()

    if delete_mode:
        ids_to_delete = [row["id"] for row in international_rows]
        if not ids_to_delete:
            print("Nothing to delete.")
        else:
            print(f"Deleting {len(ids_to_delete)} international decision points...")

            # Delete tracking points first (foreign key)
            placeholders = ",".join("?" * len(ids_to_delete))
            cursor.execute(f"DELETE FROM decision_tracking WHERE decision_id IN ({placeholders})", ids_to_delete)
            tracking_deleted = cursor.rowcount
            print(f"  Deleted {tracking_deleted} tracking points.")

            cursor.execute(f"DELETE FROM decision_points WHERE id IN ({placeholders})", ids_to_delete)
            decisions_deleted = cursor.rowcount
            print(f"  Deleted {decisions_deleted} decision points.")

            conn.commit()
            print("Done.")
    else:
        if international_rows:
            print("Dry run. Use --delete to remove international rows.")

    conn.close()


if __name__ == "__main__":
    main()

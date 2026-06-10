"""Print a token-usage / cost report from the agent_token_usage table.

Usage:
    python -m scripts.analysis.token_usage_report                 # last 7 days
    python -m scripts.analysis.token_usage_report --days 30
    python -m scripts.analysis.token_usage_report --since 2026-05-01 --until 2026-05-26
    python -m scripts.analysis.token_usage_report --ticker NVDA
    python -m scripts.analysis.token_usage_report --decision-id 1234   # per-call detail
    python -m scripts.analysis.token_usage_report --by model           # group: model|agent|stage|ticker|date
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import app.database as _db  # noqa: E402

GROUP_COLS = {
    "model":  "model",
    "agent":  "agent_name",
    "stage":  "stage",
    "ticker": "ticker",
    "date":   "run_date",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db.DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def _date_window(args: argparse.Namespace) -> tuple[str, str]:
    if args.since or args.until:
        since = args.since or "1970-01-01"
        until = args.until or dt.date.today().isoformat()
    else:
        today = dt.date.today()
        since = (today - dt.timedelta(days=args.days)).isoformat()
        until = today.isoformat()
    return since, until


def _fmt_int(n: int | None) -> str:
    return f"{int(n or 0):>12,}"


def _fmt_usd(x: float | None) -> str:
    return f"${(x or 0.0):>10,.4f}"


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(r[i].ljust(widths[i]) for i in range(len(headers))))


def _per_decision_detail(conn: sqlite3.Connection, decision_id: int) -> None:
    rows = conn.execute(
        """
        SELECT stage, agent_name, model, tokens_in, tokens_out, cost_usd
        FROM agent_token_usage
        WHERE decision_id = ?
        ORDER BY id
        """,
        (decision_id,),
    ).fetchall()
    if not rows:
        print(f"No token-usage rows for decision_id={decision_id}.")
        return
    meta = conn.execute(
        """
        SELECT ticker, MIN(run_date) AS run_date
        FROM agent_token_usage
        WHERE decision_id = ?
        """,
        (decision_id,),
    ).fetchone()
    header = f"decision_id={decision_id}"
    if meta and meta["ticker"]:
        header += f"  ticker={meta['ticker']}  run_date={meta['run_date']}"
    print(header)
    print()
    table = [
        [r["stage"], r["agent_name"], r["model"],
         f"{r['tokens_in']:>10,}", f"{r['tokens_out']:>10,}",
         _fmt_usd(r["cost_usd"])]
        for r in rows
    ]
    _print_table(["stage", "agent", "model", "tokens_in", "tokens_out", "cost_usd"], table)
    tot_in = sum(r["tokens_in"] for r in rows)
    tot_out = sum(r["tokens_out"] for r in rows)
    tot_cost = sum((r["cost_usd"] or 0.0) for r in rows)
    print()
    print(f"TOTAL  calls={len(rows)}  tokens_in={tot_in:,}  tokens_out={tot_out:,}  cost={_fmt_usd(tot_cost).strip()}")


def _summary(conn: sqlite3.Connection, since: str, until: str,
             ticker: str | None, group_by: str | None) -> None:
    where = ["run_date >= ?", "run_date <= ?"]
    params: list = [since, until]
    if ticker:
        where.append("ticker = ?")
        params.append(ticker.upper())
    where_sql = " AND ".join(where)

    overall = conn.execute(
        f"""
        SELECT COUNT(*) calls,
               COUNT(DISTINCT decision_id) decisions,
               COALESCE(SUM(tokens_in), 0)  tin,
               COALESCE(SUM(tokens_out), 0) tout,
               COALESCE(SUM(cost_usd), 0.0) cost
        FROM agent_token_usage
        WHERE {where_sql}
        """,
        params,
    ).fetchone()

    print(f"Window: {since} -> {until}" + (f"   ticker={ticker.upper()}" if ticker else ""))
    print()
    print("OVERALL")
    print(f"  decisions       : {overall['decisions']:>10,}")
    print(f"  llm calls       : {overall['calls']:>10,}")
    print(f"  tokens_in       : {overall['tin']:>10,}")
    print(f"  tokens_out      : {overall['tout']:>10,}")
    print(f"  total cost      : {_fmt_usd(overall['cost']).strip()}")
    if overall["decisions"]:
        d = overall["decisions"]
        print()
        print("AVERAGES PER DECISION")
        print(f"  calls/decision  : {overall['calls'] / d:>10,.2f}")
        print(f"  tokens_in /dec  : {overall['tin']   / d:>12,.0f}")
        print(f"  tokens_out/dec  : {overall['tout']  / d:>12,.0f}")
        print(f"  cost/decision   : ${overall['cost'] / d:>10,.4f}")

    if not group_by:
        return

    col = GROUP_COLS[group_by]
    rows = conn.execute(
        f"""
        SELECT {col} AS grp,
               COUNT(*) calls,
               COUNT(DISTINCT decision_id) decisions,
               COALESCE(SUM(tokens_in), 0)  tin,
               COALESCE(SUM(tokens_out), 0) tout,
               COALESCE(SUM(cost_usd), 0.0) cost
        FROM agent_token_usage
        WHERE {where_sql}
        GROUP BY {col}
        ORDER BY cost DESC
        """,
        params,
    ).fetchall()

    print()
    print(f"BY {group_by.upper()}")
    table = [
        [str(r["grp"]),
         f"{r['decisions']:>6,}",
         f"{r['calls']:>6,}",
         _fmt_int(r["tin"]),
         _fmt_int(r["tout"]),
         _fmt_usd(r["cost"])]
        for r in rows
    ]
    _print_table([group_by, "decisions", "calls", "tokens_in", "tokens_out", "cost_usd"], table)


def main() -> None:
    p = argparse.ArgumentParser(description="StockDrop token-usage report.")
    p.add_argument("--days", type=int, default=7,
                   help="Look back N days from today (ignored if --since/--until given). Default: 7.")
    p.add_argument("--since", help="Start run_date (YYYY-MM-DD), inclusive.")
    p.add_argument("--until", help="End run_date (YYYY-MM-DD), inclusive.")
    p.add_argument("--ticker", help="Filter to a single ticker.")
    p.add_argument("--decision-id", type=int,
                   help="Show per-call detail for one decision (ignores other filters).")
    p.add_argument("--by", choices=list(GROUP_COLS.keys()),
                   help="Add a grouped breakdown: model | agent | stage | ticker | date.")
    args = p.parse_args()

    conn = _connect()
    try:
        if args.decision_id is not None:
            _per_decision_detail(conn, args.decision_id)
        else:
            since, until = _date_window(args)
            _summary(conn, since, until, args.ticker, args.by)
    finally:
        conn.close()


if __name__ == "__main__":
    main()

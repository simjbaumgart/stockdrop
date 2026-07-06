"""Phase 0 — build the drop-type classifier dataset.

Joins decision_points labels to pre-PM text files (news context + council-1
reports) and writes one parquet + a dataset card.

Leakage rule (docs/proposals/PLAN_drop_type_classifier.md §2): inputs are
pre-PM only. The DB_COLUMNS whitelist below is the ONLY set of decision_points
columns that may enter this dataset besides label/metadata fields. Never add
reasoning, gate_reasons, deep_research_*, reassess_*, or decision_outcomes.

Usage:
    ./venv/bin/python scripts/ml/build_drop_type_dataset.py \
        --db subscribers.db --out data/ml/drop_type_dataset.parquet
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

SPLIT_BOUNDARY = "2026-06-08"  # test = decision_date >= boundary
MAX_DATE = "2026-07-05"  # dataset window freeze (inclusive) — the DB is live;
                          # rows after this date were still mutating on build day

# Whitelisted structured columns (pre-PM only).
DB_COLUMNS = [
    "drop_percent", "sector", "market_cap", "pe_ratio",
    "is_earnings_drop", "earnings_date", "surprise_pct",
]
C1_FIELDS = ["news", "competitive", "market_sentiment", "technical"]
GATE_TRIGGER_CLASSES = frozenset(
    {"EARNINGS_MISS", "COMPANY_SPECIFIC", "ANALYST_DOWNGRADE"}
)
EXPECTED_COLUMNS = [
    "symbol", "decision_date", "split", "label_batch", "label_month",
    "drop_type", "gate_trigger",
    "news_context", "c1_news", "c1_competitive", "c1_market_sentiment",
    "c1_technical",
    *DB_COLUMNS,
    "days_since_earnings", "has_surprise_pct",
]


def days_since_earnings(decision_date: str, earnings_date: object) -> Optional[int]:
    """Days from last earnings to decision day; None if unparseable/missing."""
    if not earnings_date or not isinstance(earnings_date, str):
        return None
    try:
        d = date.fromisoformat(decision_date)
        e = date.fromisoformat(earnings_date[:10])
    except ValueError:
        return None
    return (d - e).days


def load_labeled_rows(db_path: str, max_date: str = MAX_DATE) -> pd.DataFrame:
    """One row per (symbol, day): 3 pairs are duplicated — keep the latest id."""
    con = sqlite3.connect(db_path)
    try:
        cols = ", ".join(f"dp.{c}" for c in DB_COLUMNS)
        query = f"""
            SELECT dp.symbol, date(dp.timestamp) AS decision_date,
                   dp.drop_type, dp.git_version AS label_batch, {cols}
            FROM decision_points dp
            JOIN (
                SELECT MAX(id) AS id FROM decision_points
                WHERE drop_type IS NOT NULL AND date(timestamp) <= '{max_date}'
                GROUP BY symbol, date(timestamp)
            ) latest ON dp.id = latest.id
            ORDER BY decision_date, dp.symbol
        """
        return pd.read_sql_query(query, con)
    finally:
        con.close()


def attach_files(df: pd.DataFrame, news_dir: Path, council_dir: Path) -> pd.DataFrame:
    """Join to files; drop rows missing either file."""
    rows = []
    for rec in df.to_dict("records"):
        stem = f"{rec['symbol']}_{rec['decision_date']}"
        news_path = news_dir / f"{stem}_news_context.txt"
        c1_path = council_dir / f"{stem}_council1.json"
        if not news_path.exists() or not c1_path.exists():
            continue
        rec["news_context"] = news_path.read_text(errors="replace")
        c1 = json.loads(c1_path.read_text(errors="replace"))
        for field in C1_FIELDS:
            rec[f"c1_{field}"] = str(c1.get(field) or "")
        rows.append(rec)
    return pd.DataFrame(rows)


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["days_since_earnings"] = [
        days_since_earnings(d, e)
        for d, e in zip(df["decision_date"], df["earnings_date"])
    ]
    df["days_since_earnings"] = df["days_since_earnings"].astype("Int64")
    df["has_surprise_pct"] = df["surprise_pct"].notna()
    df["gate_trigger"] = df["drop_type"].isin(GATE_TRIGGER_CLASSES)
    df["split"] = (df["decision_date"] >= SPLIT_BOUNDARY).map(
        {True: "test", False: "train"}
    )
    df["label_month"] = df["decision_date"].str[:7]
    return df[EXPECTED_COLUMNS]


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def write_card(df: pd.DataFrame, parquet_path: Path, card_path: Path, max_date: str = MAX_DATE) -> None:
    sha = hashlib.sha256(parquet_path.read_bytes()).hexdigest()
    lines = [
        "# Dataset card — drop_type_dataset.parquet",
        "",
        f"- Built: {date.today().isoformat()} by `scripts/ml/build_drop_type_dataset.py` @ `{git_commit()}`",
        f"- Rows: {len(df)}",
        f"- Split boundary: test = decision_date >= {SPLIT_BOUNDARY} (train {int((df['split'] == 'train').sum())} / test {int((df['split'] == 'test').sum())})",
        f"- sha256: `{sha}`",
        "",
        "## Class counts per split",
        "",
        "| drop_type | train | test |",
        "|---|---|---|",
    ]
    counts = df.groupby(["drop_type", "split"]).size().unstack(fill_value=0)
    for label, row in counts.sort_values("train", ascending=False).iterrows():
        lines.append(f"| {label} | {row.get('train', 0)} | {row.get('test', 0)} |")
    lines += ["", "## Rows per label_month", "", "| label_month | n |", "|---|---|"]
    for month, n in df["label_month"].value_counts().sort_index().items():
        lines.append(f"| {month} | {n} |")
    lines += [
        "",
        "## Provenance",
        "",
        f"- Window frozen at decision_date <= {max_date} (DB is live; later rows excluded for reproducibility).",
        "- Teacher: `gemini-3.1-pro-preview` for all rows (constant since v0.8.2,",
        "  pre-window); unrecorded flash fallback on 503s is possible label noise.",
        "- `label_batch` = decision_points.git_version (metadata only, never a feature).",
        "- Dedup: latest id per (symbol, day); 3 duplicate pairs dropped to 1.",
        "",
    ]
    card_path.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="subscribers.db")
    parser.add_argument("--out", default="data/ml/drop_type_dataset.parquet")
    parser.add_argument("--news-dir", default="data/news")
    parser.add_argument("--council-dir", default="data/council_reports")
    parser.add_argument("--max-date", default=MAX_DATE)
    args = parser.parse_args()

    labeled = load_labeled_rows(args.db, args.max_date)
    print(f"labeled (symbol, day) pairs: {len(labeled)}")
    df = attach_files(labeled, Path(args.news_dir), Path(args.council_dir))
    print(f"with both files: {len(df)}")
    df = add_derived(df)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    write_card(df, out, out.parent / "dataset_card.md", args.max_date)
    print(f"wrote {out} ({len(df)} rows) + dataset_card.md")
    print(df["drop_type"].value_counts().to_string())


if __name__ == "__main__":
    main()

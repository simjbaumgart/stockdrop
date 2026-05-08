"""Compose the deep-dive markdown report from aggregations and chart paths."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd


def _fmt_cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        if pd.isna(v):
            return ""
        return f"{v:.3f}"
    return str(v)


def df_to_md(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "_no data_"
    formatted = df.copy()
    for col in formatted.columns:
        formatted[col] = formatted[col].map(_fmt_cell)
    try:
        return formatted.to_markdown(index=False)
    except ImportError:
        cols = list(formatted.columns)
        header = "| " + " | ".join(str(c) for c in cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in formatted.values.tolist()]
        return "\n".join([header, sep, *rows])


def img_link(path: Path, base_dir: Path) -> str:
    p = Path(path)
    base_dir = Path(base_dir)
    try:
        rel = p.relative_to(base_dir)
    except ValueError:
        rel = p
    return f"![chart]({rel})"


class Section:
    def __init__(self, title: str, body: str):
        self.title = title
        self.body = body


def render_report(
    out_path: Path,
    cohort_label: str,
    n_decisions: int,
    sections: List[Section],
    appendix: Optional[List[Section]] = None,
) -> Path:
    """Write a top-level deep-dive markdown report."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# StockDrop Performance Deep-Dive — {cohort_label}",
        "",
        f"_Generated {now}. Cohort size: {n_decisions} decisions._",
        "",
        "## Contents",
        "",
    ]
    for i, s in enumerate(sections, 1):
        anchor = s.title.lower().replace(" ", "-").replace("/", "-").replace("—", "")
        anchor = "-".join(anchor.split())
        lines.append(f"{i}. [{s.title}](#{anchor})")
    lines.append("")
    for s in sections:
        lines.append(f"## {s.title}")
        lines.append("")
        lines.append(s.body)
        lines.append("")
    if appendix:
        lines.append("---")
        lines.append("## Appendix")
        lines.append("")
        for s in appendix:
            lines.append(f"### {s.title}")
            lines.append("")
            lines.append(s.body)
            lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path

"""Matplotlib chart renderers. Each function saves a PNG and returns the path."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return path


def winrate_bar(agg: pd.DataFrame, group_col: str, title: str, out_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4))
    if agg.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, out_path)
    x = agg[group_col].astype(str)
    ax.bar(x, agg["win_rate"], color="#4C72B0")
    ax.set_ylim(0, 1.1)
    ax.set_ylabel("Win rate")
    ax.set_title(title)
    for i, (wr, n) in enumerate(zip(agg["win_rate"], agg["count"])):
        ax.text(i, wr + 0.02, f"{wr:.0%}\n(n={n})", ha="center", fontsize=8)
    plt.xticks(rotation=30, ha="right")
    return _save(fig, out_path)


def avg_return_bar(agg: pd.DataFrame, group_col: str, title: str, out_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4))
    if agg.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, out_path)
    x = agg[group_col].astype(str)
    colors = ["#55A868" if v >= 0 else "#C44E52" for v in agg["avg_return"]]
    ax.bar(x, agg["avg_return"] * 100, color=colors)
    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_ylabel("Avg return (%)")
    ax.set_title(title)
    plt.xticks(rotation=30, ha="right")
    return _save(fig, out_path)


def equity_line(curve: pd.DataFrame, title: str, out_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(9, 4))
    if curve.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, out_path)
    ax.plot(curve["decision_date"], curve["equity"], color="#4C72B0", linewidth=2)
    ax.axhline(1.0, color="grey", linewidth=0.7, linestyle="--")
    ax.set_ylabel("Equity (start = 1.0)")
    ax.set_title(title)
    fig.autofmt_xdate()
    return _save(fig, out_path)


def hist_bar(series: pd.Series, title: str, xlabel: str, out_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4))
    if series is None or series.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, out_path)
    ax.bar(series.index.astype(str), series.values, color="#8172B2")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Count")
    ax.set_title(title)
    plt.xticks(rotation=0, fontsize=8)
    return _save(fig, out_path)

"""Pure chart functions. Each takes data + output path, writes one PNG."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

FIGSIZE = (12, 8)
DPI = 100

VERDICT_COLORS = {
    "BUY": "#22c55e",
    "BUY_LIMIT": "#3b82f6",
    "WATCH": "#f59e0b",
    "AVOID": "#ef4444",
    "PASS_INSUFFICIENT_DATA": "#94a3b8",
}


def _empty_chart(out: Path, message: str) -> None:
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=18, color="#64748b")
    ax.set_axis_off()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def chart_verdict_distribution(decisions: pd.DataFrame, out: Path) -> None:
    if decisions.empty:
        _empty_chart(out, "No decisions in window")
        return
    counts = decisions["recommendation"].value_counts().sort_values()
    colors = [VERDICT_COLORS.get(v, "#94a3b8") for v in counts.index]
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.barh(counts.index, counts.values, color=colors)
    for i, v in enumerate(counts.values):
        ax.text(v + 0.5, i, str(v), va="center")
    ax.set_xlabel("Number of decisions")
    ax.set_title("PM Verdict distribution (last 30 days)")
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def chart_sector_breakdown(decisions: pd.DataFrame, out: Path) -> None:
    if decisions.empty or "sector" not in decisions.columns:
        _empty_chart(out, "No sector data")
        return
    counts = decisions["sector"].fillna("Unknown").value_counts().head(12).sort_values()
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.barh(counts.index, counts.values, color="#3b82f6")
    for i, v in enumerate(counts.values):
        ax.text(v + 0.1, i, str(v), va="center")
    ax.set_xlabel("Number of decisions")
    ax.set_title("Decisions by sector (top 12, last 30 days)")
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def chart_pnl_distribution(positions: pd.DataFrame, out: Path) -> None:
    closed = positions[positions["status"] == "CLOSED"] if not positions.empty else positions
    if closed.empty:
        _empty_chart(out, "No closed positions yet")
        return
    values = closed["realized_pnl_pct"].dropna()
    if values.empty:
        _empty_chart(out, "No realized P&L data")
        return
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.hist(values, bins=15, color="#3b82f6", edgecolor="white")
    ax.axvline(0, color="#64748b", linestyle="--", linewidth=1, label="Break-even")
    ax.axvline(values.mean(), color="#22c55e", linestyle="-", linewidth=2,
               label=f"Mean: {values.mean():+.2f}%")
    ax.set_xlabel("Realized P&L (%)")
    ax.set_ylabel("Number of closed positions")
    ax.set_title("Realized P&L distribution — closed positions")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def chart_score_vs_outcome(
    decisions: pd.DataFrame, positions: pd.DataFrame, out: Path
) -> None:
    if decisions.empty or positions.empty:
        _empty_chart(out, "No data for score-vs-outcome")
        return
    closed = positions[positions["status"] == "CLOSED"]
    if closed.empty:
        _empty_chart(out, "No closed positions yet")
        return
    joined = closed.merge(
        decisions[["id", "ai_score"]],
        left_on="decision_point_id",
        right_on="id",
        how="inner",
    ).dropna(subset=["ai_score", "realized_pnl_pct"])

    if len(joined) < 3:
        _empty_chart(out, f"Only {len(joined)} closed positions — too few to plot")
        return

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    ax.scatter(joined["ai_score"], joined["realized_pnl_pct"], s=80, alpha=0.7, color="#3b82f6")

    # Regression line + Pearson r
    x = joined["ai_score"].values
    y = joined["realized_pnl_pct"].values
    if np.std(x) > 0:
        slope, intercept = np.polyfit(x, y, 1)
        xs = np.linspace(x.min(), x.max(), 50)
        ax.plot(xs, slope * xs + intercept, color="#ef4444", linewidth=1.5, linestyle="--")
        r = float(np.corrcoef(x, y)[0, 1])
        note = f"Pearson r = {r:+.2f}, n = {len(joined)}"
    else:
        note = f"n = {len(joined)}"

    if len(joined) < 10:
        note += "  (small sample)"

    ax.axhline(0, color="#64748b", linestyle=":", linewidth=1)
    ax.set_xlabel("AI score (0–100)")
    ax.set_ylabel("Realized P&L (%)")
    ax.set_title("AI score vs. realized outcome")
    ax.text(0.02, 0.97, note, transform=ax.transAxes, va="top", fontsize=11,
            bbox=dict(facecolor="white", edgecolor="#e2e8f0"))
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)

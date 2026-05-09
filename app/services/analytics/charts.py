"""Matplotlib chart renderers. Each function saves a PNG and returns the path."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

# Stable palette so plots are consistent across reports
INTENT_COLORS = {
    "ENTER_NOW": "#22c55e",
    "ENTER_LIMIT": "#3b82f6",
    "AVOID": "#ef4444",
    "NEUTRAL": "#94a3b8",
}
DR_COLORS = {
    "BUY": "#22c55e",
    "BUY_LIMIT": "#3b82f6",
    "AVOID": "#ef4444",
    "WATCH": "#fbbf24",
    "HOLD": "#94a3b8",
}


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


def time_series_lines(
    groups: Dict[str, dict],
    title: str,
    out_path: Path,
    palette: Optional[Dict[str, str]] = None,
    spy_overlay: Optional[dict] = None,
    ylabel: str = "Return since decision",
) -> Path:
    """One median-return line per group (input shape matches `_time_series_by_group`).

    If `spy_overlay` is provided (same shape as a group), draws it as a dashed
    grey reference.
    """
    fig, ax = plt.subplots(figsize=(10, 5))
    palette = palette or {}

    plotted = False
    for grp, data in groups.items():
        if not data or not data.get("median"):
            continue
        x = data["day_offsets"]
        y = [v * 100 if v is not None else np.nan for v in data["median"]]
        color = palette.get(grp, "#cbd5e1")
        n = data.get("n_paths", 0)
        ax.plot(x, y, color=color, linewidth=2.2, label=f"{grp} (n={n})")
        plotted = True

    if spy_overlay and spy_overlay.get("median"):
        x = spy_overlay["day_offsets"]
        y = [v * 100 if v is not None else np.nan for v in spy_overlay["median"]]
        ax.plot(x, y, color="#6b7280", linewidth=1.6, linestyle="--",
                label=f"S&P 500 (n_windows={spy_overlay.get('n_paths', 0)})")
        plotted = True

    if not plotted:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return _save(fig, out_path)

    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_xlabel("Trading days since decision")
    ax.set_ylabel(ylabel + " (%)")
    ax.set_title(title)
    ax.grid(True, linestyle=":", alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    return _save(fig, out_path)


def spaghetti_plot(
    individuals: List[dict],
    medians: Dict[str, dict],
    title: str,
    out_path: Path,
    palette: Optional[Dict[str, str]] = None,
) -> Path:
    """Light grey lines for each individual path; bold colored medians overlaid."""
    fig, ax = plt.subplots(figsize=(11, 5.5))
    palette = palette or {}

    if not individuals:
        ax.text(0.5, 0.5, "no individual paths", ha="center", va="center",
                transform=ax.transAxes)
        ax.set_axis_off()
        return _save(fig, out_path)

    for p in individuals:
        rets = [v * 100 for v in p["returns"]]
        x = list(range(len(rets)))
        ax.plot(x, rets, color="#94a3b8", linewidth=0.6, alpha=0.25)

    for grp, data in medians.items():
        if not data or not data.get("median"):
            continue
        x = data["day_offsets"]
        y = [v * 100 if v is not None else np.nan for v in data["median"]]
        ax.plot(x, y, color=palette.get(grp, "#0ea5e9"), linewidth=3,
                label=f"{grp} median (n={data.get('n_paths', 0)})")

    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_xlabel("Trading days since decision")
    ax.set_ylabel("Return since decision (%)")
    ax.set_title(title)
    ax.grid(True, linestyle=":", alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    return _save(fig, out_path)


def scatter_with_regression(
    points: Sequence[dict],
    slope: Optional[float],
    intercept: Optional[float],
    pearson_r: Optional[float],
    pearson_p: Optional[float],
    spearman_rho: Optional[float],
    spearman_p: Optional[float],
    title: str,
    xlabel: str,
    out_path: Path,
) -> Path:
    """Scatter of (x, y) with the OLS line and a stats annotation."""
    fig, ax = plt.subplots(figsize=(8, 5))
    if not points:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return _save(fig, out_path)

    xs = np.asarray([p["x"] for p in points])
    ys = np.asarray([p["y"] * 100 for p in points])  # convert to percent
    ax.scatter(xs, ys, s=22, alpha=0.55, color="#3b82f6", edgecolor="none")

    if slope is not None and intercept is not None and len(xs) >= 2:
        xline = np.linspace(xs.min(), xs.max(), 50)
        yline = (slope * xline + intercept) * 100
        ax.plot(xline, yline, color="#fbbf24", linewidth=2, label="OLS fit")
        ax.legend(loc="best", fontsize=9)

    def _pf(p):
        if p is None:
            return "—"
        return "<0.001" if p < 0.001 else f"{p:.3f}"

    annot_lines = [f"n={len(points)}"]
    if pearson_r is not None:
        annot_lines.append(f"Pearson r={pearson_r:+.3f} (p={_pf(pearson_p)})")
    if spearman_rho is not None:
        annot_lines.append(f"Spearman ρ={spearman_rho:+.3f} (p={_pf(spearman_p)})")
    if slope is not None:
        annot_lines.append(f"slope={slope:+.4f}")

    ax.text(0.02, 0.98, "\n".join(annot_lines),
            transform=ax.transAxes, va="top", ha="left",
            fontsize=9, family="monospace",
            bbox=dict(facecolor="white", alpha=0.85, edgecolor="#cbd5e1"))

    ax.axhline(0, color="black", linewidth=0.4, linestyle=":")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("4-week return (%)")
    ax.set_title(title)
    ax.grid(True, linestyle=":", alpha=0.3)
    return _save(fig, out_path)


def recovery_histogram_by_group(
    df: pd.DataFrame,
    group_col: str,
    title: str,
    out_path: Path,
    max_days: int = 40,
    palette: Optional[Dict[str, str]] = None,
) -> Path:
    """Overlapping histograms of `days_to_recover`, one transparent layer per group."""
    fig, ax = plt.subplots(figsize=(10, 5))
    palette = palette or {}
    if df.empty or "days_to_recover" not in df.columns:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return _save(fig, out_path)

    bins = np.arange(0, max_days + 2)
    plotted = False
    for grp, sub in df.groupby(group_col, dropna=False):
        days = sub["days_to_recover"].dropna().clip(upper=max_days)
        if days.empty:
            continue
        color = palette.get(str(grp), "#94a3b8")
        ax.hist(days, bins=bins, alpha=0.45, color=color,
                label=f"{grp} (n={len(days)})", edgecolor=color, linewidth=0.5)
        plotted = True
    if not plotted:
        ax.text(0.5, 0.5, "no recovered decisions", ha="center", va="center",
                transform=ax.transAxes)
        ax.set_axis_off()
        return _save(fig, out_path)

    ax.set_xlabel("Trading days to recovery (capped at 40)")
    ax.set_ylabel("Count")
    ax.set_title(title)
    ax.grid(True, linestyle=":", alpha=0.3, axis="y")
    ax.legend(loc="best", fontsize=9)
    return _save(fig, out_path)


def equity_curve_chart(curve_df: pd.DataFrame, title: str, out_path: Path) -> Path:
    """Convenience wrapper around `equity_line` that takes a list of records."""
    return equity_line(curve_df, title, out_path)

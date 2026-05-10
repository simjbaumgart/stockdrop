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


def _error_pair(series_low, series_high, center) -> Optional[np.ndarray]:
    """Build a (2, n) yerr array from CI bounds; None if any bound is missing."""
    if series_low is None or series_high is None:
        return None
    low = np.asarray(series_low, dtype=float)
    high = np.asarray(series_high, dtype=float)
    c = np.asarray(center, dtype=float)
    if np.any(np.isnan(low)) or np.any(np.isnan(high)) or np.any(np.isnan(c)):
        return None
    return np.vstack([np.maximum(0, c - low), np.maximum(0, high - c)])


def winrate_bar(agg: pd.DataFrame, group_col: str, title: str, out_path: Path) -> Path:
    """Win-rate bar chart with Wilson 95% CI error bars when available."""
    fig, ax = plt.subplots(figsize=(8, 4.5))
    if agg.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, out_path)
    x = agg[group_col].astype(str)
    yerr = None
    if "win_rate_ci_low" in agg.columns and "win_rate_ci_high" in agg.columns:
        yerr = _error_pair(agg["win_rate_ci_low"], agg["win_rate_ci_high"], agg["win_rate"])
    ax.bar(x, agg["win_rate"], color="#4C72B0",
           yerr=yerr, capsize=4, ecolor="#1f2937",
           error_kw={"elinewidth": 1.2})
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("Win rate (95% Wilson CI)")
    ax.set_title(title)
    for i, (wr, n) in enumerate(zip(agg["win_rate"], agg["count"])):
        ax.text(i, min(1.10, wr + 0.04), f"{wr:.0%}\n(n={n})", ha="center", fontsize=8)
    plt.xticks(rotation=30, ha="right")
    return _save(fig, out_path)


def avg_return_bar(agg: pd.DataFrame, group_col: str, title: str, out_path: Path) -> Path:
    """Average-return bar chart with t-distribution 95% CI error bars when available."""
    fig, ax = plt.subplots(figsize=(8, 4.5))
    if agg.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, out_path)
    x = agg[group_col].astype(str)
    colors = ["#55A868" if v is not None and v >= 0 else "#C44E52" for v in agg["avg_return"]]
    centers = (agg["avg_return"] * 100).astype(float)
    yerr = None
    if "avg_return_ci_low" in agg.columns and "avg_return_ci_high" in agg.columns:
        low = agg["avg_return_ci_low"] * 100
        high = agg["avg_return_ci_high"] * 100
        yerr = _error_pair(low, high, centers)
    ax.bar(x, centers, color=colors,
           yerr=yerr, capsize=4, ecolor="#1f2937",
           error_kw={"elinewidth": 1.2})
    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_ylabel("Avg return (%) — 95% t-CI")
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
    use: str = "median",
    show_ci: bool = True,
) -> Path:
    """Per-group line chart for `_time_series_by_group` output.

    `use` selects the central tendency series ("median" or "mean").
    When `show_ci=True` and the group includes `ci_low`/`ci_high`, a translucent
    band is drawn between them; for "median" we fall back to the q25/q75 IQR
    band (which is what's available for the median).
    """
    fig, ax = plt.subplots(figsize=(10, 5))
    palette = palette or {}

    def _to_pct(seq):
        return [v * 100 if v is not None else np.nan for v in seq]

    plotted = False
    for grp, data in groups.items():
        if not data:
            continue
        center = data.get(use)
        if not center:
            continue
        x = data["day_offsets"]
        y = _to_pct(center)
        color = palette.get(grp, "#cbd5e1")
        n = data.get("n_paths", 0)

        if show_ci:
            if use == "mean" and data.get("ci_low") and data.get("ci_high"):
                lo = _to_pct(data["ci_low"])
                hi = _to_pct(data["ci_high"])
                ax.fill_between(x, lo, hi, color=color, alpha=0.12, linewidth=0)
            elif use == "median" and data.get("q25") and data.get("q75"):
                lo = _to_pct(data["q25"])
                hi = _to_pct(data["q75"])
                ax.fill_between(x, lo, hi, color=color, alpha=0.10, linewidth=0)

        ax.plot(x, y, color=color, linewidth=2.2, label=f"{grp} (n={n})")
        plotted = True

    if spy_overlay and spy_overlay.get(use):
        x = spy_overlay["day_offsets"]
        y = _to_pct(spy_overlay[use])
        ax.plot(x, y, color="#6b7280", linewidth=1.6, linestyle="--",
                label=f"S&P 500 (n_windows={spy_overlay.get('n_paths', 0)})")
        plotted = True

    if not plotted:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return _save(fig, out_path)

    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_xlabel("Trading days since decision")
    band_label = "95% CI" if use == "mean" else "IQR"
    ax.set_ylabel(f"{ylabel} (%)\n— {use} with {band_label} band")
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
    pearson_ci: Optional[tuple] = None,
    spearman_ci: Optional[tuple] = None,
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
        line = f"Pearson r={pearson_r:+.3f} (p={_pf(pearson_p)})"
        if pearson_ci and pearson_ci[0] is not None:
            line += f"\n  95% CI: [{pearson_ci[0]:+.3f}, {pearson_ci[1]:+.3f}]"
        annot_lines.append(line)
    if spearman_rho is not None:
        line = f"Spearman ρ={spearman_rho:+.3f} (p={_pf(spearman_p)})"
        if spearman_ci and spearman_ci[0] is not None:
            line += f"\n  95% CI: [{spearman_ci[0]:+.3f}, {spearman_ci[1]:+.3f}]"
        annot_lines.append(line)
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


def rr_boxplot_by_group(
    df: pd.DataFrame,
    group_col: str,
    rr_col: str,
    title: str,
    out_path: Path,
    palette: Optional[Dict[str, str]] = None,
    group_order: Optional[List[str]] = None,
    annotation: Optional[str] = None,
) -> Path:
    """Box plot of an R/R column across verdict groups, with mean marker."""
    fig, ax = plt.subplots(figsize=(9, 4.8))
    if df.empty or group_col not in df.columns or rr_col not in df.columns:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return _save(fig, out_path)

    sub = df.dropna(subset=[group_col, rr_col]).copy()
    if sub.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return _save(fig, out_path)

    if group_order:
        groups = [g for g in group_order if g in set(sub[group_col].astype(str))]
    else:
        groups = sorted(sub[group_col].astype(str).unique())

    data = [sub.loc[sub[group_col].astype(str) == g, rr_col].astype(float).values
            for g in groups]
    counts = [len(d) for d in data]
    palette = palette or {}
    box_colors = [palette.get(g, "#cbd5e1") for g in groups]

    bp = ax.boxplot(
        data, labels=groups, patch_artist=True, showmeans=True,
        meanprops=dict(marker="D", markerfacecolor="#fbbf24", markeredgecolor="#1f2937",
                       markersize=7),
        medianprops=dict(color="#1f2937", linewidth=2),
        whiskerprops=dict(color="#475569"),
        capprops=dict(color="#475569"),
        flierprops=dict(marker="o", markerfacecolor="#94a3b8",
                        markeredgecolor="#475569", markersize=4, alpha=0.6),
    )
    for patch, color in zip(bp["boxes"], box_colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.55)
        patch.set_edgecolor("#1f2937")

    # n= labels under each box
    for i, n in enumerate(counts, start=1):
        ax.text(i, ax.get_ylim()[0] + 0.02, f"n={n}",
                ha="center", va="bottom", fontsize=8, color="#475569")

    if annotation:
        ax.text(0.02, 0.98, annotation,
                transform=ax.transAxes, ha="left", va="top",
                fontsize=9, family="monospace",
                bbox=dict(facecolor="white", alpha=0.85, edgecolor="#cbd5e1"))

    ax.set_ylabel(rr_col)
    ax.set_xlabel(group_col)
    ax.set_title(title)
    ax.grid(True, axis="y", linestyle=":", alpha=0.3)
    return _save(fig, out_path)


def equity_curve_chart(curve_df: pd.DataFrame, title: str, out_path: Path) -> Path:
    """Convenience wrapper around `equity_line` that takes a list of records."""
    return equity_line(curve_df, title, out_path)


def win_loss_split_grid(
    winloss: Dict[str, dict],
    title: str,
    out_path: Path,
    palette: Optional[Dict[str, str]] = None,
    group_order: Optional[List[str]] = None,
) -> Path:
    """Small-multiples chart: one panel per group, showing winners vs losers.

    Each panel plots (a) the mean trajectory of the WINNING decisions in that
    group and (b) the mean trajectory of the LOSING decisions, both with 95%
    CI bands. The horizontal axis is trading days since decision.
    """
    palette = palette or {}
    groups = group_order if group_order else sorted(winloss.keys())
    groups = [g for g in groups if g in winloss]
    if not groups:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return _save(fig, out_path)

    n = len(groups)
    cols = min(2, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(11, 4.4 * rows), sharex=True, sharey=False)
    axes = np.atleast_1d(axes).flatten() if hasattr(axes, "flatten") else [axes]

    def _to_pct(seq):
        return [v * 100 if v is not None else np.nan for v in seq]

    for i, grp in enumerate(groups):
        ax = axes[i]
        data = winloss[grp]
        plotted = False
        # Winners
        w = data.get("winners")
        if w and w.get("mean"):
            x = w["day_offsets"]
            y = _to_pct(w["mean"])
            lo = _to_pct(w["ci_low"]); hi = _to_pct(w["ci_high"])
            ax.fill_between(x, lo, hi, color="#22c55e", alpha=0.15, linewidth=0)
            ax.plot(x, y, color="#16a34a", linewidth=2.4,
                    label=f"Winners (n={w['n_paths']})")
            plotted = True
        # Losers
        l = data.get("losers")
        if l and l.get("mean"):
            x = l["day_offsets"]
            y = _to_pct(l["mean"])
            lo = _to_pct(l["ci_low"]); hi = _to_pct(l["ci_high"])
            ax.fill_between(x, lo, hi, color="#ef4444", alpha=0.15, linewidth=0)
            ax.plot(x, y, color="#dc2626", linewidth=2.4,
                    label=f"Losers (n={l['n_paths']})")
            plotted = True

        ax.axhline(0, color="black", linewidth=0.5)
        ax.set_title(grp, fontsize=11, color=palette.get(grp, "#334155"), fontweight="bold")
        ax.grid(True, linestyle=":", alpha=0.3)
        if plotted:
            ax.legend(loc="best", fontsize=8)
        else:
            ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()

    # Hide unused axes if any
    for j in range(n, len(axes)):
        axes[j].set_visible(False)

    fig.suptitle(title, fontsize=12, y=0.995)
    fig.supxlabel("Trading days since decision", fontsize=10)
    fig.supylabel("Mean return (%) with 95% CI", fontsize=10)
    fig.tight_layout()
    fig.subplots_adjust(top=0.93)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def cum_pnl_calendar(
    series_by_group: Dict[str, dict],
    title: str,
    out_path: Path,
    palette: Optional[Dict[str, str]] = None,
) -> Path:
    """Cumulative dollar P&L over actual calendar dates, one line per group.

    Series shape (per group): {dates: [...], cumulative_pnl: [...], n_signals: int}.
    """
    palette = palette or {}
    fig, ax = plt.subplots(figsize=(11, 5))

    if not series_by_group:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return _save(fig, out_path)

    plotted = False
    for grp, data in series_by_group.items():
        if not data or not data.get("cumulative_pnl"):
            continue
        dates = pd.to_datetime(data["dates"])
        pnl = np.asarray(data["cumulative_pnl"], dtype=float)
        color = palette.get(grp, "#cbd5e1")
        ax.plot(dates, pnl, color=color, linewidth=2.2,
                label=f"{grp} (n={data.get('n_signals', 0)})")
        plotted = True

    if not plotted:
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return _save(fig, out_path)

    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_xlabel("Calendar date")
    ax.set_ylabel("Cumulative mark-to-market P&L\n($1 per signal)")
    ax.set_title(title)
    ax.grid(True, linestyle=":", alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    fig.autofmt_xdate()
    return _save(fig, out_path)

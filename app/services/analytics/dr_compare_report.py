"""Markdown report writer for Claude vs Gemini deep-research shadow comparisons.

Produces a human-readable report at:
    data/claude_shadow/_report_<stamp>.md

Report sections
---------------
1. Header warning — shadow comparisons are ANCHORED; level diffs unreliable.
2. Verdict & action agreement summary (κ, confusion matrices).
3. Level-disagreement table — one row per decision with the key level-diff
   columns and incoherence flags.

Usage::

    from app.services.analytics.dr_compare_report import write_shadow_report

    write_shadow_report(
        out_dir="data/claude_shadow",
        stamp="20260529_143000",
        summary_rows=summary,          # list of per-decision dicts
        level_comparisons=level_comps, # list of level_comparison dicts (or None)
        verdict_metrics=v_metrics,
        action_metrics=a_metrics,
    )
"""
from __future__ import annotations

import os
from typing import Optional


# ─── _fmt_confusion ───────────────────────────────────────────────────────────

def _fmt_confusion(cm: dict) -> str:
    """Render a confusion-matrix dict as a markdown code block."""
    labels = cm.get("labels", [])
    matrix = cm.get("matrix", {})
    if not labels:
        return "_No data._\n"

    col_w = max((len(lbl) for lbl in labels), default=4) + 2
    header = " " * (col_w + 4) + "  ".join(f"{lbl:>{col_w}}" for lbl in labels)
    lines = [header]
    for g in labels:
        row_cells = "  ".join(
            f"{matrix.get(g, {}).get(c, 0):>{col_w}}" for c in labels
        )
        lines.append(f"  gem={g:<{col_w}} {row_cells}")
    return "```\n" + "\n".join(lines) + "\n```\n"


# ─── _fmt_optional_float ──────────────────────────────────────────────────────

def _fmt_optional_float(v, decimals: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


# ─── write_shadow_report ──────────────────────────────────────────────────────

def write_shadow_report(
    out_dir: str,
    stamp: str,
    summary_rows: list[dict],
    level_comparisons: list[Optional[dict]],
    verdict_metrics: dict,
    action_metrics: dict,
) -> str:
    """Write the markdown shadow report and return the path written."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"_report_{stamp}.md")

    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines += [
        f"# Claude vs Gemini DR Shadow Report — {stamp}",
        "",
        "> **WARNING — ANCHORED COMPARISONS**",
        "> ",
        "> All level comparisons in this report come from a **shadow run**: Claude",
        "> received Gemini's already-refined levels as part of its input context.",
        "> Any level delta between the two models is **contaminated** (Claude is",
        "> anchored to Gemini's numbers) and **must not** be treated as an",
        "> independent signal of genuine disagreement.",
        "> ",
        "> Clean level comparisons require **live dual-runs** where Claude is",
        "> un-anchored (PM pre-DR baseline captured, Gemini levels withheld).",
        "",
        "---",
        "",
    ]

    # ── Agreement summary ─────────────────────────────────────────────────────
    n = len(summary_rows)
    total_cost = sum(r.get("cost_usd_est", 0.0) for r in summary_rows)
    avg_latency = (
        sum(r.get("latency_s", 0.0) for r in summary_rows) / max(n, 1)
    )

    lines += [
        "## Run summary",
        "",
        f"| Decisions | Est. cost | Avg latency |",
        f"|-----------|-----------|-------------|",
        f"| {n} | ${total_cost:.2f} | {avg_latency:.1f}s |",
        "",
    ]

    # ── Verdict agreement ─────────────────────────────────────────────────────
    vm = verdict_metrics
    lines += [
        "## Verdict agreement",
        "",
        f"- n = {vm.get('n', 0)}  (sentinels excluded: {vm.get('n_excluded', 0)})",
        f"- Raw agreement: {vm.get('raw_agreement', 0.0):.3f}",
        f"- Cohen's κ: {vm.get('kappa', 0.0):.3f}",
        "",
        "Confusion matrix (rows = Gemini, cols = Claude):",
        "",
        _fmt_confusion(vm.get("confusion", {})),
    ]

    # ── Action agreement ──────────────────────────────────────────────────────
    am = action_metrics
    lines += [
        "## Action agreement",
        "",
        f"- n = {am.get('n', 0)}  (excluded: {am.get('n_excluded', 0)})",
        f"- Raw agreement: {am.get('raw_agreement', 0.0):.3f}",
        f"- Cohen's κ: {am.get('kappa', 0.0):.3f}",
        "",
        "Confusion matrix (rows = Gemini, cols = Claude):",
        "",
        _fmt_confusion(am.get("confusion", {})),
    ]

    # ── Level-disagreement table ───────────────────────────────────────────────
    lines += [
        "## Level disagreements",
        "",
        "> Reminder: all `anchored=True` — shadow comparisons only.",
        "> 'Material' = entry midpoint Δ > 3%  OR  stop Δ > 5%  OR  disjoint entry bands.",
        "",
        "| Symbol | Material? | Entry overlap | Entry mid Δ% | Stop Δ% | R:R gem | R:R claude | Incoherence (gem) | Incoherence (claude) |",
        "|--------|-----------|---------------|--------------|---------|---------|------------|-------------------|----------------------|",
    ]

    for row, lc in zip(summary_rows, level_comparisons):
        symbol = row.get("symbol", "?")
        if lc is None:
            lines.append(
                f"| {symbol} | (no level data) | — | — | — | — | — | — | — |"
            )
            continue

        material = "YES" if lc.get("material") else "no"
        entry = lc.get("entry", {})
        overlap = _fmt_optional_float(entry.get("overlap_fraction"), 2)
        mid_delta = _fmt_optional_float(entry.get("midpoint_pct_delta"), 1)
        stop_pd = _fmt_optional_float(
            (lc.get("stop_loss") or {}).get("pct_delta"), 1
        )
        rr = lc.get("risk_reward_ratio", {})
        rr_gem = _fmt_optional_float(rr.get("gem"), 2)
        rr_cl = _fmt_optional_float(rr.get("claude"), 2)

        inco = lc.get("incoherence", {})
        gem_flags = "; ".join(inco.get("gem", [])) or "—"
        cl_flags = "; ".join(inco.get("claude", [])) or "—"
        # truncate long flag strings for table readability
        if len(gem_flags) > 60:
            gem_flags = gem_flags[:57] + "..."
        if len(cl_flags) > 60:
            cl_flags = cl_flags[:57] + "..."

        lines.append(
            f"| {symbol} | {material} | {overlap} | {mid_delta} | {stop_pd} "
            f"| {rr_gem} | {rr_cl} | {gem_flags} | {cl_flags} |"
        )

    lines += ["", "---", "", "_Generated by `dr_compare_report.py`._", ""]

    with open(path, "w") as f:
        f.write("\n".join(lines))

    return path

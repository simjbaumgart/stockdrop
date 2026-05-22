"""Generate the News Agent shadow-comparison report.

Reads the news_shadow_runs table and produces a side-by-side markdown report
for the production (Gemini 3.5 Flash) vs shadow (Gemini 3 Flash) models.

Usage:
    python -m scripts.analysis.news_shadow_report [--no-judge]
"""
import argparse
import datetime
import os
import sys
from typing import Any, Dict, List, Optional

# Allow running as a standalone script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app import database  # noqa: E402

# $ per 1,000,000 tokens. PLACEHOLDER RATES — confirm against current Gemini
# pricing before trusting the dollar figures. The two models currently carry
# identical rates, so the production-vs-shadow cost delta reflects token-count
# differences only until real per-model rates are filled in.
PRICING: Dict[str, Dict[str, float]] = {
    "gemini-3.5-flash-preview": {"in": 0.30, "out": 2.50},
    "gemini-3-flash-preview": {"in": 0.30, "out": 2.50},
}

OUTPUT_DIR = "audit_reports"


def _cost(model: str, tokens_in: int, tokens_out: int) -> float:
    rate = PRICING.get(model, {"in": 0.0, "out": 0.0})
    return (tokens_in / 1_000_000) * rate["in"] + (tokens_out / 1_000_000) * rate["out"]


def _as_bool(v: Any) -> Optional[bool]:
    if v is None:
        return None
    return bool(v)


def compute_deterministic_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Economics-flag agreement, cost, and latency aggregates."""
    pairs = [r for r in rows if r.get("shadow_report") and not r.get("shadow_error")]
    n = len(pairs)

    economics_agree = economics_disagree = 0
    prod_cost_total = shadow_cost_total = 0.0
    prod_latency_total = shadow_latency_total = 0

    for r in pairs:
        if _as_bool(r["production_needs_economics"]) == _as_bool(r["shadow_needs_economics"]):
            economics_agree += 1
        else:
            economics_disagree += 1
        prod_cost_total += _cost(r["production_model"],
                                 r["production_tokens_in"] or 0,
                                 r["production_tokens_out"] or 0)
        shadow_cost_total += _cost(r["shadow_model"],
                                   r["shadow_tokens_in"] or 0,
                                   r["shadow_tokens_out"] or 0)
        prod_latency_total += r["production_latency_ms"] or 0
        shadow_latency_total += r["shadow_latency_ms"] or 0

    divisor = n or 1
    return {
        "completed_pairs": n,
        "economics_flag_agree": economics_agree,
        "economics_flag_disagree": economics_disagree,
        "production_cost_per_dp": prod_cost_total / divisor,
        "shadow_cost_per_dp": shadow_cost_total / divisor,
        "production_avg_latency_ms": prod_latency_total / divisor,
        "shadow_avg_latency_ms": shadow_latency_total / divisor,
        "shadow_total_cost": shadow_cost_total,
    }


def render_report(rows: List[Dict[str, Any]],
                  judge_results: Optional[Dict[int, Dict[str, Any]]]) -> str:
    """Render the full markdown report. judge_results keyed by row id."""
    stats = compute_deterministic_stats(rows)
    errored = [r for r in rows if r.get("shadow_error")]
    n = stats["completed_pairs"]

    lines: List[str] = []
    lines.append("# News Agent Shadow Comparison Report")
    lines.append("")
    lines.append(f"Generated: {datetime.datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("- Production model: `gemini-3.5-flash-preview`")
    lines.append("- Shadow model: `gemini-3-flash-preview`")
    lines.append(f"- Completed paired runs: **{n}**")
    if errored:
        lines.append(f"- Shadow runs that errored (excluded from pairs): {len(errored)}")
    lines.append("")

    lines.append("## Summary metrics")
    lines.append("")
    lines.append("| Metric | Production (3.5 Flash) | Shadow (3 Flash) |")
    lines.append("|---|---|---|")
    lines.append(f"| Cost per decision point | ${stats['production_cost_per_dp']:.5f} "
                 f"| ${stats['shadow_cost_per_dp']:.5f} |")
    lines.append(f"| Avg latency (ms) | {stats['production_avg_latency_ms']:.0f} "
                 f"| {stats['shadow_avg_latency_ms']:.0f} |")
    lines.append("")
    lines.append(f"**Economics trigger flag agreement:** "
                 f"{stats['economics_flag_agree']}/{n} agree, "
                 f"{stats['economics_flag_disagree']}/{n} disagree.")
    lines.append("")
    lines.append(f"**Cost note:** the shadow validation cost a one-time total of "
                 f"${stats['shadow_total_cost']:.4f} across {n} runs. Ongoing "
                 f"production cost is ${stats['production_cost_per_dp']:.5f} per "
                 f"decision point. Pricing constants in this script must be "
                 f"confirmed against current Gemini pricing.")
    lines.append("")

    if judge_results:
        lines.append("## LLM-judged accuracy dimensions")
        lines.append("")
        _render_judge_summary(lines, rows, judge_results)
        lines.append("")

    lines.append("## Per-pair detail")
    lines.append("")
    for r in rows:
        lines.append(f"### Pair {r['id']} - {r['symbol']} ({r['decision_date']})")
        lines.append("")
        if r.get("shadow_error"):
            lines.append(f"_Shadow errored: {r['shadow_error']}_")
            lines.append("")
            continue
        lines.append(f"- Economics flag - production: "
                     f"`{_as_bool(r['production_needs_economics'])}`, "
                     f"shadow: `{_as_bool(r['shadow_needs_economics'])}`")
        if judge_results and r["id"] in judge_results:
            j = judge_results[r["id"]]
            lines.append(f"- Source classification: {j.get('source_classification', 'n/a')}")
            lines.append(f"- Hard-event detection: {j.get('hard_event_detection', 'n/a')}")
            lines.append(f"- Narrative coherence - production: "
                         f"{j.get('production_coherence', 'n/a')}, "
                         f"shadow: {j.get('shadow_coherence', 'n/a')}")
            if j.get("disagreements"):
                lines.append(f"- **Flagged for manual review:** {j['disagreements']}")
        lines.append("")
        lines.append("<details><summary>Production report (3.5 Flash)</summary>")
        lines.append("")
        lines.append("```")
        lines.append((r.get("production_report") or "").strip())
        lines.append("```")
        lines.append("</details>")
        lines.append("")
        lines.append("<details><summary>Shadow report (3 Flash)</summary>")
        lines.append("")
        lines.append("```")
        lines.append((r.get("shadow_report") or "").strip())
        lines.append("```")
        lines.append("</details>")
        lines.append("")

    lines.append("## Outcome")
    lines.append("")
    lines.append("Pick one based on the data above:")
    lines.append("")
    lines.append("- [ ] **Confirm** - 3.5 Flash stays in production, shadow disabled permanently.")
    lines.append("- [ ] **Roll back** - revert the News Agent to `gemini-3-flash-preview`.")
    lines.append("- [ ] **Conditional** - keep 3.5 Flash but flag scenario types where 3 Flash won.")
    lines.append("")
    return "\n".join(lines)


def _render_judge_summary(lines: List[str], rows: List[Dict[str, Any]],
                          judge_results: Dict[int, Dict[str, Any]]) -> None:
    """Aggregate the judge output."""
    judged = [judge_results[r["id"]] for r in rows if r["id"] in judge_results]
    src_better_prod = sum(1 for j in judged if j.get("source_classification") == "production_better")
    src_better_shadow = sum(1 for j in judged if j.get("source_classification") == "shadow_better")
    he_better_prod = sum(1 for j in judged if j.get("hard_event_detection") == "production_better")
    he_better_shadow = sum(1 for j in judged if j.get("hard_event_detection") == "shadow_better")
    lines.append(f"- Source classification: production better in {src_better_prod} pairs, "
                 f"shadow better in {src_better_shadow} pairs.")
    lines.append(f"- Hard-event detection: production better in {he_better_prod} pairs, "
                 f"shadow better in {he_better_shadow} pairs.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate News Agent shadow comparison report")
    parser.add_argument("--no-judge", action="store_true",
                        help="Skip the LLM-judged accuracy dimensions")
    args = parser.parse_args()

    # Idempotent — ensures the news_shadow_runs table exists on a fresh DB.
    database.init_db()

    rows = database.get_news_shadow_runs()
    if not rows:
        print("No news_shadow_runs found. Nothing to report.")
        return

    judge_results = None
    if not args.no_judge:
        try:
            from scripts.analysis.news_shadow_judge import judge_all_pairs
            judge_results = judge_all_pairs(rows)
        except Exception as e:
            print(f"LLM judge step failed, continuing without it: {e}")

    md = render_report(rows, judge_results)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(
        OUTPUT_DIR,
        f"news_shadow_comparison_{datetime.date.today().isoformat()}.md",
    )
    with open(out_path, "w") as f:
        f.write(md)
    print(f"Report written to {out_path}")


if __name__ == "__main__":
    main()

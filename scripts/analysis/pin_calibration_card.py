"""Pin the calibration-card candidate for prompt injection (Tier 2).

The nightly builder writes ``data/calibration_card_candidate.json`` (console
only). This script is the ONLY writer of ``data/calibration_card_pinned.json``
— the file prompts read via app/services/calibration_service.py. Pinning is a
deliberate manual act after a monthly audit, never automated: with ~30
actionable decisions/month, an auto-updated card amplifies noise into the very
prompts generating the next month's data (THREE_TIER_FEEDBACK_PROPOSAL.md).

Per-verdict sections are stripped at pin time: telling the PM how its own
verdicts performed is the mistake-narrative shape that causes mode collapse.

Usage:
    python -m scripts.analysis.pin_calibration_card                # dry-run
    python -m scripts.analysis.pin_calibration_card --approve \
        [--audited-at 2026-06-30]
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from typing import List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from scripts.analysis.build_calibration_card import JSON_PATH as CANDIDATE_JSON  # noqa: E402

PINNED_JSON = os.path.join(os.path.dirname(CANDIDATE_JSON), "calibration_card_pinned.json")

# Sections agents may never see (regime-dependent / self-referential — Tier 3).
DROPPED_SECTIONS = ("by_pm_verdict", "by_dr_verdict", "by_gatekeeper_tier")
MIN_MIN_N = 20
MIN_TOTAL_LABELED = 100


def validate_candidate(card: dict) -> List[str]:
    """Content rules from THREE_TIER_FEEDBACK_PROPOSAL.md. Empty list = valid."""
    errors: List[str] = []
    if not card.get("as_of"):
        errors.append("missing as_of stamp")
    if (card.get("min_n") or 0) < MIN_MIN_N:
        errors.append(f"min_n {card.get('min_n')} below floor {MIN_MIN_N}")
    if (card.get("total_labeled") or 0) < MIN_TOTAL_LABELED:
        errors.append(f"total_labeled {card.get('total_labeled')} below floor {MIN_TOTAL_LABELED}")
    if not (card.get("by_earnings") or {}):
        errors.append("no by_earnings bucket met min_n — nothing worth pinning")
    for section in ("by_drop_type", "by_earnings"):
        for key, stats in (card.get(section) or {}).items():
            if (stats.get("n") or 0) < MIN_MIN_N:
                errors.append(f"{section}/{key} has n={stats.get('n')} < {MIN_MIN_N}")
    return errors


def build_pinned(card: dict, audited_at: str, git_ref: str) -> dict:
    pinned = {k: v for k, v in card.items() if k not in DROPPED_SECTIONS}
    pinned["audited_at"] = audited_at
    pinned["pinned_git_ref"] = git_ref
    return pinned


def _git_ref() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def run(approve: bool, audited_at: Optional[str] = None) -> int:
    try:
        with open(CANDIDATE_JSON) as f:
            card = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[pin_calibration_card] cannot read candidate {CANDIDATE_JSON}: {e}")
        return 1

    errors = validate_candidate(card)
    if errors:
        print("[pin_calibration_card] candidate REJECTED:")
        for e in errors:
            print(f"  - {e}")
        return 1

    pinned = build_pinned(
        card,
        audited_at=audited_at or datetime.date.today().isoformat(),
        git_ref=_git_ref(),
    )
    print(f"[pin_calibration_card] candidate as_of={card['as_of']} "
          f"({card['total_labeled']} labeled) validates clean.")
    if not approve:
        print("Dry-run only. Re-run with --approve to write "
              f"{PINNED_JSON} (this changes live prompt content when "
              "CALIBRATION_ENABLED=1).")
        return 0

    with open(PINNED_JSON, "w") as f:
        json.dump(pinned, f, indent=2)
    print(f"[pin_calibration_card] pinned -> {PINNED_JSON} "
          f"(audited_at={pinned['audited_at']}, ref={pinned['pinned_git_ref']})")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Pin the calibration card for prompt injection")
    ap.add_argument("--approve", action="store_true",
                    help="Actually write the pinned card (default: dry-run)")
    ap.add_argument("--audited-at", default=None, metavar="YYYY-MM-DD",
                    help="Audit date stamp (default: today)")
    args = ap.parse_args()
    sys.exit(run(approve=args.approve, audited_at=args.audited_at))

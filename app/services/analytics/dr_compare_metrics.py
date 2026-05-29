"""Deep-research comparison metrics for the Claude shadow evaluation.

Pure functions, no external dependencies (scipy/sklearn not required).
Cohen's κ is implemented directly from first principles.

Sentinel verdicts — Gemini-only failure states that Claude can never emit:
  ERROR_PARSING             → Gemini failed to parse its own output.
  INCOMPLETE_TRADING_LEVELS → Gemini flagged missing price levels.
Rows carrying these gemini verdicts are excluded from agreement metrics so they
cannot artificially suppress agreement scores.

Edge-case contracts:
  cohens_kappa([])   → 0.0   (empty input; documented)
  cohens_kappa(...)  → 0.0   when pe == 1 (degenerate single-category; avoids
                              0/0 division because 1 − pe == 0)
"""
from __future__ import annotations

from typing import List, Tuple, Dict


# ─── Public constants ────────────────────────────────────────────────────────

SENTINEL_VERDICTS: frozenset = frozenset({
    "ERROR_PARSING",
    "INCOMPLETE_TRADING_LEVELS",
})


# ─── confusion_matrix ─────────────────────────────────────────────────────────

def confusion_matrix(pairs: List[Tuple[str, str]]) -> Dict:
    """Build a confusion matrix from (gemini_label, claude_label) pairs.

    Returns a dict with:
        labels  — sorted list of all unique labels seen in either column
        matrix  — nested dict  matrix[gemini_label][claude_label] = count
                  All (labels × labels) cells are present (zeros filled in).
    """
    if not pairs:
        return {"labels": [], "matrix": {}}

    labels_set: set = set()
    for gem, cl in pairs:
        labels_set.add(gem)
        labels_set.add(cl)
    labels = sorted(labels_set)

    # Initialise all cells to 0
    matrix: Dict[str, Dict[str, int]] = {
        g: {c: 0 for c in labels} for g in labels
    }
    for gem, cl in pairs:
        matrix[gem][cl] += 1

    return {"labels": labels, "matrix": matrix}


# ─── cohens_kappa ─────────────────────────────────────────────────────────────

def cohens_kappa(pairs: List[Tuple[str, str]]) -> float:
    """Compute Cohen's κ for a list of (gemini_label, claude_label) pairs.

    κ = (po − pe) / (1 − pe)

    where:
        po = fraction of pairs that agree (observed agreement)
        pe = Σ_k  p_gem(k) · p_claude(k)   (expected agreement by chance)

    Special cases:
        empty input  → 0.0  (nothing to measure)
        pe == 1      → 0.0  (all labels identical on both sides; 1 − pe == 0
                             would be a division by zero; return 0.0 with the
                             understanding that perfect chance-agreement leaves
                             no room for above-chance agreement to measure)
    """
    n = len(pairs)
    if n == 0:
        return 0.0

    # Observed agreement (po)
    po = sum(1 for g, c in pairs if g == c) / n

    # Marginal frequencies
    from collections import Counter
    gem_counts: Counter = Counter()
    cl_counts: Counter = Counter()
    for g, c in pairs:
        gem_counts[g] += 1
        cl_counts[c] += 1

    all_labels = set(gem_counts) | set(cl_counts)

    # Expected agreement (pe)
    pe = sum(
        (gem_counts.get(k, 0) / n) * (cl_counts.get(k, 0) / n)
        for k in all_labels
    )

    denominator = 1.0 - pe
    if denominator == 0.0:
        # Degenerate case: pe == 1 → every prediction is the same label on both
        # sides. κ is undefined (0/0); return 0.0 by convention.
        return 0.0

    return (po - pe) / denominator


# ─── Shared helper ────────────────────────────────────────────────────────────

def _compute_agreement(
    pairs: List[Tuple[str, str]],
    n_excluded: int,
) -> Dict:
    """Internal helper: compute metrics dict from filtered pairs."""
    n = len(pairs)
    if n == 0:
        return {
            "n": 0,
            "n_excluded": n_excluded,
            "raw_agreement": 0.0,
            "kappa": 0.0,
            "confusion": confusion_matrix([]),
        }
    raw_agreement = sum(1 for g, c in pairs if g == c) / n
    return {
        "n": n,
        "n_excluded": n_excluded,
        "raw_agreement": raw_agreement,
        "kappa": cohens_kappa(pairs),
        "confusion": confusion_matrix(pairs),
    }


# ─── verdict_agreement ────────────────────────────────────────────────────────

def verdict_agreement(rows: List[Dict]) -> Dict:
    """Compute verdict-level agreement metrics, excluding sentinel Gemini verdicts.

    Args:
        rows: list of dicts each with keys ``gemini_verdict`` and ``claude_verdict``.

    Returns:
        dict with keys:
            n             — number of non-excluded pairs used
            n_excluded    — rows whose gemini_verdict was in SENTINEL_VERDICTS
            raw_agreement — fraction of remaining pairs that agree (0.0 when n==0)
            kappa         — Cohen's κ on the remaining pairs
            confusion     — confusion_matrix result on the remaining pairs
    """
    n_excluded = 0
    pairs: List[Tuple[str, str]] = []
    for row in rows:
        gem = row.get("gemini_verdict") or ""
        cl = row.get("claude_verdict") or ""
        if gem in SENTINEL_VERDICTS:
            n_excluded += 1
            continue
        pairs.append((gem, cl))
    return _compute_agreement(pairs, n_excluded)


# ─── action_agreement ─────────────────────────────────────────────────────────

def action_agreement(rows: List[Dict]) -> Dict:
    """Compute action-level agreement metrics.

    No sentinel exclusion is applied to actions — the action enums are shared
    between Gemini and Claude (BUY, BUY_LIMIT, WATCH, AVOID).
    n_excluded is always 0; it is present only for API symmetry with
    verdict_agreement.

    Args:
        rows: list of dicts each with keys ``gemini_action`` and ``claude_action``.

    Returns:
        dict with keys: n, n_excluded (always 0), raw_agreement, kappa, confusion.
    """
    pairs: List[Tuple[str, str]] = [
        (row.get("gemini_action") or "", row.get("claude_action") or "")
        for row in rows
    ]
    return _compute_agreement(pairs, n_excluded=0)

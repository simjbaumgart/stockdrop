"""SA Quant ranked grades — informational ratings appended to the PM verdict.

CRITICAL: ratings produced by this service must NEVER be passed into any LLM
agent prompt (Phase 1 sensors, Phase 2 debate, PM, deep research, Sell Council).
They are looked up *after* `final_decision` is finalized and stored in dedicated
`decision_points` columns. Correlation analysis (scripts/analysis/sa_ranking_correlation.py)
found no useful predictive signal against our cohort returns, so feeding them
into prompts would only contaminate the model.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV_PATH = REPO_ROOT / "data" / "SAgrades" / "SA_Quant_Ranked_Clean.csv"

_RATING_TAIL_RE = re.compile(r"([0-9]+\.?[0-9]*)\s*$")


def parse_rating(raw) -> Optional[float]:
    """Extract numeric tail from strings like 'Rating: Strong Buy4.99'.

    Returns None for empty / NaN / unparseable input. Matches the helper from
    scripts/analysis/sa_ranking_correlation.py — single source of truth.
    """
    if raw is None:
        return None
    try:
        # Catch float NaN without importing pandas at module top-level.
        if isinstance(raw, float) and raw != raw:  # noqa: PLR0124
            return None
    except Exception:
        pass
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    m = _RATING_TAIL_RE.search(s)
    return float(m.group(1)) if m else None


_NULL_RESULT_AVAILABLE = {
    "sa_quant_rating": None,
    "sa_authors_rating": None,
    "wall_street_rating": None,
    "sa_rank": None,
    "total_ranked": None,
    "available": True,
}

_NULL_RESULT_UNAVAILABLE = {**_NULL_RESULT_AVAILABLE, "available": False, "total_ranked": None}


class SAGradesService:
    def __init__(self, csv_path: Optional[str] = None):
        self._explicit_path = csv_path
        self._loaded = False
        self._available = False
        self._rows: Dict[str, dict] = {}
        self._total_ranked: Optional[int] = None
        self._warned = False

    def _resolve_path(self) -> Path:
        if self._explicit_path:
            return Path(self._explicit_path)
        env = os.environ.get("SA_GRADES_CSV_PATH")
        if env:
            return Path(env)
        return DEFAULT_CSV_PATH

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self._resolve_path()
        if not path.exists():
            if not self._warned:
                logger.warning("[SA Grades] CSV not found at %s — ratings will be unavailable.", path)
                self._warned = True
            return
        try:
            import pandas as pd
            df = pd.read_csv(path)
        except Exception as e:
            if not self._warned:
                logger.warning("[SA Grades] Failed to read %s: %s", path, e)
                self._warned = True
            return

        rows: Dict[str, dict] = {}
        for _, r in df.iterrows():
            sym = str(r.get("Symbol", "")).strip().upper()
            if not sym:
                continue
            rank_raw = r.get("Rank")
            try:
                rank_val = int(rank_raw) if rank_raw is not None and str(rank_raw).strip() != "" else None
            except (ValueError, TypeError):
                rank_val = None
            rows[sym] = {
                "sa_quant_rating": parse_rating(r.get("Quant Rating")),
                "sa_authors_rating": parse_rating(r.get("SA Analyst Ratings")),
                "wall_street_rating": parse_rating(r.get("Wall Street Ratings")),
                "sa_rank": rank_val,
            }
        self._rows = rows
        self._total_ranked = len(rows)
        self._available = True

    def lookup(self, ticker: str) -> dict:
        self._ensure_loaded()
        if not self._available:
            return dict(_NULL_RESULT_UNAVAILABLE)
        key = (ticker or "").strip().upper()
        row = self._rows.get(key)
        if row is None:
            return {**_NULL_RESULT_AVAILABLE, "total_ranked": self._total_ranked}
        return {**row, "total_ranked": self._total_ranked, "available": True}


# Singleton — mirrors other service modules in app/services/.
sa_grades_service = SAGradesService()

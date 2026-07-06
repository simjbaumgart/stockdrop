"""Shared feature construction for the drop-type classifier (Phases 2–3).

Leakage rule: only columns already in the Phase-0 parquet may be used —
never re-join to the database here. `sector` is deliberately excluded
(NULL for 1,010/1,020 labeled rows). label_batch/label_month are metadata,
never features.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

ENCODER_NAME = "BAAI/bge-small-en-v1.5"
TEXT_COLS_TIER_A = ["news_context", "c1_news"]
TEXT_COLS_TIER_B = ["c1_competitive", "c1_market_sentiment", "c1_technical"]
DAYS_SENTINEL = 999.0


def dataset_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_structured(df: pd.DataFrame) -> Tuple[np.ndarray, List[str]]:
    """Numeric block: missing values -> 0/sentinel plus an explicit flag."""
    out = pd.DataFrame(index=df.index)
    out["drop_percent"] = df["drop_percent"].fillna(0.0)
    out["log_market_cap"] = np.log1p(df["market_cap"].fillna(0.0).clip(lower=0))
    out["pe_missing"] = df["pe_ratio"].isna().astype(float)
    out["pe_ratio"] = df["pe_ratio"].fillna(0.0).clip(-500, 500)
    out["is_earnings_drop"] = df["is_earnings_drop"].fillna(0).astype(float)
    days = df["days_since_earnings"].astype("Float64")
    out["days_missing"] = days.isna().astype(float)
    out["days_since_earnings"] = (
        days.fillna(DAYS_SENTINEL).clip(-DAYS_SENTINEL, DAYS_SENTINEL).astype(float)
    )
    out["has_surprise_pct"] = df["has_surprise_pct"].astype(float)
    out["surprise_pct"] = df["surprise_pct"].fillna(0.0).clip(-200, 200)
    return out.to_numpy(dtype=np.float32), list(out.columns)


def load_or_build_embeddings(
    df: pd.DataFrame, text_cols: List[str], cache_path: Path, dataset_sha: str
) -> Dict[str, np.ndarray]:
    """One (n_rows, 384) matrix per text column; cached in one npz keyed by
    dataset sha so a rebuilt parquet invalidates the cache."""
    cache_path = Path(cache_path)
    if cache_path.exists():
        z = np.load(cache_path)
        if "sha" in z and str(z["sha"][0]) == dataset_sha and all(
            c in z for c in text_cols
        ):
            return {c: z[c] for c in text_cols}
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(ENCODER_NAME, device="cpu")
    model.max_seq_length = 512  # truncate each doc to its first 512 tokens
    embs: Dict[str, np.ndarray] = {}
    for col in text_cols:
        texts = df[col].fillna("").astype(str).tolist()
        print(f"embedding {col} ({len(texts)} docs)...")
        embs[col] = model.encode(
            texts, batch_size=32, normalize_embeddings=True,
            show_progress_bar=True,
        ).astype(np.float32)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache_path, sha=np.array([dataset_sha]), **embs)
    return embs


def assemble(df: pd.DataFrame, tier: str, embs: Dict[str, np.ndarray]) -> np.ndarray:
    """Feature matrix for a tier; rows align with df row order."""
    cols = TEXT_COLS_TIER_A + (TEXT_COLS_TIER_B if tier == "A+B" else [])
    x_struct, _ = build_structured(df)
    return np.hstack([embs[c] for c in cols] + [x_struct])

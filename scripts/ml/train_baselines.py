"""Phase 1 — baselines: majority, earnings-window heuristic, TF-IDF + LogReg.

Evaluated on the fixed time-split test set for both targets (7-class
drop_type, binary gate_trigger). Includes the leakage sanity check: TF-IDF
macro-F1 > 0.95 means inputs probably leak the label — stop loudly.

Usage: ./venv/bin/python scripts/ml/train_baselines.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    f1_score,
    precision_score,
    recall_score,
)

TEXT_COLS = [
    "news_context", "c1_news", "c1_competitive",
    "c1_market_sentiment", "c1_technical",
]
LEAKAGE_MACRO_F1 = 0.95


def metrics_7class(y_true: List[str], y_pred: List[str]) -> Dict:
    labels = sorted(set(y_true) | set(y_pred))
    per = f1_score(y_true, y_pred, average=None, labels=labels, zero_division=0)
    return {
        "macro_f1": round(float(f1_score(
            y_true, y_pred, average="macro", zero_division=0)), 4),
        "per_class_f1": {l: round(float(v), 4) for l, v in zip(labels, per)},
    }


def metrics_binary(y_true, y_pred) -> Dict:
    return {
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
    }


def concat_text(df: pd.DataFrame) -> pd.Series:
    return df[TEXT_COLS].fillna("").agg("\n".join, axis=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="data/ml/drop_type_dataset.parquet")
    parser.add_argument("--out", default="data/ml/results_baselines.json")
    args = parser.parse_args()

    path = Path(args.dataset)
    df = pd.read_parquet(path)
    train, test = df[df["split"] == "train"], df[df["split"] == "test"]
    y7_tr, y7_te = train["drop_type"], test["drop_type"]
    yb_tr, yb_te = train["gate_trigger"], test["gate_trigger"]
    print(f"train {len(train)} / test {len(test)}")

    results: Dict = {
        "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "baselines": {},
    }

    # 1. Majority class (from train).
    maj7, majb = y7_tr.mode()[0], bool(yb_tr.mode()[0])
    results["baselines"]["majority"] = {
        "seven_class": metrics_7class(list(y7_te), [maj7] * len(test)),
        "binary": metrics_binary(yb_te, [majb] * len(test)),
    }

    # 2. Earnings-window heuristic (spec: days_since_earnings <= 2, null -> else).
    days = test["days_since_earnings"]
    is_recent = days.notna() & (days <= 2)
    pred7 = np.where(is_recent, "EARNINGS_MISS", "COMPANY_SPECIFIC")
    results["baselines"]["earnings_window"] = {
        "seven_class": metrics_7class(list(y7_te), list(pred7)),
        "binary": metrics_binary(yb_te, is_recent.to_numpy()),
    }

    # 3. TF-IDF + LogReg over concatenated text.
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=3)
    x_tr = vec.fit_transform(concat_text(train))
    x_te = vec.transform(concat_text(test))
    clf7 = LogisticRegression(class_weight="balanced", max_iter=2000)
    clf7.fit(x_tr, y7_tr)
    pred7_tfidf = clf7.predict(x_te)
    clfb = LogisticRegression(class_weight="balanced", max_iter=2000)
    clfb.fit(x_tr, yb_tr)
    results["baselines"]["tfidf_logreg"] = {
        "seven_class": metrics_7class(list(y7_te), list(pred7_tfidf)),
        "binary": metrics_binary(yb_te, clfb.predict(x_te)),
    }

    # Confusion matrix for the strongest baseline.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 8))
    ConfusionMatrixDisplay.from_predictions(
        y7_te, pred7_tfidf, xticks_rotation=45, ax=ax, colorbar=False
    )
    ax.set_title("TF-IDF + LogReg baseline — 7-class confusion (test)")
    fig.tight_layout()
    fig.savefig("docs/images/drop_type_confusion_baseline.png", dpi=150)

    Path(args.out).write_text(json.dumps(results, indent=2))

    print(f"\n{'baseline':<18} {'7c macro-F1':>12} {'bin F1':>8} {'bin P':>7} {'bin R':>7}")
    for name, r in results["baselines"].items():
        b = r["binary"]
        print(f"{name:<18} {r['seven_class']['macro_f1']:>12.4f} "
              f"{b['f1']:>8.4f} {b['precision']:>7.4f} {b['recall']:>7.4f}")

    tfidf_f1 = results["baselines"]["tfidf_logreg"]["seven_class"]["macro_f1"]
    if tfidf_f1 > LEAKAGE_MACRO_F1:
        print(f"\n{'!' * 70}\nLEAKAGE WARNING: TF-IDF macro-F1 {tfidf_f1} > "
              f"{LEAKAGE_MACRO_F1} — inputs probably leak the label. STOPPING."
              f"\n{'!' * 70}")
        raise SystemExit(1)
    print(f"\nwrote {args.out} + docs/images/drop_type_confusion_baseline.png")


if __name__ == "__main__":
    main()

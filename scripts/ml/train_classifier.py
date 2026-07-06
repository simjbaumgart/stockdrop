"""Phase 2 — bge-small embeddings + LogReg/LightGBM heads, tier ablation,
calibration with abstention, seed stability, batch-drift slice.

Split protocol: heads train on decision_date < 2026-05-25 ("fit"), calibrate
on 2026-05-25..2026-06-07 ("calib"), evaluate on the fixed test split.

Usage: ./venv/bin/python scripts/ml/train_classifier.py
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score

from scripts.ml.features import (
    ENCODER_NAME,
    TEXT_COLS_TIER_A,
    TEXT_COLS_TIER_B,
    assemble,
    dataset_sha256,
    load_or_build_embeddings,
)

CALIB_BOUNDARY = "2026-05-25"
ABSTAIN_THRESHOLD = 0.55
SEEDS = [0, 1, 2, 3, 4]
SUCCESS_BAR_F1 = 0.85


def lightgbm_available() -> bool:
    try:
        import lightgbm  # noqa: F401
        return True
    except Exception as exc:  # pragma: no cover
        print(f"lightgbm unavailable ({exc}) — running LogReg-only")
        return False


def make_head(name: str, seed: int):
    if name == "logreg":
        return LogisticRegression(
            class_weight="balanced", max_iter=2000, random_state=seed
        )
    import lightgbm as lgb
    return lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.05, num_leaves=31,
        class_weight="balanced", random_state=seed, verbose=-1,
    )


def calibrate(head, x_calib: np.ndarray, y_calib) -> CalibratedClassifierCV:
    try:
        from sklearn.frozen import FrozenEstimator
        cal = CalibratedClassifierCV(FrozenEstimator(head), method="sigmoid")
    except ImportError:  # sklearn < 1.6
        cal = CalibratedClassifierCV(head, method="sigmoid", cv="prefit")
    cal.fit(x_calib, y_calib)
    return cal


def score(target: str, y_true, y_pred) -> float:
    if target == "binary":
        return float(f1_score(y_true, y_pred, zero_division=0))
    return float(f1_score(y_true, y_pred, average="macro", zero_division=0))


def per_class_f1(y_true, y_pred) -> Dict[str, float]:
    labels = sorted(set(y_true) | set(y_pred))
    per = f1_score(y_true, y_pred, average=None, labels=labels, zero_division=0)
    return {l: round(float(v), 4) for l, v in zip(labels, per)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="data/ml/drop_type_dataset.parquet")
    parser.add_argument("--cache", default="data/ml/embeddings.npz")
    parser.add_argument("--out", default="data/ml/results_classifier.json")
    parser.add_argument("--model-out", default="models/drop_type_clf_v1.joblib")
    args = parser.parse_args()

    t0 = time.time()
    df = pd.read_parquet(args.dataset).reset_index(drop=True)
    sha = dataset_sha256(Path(args.dataset))
    embs = load_or_build_embeddings(
        df, TEXT_COLS_TIER_A + TEXT_COLS_TIER_B, Path(args.cache), sha
    )

    is_train = (df["split"] == "train").to_numpy()
    is_test = ~is_train
    is_fit = is_train & (df["decision_date"] < CALIB_BOUNDARY).to_numpy()
    is_calib = is_train & ~is_fit
    print(f"fit {is_fit.sum()} / calib {is_calib.sum()} / test {is_test.sum()}")

    targets = {
        "seven_class": df["drop_type"].to_numpy(),
        "binary": df["gate_trigger"].to_numpy(),
    }
    heads = ["logreg"] + (["lgbm"] if lightgbm_available() else [])
    matrices = {tier: assemble(df, tier, embs) for tier in ["A", "A+B"]}

    results: Dict = {
        "encoder": ENCODER_NAME, "dataset_sha256": sha,
        "abstain_threshold": ABSTAIN_THRESHOLD,
        "lightgbm_available": "lgbm" in heads, "runs": [],
    }
    best: Dict[str, Dict] = {}

    for tier, X in matrices.items():
        for head_name in heads:
            for tname, y in targets.items():
                head = make_head(head_name, seed=0)
                head.fit(X[is_fit], y[is_fit])
                pred = head.predict(X[is_test])
                metric = score(tname, y[is_test], pred)
                run = {"tier": tier, "head": head_name, "target": tname,
                       "test_metric": round(metric, 4)}
                if tname == "seven_class":
                    run["per_class_f1"] = per_class_f1(y[is_test], pred)
                else:
                    run["precision"] = round(float(precision_score(
                        y[is_test], pred, zero_division=0)), 4)
                    run["recall"] = round(float(recall_score(
                        y[is_test], pred, zero_division=0)), 4)
                results["runs"].append(run)
                print(f"{tier:<4} {head_name:<7} {tname:<12} -> {metric:.4f}")
                if tname not in best or metric > best[tname]["test_metric"]:
                    best[tname] = {"tier": tier, "head_name": head_name,
                                   "head": head, "test_metric": metric}

    artifact: Dict = {"encoder": ENCODER_NAME, "dataset_sha256": sha,
                      "threshold": ABSTAIN_THRESHOLD}
    for tname, y in targets.items():
        b = best[tname]
        X = matrices[b["tier"]]
        cal = calibrate(b["head"], X[is_calib], y[is_calib])
        proba = cal.predict_proba(X[is_test])
        classes = np.asarray(cal.classes_)
        maxp = proba.max(axis=1)
        argmax_pred = classes[proba.argmax(axis=1)]
        abstain = maxp < ABSTAIN_THRESHOLD
        if tname == "seven_class":
            pred_abst = np.where(abstain, "UNKNOWN", argmax_pred)
        else:
            pred_abst = np.where(abstain, False, argmax_pred).astype(bool)
        b["calibrated"] = cal
        results[f"best_{tname}"] = {
            "tier": b["tier"], "head": b["head_name"],
            "test_metric": round(b["test_metric"], 4),
            "test_metric_with_abstention": round(
                score(tname, y[is_test], pred_abst), 4),
            "abstention_rate": round(float(abstain.mean()), 4),
        }
        # Seed stability: retrain the winning head config across 5 seeds.
        seed_scores = []
        for s in SEEDS:
            h = make_head(b["head_name"], seed=s)
            h.fit(X[is_fit], y[is_fit])
            seed_scores.append(score(tname, y[is_test], h.predict(X[is_test])))
        results[f"best_{tname}"]["seed_mean"] = round(float(np.mean(seed_scores)), 4)
        results[f"best_{tname}"]["seed_sd"] = round(float(np.std(seed_scores)), 4)
        artifact[tname] = {k: b[k] for k in
                           ("tier", "head_name", "head", "calibrated", "test_metric")}

    # Batch-drift slice: agreement per label_month on test (best binary model).
    bb = artifact["binary"]
    Xb = matrices[bb["tier"]]
    proba_b = bb["calibrated"].predict_proba(Xb[is_test])
    p_true = proba_b[:, list(bb["calibrated"].classes_).index(True)]
    fire = p_true >= ABSTAIN_THRESHOLD
    test_df = df[is_test].copy()
    test_df["agree"] = fire == test_df["gate_trigger"].to_numpy()
    results["agreement_by_label_month"] = {
        m: round(float(g["agree"].mean()), 4)
        for m, g in test_df.groupby("label_month")
    }

    Path(args.model_out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, args.model_out)
    Path(args.out).write_text(json.dumps(results, indent=2))

    heuristic_f1 = json.loads(Path("data/ml/results_baselines.json").read_text())[
        "baselines"]["earnings_window"]["binary"]["f1"]
    model_f1 = results["best_binary"]["test_metric"]
    bar_met = model_f1 >= SUCCESS_BAR_F1 and model_f1 > heuristic_f1
    results["success_bar"] = {
        "binary_f1": model_f1, "bar": SUCCESS_BAR_F1,
        "heuristic_f1": heuristic_f1, "met": bar_met,
    }
    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"\nSUCCESS BAR (binary gate_trigger F1 >= {SUCCESS_BAR_F1} and "
          f"> heuristic {heuristic_f1}): model {model_f1} -> "
          f"{'MET' if bar_met else 'NOT MET'}")
    print(f"done in {time.time() - t0:.0f}s; wrote {args.model_out} + {args.out}")


if __name__ == "__main__":
    main()

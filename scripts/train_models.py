"""Train Isolation Forest + Autoencoder on the preprocessed features.

Expected inputs (produced by scripts/run_data_preprocessing.py):
    data/processed/features_train_resampled.parquet  (for IsolationForest)
    data/processed/features_train.parquet            (for AE: normals only)
    data/processed/features_test.parquet             (for evaluation)
    data/processed/preprocessor.joblib

Outputs:
    models/isolation_forest.joblib
    models/autoencoder.joblib
    models/ensemble.joblib
    models/metrics.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    precision_recall_fscore_support,
    roc_auc_score,
)

from sklearn.model_selection import train_test_split  # noqa: E402

from src.models import AutoencoderScorer, Ensemble, IsolationForestScorer  # noqa: E402
from src.models.ensemble import combine_scores  # noqa: E402


def _load_split(path: Path) -> tuple[np.ndarray, np.ndarray, list[str]]:
    df = pd.read_parquet(path)
    meta_cols = {c for c in ("claim_id", "is_fraud") if c in df.columns}
    feature_cols = [c for c in df.columns if c not in meta_cols]
    X = df[feature_cols].to_numpy()
    y = df["is_fraud"].to_numpy() if "is_fraud" in df.columns else np.zeros(len(df))
    return X, y, feature_cols


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", default="data/processed")
    parser.add_argument("--models-dir", default="models")
    parser.add_argument("--contamination", type=float, default=0.1,
                        help="Expected fraud rate; drives thresholds")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    processed = Path(args.processed_dir)
    out = Path(args.models_dir)
    out.mkdir(parents=True, exist_ok=True)

    print("Loading splits...")
    X_train_res, y_train_res, feat_cols = _load_split(processed / "features_train_resampled.parquet")
    X_train, y_train, _ = _load_split(processed / "features_train.parquet")
    X_test, y_test, _ = _load_split(processed / "features_test.parquet")

    print(f"  train (resampled): {X_train_res.shape}, fraud rate {y_train_res.mean():.2%}")
    print(f"  train (original):  {X_train.shape},     fraud rate {y_train.mean():.2%}")
    print(f"  test:              {X_test.shape},      fraud rate {y_test.mean():.2%}")
    print(f"  features: {len(feat_cols)}")

    # --- Isolation Forest ---------------------------------------------
    print("\n[1/2] Training Isolation Forest on resampled train...")
    iforest = IsolationForestScorer.train(
        X_train_res,
        contamination=args.contamination,
        random_state=args.seed,
    )
    iforest.save(str(out / "isolation_forest.joblib"))

    # --- Autoencoder (normals only, original distribution) ------------
    print("\n[2/2] Training Autoencoder on NORMAL training rows only...")
    X_train_normal = X_train[y_train == 0]
    print(f"  training on {len(X_train_normal)} normal rows")
    autoencoder = AutoencoderScorer.train(
        X_train_normal,
        hidden_sizes=(16, 8, 16),
        max_iter=300,
        contamination=args.contamination,
        random_state=args.seed,
    )
    autoencoder.save(str(out / "autoencoder.joblib"))

    # --- Grid-search weights + thresholds on held-out training data ---
    # Rationale: the fixed 50/50 weights and 0.5/0.75 thresholds are
    # intuition-chosen. We can do better by searching over a small grid
    # on held-out train data (NOT the test set — that would leak and
    # bias the final numbers upwards).
    print("\nGrid-searching weights + thresholds on held-out train...")
    X_tune, X_tune_val, y_tune, y_tune_val = train_test_split(
        X_train, y_train, test_size=0.25, stratify=y_train,
        random_state=args.seed,
    )
    if_scores_val = iforest.anomaly_score(X_tune_val)
    ae_scores_val = autoencoder.anomaly_score(X_tune_val)

    best_f1, best_params = -1.0, None
    for w_if in [0.2, 0.3, 0.35, 0.4, 0.5, 0.6, 0.65, 0.7, 0.8]:
        w_ae = 1.0 - w_if
        combined_val = combine_scores(if_scores_val, ae_scores_val, w_if, w_ae)
        # Sweep suspicious + anomalous thresholds (anom >= susp).
        for susp in np.arange(0.30, 0.71, 0.05):
            for anom in np.arange(susp + 0.05, 0.96, 0.05):
                preds = (combined_val >= susp).astype(int)
                p, r, f1, _ = precision_recall_fscore_support(
                    y_tune_val, preds, average="binary", zero_division=0
                )
                if f1 > best_f1:
                    best_f1 = f1
                    best_params = {
                        "weight_if": round(float(w_if), 3),
                        "weight_ae": round(float(w_ae), 3),
                        "suspicious_threshold": round(float(susp), 3),
                        "anomalous_threshold": round(float(anom), 3),
                        "val_f1": float(f1),
                        "val_precision": float(p),
                        "val_recall": float(r),
                    }
    print(f"  Best on val: {best_params}")

    # --- Assemble final ensemble with tuned params -------------------
    print("\nAssembling ensemble with tuned parameters...")
    ensemble = Ensemble(
        iforest=iforest,
        autoencoder=autoencoder,
        feature_names=feat_cols,
        suspicious_threshold=best_params["suspicious_threshold"],
        anomalous_threshold=best_params["anomalous_threshold"],
        weight_if=best_params["weight_if"],
        weight_ae=best_params["weight_ae"],
    )
    ensemble.save(str(out / "ensemble.joblib"))

    print("\nEvaluating on held-out test set (never seen during training)...")
    scored = ensemble.score(X_test)

    metrics = {}
    for name, pred_key, score_key in [
        ("isolation_forest", "prediction", "isolation_forest_score"),
        ("autoencoder", "prediction", "autoencoder_score"),
        ("ensemble", "prediction", "combined_anomaly_score"),
    ]:
        if name == "isolation_forest":
            preds = iforest.predict(X_test)
            scores = scored["isolation_forest_score"]
        elif name == "autoencoder":
            preds = autoencoder.predict(X_test)
            scores = scored["autoencoder_score"]
        else:
            preds = scored["prediction"]
            scores = scored["combined_anomaly_score"]

        p, r, f1, _ = precision_recall_fscore_support(
            y_test, preds, average="binary", zero_division=0
        )
        try:
            auc = roc_auc_score(y_test, scores)
        except ValueError:
            auc = float("nan")
        tn, fp, fn, tp = confusion_matrix(y_test, preds, labels=[0, 1]).ravel()
        metrics[name] = {
            "precision": float(p),
            "recall": float(r),
            "f1": float(f1),
            "roc_auc": float(auc),
            "true_positives": int(tp),
            "false_positives": int(fp),
            "true_negatives": int(tn),
            "false_negatives": int(fn),
        }
        print(f"\n  {name:18s} P={p:.3f}  R={r:.3f}  F1={f1:.3f}  AUC={auc:.3f}")
        print(f"     TP={tp:3d} FP={fp:3d} TN={tn:3d} FN={fn:3d}")

    metrics["tuned_params"] = best_params
    metrics_path = out / "metrics.json"
    with metrics_path.open("w") as f:
        json.dump(metrics, f, indent=2)
    print(f"\nModels and metrics written to {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

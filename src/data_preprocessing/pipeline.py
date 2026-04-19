"""End-to-end data preprocessing pipeline.

    raw claims CSV
        --> engineer features (amount ratios, temporal, vendor behaviour)
        --> train/test split (stratified on is_fraud)
        --> fit StandardScaler + OneHotEncoder on train only
        --> SMOTE-resample the training set (train-only!)
        --> save: features.parquet, features_resampled.parquet,
                  preprocessor.joblib, metadata.json

Why SMOTE only on train:
    The fraud class is heavily minority (~5-10%). Leaving the test set
    imbalanced is essential — it reflects the real deployed distribution.
    Resampling the test set would inflate your recall numbers artificially.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from imblearn.over_sampling import SMOTE
from sklearn.model_selection import train_test_split

from .feature_engineering import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    build_preprocessor,
    engineer_features,
)


@dataclass
class PreprocessingReport:
    n_rows_input: int
    n_rows_after_engineering: int
    n_train: int
    n_test: int
    n_train_after_smote: int
    fraud_rate_input: float
    fraud_rate_train: float
    fraud_rate_train_after_smote: float
    fraud_rate_test: float
    numeric_features: list[str]
    categorical_features: list[str]
    feature_matrix_shape: list[int]


def run_preprocessing(
    input_csv: str | Path,
    output_dir: str | Path,
    test_size: float = 0.2,
    use_smote: bool = True,
    random_state: int = 42,
) -> PreprocessingReport:
    """Run feature engineering + split + fit + SMOTE + save artefacts."""
    input_csv = Path(input_csv)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load
    df = pd.read_csv(input_csv)
    n_in = len(df)

    # 2. Feature engineering (runs on the full dataframe: these features
    # are stateful per-employee/per-vendor, not learned parameters, so
    # computing them pre-split is correct and mirrors the production
    # feature store which sees all historical data).
    engineered = engineer_features(df)
    n_eng = len(engineered)

    if "is_fraud" not in engineered.columns:
        raise ValueError(
            "Input CSV must contain an 'is_fraud' column with 0/1 labels. "
            "If you only have SROIE data, run "
            "`scripts/generate_synthetic_claims.py` first."
        )

    feature_cols = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    X = engineered[feature_cols].copy()
    y = engineered["is_fraud"].astype(int).to_numpy()

    fraud_rate_in = float(y.mean())

    # 3. Stratified split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=test_size,
        random_state=random_state,
        stratify=y,
    )

    # 4. Fit preprocessor on train only — critical to avoid leakage
    preprocessor = build_preprocessor()
    X_train_t = preprocessor.fit_transform(X_train)
    X_test_t = preprocessor.transform(X_test)

    # 5. SMOTE on train only
    if use_smote and y_train.sum() > 1:
        # k_neighbors must be <= minority count - 1
        k = min(5, int(y_train.sum()) - 1)
        smote = SMOTE(random_state=random_state, k_neighbors=max(1, k))
        X_train_res, y_train_res = smote.fit_resample(X_train_t, y_train)
    else:
        X_train_res, y_train_res = X_train_t, y_train

    # 6. Persist artefacts
    feature_names = _extract_feature_names(preprocessor, feature_cols)

    _save_matrix(
        output_dir / "features_train.parquet",
        X_train_t, y_train, feature_names,
        claim_ids=engineered.loc[X_train.index, "claim_id"].to_numpy(),
    )
    _save_matrix(
        output_dir / "features_test.parquet",
        X_test_t, y_test, feature_names,
        claim_ids=engineered.loc[X_test.index, "claim_id"].to_numpy(),
    )
    _save_matrix(
        output_dir / "features_train_resampled.parquet",
        X_train_res, y_train_res, feature_names,
        claim_ids=None,
    )

    joblib.dump(preprocessor, output_dir / "preprocessor.joblib")
    # Save the engineered (but untransformed) DataFrame too — handy
    # for sanity checks and for the Drools/policy engine which wants
    # raw fields, not one-hot vectors.
    engineered.to_parquet(output_dir / "engineered.parquet", index=False)

    report = PreprocessingReport(
        n_rows_input=n_in,
        n_rows_after_engineering=n_eng,
        n_train=int(len(y_train)),
        n_test=int(len(y_test)),
        n_train_after_smote=int(len(y_train_res)),
        fraud_rate_input=fraud_rate_in,
        fraud_rate_train=float(y_train.mean()),
        fraud_rate_train_after_smote=float(y_train_res.mean()),
        fraud_rate_test=float(y_test.mean()),
        numeric_features=list(NUMERIC_FEATURES),
        categorical_features=list(CATEGORICAL_FEATURES),
        feature_matrix_shape=list(X_train_res.shape),
    )

    with (output_dir / "preprocessing_report.json").open("w") as f:
        json.dump(asdict(report), f, indent=2)

    return report


def _extract_feature_names(preprocessor, original_cols: list[str]) -> list[str]:
    """Pull the post-transform column names out of the fitted ColumnTransformer."""
    try:
        return list(preprocessor.get_feature_names_out())
    except Exception:
        # Fallback: just index them.
        return [f"f_{i}" for i in range(preprocessor.transform(
            pd.DataFrame([[0] * len(original_cols)], columns=original_cols)
        ).shape[1])]


def _save_matrix(
    path: Path,
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    claim_ids: np.ndarray | None = None,
) -> None:
    df = pd.DataFrame(X, columns=feature_names)
    df["is_fraud"] = y
    if claim_ids is not None:
        df.insert(0, "claim_id", claim_ids)
    df.to_parquet(path, index=False)

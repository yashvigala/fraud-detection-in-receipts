"""Feature engineering for anomaly-detection model training.

The spec's Step 4b requires the input to Isolation Forest + Autoencoder
to be a numeric feature vector. The raw claims DataFrame has strings,
timestamps, and an imbalanced amount distribution — none of which the
models can consume directly. This module turns raw claims into a clean
numeric matrix, using behavioural and temporal features that are known
to separate normal claims from anomalies.

Design choices:

    * Behavioural features (amount_vs_employee_mean, vendor_repeat_count)
      are computed per-employee before splitting train/test. That is fine:
      they are stateful features, not labels, and will also be recomputed
      at inference using the production feature store.

    * The sklearn transformer (build_preprocessor) is what you fit on
      the training split and serialise. At inference you reload it and
      call .transform() — no re-fit. This keeps train/serve consistent.

    * Everything is built on sklearn's ColumnTransformer so the same
      object handles numeric scaling, categorical one-hot, and passes
      through features that are already numeric.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

NUMERIC_FEATURES: list[str] = [
    "amount",
    "amount_log",
    "amount_vs_category_mean",
    "amount_vs_employee_mean",
    "day_of_week",
    "hour_of_day",
    "is_weekend",
    "is_off_hours",
    "days_since_last_claim",
    "vendor_frequency",
    "vendor_repeat_count_3d",
]

CATEGORICAL_FEATURES: list[str] = [
    "category",
    "department",
    "grade",
]


@dataclass
class FeatureEngineeringReport:
    n_rows_in: int
    n_rows_out: int
    n_numeric_features: int
    n_categorical_features: int
    columns_used: list[str]


def _ensure_datetime(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if not np.issubdtype(df[col].dtype, np.datetime64):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return ``df`` augmented with the engineered feature columns.

    Does not modify the input. Expects columns:
        claim_id, employee_id, department, grade, vendor, category,
        amount, submitted_at
    """
    required = {
        "claim_id", "employee_id", "department", "grade",
        "vendor", "category", "amount", "submitted_at",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    df = df.copy()
    df = _ensure_datetime(df, "submitted_at")
    # Drop rows where the timestamp could not be parsed.
    df = df.dropna(subset=["submitted_at"]).reset_index(drop=True)

    # --- Amount features ------------------------------------------------
    df["amount"] = df["amount"].astype(float)
    df["amount_log"] = np.log1p(df["amount"])

    category_mean = df.groupby("category")["amount"].transform("mean")
    df["amount_vs_category_mean"] = df["amount"] / category_mean.replace(0, np.nan)
    df["amount_vs_category_mean"] = df["amount_vs_category_mean"].fillna(1.0)

    employee_mean = df.groupby("employee_id")["amount"].transform("mean")
    df["amount_vs_employee_mean"] = df["amount"] / employee_mean.replace(0, np.nan)
    df["amount_vs_employee_mean"] = df["amount_vs_employee_mean"].fillna(1.0)

    # --- Temporal features ---------------------------------------------
    df["day_of_week"] = df["submitted_at"].dt.dayofweek.astype(int)
    df["hour_of_day"] = df["submitted_at"].dt.hour.astype(int)
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["is_off_hours"] = (
        (df["hour_of_day"] < 8) | (df["hour_of_day"] > 20)
    ).astype(int)

    # --- Per-employee temporal features --------------------------------
    df = df.sort_values(["employee_id", "submitted_at"]).reset_index(drop=True)
    df["days_since_last_claim"] = (
        df.groupby("employee_id")["submitted_at"]
        .diff()
        .dt.total_seconds()
        .div(86_400)
        .fillna(9999.0)  # first-ever claim: set large sentinel
        .clip(upper=9999.0)
    )

    # --- Vendor features -----------------------------------------------
    vendor_counts = df["vendor"].value_counts()
    df["vendor_frequency"] = df["vendor"].map(vendor_counts).astype(int)

    # Rolling duplicate-vendor count per employee within 3 days.
    df["vendor_repeat_count_3d"] = _rolling_vendor_repeat(df, window_days=3)

    return df


def _rolling_vendor_repeat(df: pd.DataFrame, window_days: int = 3) -> pd.Series:
    """For each (employee_id, vendor) pair, count how many of that
    employee's claims to that vendor fall within the preceding
    ``window_days`` (excluding the row itself)."""
    window = pd.Timedelta(days=window_days)
    counts = np.zeros(len(df), dtype=int)

    for (_, _), group in df.groupby(["employee_id", "vendor"], sort=False):
        times = group["submitted_at"].to_numpy()
        idxs = group.index.to_numpy()
        # For each row, count previous rows within window.
        for i, (idx, t) in enumerate(zip(idxs, times)):
            cutoff = t - window
            # Previous rows are times[:i]; count those >= cutoff.
            counts[idx] = int(np.sum(times[:i] >= cutoff))

    return pd.Series(counts, index=df.index, name="vendor_repeat_count_3d")


def build_preprocessor(
    numeric_features: Sequence[str] = tuple(NUMERIC_FEATURES),
    categorical_features: Sequence[str] = tuple(CATEGORICAL_FEATURES),
) -> Pipeline:
    """Build the sklearn Pipeline that transforms engineered features
    into the model-ready numeric matrix. Fit this on the training
    split and serialise it with joblib for reuse at inference."""
    numeric_pipeline = Pipeline([
        ("scaler", StandardScaler()),
    ])

    categorical_pipeline = Pipeline([
        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ])

    column_transformer = ColumnTransformer(
        transformers=[
            ("num", numeric_pipeline, list(numeric_features)),
            ("cat", categorical_pipeline, list(categorical_features)),
        ],
        remainder="drop",
    )

    return Pipeline([("features", column_transformer)])

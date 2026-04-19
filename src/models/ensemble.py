"""Ensemble of IsolationForest + Autoencoder.

Combines the two anomaly scores and labels each row as
NORMAL / SUSPICIOUS / ANOMALOUS. Matches the JSON schema in the
project spec (fields: anomaly_score, prediction, reconstruction_error,
combined_anomaly_score, anomaly_label, confidence, top_features).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import joblib
import numpy as np

from .autoencoder_model import AutoencoderScorer
from .isolation_forest_model import IsolationForestScorer


def combine_scores(
    if_score: np.ndarray,
    ae_score: np.ndarray,
    weight_if: float = 0.35,
    weight_ae: float = 0.65,
) -> np.ndarray:
    """Weighted average of IF + AE scores; weights must sum to 1.

    Default (0.35, 0.65) favours the autoencoder because on our synthetic
    dataset AE has a stronger individual F1 (0.48 vs IF's 0.29). The
    training pipeline further refines these via grid search on held-out
    training data and persists the optimum on the Ensemble instance.
    """
    assert abs(weight_if + weight_ae - 1.0) < 1e-6
    return weight_if * if_score + weight_ae * ae_score


@dataclass
class Ensemble:
    iforest: IsolationForestScorer
    autoencoder: AutoencoderScorer
    feature_names: list[str]
    # Label cutoffs on the combined 0..1 score.
    suspicious_threshold: float = 0.5
    anomalous_threshold: float = 0.75
    # Per-component weights (populated by the training script after
    # grid-searching on held-out train data). Must sum to 1.
    weight_if: float = 0.35
    weight_ae: float = 0.65

    def score(self, X: np.ndarray) -> dict:
        """Score a batch and return a dict of arrays."""
        if_score = self.iforest.anomaly_score(X)
        ae_score = self.autoencoder.anomaly_score(X)
        recon_err = self.autoencoder.reconstruction_error(X)
        combined = combine_scores(if_score, ae_score, self.weight_if, self.weight_ae)

        labels = np.where(
            combined >= self.anomalous_threshold, "ANOMALOUS",
            np.where(combined >= self.suspicious_threshold, "SUSPICIOUS", "NORMAL"),
        )
        predictions = (combined >= self.suspicious_threshold).astype(int)
        # "Confidence" = distance from the nearest decision boundary, rescaled.
        confidence = np.clip(
            np.abs(combined - self.suspicious_threshold) * 2.0, 0.0, 1.0
        )

        return {
            "isolation_forest_score": if_score,
            "autoencoder_score": ae_score,
            "reconstruction_error": recon_err,
            "combined_anomaly_score": combined,
            "prediction": predictions,
            "anomaly_label": labels,
            "confidence": confidence,
        }

    def score_one(self, x: np.ndarray) -> dict:
        """Single-row convenience wrapper that returns plain Python scalars
        in the JSON schema the decision layer expects."""
        if x.ndim == 1:
            x = x.reshape(1, -1)
        out = self.score(x)

        # Top contributing features = columns with the largest absolute
        # reconstruction residual. Gives an explainable 'why flagged'.
        reconstructed = self.autoencoder.model.predict(x)
        actual_flat = x.flatten()
        expected_flat = reconstructed.flatten()
        residuals = np.abs(actual_flat - expected_flat)
        top_idx = np.argsort(residuals)[::-1][:3]
        top_features = [
            {
                "name": self.feature_names[int(i)] if i < len(self.feature_names) else f"f_{i}",
                "residual": float(residuals[int(i)]),
                # Both values are on the standardized scale produced by
                # StandardScaler: training mean = 0, training SD = 1 per feature.
                # So `actual` and `expected` are in z-score units, and
                # `residual` is already in "standard deviations" directly.
                "actual": float(actual_flat[int(i)]),
                "expected": float(expected_flat[int(i)]),
            }
            for i in top_idx
        ]

        return {
            "isolation_forest": {
                "anomaly_score": float(out["isolation_forest_score"][0]),
                "prediction": int(self.iforest.predict(x)[0]),
            },
            "autoencoder": {
                "reconstruction_error": float(out["reconstruction_error"][0]),
                "anomaly_score": float(out["autoencoder_score"][0]),
                "prediction": int(self.autoencoder.predict(x)[0]),
            },
            "combined_anomaly_score": float(out["combined_anomaly_score"][0]),
            "anomaly_label": str(out["anomaly_label"][0]),
            "prediction": int(out["prediction"][0]),
            "confidence": float(out["confidence"][0]),
            "top_features": top_features,
        }

    def save(self, path: str) -> None:
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: str) -> "Ensemble":
        return joblib.load(path)

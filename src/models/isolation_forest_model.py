"""Isolation Forest wrapper.

Isolation Forest is a tree-based anomaly detector. It isolates observations
by randomly picking a feature and a split value; anomalies need fewer splits
to isolate than normal points. Output: a score in ~[-0.5, 0.5], where more
negative = more anomalous. We normalise to [0, 1] (1 = most anomalous).
"""
from __future__ import annotations

from dataclasses import dataclass

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest


@dataclass
class IsolationForestScorer:
    """Thin wrapper around sklearn's IsolationForest that produces
    a 0..1 anomaly score and a binary prediction."""

    model: IsolationForest
    score_min: float
    score_max: float
    threshold: float

    @classmethod
    def train(
        cls,
        X: np.ndarray,
        contamination: float = 0.1,
        n_estimators: int = 200,
        random_state: int = 42,
    ) -> "IsolationForestScorer":
        model = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            random_state=random_state,
            n_jobs=-1,
        )
        model.fit(X)
        # score_samples: higher = more normal; lower (more negative) = more anomalous.
        raw = model.score_samples(X)
        score_min = float(raw.min())
        score_max = float(raw.max())
        # Threshold chosen so that contamination % of training rows are flagged.
        threshold = float(np.quantile(raw, contamination))
        return cls(model=model, score_min=score_min, score_max=score_max, threshold=threshold)

    def anomaly_score(self, X: np.ndarray) -> np.ndarray:
        """Return a 0..1 anomaly score (higher = more anomalous)."""
        raw = self.model.score_samples(X)
        # Flip and min-max normalise to [0, 1].
        denom = self.score_max - self.score_min or 1.0
        normed = (self.score_max - raw) / denom
        return np.clip(normed, 0.0, 1.0)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Return 1 = anomaly, 0 = normal (using the training-fit threshold)."""
        raw = self.model.score_samples(X)
        return (raw <= self.threshold).astype(int)

    def save(self, path: str) -> None:
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: str) -> "IsolationForestScorer":
        return joblib.load(path)

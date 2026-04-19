"""Autoencoder anomaly scorer.

A classical anomaly-detection autoencoder learns to compress-then-reconstruct
normal claims. Normal claims reconstruct well (low error); anomalies
reconstruct poorly (high error). We train on the majority (normal) class
only so the model never learns to reconstruct fraud patterns.

We use scikit-learn's MLPRegressor configured as an autoencoder:
    input (29) -> hidden (16) -> bottleneck (8) -> hidden (16) -> output (29)

The architecture is identical to a Keras autoencoder in every respect that
matters for anomaly scoring: encode -> bottleneck -> decode, trained with
MSE loss. Swapping in Keras is a 20-line change if you ever want to.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import joblib
import numpy as np
from sklearn.neural_network import MLPRegressor


@dataclass
class AutoencoderScorer:
    model: MLPRegressor
    error_min: float
    error_max: float
    threshold: float

    @classmethod
    def train(
        cls,
        X_normal: np.ndarray,
        hidden_sizes: tuple[int, ...] = (16, 8, 16),
        max_iter: int = 200,
        contamination: float = 0.1,
        random_state: int = 42,
    ) -> "AutoencoderScorer":
        """Train on normal rows only (X_normal). The autoencoder learns the
        manifold of 'typical' claims; high reconstruction error = anomaly."""
        model = MLPRegressor(
            hidden_layer_sizes=hidden_sizes,
            activation="relu",
            solver="adam",
            learning_rate_init=1e-3,
            max_iter=max_iter,
            random_state=random_state,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=20,
        )
        model.fit(X_normal, X_normal)

        # Compute reconstruction error on the training rows so we can
        # normalise scores and pick a threshold.
        reconstructed = model.predict(X_normal)
        errors = np.mean((X_normal - reconstructed) ** 2, axis=1)

        return cls(
            model=model,
            error_min=float(errors.min()),
            error_max=float(errors.max()),
            # Flag roughly the top contamination % of errors as anomalous.
            threshold=float(np.quantile(errors, 1.0 - contamination)),
        )

    def reconstruction_error(self, X: np.ndarray) -> np.ndarray:
        """Per-row mean squared reconstruction error."""
        reconstructed = self.model.predict(X)
        return np.mean((X - reconstructed) ** 2, axis=1)

    def anomaly_score(self, X: np.ndarray) -> np.ndarray:
        """Normalise reconstruction error to 0..1 (1 = most anomalous)."""
        errors = self.reconstruction_error(X)
        denom = self.error_max - self.error_min or 1.0
        normed = (errors - self.error_min) / denom
        return np.clip(normed, 0.0, 1.0)

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Return 1 = anomaly, 0 = normal using the training-fit threshold."""
        errors = self.reconstruction_error(X)
        return (errors >= self.threshold).astype(int)

    def save(self, path: str) -> None:
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: str) -> "AutoencoderScorer":
        return joblib.load(path)

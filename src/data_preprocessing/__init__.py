"""Data preprocessing pipeline for the anomaly detection engine.

Implements the feature engineering step that precedes training
Isolation Forest and the Autoencoder (Step 4b of the project spec).
"""
from .synthetic_generator import generate_claims
from .feature_engineering import build_preprocessor, engineer_features
from .pipeline import run_preprocessing

__all__ = [
    "generate_claims",
    "build_preprocessor",
    "engineer_features",
    "run_preprocessing",
]

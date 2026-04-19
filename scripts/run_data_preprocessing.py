"""CLI: run feature engineering + SMOTE on a labelled claims CSV.

Example:
    python scripts/run_data_preprocessing.py \\
        --input data/synthetic/claims.csv \\
        --output data/processed
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_preprocessing import run_preprocessing  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Labelled claims CSV")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--no-smote", action="store_true", help="Skip SMOTE step")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    report = run_preprocessing(
        input_csv=args.input,
        output_dir=args.output,
        test_size=args.test_size,
        use_smote=not args.no_smote,
        random_state=args.seed,
    )
    print(json.dumps(asdict(report), indent=2))
    print(f"\nArtefacts written to: {args.output}")
    print("  features_train.parquet            (transformed train matrix)")
    print("  features_test.parquet             (transformed test matrix)")
    print("  features_train_resampled.parquet  (SMOTE-balanced train)")
    print("  engineered.parquet                (raw engineered features)")
    print("  preprocessor.joblib               (fitted sklearn Pipeline)")
    print("  preprocessing_report.json         (this report)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

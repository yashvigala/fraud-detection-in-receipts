"""CLI: generate a synthetic labelled claims dataset.

Example:
    python scripts/generate_synthetic_claims.py \\
        --n-normal 5000 \\
        --n-fraud 500 \\
        --output data/synthetic/claims.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data_preprocessing.synthetic_generator import generate_claims, save_claims  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-normal", type=int, default=5000)
    parser.add_argument("--n-fraud", type=int, default=500)
    parser.add_argument("--n-employees", type=int, default=100)
    parser.add_argument("--n-vendors", type=int, default=80)
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", required=True, help="CSV output path")
    args = parser.parse_args()

    df = generate_claims(
        n_normal=args.n_normal,
        n_fraud=args.n_fraud,
        n_employees=args.n_employees,
        n_vendors=args.n_vendors,
        start_date=args.start_date,
        end_date=args.end_date,
        seed=args.seed,
    )
    out = save_claims(df, args.output)

    fraud_count = int(df["is_fraud"].sum())
    print(f"Wrote {len(df)} claims to {out}")
    print(f"  normal:   {len(df) - fraud_count}")
    print(f"  fraud:    {fraud_count} ({fraud_count / len(df):.1%})")
    print(f"  columns:  {list(df.columns)}")
    print("  fraud types:")
    for ft, c in df.loc[df['is_fraud'] == 1, 'fraud_type'].value_counts().items():
        print(f"    {ft:25s} {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

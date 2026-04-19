"""Convert SROIE key/*.json ground-truth files into a single CSV with
columns compatible with the data preprocessing pipeline.

Each SROIE key JSON looks like:
    {"company": "...", "date": "25/12/2018", "address": "...", "total": "9.00"}

We map these onto our claim schema so they can be merged with synthetic
data (synthetic provides employee/department/fraud labels that SROIE
lacks; SROIE provides real vendor/amount distributions).

Example:
    python scripts/extract_sroie_fields.py \\
        --input data/sroie/key \\
        --output data/sroie/sroie_claims.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from dateutil import parser as dtparser


def _parse_date(raw: str):
    if not raw:
        return None
    try:
        return dtparser.parse(raw, dayfirst=True)
    except (ValueError, OverflowError, TypeError):
        return None


def _parse_amount(raw: str):
    if not raw:
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except ValueError:
        return None


def extract(input_dir: Path, output_csv: Path) -> pd.DataFrame:
    rows = []
    for path in sorted(input_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            continue

        submitted = _parse_date(data.get("date", ""))
        amount = _parse_amount(data.get("total", ""))

        rows.append({
            "claim_id": f"SROIE_{path.stem}",
            "vendor": (data.get("company") or "").strip() or None,
            "address": (data.get("address") or "").strip() or None,
            "amount": amount,
            "submitted_at": submitted.isoformat() if submitted else None,
        })

    df = pd.DataFrame(rows)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_csv, index=False)
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="data/sroie/key", help="Folder of SROIE key JSONs")
    parser.add_argument("--output", default="data/sroie/sroie_claims.csv", help="Output CSV path")
    args = parser.parse_args()

    df = extract(Path(args.input), Path(args.output))
    print(f"Wrote {len(df)} rows to {args.output}")
    print(f"  with vendor: {df['vendor'].notna().sum()}")
    print(f"  with amount: {df['amount'].notna().sum()}")
    print(f"  with date:   {df['submitted_at'].notna().sum()}")
    if df['amount'].notna().any():
        print(f"  amount range: {df['amount'].min():.2f} - {df['amount'].max():.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

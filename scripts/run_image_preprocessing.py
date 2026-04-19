"""CLI: run the image preprocessing pipeline on a folder of receipts.

Example:
    python scripts/run_image_preprocessing.py \\
        --input data/sroie/img \\
        --output data/processed/images
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make 'src.*' importable when the script is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.image_preprocessing import preprocess_folder  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Folder of raw receipt images")
    parser.add_argument("--output", required=True, help="Where to write cleaned images")
    parser.add_argument(
        "--binarise-method",
        default="otsu",
        choices=["otsu", "adaptive"],
        help="Thresholding method (default: otsu)",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Reprocess images even if the output already exists",
    )
    args = parser.parse_args()

    stats = preprocess_folder(
        input_dir=args.input,
        output_dir=args.output,
        binarise_method=args.binarise_method,
        skip_existing=not args.no_skip_existing,
    )
    print(json.dumps(stats, indent=2))
    return 0 if not stats["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""End-to-end image preprocessing pipeline.

    raw image  -->  deskew  -->  denoise  -->  binarise  -->  clean image

This is the Step 2 module of the project workflow. Output images are
ready to be sent to Gemini Flash 2.0 OCR.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from tqdm import tqdm

from .binarise import binarise
from .denoise import denoise
from .deskew import deskew

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def preprocess_image(
    image: np.ndarray,
    binarise_method: str = "otsu",
) -> np.ndarray:
    """Run the full deskew -> denoise -> binarise pipeline on one image."""
    deskewed = deskew(image)
    denoised = denoise(deskewed)
    binary = binarise(denoised, method=binarise_method)
    return binary


def _iter_image_paths(folder: Path) -> Iterable[Path]:
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def preprocess_folder(
    input_dir: str | Path,
    output_dir: str | Path,
    binarise_method: str = "otsu",
    skip_existing: bool = True,
) -> dict:
    """Process every image in ``input_dir`` and write to ``output_dir``.

    Returns a small stats dict useful for logs / reports.
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input folder not found: {input_dir}")

    paths = list(_iter_image_paths(input_dir))
    processed = 0
    skipped = 0
    failed: list[str] = []

    for path in tqdm(paths, desc="Preprocessing images", unit="img"):
        out_path = output_dir / f"{path.stem}.png"
        if skip_existing and out_path.exists():
            skipped += 1
            continue

        image = cv2.imread(str(path))
        if image is None:
            failed.append(path.name)
            continue

        try:
            clean = preprocess_image(image, binarise_method=binarise_method)
            cv2.imwrite(str(out_path), clean)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{path.name}: {exc}")

    return {
        "total": len(paths),
        "processed": processed,
        "skipped_existing": skipped,
        "failed": failed,
    }

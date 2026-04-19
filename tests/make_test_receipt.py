"""Generate a synthetic skewed + noisy receipt image for testing the
image preprocessing pipeline. Writes to data/synthetic/test_receipts/."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def make_receipt(out_path: Path, width: int = 600, height: int = 900) -> None:
    # White background
    img = np.ones((height, width, 3), dtype=np.uint8) * 255

    lines = [
        ("STARBUCKS COFFEE", 1.2),
        ("Mumbai, India", 0.6),
        ("----------------------", 0.6),
        ("Cappuccino         250", 0.8),
        ("Sandwich           350", 0.8),
        ("Tax                 60", 0.8),
        ("----------------------", 0.6),
        ("TOTAL              660", 1.0),
        ("Date: 2025-03-15", 0.6),
        ("Thank you!", 0.7),
    ]

    font = cv2.FONT_HERSHEY_SIMPLEX
    y = 80
    for text, scale in lines:
        cv2.putText(img, text, (40, y), font, scale, (10, 10, 10), 2, cv2.LINE_AA)
        y += int(60 * scale)

    # Add Gaussian noise
    noise = np.random.normal(0, 15, img.shape).astype(np.int16)
    noisy = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    # Skew by 12 degrees (typical phone photo angle)
    m = cv2.getRotationMatrix2D((width // 2, height // 2), 12, 1.0)
    skewed = cv2.warpAffine(noisy, m, (width, height), borderValue=(255, 255, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), skewed)


if __name__ == "__main__":
    for i in range(3):
        make_receipt(
            Path(__file__).resolve().parent.parent
            / "data" / "synthetic" / "test_receipts" / f"receipt_{i:02d}.png"
        )
    print("Wrote 3 synthetic receipt images.")

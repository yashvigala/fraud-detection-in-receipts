"""Binarise a receipt image to clean black-on-white.

Thermal receipt paper is low-contrast and often unevenly lit. Global
thresholds (e.g. plain Otsu on the whole image) wash out faint ink under
shadows. We offer two modes:

    * ``otsu``    — global Otsu. Fast, works well on well-lit scans.
    * ``adaptive``— Gaussian-weighted local threshold. Robust to uneven
                    lighting, which is common in mobile phone captures.

The project spec recommends Otsu for thermal paper; we default to that,
but expose adaptive as an option for shadowed or curled receipts.
"""
from __future__ import annotations

import cv2
import numpy as np


def binarise(
    image: np.ndarray,
    method: str = "otsu",
    adaptive_block_size: int = 31,
    adaptive_C: int = 10,
) -> np.ndarray:
    """Return a binary (0/255) image.

    Parameters
    ----------
    image : np.ndarray
        Grayscale or BGR input.
    method : {"otsu", "adaptive"}
        Thresholding strategy.
    adaptive_block_size : int
        Odd block size for adaptive thresholding. Larger = smoother.
    adaptive_C : int
        Constant subtracted from the local mean. Higher = stricter.
    """
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    if method == "otsu":
        _, binary = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
        )
    elif method == "adaptive":
        if adaptive_block_size % 2 == 0:
            raise ValueError("adaptive_block_size must be odd")
        binary = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            adaptive_block_size,
            adaptive_C,
        )
    else:
        raise ValueError(f"Unknown binarisation method: {method!r}")

    return binary

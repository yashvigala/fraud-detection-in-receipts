"""Denoise a receipt image.

Receipt photos carry three kinds of noise that hurt OCR:

    * Photo grain from low-light capture.
    * Uneven shadow across the receipt.
    * JPEG compression artefacts on thermal paper.

A Gaussian blur softens grain, and a mild bilateral filter preserves
text edges while smoothing flat regions. We skip aggressive denoising
(like non-local means) because it is slow and can blur small digits.
"""
from __future__ import annotations

import cv2
import numpy as np


def denoise(
    image: np.ndarray,
    gaussian_ksize: int = 3,
    bilateral_d: int = 5,
    bilateral_sigma_color: int = 50,
    bilateral_sigma_space: int = 50,
) -> np.ndarray:
    """Return a denoised copy of ``image``.

    Parameters
    ----------
    image : np.ndarray
        BGR or grayscale.
    gaussian_ksize : int
        Kernel size for Gaussian blur. Must be odd.
    bilateral_d, bilateral_sigma_color, bilateral_sigma_space : int
        Bilateral filter parameters. Defaults are tuned for mobile-phone
        photos of receipts; raise sigma_color if grain is heavy.
    """
    if gaussian_ksize % 2 == 0:
        raise ValueError("gaussian_ksize must be odd")

    blurred = cv2.GaussianBlur(image, (gaussian_ksize, gaussian_ksize), 0)
    smoothed = cv2.bilateralFilter(
        blurred,
        bilateral_d,
        bilateral_sigma_color,
        bilateral_sigma_space,
    )
    return smoothed

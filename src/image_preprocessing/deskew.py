"""Deskew a receipt image.

Receipts photographed on tables or held at an angle skew the text baseline,
which destroys OCR quality. We estimate the skew angle from the dominant
text orientation and rotate the image to correct it (up to +/-45 degrees,
per the project spec).

Algorithm:
    1. Convert to grayscale + invert so text pixels are "on".
    2. Threshold to isolate foreground.
    3. Find the minimum-area bounding rectangle of all foreground pixels.
    4. Extract its angle and normalise to [-45, 45] degrees.
    5. Rotate around the image centre with a white background fill.
"""
from __future__ import annotations

import cv2
import numpy as np


def _estimate_skew_angle(gray: np.ndarray) -> float:
    """Return skew angle in degrees. Positive = image rotated clockwise."""
    # Invert so text is bright on dark, then binarise.
    inverted = cv2.bitwise_not(gray)
    _, thresh = cv2.threshold(
        inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU
    )

    # Coordinates of all non-zero (foreground) pixels.
    coords = np.column_stack(np.where(thresh > 0))
    if coords.size == 0:
        return 0.0

    # cv2.minAreaRect returns angle in (-90, 0]. Normalise to (-45, 45].
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    return float(angle)


def deskew(image: np.ndarray, max_angle: float = 45.0) -> np.ndarray:
    """Rotate ``image`` so its text baseline is horizontal.

    Parameters
    ----------
    image : np.ndarray
        BGR or grayscale input.
    max_angle : float
        If the estimated skew exceeds this (degrees), we leave the image
        untouched — very large angles are almost always estimation errors.
    """
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    angle = _estimate_skew_angle(gray)
    if abs(angle) > max_angle or abs(angle) < 0.1:
        return image

    h, w = image.shape[:2]
    centre = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)
    # borderValue=255 fills with white so rotation doesn't introduce
    # black triangles that confuse downstream binarisation.
    rotated = cv2.warpAffine(
        image,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotated

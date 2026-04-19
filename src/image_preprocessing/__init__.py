"""Image preprocessing pipeline for receipt images.

Implements Step 2 of the project workflow: deskew, denoise, binarise.
"""
from .pipeline import preprocess_image, preprocess_folder
from .deskew import deskew
from .denoise import denoise
from .binarise import binarise

__all__ = [
    "preprocess_image",
    "preprocess_folder",
    "deskew",
    "denoise",
    "binarise",
]

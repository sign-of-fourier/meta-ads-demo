"""
Combines text and image embedding vectors into a single feature vector for BO/GPR.

Strategy: truncate each modality to a fixed dimension, then concatenate.
If a vector is shorter than the target dim it is zero-padded on the right.

TEXT_DIM and IMAGE_DIM are the only constants that need to change when tuning
the feature representation — everything downstream reads output_dim().
"""

from __future__ import annotations

import numpy as np

TEXT_DIM: int = 128
IMAGE_DIM: int = 128


def truncate_pad(vec: np.ndarray, dim: int) -> np.ndarray:
    """Return a float32 array of exactly `dim` elements: truncated or zero-padded."""
    vec = np.asarray(vec, dtype=np.float32)
    if vec.shape[0] >= dim:
        return vec[:dim]
    padded = np.zeros(dim, dtype=np.float32)
    padded[: vec.shape[0]] = vec
    return padded


def combine(
    text_vec: np.ndarray,
    image_vec: np.ndarray,
    text_dim: int = TEXT_DIM,
    image_dim: int = IMAGE_DIM,
) -> np.ndarray:
    """
    Concatenate truncated/padded text and image embeddings.
    Returns a float32 array of shape (text_dim + image_dim,).
    """
    return np.concatenate([truncate_pad(text_vec, text_dim), truncate_pad(image_vec, image_dim)])


def output_dim(text_dim: int = TEXT_DIM, image_dim: int = IMAGE_DIM) -> int:
    """Total feature dimension produced by combine()."""
    return text_dim + image_dim

"""
Combines text and image embedding vectors into a single feature vector for BO/GPR.

Strategy: pass the full embedding from each modality (no truncation), then
concatenate. PCA in the BO pipeline reduces this to the final working dimension.

TEXT_DIM and IMAGE_DIM must match the actual embedding model output dimensions:
  - TEXT_DIM=1536  matches text-embedding-3-small (OpenAI)
  - IMAGE_DIM=1536 matches embed-v-4-0 (Azure AI Inference)

Changing these constants is the only knob needed when switching embedding models.
Everything downstream reads output_dim().
"""

from __future__ import annotations

import numpy as np

TEXT_DIM: int = 1536
IMAGE_DIM: int = 1536


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
    image_vec: np.ndarray | None,
    text_dim: int = TEXT_DIM,
    image_dim: int = IMAGE_DIM,
) -> np.ndarray:
    """
    Concatenate truncated/padded text and image embeddings.
    Returns a float32 array of shape (text_dim + image_dim,).
    image_vec may be None — zeros are used in that slot.
    """
    img = np.zeros(image_dim, dtype=np.float32) if image_vec is None else truncate_pad(image_vec, image_dim)
    return np.concatenate([truncate_pad(text_vec, text_dim), img])


def output_dim(text_dim: int = TEXT_DIM, image_dim: int = IMAGE_DIM) -> int:
    """Total feature dimension produced by combine()."""
    return text_dim + image_dim

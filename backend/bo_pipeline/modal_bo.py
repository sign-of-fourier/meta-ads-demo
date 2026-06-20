"""
Modal GP batch BO helpers.

Encapsulates PCA fitting / projection (dimensionality reduction before the
Modal API call). All configuration is in bo_pipeline.config — see that module
for documentation on MODAL_BO_API_URL, MODAL_BO_PCA_DIMS, GOOGLE_BO_PCA_DIMS.

The HTTP calls to the Modal GP service live in quantecarlo._modal_api and are
re-exported here so existing callers don't need to change their import paths.
"""

from __future__ import annotations

import logging

import numpy as np
from sklearn.decomposition import PCA

from quantecarlo import call_modal_api, call_modal_api_multioutput  # noqa: F401 — re-exported

from bo_pipeline.config import (  # noqa: E402
    GOOGLE_BO_PCA_DIMS,
    MODAL_BO_API_URL,
    MODAL_BO_PCA_DIMS,
    modal_bo_enabled,
    pca_dims_for_platform,
)

# Re-export so existing callers (pipeline.py etc.) don't need to change imports
__all__ = [
    "call_modal_api",
    "call_modal_api_multioutput",
    "modal_bo_enabled",
    "pca_dims_for_platform",
    "MODAL_BO_API_URL",
    "MODAL_BO_PCA_DIMS",
    "GOOGLE_BO_PCA_DIMS",
    "fit_pca",
    "project",
    "dim_bounds",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PCA helpers
# ---------------------------------------------------------------------------

def fit_pca(X: np.ndarray, n_components: int | None = None) -> tuple[PCA, np.ndarray]:
    """
    Fit PCA on X and return (fitted_pca, X_reduced).

    n_components defaults to MODAL_BO_PCA_DIMS (see bo_pipeline.config).
    Capped at min(n_components, n_samples, n_features) so it never fails on
    small inputs.
    """
    n_components = n_components if n_components is not None else MODAL_BO_PCA_DIMS
    n_components = min(n_components, X.shape[0], X.shape[1])
    pca = PCA(n_components=n_components, random_state=42)
    X_reduced = pca.fit_transform(X).astype(np.float32)
    logger.debug(
        "fit_pca: %d→%d dims | explained variance: %.3f",
        X.shape[1], n_components, float(pca.explained_variance_ratio_.sum()),
    )
    return pca, X_reduced


def project(pca: PCA, X: np.ndarray) -> np.ndarray:
    """Project X into the already-fitted PCA space."""
    return pca.transform(X).astype(np.float32)


def dim_bounds(X_pca: np.ndarray) -> list[tuple[float, float]]:
    """Per-dimension (min, max) bounds derived from the PCA-projected pool."""
    return [
        (float(X_pca[:, i].min()), float(X_pca[:, i].max()))
        for i in range(X_pca.shape[1])
    ]


# ---------------------------------------------------------------------------
# Convenience: is Modal BO configured?
# ---------------------------------------------------------------------------


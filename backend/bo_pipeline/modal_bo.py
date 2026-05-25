"""
Modal GP batch BO helpers.

Encapsulates:
  - PCA fitting / projection (dimensionality reduction before the API call)
  - HTTP call to the Modal GP service (stateless q-EI endpoint)
  - Nearest-pool-member snap (maps GP-suggested PCA coords → candidate index)

None of these functions touch the database or know about the ad schema.
They operate purely on numpy arrays and plain Python objects.

Environment variables
---------------------
MODAL_BO_API_URL   — full URL of the Modal GP endpoint
                     (e.g. https://markshipman4273--bo-gp-service-gp-suggest.modal.run)
                     Leave blank or unset to disable Modal BO (falls back to local).
MODAL_BO_PCA_DIMS  — integer; number of PCA components to reduce to before calling
                     the API (default 64).  Set lower for speed, higher for fidelity.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

import numpy as np
from sklearn.decomposition import PCA

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env-var configuration
# ---------------------------------------------------------------------------

def _api_url() -> str:
    return os.environ.get("MODAL_BO_API_URL", "").strip()


def _pca_dims() -> int:
    raw = os.environ.get("MODAL_BO_PCA_DIMS", "64").strip()
    try:
        v = int(raw)
        if v < 1:
            raise ValueError
        return v
    except ValueError:
        logger.warning("MODAL_BO_PCA_DIMS=%r is not a positive integer; using 64", raw)
        return 64


# ---------------------------------------------------------------------------
# PCA helpers
# ---------------------------------------------------------------------------

def fit_pca(X: np.ndarray, n_components: int | None = None) -> tuple[PCA, np.ndarray]:
    """
    Fit PCA on X and return (fitted_pca, X_reduced).

    n_components defaults to MODAL_BO_PCA_DIMS env var (default 64).
    Capped at min(n_components, n_samples, n_features) so it never fails on
    small inputs.
    """
    n_components = n_components if n_components is not None else _pca_dims()
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
# Modal API call
# ---------------------------------------------------------------------------

def call_modal_api(
    api_url: str,
    X_train_pca: np.ndarray,
    y: np.ndarray,
    bounds: list[tuple[float, float]],
    q: int = 2,
    n_candidates: int = 512,
    train_steps: int = 100,
    lr: float = 0.1,
    xi: float = 0.01,
    timeout: float = 120.0,
) -> list[np.ndarray]:
    """
    POST to the Modal GP endpoint and return a list of q candidate vectors
    in PCA space.

    y convention: higher = better (the Modal API is a maximisation service).
    Pass scores directly; do NOT negate them.

    Returns a list of q np.ndarray, each of shape (n_pca_dims,).
    Raises on HTTP error or JSON decode failure — caller should catch and fall back.
    """
    n_dims = X_train_pca.shape[1]
    search_space = [
        {"name": f"pca_{i}", "type": "float", "low": bounds[i][0], "high": bounds[i][1]}
        for i in range(n_dims)
    ]

    payload: dict[str, Any] = {
        "X": X_train_pca.tolist(),
        "y": y.tolist(),
        "search_space": search_space,
        "q": q,
        "n_candidates": n_candidates,
        "train_steps": train_steps,
        "lr": lr,
        "xi": xi,
        "mode": "production",
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    logger.debug("call_modal_api: POST %s (n_obs=%d, n_dims=%d, q=%d)", api_url, len(y), n_dims, q)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    candidates = data["candidates"]
    return [
        np.array([float(c["x"][i]) for i in range(n_dims)], dtype=np.float32)
        for c in candidates
    ]


# ---------------------------------------------------------------------------
# Nearest-pool-member snap
# ---------------------------------------------------------------------------

def snap_to_pool(
    suggestions_pca: list[np.ndarray],
    X_cands_pca: np.ndarray,
    candidates: list[dict],
) -> list[int]:
    """
    Map each GP-suggested PCA point to the index of the nearest unvisited
    candidate.  Greedy deduplication: once a candidate is claimed by an
    earlier suggestion it is not available to later ones.

    Returns a list of candidate indices (same length as suggestions_pca,
    possibly shorter if the pool runs out).
    """
    available = list(range(len(candidates)))
    picked: list[int] = []

    for suggestion in suggestions_pca:
        if not available:
            break
        avail_X = X_cands_pca[available]
        dists = np.linalg.norm(avail_X - suggestion, axis=1)
        local_idx = int(np.argmin(dists))
        pool_idx = available[local_idx]
        picked.append(pool_idx)
        available.remove(pool_idx)

    return picked


# ---------------------------------------------------------------------------
# Convenience: is Modal BO configured?
# ---------------------------------------------------------------------------

def modal_bo_enabled() -> bool:
    """Return True if MODAL_BO_API_URL is set to a non-empty string."""
    return bool(_api_url())

"""
ECDF-based target transformation for cross-platform Bayesian Optimisation.

fit_ecdf fits an empirical CDF on a *combined* pool of raw scores from multiple
platforms (Meta, Google, ...).  The returned callable maps any array of raw
scores to standard-normal targets anchored to that combined distribution.

This is used instead of the per-group transform_y (gpr.py) when running
cross-platform BO: all platforms share the same ranking, so their GPR targets
— and therefore their EI values — are directly comparable.

Relationship to transform_y (gpr.py)
--------------------------------------
transform_y ranks within a *single* group and maps to standard-normal.
fit_ecdf does the same but ranks against a *combined* pool, so a score's
quantile reflects its position in the full cross-platform distribution rather
than just its own platform's distribution.

The numerical formula is identical:
    u  = rank / (n + 1)          where rank ∈ {1, …, n} (searchsorted, side='right')
    u  = u * 0.9999 + 0.00005    clamp away from 0 and 1
    y  = norm.ppf(u)             inverse-normal CDF → Gaussian targets
"""

from __future__ import annotations

from typing import Callable

import numpy as np
from scipy.stats import norm


def fit_ecdf(
    all_scores: list[float] | np.ndarray,
) -> Callable[[np.ndarray], np.ndarray]:
    """
    Fit an ECDF on *all_scores* (the combined pool from all platforms).

    Parameters
    ----------
    all_scores : 1-D array-like of raw scores from ALL groups combined.
                 Must contain at least 1 value.
                 Pass higher-is-better scores directly; negate minimisation
                 objectives before calling.

    Returns
    -------
    transform : callable(scores: array-like) -> np.ndarray
        Maps a group's raw scores to standard-normal targets anchored to the
        combined distribution.

        Ranks each input score against the fitted pool via ``np.searchsorted``
        (side='right') — equivalently, counts how many pool scores are ≤ the
        input.  This gives ranks in {1, …, n} (same convention as transform_y).

        Safe to call with scores that lie outside the original pool range:
        they will be mapped to the extreme quantiles (clamped away from 0/1
        before norm.ppf, so no ±∞ results).

    Example
    -------
    >>> meta_scores  = [3.5, 4.2, 5.1]
    >>> google_scores = [4.7, 3.9]
    >>> transform = fit_ecdf(meta_scores + google_scores)
    >>> transform(np.array([3.5, 5.1]))   # meta scores in combined context
    array([-1.28...,  0.52...])
    """
    sorted_pool = np.sort(np.asarray(all_scores, dtype=np.float64))
    n = len(sorted_pool)

    def transform(scores: list[float] | np.ndarray) -> np.ndarray:
        scores = np.asarray(scores, dtype=np.float64)
        # side='right' counts elements ≤ score → rank in {1, …, n}
        ranks = np.searchsorted(sorted_pool, scores, side="right").astype(np.float64)
        u = ranks / (n + 1.0)
        # Same clamp as transform_y: shrink [0,1] by 0.0001 total, symmetrically
        u = u * 0.9999 + 0.00005
        return norm.ppf(u)

    return transform

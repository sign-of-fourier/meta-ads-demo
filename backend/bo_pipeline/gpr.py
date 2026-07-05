"""
Pure GPR functions for Bayesian Optimisation over ad combinations.

Kernel: ConstantKernel * RBF + WhiteKernel — same family as gpr_pipeline.py.
ConvergenceWarnings from sklearn are suppressed; the fit still returns a usable
model (sklearn guarantees this even when the optimiser hasn't fully converged).

All functions are stateless; they receive and return plain numpy arrays,
GaussianProcessRegressor instances, and StandardScaler instances.
"""

from __future__ import annotations

import warnings

import numpy as np
from scipy.stats import norm
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel
from sklearn.preprocessing import StandardScaler

from bo_pipeline.config import LOW_CONFIDENCE_COSINE_DISTANCE, LOW_CONFIDENCE_GPR_STD, MIN_TRAINING_POINTS  # noqa: E402


def transform_y(y: np.ndarray) -> np.ndarray:
    """
    Rank-transform y to standard-normal via the Probability Integral Transform.

    Mirrors _transform_y in the Modal GP service (modal_gp_api.py) so the local
    GPR path and the Modal path see identically-shaped targets.

    Steps
    -----
    1. Empirical CDF:  u = (rank + 1) / (n + 1)
       Avoids u=0 and u=1 at the extremes (which would give −∞/+∞ from ppf).
    2. Clamp to (0.00005, 0.99995):  u = u * 0.9999 + 0.00005
       Shrinks the range by 0.0001 total; the addend is half that shrinkage,
       keeping the distribution symmetric.
    3. Inverse normal CDF:  norm.ppf(u)
       Maps the uniform to a standard-normal.  The GP sees Gaussian targets,
       which ExactGP / sklearn GPR both require.  Equivalently this is "apply
       the inverse lognormal CDF then take the log" — the log of exp(norm.ppf(u))
       is just norm.ppf(u), so the GP fits the log of a lognormal-distributed target.

    Pass y where higher = better (negate minimisation objectives before calling).
    """
    n = len(y)
    ranks = np.argsort(np.argsort(y)).astype(np.float64)
    u = (ranks + 1.0) / (n + 1.0)
    u = u * 0.9999 + 0.00005
    return norm.ppf(u)


def _make_kernel() -> object:
    return ConstantKernel(1.0) * RBF(length_scale=1.0) + WhiteKernel(noise_level=0.1)


def fit_gpr(
    X: np.ndarray,
    y: np.ndarray,
    n_restarts: int = 5,
    random_state: int = 42,
) -> tuple[GaussianProcessRegressor, StandardScaler]:
    """
    Fit a GPR on (X, y). ConvergenceWarnings are suppressed.
    Returns (gpr, scaler); scaler has already been fit on X.
    """
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    gpr = GaussianProcessRegressor(
        kernel=_make_kernel(),
        n_restarts_optimizer=n_restarts,
        normalize_y=True,
        random_state=random_state,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gpr.fit(X_scaled, y)
    return gpr, scaler


def predict_with_std(
    gpr: GaussianProcessRegressor,
    scaler: StandardScaler,
    X: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (mean, std) arrays for each row of X."""
    X_scaled = scaler.transform(X)
    mu, sigma = gpr.predict(X_scaled, return_std=True)
    return mu, sigma


def expected_improvement(
    gpr: GaussianProcessRegressor,
    scaler: StandardScaler,
    X_candidates: np.ndarray,
    y_best: float,
    xi: float = 0.01,
) -> np.ndarray:
    """
    EI acquisition function. Higher = more promising candidate.
    xi controls the exploration/exploitation trade-off (default 0.01).
    """
    mu, sigma = predict_with_std(gpr, scaler, X_candidates)
    sigma = np.maximum(sigma, 1e-9)
    z = (mu - y_best - xi) / sigma
    ei = (mu - y_best - xi) * norm.cdf(z) + sigma * norm.pdf(z)
    ei[sigma < 1e-9] = 0.0
    return ei


def fantasize(
    gpr: GaussianProcessRegressor,
    scaler: StandardScaler,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_new: np.ndarray,
) -> tuple[GaussianProcessRegressor, StandardScaler]:
    """
    Fantasy step for batch BO: add X_new (with its GPR-predicted mean as a
    "fantasized" label) to the training set and refit.  Used to select the
    second recommendation without re-running real experiments.
    """
    mu, _ = predict_with_std(gpr, scaler, X_new)
    X_aug = np.vstack([X_train, X_new])
    y_aug = np.append(y_train, mu[0])
    return fit_gpr(X_aug, y_aug)


def confidence_label(gpr_std: float | None, nearest_known: list[dict] | None) -> str | None:
    """
    T12 (testing an approach, not yet signed off): derive a coarse confidence
    label from signals BO already computes per pick.

    Returns "low" if either signal crosses its threshold, else None (no badge
    shown for the default/normal case — there is deliberately no "high" or
    "medium" tier yet, see TECHNICAL_DEBT.md T12).

    gpr_std near the marginal variance of the (rank-transformed, ~N(0,1))
    target means the GP learned little near this point; a large cosine
    distance to the nearest scored observation means there is no real
    precedent for this combination even if the posterior otherwise looks
    confident. Either alone can mislead, so both are checked.
    """
    if gpr_std is not None and gpr_std >= LOW_CONFIDENCE_GPR_STD:
        return "low"
    if nearest_known:
        min_dist = min(n["cosine_distance"] for n in nearest_known)
        if min_dist >= LOW_CONFIDENCE_COSINE_DISTANCE:
            return "low"
    return None

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

MIN_TRAINING_POINTS = 2  # below this, fit() is not called (caller should fallback)


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

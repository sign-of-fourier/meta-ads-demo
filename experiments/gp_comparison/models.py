"""
Three GP model wrappers for the cross-platform BO comparison experiment.

Uniform interface for all three:

    model.fit(X_meta_train, X_google_train, y_meta_train, y_google_train,
              X_meta_all, X_google_all)           # all vecs for PCA fitting
    model.predict(X_meta_test, X_google_test)     -> (mean, std)    len = N_meta+N_google
    model.predict_cov(X_meta_test, X_google_test) -> (mean, cov)    cov is (N, N)
    model.sample(X_meta_test, X_google_test, n, rng) -> (n, N) array

X_meta shape:   (N, 3072) float32
X_google shape: (N, 1536) float32
Outputs are ordered meta-first, google-second (same order as the stacked inputs).
"""

import sys
import warnings
import numpy as np
from pathlib import Path
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, RBF, WhiteKernel

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "chi_bad_ads"))
from multioutput_gp import MultiOutputGP  # noqa: E402

PCA_DIMS = 8
D_META   = 0
D_GOOGLE = 1


# ── internal helpers ──────────────────────────────────────────────────────────

def _make_kernel():
    return ConstantKernel(1.0) * RBF(1.0) + WhiteKernel(0.1)


def _fit_gpr(X: np.ndarray, y: np.ndarray) -> GaussianProcessRegressor:
    gpr = GaussianProcessRegressor(
        kernel=_make_kernel(),
        n_restarts_optimizer=5,
        normalize_y=True,
        random_state=42,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gpr.fit(X, y)
    return gpr


def _fit_pca_clean(X_all: np.ndarray, n_components: int):
    nc = min(n_components, X_all.shape[0], X_all.shape[1])
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_all)
    pca = PCA(n_components=nc, random_state=42)
    pca.fit(X_scaled)
    return scaler, pca


def _zero_pad(X_google: np.ndarray, target_dim: int = 3072) -> np.ndarray:
    pad_width = target_dim - X_google.shape[1]
    return np.hstack([X_google, np.zeros((len(X_google), pad_width), dtype=X_google.dtype)])


def _median_dist(X: np.ndarray, rng: np.random.Generator, sample: int = 80) -> float:
    idx = rng.choice(len(X), min(sample, len(X)), replace=False)
    s = X[idx]
    dists = [np.linalg.norm(s[i] - s[j]) for i in range(len(s)) for j in range(i + 1, len(s))]
    return float(np.median(dists)) if dists else 1.0


def _pad_cols(X: np.ndarray, target_cols: int) -> np.ndarray:
    if X.shape[1] == target_cols:
        return X
    return np.hstack([X, np.zeros((len(X), target_cols - X.shape[1]), dtype=X.dtype)])


# ── Model 1: PooledGP ─────────────────────────────────────────────────────────

class PooledGP:
    """
    Current production approach.

    Google RSA zero-padded to 3072, pooled with Meta into a shared PCA (→K),
    then a single sklearn GPR. Platform identity d is never seen by the GP.
    """

    def __init__(self, n_components: int = PCA_DIMS):
        self.n_components = n_components
        self.scaler = None
        self.pca    = None
        self.gpr    = None

    def _raw(self, X_meta: np.ndarray, X_google: np.ndarray) -> np.ndarray:
        return np.vstack([X_meta, _zero_pad(X_google)]).astype(np.float64)

    def _project(self, X_meta: np.ndarray, X_google: np.ndarray) -> np.ndarray:
        return self.pca.transform(self.scaler.transform(self._raw(X_meta, X_google)))

    def fit(self, X_meta_tr, X_google_tr, y_meta, y_google, X_meta_all, X_google_all):
        self.scaler, self.pca = _fit_pca_clean(self._raw(X_meta_all, X_google_all), self.n_components)
        X_tr = self._project(X_meta_tr, X_google_tr)
        self.gpr = _fit_gpr(X_tr, np.concatenate([y_meta, y_google]))
        return self

    def predict(self, X_meta_te, X_google_te):
        X = self._project(X_meta_te, X_google_te)
        return self.gpr.predict(X, return_std=True)

    def predict_cov(self, X_meta_te, X_google_te):
        X = self._project(X_meta_te, X_google_te)
        return self.gpr.predict(X, return_cov=True)

    def sample(self, X_meta_te, X_google_te, n: int = 1, rng=None):
        rng = rng or np.random.default_rng(42)
        mean, cov = self.predict_cov(X_meta_te, X_google_te)
        cov = cov + 1e-6 * np.eye(len(mean))
        return rng.multivariate_normal(mean, cov, size=n)


# ── Model 2: PooledGP + Platform Flag ────────────────────────────────────────

class PooledGPWithFlag:
    """
    Same zero-pad + shared PCA as PooledGP, but appends a binary platform
    indicator (0=Meta, 1=Google) as an extra feature after PCA reduction.
    The GPR sees K+1-dimensional inputs.
    """

    def __init__(self, n_components: int = PCA_DIMS):
        self.n_components = n_components
        self.scaler = None
        self.pca    = None
        self.gpr    = None

    def _raw(self, X_meta: np.ndarray, X_google: np.ndarray) -> np.ndarray:
        return np.vstack([X_meta, _zero_pad(X_google)]).astype(np.float64)

    def _project_flagged(self, X_meta: np.ndarray, X_google: np.ndarray) -> np.ndarray:
        X_pca = self.pca.transform(self.scaler.transform(self._raw(X_meta, X_google)))
        d = np.concatenate([np.zeros(len(X_meta)), np.ones(len(X_google))])
        return np.hstack([X_pca, d[:, None]])

    def fit(self, X_meta_tr, X_google_tr, y_meta, y_google, X_meta_all, X_google_all):
        self.scaler, self.pca = _fit_pca_clean(self._raw(X_meta_all, X_google_all), self.n_components)
        X_tr = self._project_flagged(X_meta_tr, X_google_tr)
        self.gpr = _fit_gpr(X_tr, np.concatenate([y_meta, y_google]))
        return self

    def predict(self, X_meta_te, X_google_te):
        X = self._project_flagged(X_meta_te, X_google_te)
        return self.gpr.predict(X, return_std=True)

    def predict_cov(self, X_meta_te, X_google_te):
        X = self._project_flagged(X_meta_te, X_google_te)
        return self.gpr.predict(X, return_cov=True)

    def sample(self, X_meta_te, X_google_te, n: int = 1, rng=None):
        rng = rng or np.random.default_rng(42)
        mean, cov = self.predict_cov(X_meta_te, X_google_te)
        cov = cov + 1e-6 * np.eye(len(mean))
        return rng.multivariate_normal(mean, cov, size=n)


# ── Model 3: Multi-Output GP ──────────────────────────────────────────────────

class MultiOutputGPModel:
    """
    Separate PCA per platform (no zero-padding), then MultiOutputGP with a
    coregionalization matrix B where B[0,1] = rho models Meta-Google correlation.

    Each platform's PCA projects to the same K-dim output space; the GP then
    operates jointly over all K-dim points tagged with their platform index d.
    """

    def __init__(self, n_components: int = PCA_DIMS, rho: float = 0.5, noise: float = 0.15):
        self.n_components  = n_components
        self.rho           = rho
        self.noise         = noise
        self.scaler_meta   = None
        self.pca_meta      = None
        self.scaler_google = None
        self.pca_google    = None
        self.gp            = None
        self.ls            = 1.0

    def _fit_platform_pcas(self, X_meta_all: np.ndarray, X_google_all: np.ndarray):
        self.scaler_meta, self.pca_meta     = _fit_pca_clean(X_meta_all.astype(np.float64),   self.n_components)
        self.scaler_google, self.pca_google = _fit_pca_clean(X_google_all.astype(np.float64), self.n_components)

    def _proj_meta(self, X: np.ndarray) -> np.ndarray:
        Xp = self.pca_meta.transform(self.scaler_meta.transform(X.astype(np.float64)))
        return _pad_cols(Xp, self.n_components)

    def _proj_google(self, X: np.ndarray) -> np.ndarray:
        Xp = self.pca_google.transform(self.scaler_google.transform(X.astype(np.float64)))
        return _pad_cols(Xp, self.n_components)

    def _build(self, X_meta: np.ndarray, X_google: np.ndarray):
        Xm = self._proj_meta(X_meta)
        Xg = self._proj_google(X_google)
        X  = np.vstack([Xm, Xg])
        d  = np.concatenate([
            np.full(len(Xm), D_META,   dtype=int),
            np.full(len(Xg), D_GOOGLE, dtype=int),
        ])
        return X, d

    def fit(self, X_meta_tr, X_google_tr, y_meta, y_google, X_meta_all, X_google_all, rng=None):
        rng = rng or np.random.default_rng(42)
        self._fit_platform_pcas(X_meta_all, X_google_all)

        # Lengthscale: median pairwise distance over the full projected pool
        Xm_all = self._proj_meta(X_meta_all)
        Xg_all = self._proj_google(X_google_all)
        self.ls = _median_dist(np.vstack([Xm_all, Xg_all]), rng)

        X_tr, d_tr = self._build(X_meta_tr, X_google_tr)
        y_tr = np.concatenate([y_meta, y_google]).astype(np.float64)

        B = np.array([[1.0, self.rho], [self.rho, 1.0]])
        self.gp = MultiOutputGP(B=B, lengthscale=self.ls, variance=1.0, noise=self.noise)
        self.gp.fit(X_tr, d_tr, y_tr)
        return self

    def predict(self, X_meta_te, X_google_te):
        X_te, d_te = self._build(X_meta_te, X_google_te)
        mean, var  = self.gp.predict(X_te, d_te)
        return mean, np.sqrt(np.maximum(var, 0.0))

    def predict_cov(self, X_meta_te, X_google_te):
        X_te, d_te = self._build(X_meta_te, X_google_te)
        return self.gp.predict(X_te, d_te, return_cov=True)

    def sample(self, X_meta_te, X_google_te, n: int = 1, rng=None):
        X_te, d_te = self._build(X_meta_te, X_google_te)
        seed = int(rng.integers(1_000_000)) if rng is not None else 42
        return self.gp.sample_posterior(X_te, d_te, n_samples=n, random_state=seed)

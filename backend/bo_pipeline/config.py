"""
Central configuration for the Bayesian Optimisation pipeline.

Every tunable parameter lives here and is read from an environment variable
so it can be changed in backend/.env without touching code. Each constant is
documented with its purpose, the formula or reasoning behind the default, and
what happens when you raise or lower it.

See ENV.md §5 — "Bayesian Optimisation tuning" for the operator reference.

IMPORTANT: This module is imported at server startup. All env vars are resolved
once. If you change .env, restart the backend for new values to take effect.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# ── Metric selection ──────────────────────────────────────────────────────────

METRIC_PREFERENCE: tuple[str, ...] = ("ctr", "cvr", "roas", "qwen", "qwen_warm", "synthetic")
"""
Resolution order used when target_metric is not explicitly set by the caller.

The pipeline tries each metric in order and uses the first that has sufficient
observations (see MIN_REAL_OBS). The order encodes trust:

  1. ctr        — click-through rate observed on real platform (Meta / Google)
  2. cvr        — conversion rate; not collected by fake server (dead in demo mode)
  3. roas       — return on ad spend; similarly sparse / unavailable in demo
  4. qwen       — Qwen2-VL quality score from the image-generation pipeline;
                  same numeric scale within a single seed ad, but NOT comparable
                  to CTR — never mixed with real metrics in a single GP fit
  5. qwen_warm  — Qwen scores written by the warm-start mini-BO, scaled to an
                  approximate CTR range to bootstrap cold starts
  6. synthetic  — fully synthetic CTR estimates seeded at startup for the
                  cold-start case when no Qwen scores exist yet either

Rule: the GP always fits on exactly one metric type per run. Mixing is prevented
by the preference walk in selector.get_scored_combinations.
"""

REAL_METRICS: frozenset[str] = frozenset(("ctr", "cvr", "roas"))
"""
The subset of METRIC_PREFERENCE backed by real platform signals.
These are held to a higher observation-count bar (MIN_REAL_OBS) before they
displace warm-start fallbacks, because a single noisy real observation is
less useful than several consistent Qwen-derived ones.
"""

MIN_REAL_OBS: int = int(os.getenv("BO_MIN_REAL_OBS", "5"))
"""
Minimum number of real-platform observations (ctr/cvr/roas) required before
the GP abandons warm-start fallback scores and fits on real data exclusively.

Why this matters: below ~5 points a GP kernel cannot reliably distinguish
signal from noise in a high-dimensional embedding space. Keeping the
warm-start scores active until real data is sufficient produces better
early-stage recommendations than switching too soon.

Must be >= MIN_TRAINING_POINTS (2) — the mathematical floor below which the
GP covariance matrix becomes singular and sklearn raises a ConvergenceWarning.

Raise to delay the switch to real metrics (longer warm-start phase, more
conservative). Lower to switch sooner (less warm-start, noisier early picks).
Set BO_MIN_REAL_OBS=2 in .env to get the old one-observation behaviour.
"""


# ── GP fitting ────────────────────────────────────────────────────────────────

MIN_TRAINING_POINTS: int = 2
"""
Absolute minimum observations required before calling GPR.fit().

Below this the kernel optimiser is numerically unstable (near-singular
covariance matrix). The pipeline falls back to random candidate selection
when scored observations are fewer than this value.

This is a mathematical floor, not a tuning knob — do not lower it below 2.
Not exposed as an env var intentionally.
"""


# ── Dimensionality reduction ──────────────────────────────────────────────────

def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        v = int(raw)
        if v < 1:
            raise ValueError
        return v
    except ValueError:
        logger.warning("BO config: %s=%r is not a positive integer; using %d", name, raw, default)
        return default


MODAL_BO_PCA_DIMS: int = _parse_int_env("MODAL_BO_PCA_DIMS", 64)
"""
PCA output dimension for Meta combined (text + image) embeddings before the
Modal GP API call. Input vectors are 3072-dim (1536 text + 1536 image).

Lower = faster API call and less memory, but loses embedding variance.
Default 64 captures roughly 90-95% of variance in practice for typical ad pools.
Set MODAL_BO_PCA_DIMS in .env to tune.
"""

GOOGLE_BO_PCA_DIMS: int = _parse_int_env("GOOGLE_BO_PCA_DIMS", 32)
"""
PCA output dimension for Google RSA embeddings (text-only, 1536-dim input).

Defaults to 32 — half of MODAL_BO_PCA_DIMS — because RSA has no image embedding
so the input space is half the size of Meta's combined vector. Keeping it
proportionally smaller avoids over-parameterising a lower-information input.
Set GOOGLE_BO_PCA_DIMS in .env to tune.
"""

MODAL_BO_API_URL: str = os.getenv("MODAL_BO_API_URL", "").strip()
"""
Full URL of the Modal GP q-EI endpoint (e.g. https://…modal.run).

When blank or unset, run_bo() falls back to local sklearn GPR automatically.
The Modal path uses batch q-EI via quantecarlo.call_modal_api; the local path
uses sequential EI via bo_pipeline.gpr. Results are comparable but Modal is
faster for large candidate pools.

Set MODAL_BO_API_URL in .env to enable. Leave blank for fully offline operation.
"""


def modal_bo_enabled() -> bool:
    """Return True if MODAL_BO_API_URL is configured to a non-empty string."""
    return bool(MODAL_BO_API_URL)


def pca_dims_for_platform(platform: str) -> int:
    """
    Return the configured PCA output dimension for the given platform.

    "google"   → GOOGLE_BO_PCA_DIMS  (default 32, text-only input)
    all others → MODAL_BO_PCA_DIMS   (default 64, combined text+image input)
    """
    return GOOGLE_BO_PCA_DIMS if platform == "google" else MODAL_BO_PCA_DIMS


# ── Convergence thresholds ────────────────────────────────────────────────────

CONVERGENCE_FLOOR: int = int(os.getenv("MIN_CONVERGENCE_IMPRESSIONS", "500"))
"""
Absolute floor: a pushed clone must accumulate at least this many lifetime
impressions before the convergence check fires, regardless of what the CTR
formula computes. Protects against declaring convergence on a handful of
impressions at an unusually high CTR.

Default 500. Set MIN_CONVERGENCE_IMPRESSIONS=100 in .env for faster turnover
in demo mode. Combine with CONVERGENCE_MARGIN_FRACTION=0.80 for FAST_RAMP.
"""

CONVERGENCE_MARGIN: float = float(os.getenv("CONVERGENCE_MARGIN_FRACTION", "0.20"))
"""
Width of the relative CTR confidence interval used to compute the required
impression count before a clone's CTR is considered reliable:

  n = z² × (1-p) / (f² × p)   where p = baseline CTR, z = 1.96, f = this value

f=0.20 means we wait until the clone CTR is measured within ±20% of the
campaign baseline CTR. Example: 3.5% baseline → ~2,650 impressions needed.

Raise f to converge sooner (less precise). Lower to converge later (more precise).
Set CONVERGENCE_MARGIN_FRACTION=0.80 in .env to converge quickly with FAST_RAMP.
"""

CONVERGENCE_MIN_DAYS: int = int(os.getenv("MIN_CONVERGENCE_DAYS", "3"))
"""
Minimum days a clone must be running before the convergence check fires.
Prevents a brief traffic spike from prematurely converging a test.

NOTE: This parameter is documented and read here but not yet enforced in the
convergence checker (_check_meta_convergence / _check_google_convergence in
main.py). Those functions currently check impressions only. Wiring this in is
a known gap — see TECHNICAL_DEBT.md.

Default 3. Set MIN_CONVERGENCE_DAYS in .env (no effect until the gap is closed).
"""

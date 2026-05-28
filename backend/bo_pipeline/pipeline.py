"""
Bayesian Optimisation pipeline for ad combination selection.

Given a seed ad and a source of text combinations, selects two candidate
combinations to test next.

Two methods are available (controlled by the `method` parameter to run_bo):

  "modal"  (default)
    Reduces the combined embeddings to PCA space, sends the actual discrete
    candidate vectors to the Modal GP service (stateless q-EI endpoint), and
    receives back the indices of the q=2 best candidates.  No snap-to-pool
    step — the API selects directly from the real candidate pool.
    Requires MODAL_BO_API_URL to be set; silently falls back to "local" if
    the env var is absent or the API call fails.

  "local"
    Fits a local sklearn GPR, picks the highest-EI candidate, then applies a
    "fantasy step" (augmented refit) to pick a second candidate.  No network
    calls; works with no env vars.

Scored observations come from ad_generation_variants (image variants that have
been model-scored).  Candidates are text combinations from
ad_text_combination_embeddings paired with the seed ad's image embedding.

Per-ad constraint: seed_ad_id and text_source_id must refer to the same ad.
The caller is responsible for providing consistent identifiers.

Higher score = better ad (consistent with the 1–7 rating scale).
If your scorer inverts this, negate scores before calling or pass higher_is_better=False.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ad_embedding_combiner import combine
from bo_pipeline.gpr import MIN_TRAINING_POINTS, expected_improvement, fantasize, fit_gpr, predict_with_std, transform_y
from bo_pipeline.selector import get_candidate_combinations, get_scored_combinations
from bo_pipeline.storage import DB_PATH

logger = logging.getLogger(__name__)

_FALLBACK_TYPE  = "random"
_EI_TYPE        = "ei"
_FANTASY_TYPE   = "fantasy"
_MODAL_TYPE     = "modal_q_ei"


def _build_X(combinations: list[dict]) -> np.ndarray:
    """Stack combined embeddings for a list of combination dicts."""
    return np.vstack([combine(c["text_vector"], c["image_vector"]) for c in combinations])


# ---------------------------------------------------------------------------
# Local (fantasize) path
# ---------------------------------------------------------------------------

def _run_local_bo(
    scored: list[dict],
    candidates: list[dict],
    xi: float,
    higher_is_better: bool,
) -> list[dict]:
    """
    GPR + EI pick 1, fantasy-step pick 2.  Pure local sklearn — no network.
    """
    y_raw = np.array([s["score"] for s in scored], dtype=np.float64)
    if not higher_is_better:
        y_raw = -y_raw
    # Rank-transform to standard-normal so the GP sees Gaussian targets.
    # Mirrors _transform_y in the Modal GP service — ECDF → clamp → norm.ppf.
    y = transform_y(y_raw)
    y_best = float(y.max())

    X_train = _build_X(scored).astype(np.float64)
    X_cands = _build_X(candidates).astype(np.float64)

    gpr, scaler = fit_gpr(X_train, y)

    # Pick 1: highest EI
    ei_scores = expected_improvement(gpr, scaler, X_cands, y_best, xi=xi)
    pick1_idx = int(np.argmax(ei_scores))
    pick1_cand = candidates[pick1_idx]
    mu1, sigma1 = predict_with_std(gpr, scaler, X_cands[[pick1_idx]])
    pick1 = _make_pick(
        pick1_cand,
        _EI_TYPE,
        ei_score=float(ei_scores[pick1_idx]),
        gpr_mean=float(mu1[0]),
        gpr_std=float(sigma1[0]),
    )

    if len(candidates) == 1:
        return [pick1]

    # Pick 2: fantasy step — pass transformed y so the augmented training set
    # stays in the same (normal) space as the initial fit.
    gpr2, scaler2 = fantasize(gpr, scaler, X_train, y, X_cands[[pick1_idx]])
    remaining_idx = [i for i in range(len(candidates)) if i != pick1_idx]
    X_remaining = X_cands[remaining_idx]
    cands_remaining = [candidates[i] for i in remaining_idx]

    ei_scores2 = expected_improvement(gpr2, scaler2, X_remaining, y_best, xi=xi)
    pick2_idx = int(np.argmax(ei_scores2))
    pick2_cand = cands_remaining[pick2_idx]
    mu2, sigma2 = predict_with_std(gpr2, scaler2, X_remaining[[pick2_idx]])
    pick2 = _make_pick(
        pick2_cand,
        _FANTASY_TYPE,
        ei_score=float(ei_scores2[pick2_idx]),
        gpr_mean=float(mu2[0]),
        gpr_std=float(sigma2[0]),
    )

    return [pick1, pick2]


# ---------------------------------------------------------------------------
# Modal (q-EI + PCA) path
# ---------------------------------------------------------------------------

def _run_modal_bo(
    scored: list[dict],
    candidates: list[dict],
    xi: float,
    higher_is_better: bool,
) -> list[dict]:
    """
    PCA → Modal GP q-EI → nearest-pool-member snap.

    Returns picks in the same format as _run_local_bo.
    Raises on any error so the caller can fall back gracefully.
    """
    from bo_pipeline.modal_bo import (
        call_modal_api,
        fit_pca,
        modal_bo_enabled,
        _api_url,
    )

    api_url = _api_url()

    y_raw = np.array([s["score"] for s in scored], dtype=np.float32)
    # Modal API is a maximisation service (higher = better).
    # NOTE: `higher_is_better=False` is NEVER passed by any current call site.
    # The `-y_raw` branch is dead code kept as a hook for future "minimise metric"
    # use cases (e.g. minimise CPC). Do not remove silently.
    y = y_raw if higher_is_better else -y_raw  # noqa: SIM210 (dead branch, intentional)

    # Build full embedding pool for PCA fitting (scored ∪ candidates)
    X_scored = _build_X(scored).astype(np.float32)
    X_cands  = _build_X(candidates).astype(np.float32)
    X_all    = np.vstack([X_scored, X_cands])

    # Fit PCA on the union so the projection captures the full space
    pca, X_all_pca = fit_pca(X_all)
    X_train_pca = X_all_pca[: len(scored)]
    X_cands_pca = X_all_pca[len(scored) :]

    # Call Modal GP service — raises on failure.
    # Sends the actual discrete candidate vectors; Modal selects via q-EI and
    # returns indices directly — no snap-to-pool step needed.
    suggestions = call_modal_api(
        api_url=api_url,
        X_train_pca=X_train_pca,
        y=y,
        X_cands_pca=X_cands_pca,
        q=min(2, len(candidates)),
        xi=xi,
    )

    picks = []
    seen_indices: set[int] = set()
    for suggestion in suggestions:
        idx = suggestion["index"]
        if idx in seen_indices:
            continue
        seen_indices.add(idx)
        picks.append(_make_pick(candidates[idx], _MODAL_TYPE))

    return picks


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_bo(
    seed_ad_id: str,
    text_source_id: str,
    user_id: int,
    db_path: Path = DB_PATH,
    xi: float = 0.01,
    higher_is_better: bool = True,
    method: str = "modal",
) -> tuple[list[dict], str | None]:
    """
    Select up to 2 combinations to test next via Bayesian Optimisation.

    method="modal"  (default) — uses the Modal GP service with PCA preprocessing
                                and proper batch q-EI; falls back to "local" if
                                MODAL_BO_API_URL is unset or the API call fails.
    method="local"            — local sklearn GPR + EI + fantasy step; no network.

    Returns (picks, warning) where:
      picks   — list of 1 or 2 dicts, each with:
                  combination_key, combination, selection_type,
                  ei_score, gpr_mean, gpr_std
      warning — non-None string when Modal was configured but failed and the
                response fell back to the local GPR; None otherwise.

    Falls back to random selection when there are fewer than MIN_TRAINING_POINTS
    scored combinations available.
    """
    from bo_pipeline.modal_bo import modal_bo_enabled

    scored = get_scored_combinations(seed_ad_id, text_source_id, user_id, db_path)
    scored_keys = {s["combination_key"] for s in scored}
    candidates = get_candidate_combinations(
        text_source_id, seed_ad_id, user_id, exclude_keys=scored_keys, db_path=db_path
    )

    logger.info(
        "run_bo: seed_ad_id=%s scored=%d candidates=%d method=%s",
        seed_ad_id, len(scored), len(candidates), method,
    )

    if not candidates:
        logger.warning("run_bo: no candidates available for seed_ad_id=%s", seed_ad_id)
        return [], None

    # --- Fallback: not enough data to fit a reliable model ---
    if len(scored) < MIN_TRAINING_POINTS:
        logger.info(
            "run_bo: insufficient scored data (%d < %d) — random fallback",
            len(scored), MIN_TRAINING_POINTS,
        )
        rng = np.random.default_rng()
        chosen = rng.choice(len(candidates), size=min(2, len(candidates)), replace=False)
        return [_make_pick(candidates[i], _FALLBACK_TYPE) for i in chosen], None

    # --- Route to Modal or local ---
    use_modal = (method == "modal") and modal_bo_enabled()
    modal_warning: str | None = None

    if use_modal:
        try:
            return _run_modal_bo(scored, candidates, xi=xi, higher_is_better=higher_is_better), None
        except Exception as exc:
            logger.warning("run_bo: Modal BO failed (%s) — falling back to local GPR", exc)
            modal_warning = f"Modal GP failed ({type(exc).__name__}: {exc}) — used local GPR"

    return _run_local_bo(scored, candidates, xi=xi, higher_is_better=higher_is_better), modal_warning


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _make_pick(
    cand: dict,
    selection_type: str,
    ei_score: float | None = None,
    gpr_mean: float | None = None,
    gpr_std: float | None = None,
) -> dict:
    return {
        "combination_key": cand["combination_key"],
        "combination": cand["combination"],
        "selection_type": selection_type,
        "ei_score": ei_score,
        "gpr_mean": gpr_mean,
        "gpr_std": gpr_std,
    }

"""
Bayesian Optimisation pipeline for ad combination selection.

Given a seed ad and a source of text combinations, selects two candidate
combinations to test next: one via Expected Improvement, one via a fantasy
step (batch BO second pick).

Scored observations come from ad_generation_variants (image variants that have
been model-scored).  Candidates are text combinations from
ad_text_combination_embeddings paired with the seed ad's image embedding.

Per-ad constraint: seed_ad_id and text_source_id must refer to the same ad.
The caller is responsible for providing consistent identifiers.

Higher score = better ad (consistent with the 1–7 rating scale in gpr_pipeline.py).
If your scorer inverts this, negate scores before calling or pass higher_is_better=False.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ad_embedding_combiner import combine
from bo_pipeline.gpr import MIN_TRAINING_POINTS, expected_improvement, fantasize, fit_gpr, predict_with_std
from bo_pipeline.selector import get_candidate_combinations, get_scored_combinations
from bo_pipeline.storage import DB_PATH

logger = logging.getLogger(__name__)

_FALLBACK_TYPE = "random"
_EI_TYPE = "ei"
_FANTASY_TYPE = "fantasy"


def _build_X(combinations: list[dict]) -> np.ndarray:
    """Stack combined embeddings for a list of combination dicts."""
    return np.vstack([combine(c["text_vector"], c["image_vector"]) for c in combinations])


def run_bo(
    seed_ad_id: str,
    text_source_id: str,
    user_id: int,
    db_path: Path = DB_PATH,
    xi: float = 0.01,
    higher_is_better: bool = True,
) -> list[dict]:
    """
    Select up to 2 combinations to test next via GPR-based Bayesian Optimisation.

    Returns a list of 1 or 2 dicts, each with:
      combination_key  — str
      combination      — dict (text slot values)
      selection_type   — 'ei' | 'fantasy' | 'random'
      ei_score         — float | None
      gpr_mean         — float | None
      gpr_std          — float | None

    Falls back to random selection when there are fewer than MIN_TRAINING_POINTS
    scored combinations available.
    """
    scored = get_scored_combinations(seed_ad_id, text_source_id, user_id, db_path)
    scored_keys = {s["combination_key"] for s in scored}
    candidates = get_candidate_combinations(
        text_source_id, seed_ad_id, user_id, exclude_keys=scored_keys, db_path=db_path
    )

    logger.info(
        "run_bo: seed_ad_id=%s scored=%d candidates=%d",
        seed_ad_id, len(scored), len(candidates),
    )

    if not candidates:
        logger.warning("run_bo: no candidates available for seed_ad_id=%s", seed_ad_id)
        return []

    # --- Fallback: not enough data to fit a reliable GPR ---
    if len(scored) < MIN_TRAINING_POINTS:
        logger.info("run_bo: insufficient scored data (%d < %d) — random fallback", len(scored), MIN_TRAINING_POINTS)
        rng = np.random.default_rng()
        chosen = rng.choice(len(candidates), size=min(2, len(candidates)), replace=False)
        return [_make_pick(candidates[i], _FALLBACK_TYPE) for i in chosen]

    # --- Build training arrays ---
    y = np.array([s["score"] for s in scored], dtype=np.float64)
    if not higher_is_better:
        y = -y
    y_best = float(y.max())

    X_train = _build_X(scored).astype(np.float64)
    X_cands = _build_X(candidates).astype(np.float64)

    # --- Fit GPR ---
    gpr, scaler = fit_gpr(X_train, y)

    # --- Pick 1: highest Expected Improvement ---
    ei_scores = expected_improvement(gpr, scaler, X_cands, y_best, xi=xi)
    pick1_idx = int(np.argmax(ei_scores))
    pick1_cand = candidates[pick1_idx]
    mu1, sigma1 = predict_with_std(gpr, scaler, X_cands[[pick1_idx]])
    pick1 = _make_pick(
        pick1_cand,
        _EI_TYPE,
        ei_score=float(ei_scores[pick1_idx]),
        gpr_mean=float(mu1[0]) if not higher_is_better else float(mu1[0]),
        gpr_std=float(sigma1[0]),
    )

    if len(candidates) == 1:
        return [pick1]

    # --- Pick 2: fantasy step ---
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

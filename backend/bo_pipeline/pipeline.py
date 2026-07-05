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

Scored observations come from scored_observations (written by the seed script,
Qwen2-VL pipeline, or convergence checking).  Candidates are text combinations
from ad_text_combination_embeddings paired with the seed ad's image embedding.

For single-ad runs, seed_ad_id and text_source_id refer to the same ad.
For multi-member generators, pass seed_ad_ids / text_source_ids / image_ad_ids
to merge asset pools across multiple dynamic ads.

Higher score = better ad (consistent with the 1–7 rating scale).
If your scorer inverts this, negate scores before calling or pass higher_is_better=False.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ad_embedding_combiner import combine
from bo_pipeline.gpr import MIN_TRAINING_POINTS, confidence_label, expected_improvement, fantasize, fit_gpr, predict_with_std, transform_y
from bo_pipeline.selector import get_candidate_combinations, get_scored_combinations
from bo_pipeline.storage import DB_PATH

logger = logging.getLogger(__name__)

_FALLBACK_TYPE  = "random"
_EI_TYPE        = "ei"
_FANTASY_TYPE   = "fantasy"
_MODAL_TYPE     = "modal_q_ei"


def _build_X(combinations: list[dict]) -> np.ndarray:
    """Stack feature vectors for a list of combination dicts.

    Text-only (1536-dim) when no image vector is present; text+image concat
    (3072-dim) otherwise.  Determined by data, not platform name.
    """
    if combinations[0].get("image_vector") is None:
        return np.vstack([c["text_vector"] for c in combinations])
    return np.vstack([combine(c["text_vector"], c["image_vector"]) for c in combinations])


# ---------------------------------------------------------------------------
# _expl_* helpers — explainability only, never used in BO selection decisions
# ---------------------------------------------------------------------------
# Functions prefixed _expl_ compute statistics purely for UI display.
# They must not influence which candidates are chosen.

def _expl_combo_label(combination: dict) -> str:
    """Short human-readable label from a combination's text slots."""
    for key in ("headline", "primary_text", "description"):
        val = combination.get(key)
        if val:
            s = str(val)
            return s if len(s) <= 50 else s[:50] + "…"
    # Fallback for arbitrary slot names (e.g. manual platform)
    for val in combination.values():
        if val:
            s = str(val)
            return s if len(s) <= 50 else s[:50] + "…"
    return "—"


def _expl_nearest_known(
    pick_vec: np.ndarray,
    scored: list[dict],
    X_scored: np.ndarray,
    k: int = 3,
) -> list[dict]:
    """
    Top-k scored observations nearest to pick_vec by cosine distance.
    Operates in the original (pre-PCA) embedding space.
    """
    if not scored:
        return []
    a = pick_vec.ravel().astype(np.float64)
    na = float(np.linalg.norm(a))
    out = []
    for i, s in enumerate(scored):
        b = X_scored[i].ravel().astype(np.float64)
        nb = float(np.linalg.norm(b))
        dist = 1.0 if (na == 0 or nb == 0) else float(1.0 - np.dot(a, b) / (na * nb))
        out.append({
            "label": _expl_combo_label(s["combination"]),
            "score": float(s["score"]),
            "cosine_distance": round(dist, 4),
            "combination": s["combination"],
        })
    out.sort(key=lambda x: x["cosine_distance"])
    return out[:k]


def _expl_local_gp_stats(
    picked_indices: list[int],
    X_train: np.ndarray,
    y_transformed: np.ndarray,
    X_cands: np.ndarray,
    xi: float = 0.01,
) -> list[dict]:
    """
    Fit a local GPR on PCA-projected data to get per-pick GP statistics for display.

    The actual candidate selection was performed by Modal q-EI — this local GPR
    exists solely so the UI can show Probable Score, Uncertainty, and univariate EI
    next to each recommendation. It does not affect which candidates were chosen.
    """
    try:
        gpr, scaler = fit_gpr(X_train.astype(np.float64), y_transformed.astype(np.float64))
        y_best = float(y_transformed.max())
        out = []
        for idx in picked_indices:
            x = X_cands[[idx]].astype(np.float64)
            mu, sigma = predict_with_std(gpr, scaler, x)
            ei = expected_improvement(gpr, scaler, x, y_best, xi=xi)
            out.append({"gpr_mean": float(mu[0]), "gpr_std": float(sigma[0]), "ei_score": float(ei[0])})
        logger.info("_expl_local_gp_stats [pipeline]: y_best=%.4f results=%s", y_best, out)
        return out
    except Exception:
        logger.exception("_expl_local_gp_stats [pipeline]: failed for indices=%s X_train.shape=%s", picked_indices, X_train.shape)
        return [{"gpr_mean": None, "gpr_std": None, "ei_score": None}] * len(picked_indices)


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
        nearest_known=_expl_nearest_known(X_cands[pick1_idx], scored, X_train),
    )

    if len(candidates) == 1:
        return [pick1]

    # Pick 2: fantasy step
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
        nearest_known=_expl_nearest_known(X_cands[remaining_idx[pick2_idx]], scored, X_train),
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
) -> tuple[list[dict], str | None]:
    """
    PCA → Modal GP q-EI → nearest-pool-member snap.

    Returns (picks, pca_warning) — picks in the same format as _run_local_bo,
    pca_warning set (T12, not yet signed off) when PCA got capped below its
    requested dimensionality.
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
    pca, X_all_pca, pca_warning = fit_pca(X_all)
    X_train_pca = X_all_pca[: len(scored)]
    X_cands_pca = X_all_pca[len(scored) :]

    # Call Modal GP service — raises on failure.
    # Sends the actual discrete candidate vectors; Modal selects via q-EI and
    # returns indices directly — no snap-to-pool step needed.
    suggestions = call_modal_api(
        api_url=api_url,
        X=X_train_pca,
        y=y,
        candidates=X_cands_pca,
        q=min(2, len(candidates)),
        xi=xi,
    )

    # Deduplicate Modal suggestions while preserving rank order.
    ordered_indices: list[int] = []
    seen_indices: set[int] = set()
    for suggestion in suggestions:
        idx = suggestion["index"]
        if idx not in seen_indices:
            seen_indices.add(idx)
            ordered_indices.append(idx)

    # EXPLAINABILITY: fit a local GPR on the PCA-projected data to get per-pick
    # GP statistics for the UI (Probable Score, Uncertainty, univariate EI).
    # Modal q-EI already chose these candidates — the local GPR only provides display values.
    y_expl = transform_y(y_raw.astype(np.float64))
    expl_stats = _expl_local_gp_stats(ordered_indices, X_train_pca, y_expl, X_cands_pca, xi)

    picks = []
    for i, idx in enumerate(ordered_indices):
        expl = expl_stats[i]
        picks.append(_make_pick(
            candidates[idx], _MODAL_TYPE,
            ei_score=expl["ei_score"],
            gpr_mean=expl["gpr_mean"],
            gpr_std=expl["gpr_std"],
            nearest_known=_expl_nearest_known(X_cands[idx], scored, X_scored),
        ))

    return picks, pca_warning


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
    platform: str = "meta",
    additional_exclude_keys: set[str] | None = None,
    target_metric: str | None = None,
    seed_ad_ids: list[str] | None = None,
    text_source_ids: list[str] | None = None,
    image_ad_ids: list[str] | None = None,
) -> tuple[list[dict], str | None, int, int]:
    """
    Select up to 2 combinations to test next via Bayesian Optimisation.

    For single-ad runs: seed_ad_id and text_source_id identify the ad.
    For multi-member generators: seed_ad_ids / text_source_ids / image_ad_ids
    override the single-ad params and merge pools across all member ads.

    method="modal"  (default) — uses the Modal GP service with PCA preprocessing
                                and proper batch q-EI; falls back to "local" if
                                MODAL_BO_API_URL is unset or the API call fails.
    method="local"            — local sklearn GPR + EI + fantasy step; no network.

    Returns (picks, warning, scored_count, candidate_count) where:
      picks           — list of 1 or 2 dicts, each with:
                          combination_key, combination, selection_type,
                          ei_score, gpr_mean, gpr_std
      warning         — non-None string when Modal was configured but failed and the
                        response fell back to the local GPR; None otherwise.
      scored_count    — number of scored combinations found
      candidate_count — number of candidate combinations found

    Falls back to random selection when there are fewer than MIN_TRAINING_POINTS
    scored combinations available.
    """
    from bo_pipeline.modal_bo import modal_bo_enabled

    scored = get_scored_combinations(
        seed_ad_id, text_source_id, user_id, db_path,
        target_metric=target_metric,
        seed_ad_ids=seed_ad_ids,
    )
    scored_keys = {s["combination_key"] for s in scored}
    all_exclude = scored_keys | (additional_exclude_keys or set())
    candidates = get_candidate_combinations(
        text_source_id, seed_ad_id, user_id,
        exclude_keys=all_exclude, db_path=db_path,
        text_source_ids=text_source_ids,
        image_ad_ids=image_ad_ids,
    )
    scored_count = len(scored)
    candidate_count = len(candidates)

    logger.info(
        "run_bo: seed_ad_id=%s scored=%d candidates=%d method=%s",
        seed_ad_id, scored_count, candidate_count, method,
    )

    if not candidates:
        logger.warning("run_bo: no candidates available for seed_ad_id=%s", seed_ad_id)
        return [], None, scored_count, candidate_count

    # --- Fallback: not enough data to fit a reliable model ---
    if len(scored) < MIN_TRAINING_POINTS:
        logger.info(
            "run_bo: insufficient scored data (%d < %d) — random fallback",
            len(scored), MIN_TRAINING_POINTS,
        )
        rng = np.random.default_rng()
        chosen = rng.choice(len(candidates), size=min(2, len(candidates)), replace=False)
        return [_make_pick(candidates[i], _FALLBACK_TYPE) for i in chosen], None, scored_count, candidate_count

    # --- Route to Modal or local ---
    use_modal = (method == "modal") and modal_bo_enabled()
    modal_warning: str | None = None

    if use_modal:
        try:
            picks, pca_warning = _run_modal_bo(scored, candidates, xi=xi, higher_is_better=higher_is_better)
            return picks, pca_warning, scored_count, candidate_count
        except Exception as exc:
            logger.warning("run_bo: Modal BO failed (%s) — falling back to local GPR", exc)
            modal_warning = f"Modal GP failed ({type(exc).__name__}: {exc}) — used local GPR"

    return _run_local_bo(scored, candidates, xi=xi, higher_is_better=higher_is_better), modal_warning, scored_count, candidate_count


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _make_pick(
    cand: dict,
    selection_type: str,
    ei_score: float | None = None,
    gpr_mean: float | None = None,
    gpr_std: float | None = None,
    nearest_known: list[dict] | None = None,
) -> dict:
    nearest_known = nearest_known if nearest_known is not None else []
    return {
        "combination_key": cand["combination_key"],
        "combination": cand["combination"],
        "selection_type": selection_type,
        "ei_score": ei_score,
        "gpr_mean": gpr_mean,
        "gpr_std": gpr_std,
        "nearest_known": nearest_known,
        "confidence": confidence_label(gpr_std, nearest_known),
    }

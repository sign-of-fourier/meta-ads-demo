"""
Cross-platform Bayesian Optimisation.

Runs BO jointly over Meta and Google (or any mix of platforms) with:

  - A **shared ECDF** fit on the combined score pool from all platforms.
    Both platforms' GPR targets are on the same [0,1] → standard-normal scale,
    so their EI values are directly comparable.

  - **Per-platform PCA + GPR**.  Meta and Google have different input spaces:
      Meta:   combine(text_vec, image_vec) → 3072-dim → PCA → 64-dim  (MODAL_BO_PCA_DIMS)
      Google: text_vec only               → 1536-dim → PCA → 32-dim  (GOOGLE_BO_PCA_DIMS)
    The different output lengths make it physically impossible to mix the two
    groups into a single GPR accidentally.

  - **Global EI ranking**.  After both GPRs compute EI over their candidate
    pools, all picks are sorted by EI descending and the top-N are returned,
    each tagged with the originating platform.

Two methods are available (controlled by the ``method`` kwarg):

  ``"local"``  — local sklearn GPR + EI + fantasy step per group; no network.
  ``"modal"``  — PCA → Modal q-EI endpoint per group (two independent calls,
                 one per platform); requires MODAL_BO_API_URL.  Falls back to
                 ``"local"`` per-group if the API call fails.  The ECDF
                 transform happens client-side before any network call,
                 same as the single-platform Modal path.

Usage
-----
    from bo_pipeline.cross_platform import run_cross_platform_bo

    picks = run_cross_platform_bo(
        pairs=[
            {"platform": "meta",   "seed_ad_id": "meta_ad_1",   "text_source_id": "meta_ad_1"},
            {"platform": "google", "seed_ad_id": "google_ad_1", "text_source_id": "google_ad_1"},
        ],
        user_id=1,
        db_path=db_path,
    )

Each returned pick dict has the standard run_bo() fields plus:
    platform       — "meta" | "google"
    seed_ad_id     — which ad this pick belongs to
    text_source_id — which text source this pick came from

Environment variables
---------------------
MODAL_BO_PCA_DIMS   — PCA output dims for Meta groups (default 64)
GOOGLE_BO_PCA_DIMS  — PCA output dims for Google groups (default 32)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ad_embedding_combiner import combine
from bo_pipeline.ecdf import fit_ecdf
from bo_pipeline.gpr import (
    MIN_TRAINING_POINTS,
    expected_improvement,
    fantasize,
    fit_gpr,
    predict_with_std,
)
from bo_pipeline.modal_bo import fit_pca, pca_dims_for_platform
from bo_pipeline.selector import get_candidate_combinations, get_scored_combinations
from bo_pipeline.storage import DB_PATH

logger = logging.getLogger(__name__)

_FALLBACK_TYPE = "random"
_EI_TYPE = "ei"
_FANTASY_TYPE = "fantasy"
_MODAL_TYPE = "modal_q_ei"

# Sentinel used for global EI ranking: random picks sort last.
_RANDOM_SORT_KEY = -1.0


# ─────────────────────────────────────────────────────────────────────────────
# BOGroup — data + platform-specific X construction
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BOGroup:
    """
    All data for one platform's contribution to a cross-platform BO run.

    ``platform`` controls how build_X assembles the feature matrix:
      "google"        — text_vector only (1536-dim raw; PCA → GOOGLE_BO_PCA_DIMS)
      anything else   — combine(text_vector, image_vector) (3072-dim raw; PCA → MODAL_BO_PCA_DIMS)

    The different raw dimensions mean the two groups will always produce
    different-length feature vectors, making it physically impossible to mix
    them into a single GPR.
    """

    platform: str
    seed_ad_id: str
    text_source_id: str
    scored: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)

    @property
    def pca_dims(self) -> int:
        """PCA output dimension for this group's platform."""
        return pca_dims_for_platform(self.platform)

    def build_X(self, observations: list[dict]) -> np.ndarray:
        """
        Build the raw (pre-PCA) feature matrix for a list of observation dicts.

        Each dict must have ``text_vector`` (np.ndarray float32) and optionally
        ``image_vector`` (np.ndarray float32 | None).

        Google:      stacks text_vector rows                  → (N, 1536)
        Other (Meta): stacks combine(text_vec, image_vec) rows → (N, 3072)
        """
        if self.platform == "google":
            return np.vstack(
                [obs["text_vector"] for obs in observations]
            ).astype(np.float32)
        return np.vstack(
            [combine(obs["text_vector"], obs["image_vector"]) for obs in observations]
        ).astype(np.float32)

    def build_X_unified(self, observations: list[dict]) -> np.ndarray:
        """
        Build a 3072-dim feature matrix for all platforms.

        Google RSA ads have no image embedding; image half is zero-padded via
        combine(text_vec, None).  This produces the same output dimension as
        Meta so all groups can be pooled into a single shared PCA.
        """
        return np.vstack(
            [combine(obs["text_vector"], obs.get("image_vector")) for obs in observations]
        ).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_pick(
    cand: dict,
    selection_type: str,
    platform: str,
    seed_ad_id: str,
    text_source_id: str,
    ei_score: float | None = None,
    gpr_mean: float | None = None,
    gpr_std: float | None = None,
    _sort_key: float = _RANDOM_SORT_KEY,
) -> dict:
    """Build a pick dict.  _sort_key is stripped before the public return."""
    return {
        "combination_key": cand["combination_key"],
        "combination": cand["combination"],
        "selection_type": selection_type,
        "ei_score": ei_score,
        "gpr_mean": gpr_mean,
        "gpr_std": gpr_std,
        "platform": platform,
        "seed_ad_id": seed_ad_id,
        "text_source_id": text_source_id,
        "_sort_key": _sort_key,
    }


def _random_picks(group: BOGroup, n: int = 2) -> list[dict]:
    """Return up to n randomly chosen candidates from group, tagged as random."""
    rng = np.random.default_rng()
    size = min(n, len(group.candidates))
    chosen = rng.choice(len(group.candidates), size=size, replace=False)
    return [
        _make_pick(
            group.candidates[int(i)],
            _FALLBACK_TYPE,
            group.platform,
            group.seed_ad_id,
            group.text_source_id,
            _sort_key=_RANDOM_SORT_KEY,
        )
        for i in chosen
    ]


def _run_group_bo(
    group: BOGroup,
    ecdf_transform,         # callable returned by fit_ecdf
    xi: float,
    sign: float = 1.0,      # 1.0 = higher-is-better; -1.0 = flip for minimisation
) -> list[dict]:
    """
    Run GPR + EI (+ fantasy pick) for one group using the shared ECDF transform.

    PCA is always applied here (unlike the single-platform local path) because:
      a) it matches the Modal path behaviour that the cross-platform run mirrors
      b) it enforces different output dimensions for Meta (64) vs Google (32)
         so the two groups can never be confused

    Falls back to random if the group has fewer than MIN_TRAINING_POINTS scored
    observations or an empty candidate pool.
    """
    if not group.candidates:
        return []

    if len(group.scored) < MIN_TRAINING_POINTS:
        logger.info(
            "cross_platform_bo[%s]: insufficient scored data (%d < %d) — random fallback",
            group.platform, len(group.scored), MIN_TRAINING_POINTS,
        )
        return _random_picks(group)

    # ── Targets: apply shared ECDF to this group's (possibly sign-flipped) scores ──
    y_raw = np.array([s["score"] * sign for s in group.scored], dtype=np.float64)
    y = ecdf_transform(y_raw)
    y_best = float(y.max())

    # ── Features: build raw X then PCA-reduce ──────────────────────────────────
    X_scored_raw = group.build_X(group.scored).astype(np.float32)
    X_cands_raw = group.build_X(group.candidates).astype(np.float32)

    # Fit PCA on union of scored + candidates so the projection captures the
    # full candidate space (same strategy as _run_modal_bo in pipeline.py).
    X_all = np.vstack([X_scored_raw, X_cands_raw])
    _, X_all_pca = fit_pca(X_all, n_components=group.pca_dims)
    X_train_pca = X_all_pca[: len(group.scored)].astype(np.float64)
    X_cands_pca = X_all_pca[len(group.scored) :].astype(np.float64)

    # ── GPR fit ────────────────────────────────────────────────────────────────
    gpr, scaler = fit_gpr(X_train_pca, y)

    # ── Pick 1: highest EI ─────────────────────────────────────────────────────
    ei_scores = expected_improvement(gpr, scaler, X_cands_pca, y_best, xi=xi)
    p1_idx = int(np.argmax(ei_scores))
    mu1, sigma1 = predict_with_std(gpr, scaler, X_cands_pca[[p1_idx]])
    ei1 = float(ei_scores[p1_idx])
    picks = [
        _make_pick(
            group.candidates[p1_idx],
            _EI_TYPE,
            group.platform,
            group.seed_ad_id,
            group.text_source_id,
            ei_score=ei1,
            gpr_mean=float(mu1[0]),
            gpr_std=float(sigma1[0]),
            _sort_key=ei1,
        )
    ]

    if len(group.candidates) == 1:
        return picks

    # ── Pick 2: fantasy step ───────────────────────────────────────────────────
    gpr2, scaler2 = fantasize(gpr, scaler, X_train_pca, y, X_cands_pca[[p1_idx]])
    remaining_idx = [i for i in range(len(group.candidates)) if i != p1_idx]
    X_remaining = X_cands_pca[remaining_idx]
    cands_remaining = [group.candidates[i] for i in remaining_idx]

    ei_scores2 = expected_improvement(gpr2, scaler2, X_remaining, y_best, xi=xi)
    p2_idx = int(np.argmax(ei_scores2))
    mu2, sigma2 = predict_with_std(gpr2, scaler2, X_remaining[[p2_idx]])
    ei2 = float(ei_scores2[p2_idx])
    picks.append(
        _make_pick(
            cands_remaining[p2_idx],
            _FANTASY_TYPE,
            group.platform,
            group.seed_ad_id,
            group.text_source_id,
            ei_score=ei2,
            gpr_mean=float(mu2[0]),
            gpr_std=float(sigma2[0]),
            _sort_key=ei2,
        )
    )

    return picks


def _run_group_modal_bo(
    group: BOGroup,
    ecdf_transform,         # callable returned by fit_ecdf
    xi: float,
    sign: float = 1.0,
) -> list[dict]:
    """
    Modal q-EI path for one group.

    Applies the shared ECDF transform client-side (same as the local path),
    PCA-reduces to group.pca_dims, then calls the Modal GP endpoint with q=2.
    Falls back to _run_group_bo on any API error so the overall run never
    silently loses picks.

    Returned picks carry ``selection_type="modal_q_ei"`` and
    ``ei_score=gpr_mean=gpr_std=None`` (the batch q-EI endpoint does not
    return per-pick EI decompositions).  Sort keys are rank-based so the
    first Modal suggestion sorts above the second, and all Modal picks sort
    above random-fallback picks.
    """
    from bo_pipeline.modal_bo import (
        _api_url,
        call_modal_api,
        dim_bounds,
        snap_to_pool,
    )

    if not group.candidates:
        return []

    if len(group.scored) < MIN_TRAINING_POINTS:
        logger.info(
            "cross_platform_bo[%s]: insufficient scored data (%d < %d) — random fallback",
            group.platform, len(group.scored), MIN_TRAINING_POINTS,
        )
        return _random_picks(group)

    # Apply shared ECDF to this group's (possibly sign-flipped) scores
    y_raw = np.array([s["score"] * sign for s in group.scored], dtype=np.float64)
    y = ecdf_transform(y_raw).astype(np.float32)

    # PCA-reduce (fit on scored ∪ candidates to capture the full space)
    X_scored_raw = group.build_X(group.scored).astype(np.float32)
    X_cands_raw  = group.build_X(group.candidates).astype(np.float32)
    X_all        = np.vstack([X_scored_raw, X_cands_raw])

    _, X_all_pca = fit_pca(X_all, n_components=group.pca_dims)
    X_train_pca  = X_all_pca[: len(group.scored)]
    X_cands_pca  = X_all_pca[len(group.scored) :]

    bounds = dim_bounds(X_all_pca)

    suggestions_pca = call_modal_api(
        api_url=_api_url(),
        X_train_pca=X_train_pca,
        y=y,
        bounds=bounds,
        q=min(2, len(group.candidates)),
        xi=xi,
    )

    picked_indices = snap_to_pool(suggestions_pca, X_cands_pca, group.candidates)

    # Rank-based sort keys: first suggestion > second > random (_RANDOM_SORT_KEY = -1.0)
    n_picks = len(picked_indices)
    picks = []
    for rank, idx in enumerate(picked_indices):
        picks.append(_make_pick(
            group.candidates[idx],
            _MODAL_TYPE,
            group.platform,
            group.seed_ad_id,
            group.text_source_id,
            ei_score=None,
            gpr_mean=None,
            gpr_std=None,
            _sort_key=float(n_picks - rank),
        ))

    return picks


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_cross_platform_bo(
    pairs: list[dict],
    user_id: int,
    db_path: Path = DB_PATH,
    xi: float = 0.01,
    higher_is_better: bool = True,
    top_n: int = 2,
    method: str = "modal",
    target_metric: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Run cross-platform BO across all (platform, seed_ad_id, text_source_id) pairs.

    Parameters
    ----------
    pairs : list of dicts, each with keys:
              platform       — "meta" | "google"
              seed_ad_id     — seed ad identifier
              text_source_id — text combination source identifier
    user_id : authenticated user
    db_path : SQLite DB path
    xi      : EI exploration-exploitation trade-off (default 0.01)
    higher_is_better : set False to minimise the raw score (rare; see note below)
    top_n   : maximum number of picks to return across all platforms (default 2)
    method  : ``"local"`` (default) or ``"modal"``.  ``"modal"`` calls the Modal
              GP endpoint per group; requires MODAL_BO_API_URL; falls back to
              local per-group on API failure.

    Returns
    -------
    list of pick dicts (length ≤ top_n), each containing the standard run_bo()
    fields plus:
        platform       — "meta" | "google"
        seed_ad_id     — which ad this pick belongs to
        text_source_id — which text source this pick came from

    Picks are ordered by EI descending (best first).  Random-fallback picks
    (insufficient scored data) sort below any real EI pick and are only
    included when needed to fill top_n.

    Note on higher_is_better=False
    --------------------------------
    When False, raw scores are negated before ECDF fitting and before GPR
    targets are computed.  The GPR then maximises the negated objective,
    which is equivalent to minimising the original.  This branch is present
    for completeness; all current call sites use higher_is_better=True.
    """
    sign = 1.0 if higher_is_better else -1.0

    # ── 1. Load scored observations and candidates for each group ─────────────
    groups: list[BOGroup] = []
    group_stats: list[dict] = []
    for p in pairs:
        platform = p["platform"]
        seed_ad_id = p["seed_ad_id"]
        text_source_id = p["text_source_id"]

        scored = get_scored_combinations(seed_ad_id, text_source_id, user_id, db_path, target_metric=target_metric)
        scored_keys = {s["combination_key"] for s in scored}
        candidates = get_candidate_combinations(
            text_source_id, seed_ad_id, user_id,
            exclude_keys=scored_keys,
            db_path=db_path,
        )

        g = BOGroup(
            platform=platform,
            seed_ad_id=seed_ad_id,
            text_source_id=text_source_id,
            scored=scored,
            candidates=candidates,
        )
        groups.append(g)
        group_stats.append({
            "platform": platform,
            "seed_ad_id": seed_ad_id,
            "scored_count": len(scored),
            "candidate_count": len(candidates),
        })
        logger.info(
            "cross_platform_bo[%s]: seed=%s scored=%d candidates=%d pca_dims=%d",
            platform, seed_ad_id, len(scored), len(candidates), g.pca_dims,
        )

    # ── 2. Fit shared ECDF on the combined (sign-adjusted) score pool ─────────
    all_raw_scores = [s["score"] * sign for g in groups for s in g.scored]

    if all_raw_scores:
        ecdf_transform = fit_ecdf(all_raw_scores)
    else:
        # No scored data anywhere — fall back to random for all groups.
        ecdf_transform = None
        logger.info("cross_platform_bo: no scored data across all groups — random fallback")

    # ── 3. Run per-group BO using the shared ECDF ─────────────────────────────
    from bo_pipeline.modal_bo import modal_bo_enabled
    use_modal = method == "modal" and modal_bo_enabled()
    if use_modal:
        logger.info("cross_platform_bo: using Modal q-EI path")

    all_picks: list[dict] = []
    for g in groups:
        if not g.candidates:
            continue
        if ecdf_transform is None:
            all_picks.extend(_random_picks(g))
        elif use_modal:
            try:
                all_picks.extend(_run_group_modal_bo(g, ecdf_transform, xi=xi, sign=sign))
            except Exception as exc:
                logger.warning(
                    "cross_platform_bo[%s]: Modal API failed (%s) — local GPR fallback",
                    g.platform, exc,
                )
                all_picks.extend(_run_group_bo(g, ecdf_transform, xi=xi, sign=sign))
        else:
            all_picks.extend(_run_group_bo(g, ecdf_transform, xi=xi, sign=sign))

    if not all_picks:
        return [], group_stats

    # ── 4. Global ranking: sort by EI descending, return top_n ───────────────
    all_picks.sort(key=lambda p: p["_sort_key"], reverse=True)
    result = all_picks[:top_n]

    # Strip the internal sort key before returning to callers.
    for pick in result:
        pick.pop("_sort_key", None)

    return result, group_stats


# ─────────────────────────────────────────────────────────────────────────────
# Unified cross-platform BO — single PCA space, single GP call
# ─────────────────────────────────────────────────────────────────────────────

def run_unified_cross_platform_bo(
    pairs: list[dict],
    user_id: int,
    db_path: Path = DB_PATH,
    xi: float = 0.01,
    higher_is_better: bool = True,
    top_n: int = 4,
    method: str = "modal",
    target_metric: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Unified cross-platform BO: per-group PCA each to the same output dimension
    (MODAL_BO_PCA_DIMS for all platforms), then pool all projected vectors into
    a single GP/Modal call.  Returns globally-ranked top_n picks.

    Key differences from run_cross_platform_bo:
      - Both Meta (3072-dim raw) and Google (1536-dim raw) are PCA'd to the
        same K-dim output so they can be pooled into one matrix.
      - A single GP is fit on the pooled scored observations; a single EI pass
        ranks all candidates (Meta + Google) together.
      - For the local path, the fantasy loop generalises to top_n picks
        (one EI pick + top_n-1 sequential fantasy steps).
      - For the Modal path, one q=top_n call is made on the pooled matrices.

    The two platforms will naturally cluster in different sub-regions of the
    K-dim space (Meta's PCA captures text+image variance; Google's captures
    text-only variance), so the GP learns separate response surfaces per
    platform while sharing kernel hyperparameters — a practical win when
    scored observations are few on either side.
    """
    from bo_pipeline.modal_bo import fit_pca, modal_bo_enabled, _api_url, pca_dims_for_platform

    sign = 1.0 if higher_is_better else -1.0
    # All groups PCA to the same dimension for pooling.
    K = pca_dims_for_platform("meta")  # MODAL_BO_PCA_DIMS (default 64)

    # ── 1. Load data and build group_stats ────────────────────────────────────
    groups: list[BOGroup] = []
    group_stats: list[dict] = []
    for p in pairs:
        platform   = p["platform"]
        seed_ad_id = p["seed_ad_id"]
        text_source_id = p["text_source_id"]

        scored = get_scored_combinations(seed_ad_id, text_source_id, user_id, db_path, target_metric=target_metric)
        scored_keys = {s["combination_key"] for s in scored}
        candidates = get_candidate_combinations(
            text_source_id, seed_ad_id, user_id,
            exclude_keys=scored_keys,
            db_path=db_path,
        )
        groups.append(BOGroup(
            platform=platform,
            seed_ad_id=seed_ad_id,
            text_source_id=text_source_id,
            scored=scored,
            candidates=candidates,
        ))
        group_stats.append({
            "platform": platform,
            "seed_ad_id": seed_ad_id,
            "scored_count": len(scored),
            "candidate_count": len(candidates),
        })
        logger.info(
            "unified_cross_platform_bo[%s]: seed=%s scored=%d candidates=%d",
            platform, seed_ad_id, len(scored), len(candidates),
        )

    # ── 2. Fit shared ECDF on the combined score pool ─────────────────────────
    all_raw_scores = [s["score"] * sign for g in groups for s in g.scored]
    ecdf_transform = fit_ecdf(all_raw_scores) if all_raw_scores else None

    # ── 3. Build 3072-dim raw vectors for all groups, then fit ONE shared PCA ──
    # Google ads use combine(text_vec, None) → 3072-dim (image half zero-padded).
    # Meta ads use combine(text_vec, image_vec) → 3072-dim.
    # A single PCA over the pooled union captures variance across both platforms;
    # EI scores from the resulting GP are in one comparable latent space.
    scored_raw_rows: list[np.ndarray] = []   # 3072-dim scored obs, all groups
    cands_raw_rows: list[np.ndarray]  = []   # 3072-dim candidates, all groups
    scored_meta: list[dict]           = []   # flattened scored dicts (for y)
    cands_meta: list[dict]            = []   # flattened candidate dicts (for picks)

    # Track per-group candidate offsets so we can map GP indices → platform.
    group_cand_offsets: list[tuple[int, int, BOGroup]] = []
    offset = 0

    for g in groups:
        if not g.candidates:
            group_cand_offsets.append((offset, offset, g))
            continue

        X_scored_raw = g.build_X_unified(g.scored).astype(np.float32) if g.scored else None
        X_cands_raw  = g.build_X_unified(g.candidates).astype(np.float32)

        if X_scored_raw is not None:
            scored_raw_rows.append(X_scored_raw)
            scored_meta.extend(g.scored)

        cands_raw_rows.append(X_cands_raw)
        cands_meta.extend(g.candidates)
        group_cand_offsets.append((offset, offset + len(g.candidates), g))
        offset += len(g.candidates)

    if not cands_meta:
        return [], group_stats

    # ── 4. Handle no scored data — random fallback for all ───────────────────
    if ecdf_transform is None or not scored_meta:
        logger.info("unified_cross_platform_bo: no scored data — random fallback")
        rng = np.random.default_rng()
        chosen = rng.choice(len(cands_meta), size=min(top_n, len(cands_meta)), replace=False)
        picks = []
        for idx in chosen:
            g = next(g for start, end, g in group_cand_offsets if start <= idx < end)
            local_idx = idx - next(start for start, end, grp in group_cand_offsets if grp is g)
            picks.append(_make_pick(g.candidates[local_idx], _FALLBACK_TYPE, g.platform, g.seed_ad_id, g.text_source_id, _sort_key=_RANDOM_SORT_KEY))
        for p in picks:
            p.pop("_sort_key", None)
        return picks, group_stats

    # Fit ONE shared PCA on the union of all scored + candidate raw vectors.
    # This ensures the GP operates in a single latent space where Meta and Google
    # embeddings (both 3072-dim) are jointly projected and directly comparable.
    X_all_raw = np.vstack(scored_raw_rows + cands_raw_rows).astype(np.float32)
    n_scored_total = sum(r.shape[0] for r in scored_raw_rows)
    n_components = min(K, X_all_raw.shape[0], X_all_raw.shape[1])
    _, X_all_pca = fit_pca(X_all_raw, n_components=n_components)

    X_scored_pool = X_all_pca[:n_scored_total].astype(np.float64)
    X_cands_pool_raw = X_all_pca[n_scored_total:].astype(np.float64)

    # Pad to K if the shared PCA produced fewer components than K (small datasets).
    def _pad_to_K(arr: np.ndarray) -> np.ndarray:
        if arr.shape[1] == K:
            return arr
        pad = np.zeros((arr.shape[0], K - arr.shape[1]), dtype=arr.dtype)
        return np.hstack([arr, pad])

    X_scored_pool = _pad_to_K(X_scored_pool)
    X_cands_pool  = _pad_to_K(X_cands_pool_raw)

    y_raw  = np.array([s["score"] * sign for s in scored_meta], dtype=np.float64)
    y_pool = ecdf_transform(y_raw)
    y_best = float(y_pool.max())

    # ── 5. Single GP call ─────────────────────────────────────────────────────
    use_modal = method == "modal" and modal_bo_enabled()
    all_picks: list[dict] = []

    if use_modal:
        from bo_pipeline.modal_bo import call_modal_api
        try:
            suggestions = call_modal_api(
                api_url=_api_url(),
                X_train_pca=X_scored_pool.astype(np.float32),
                y=y_pool.astype(np.float32),
                X_cands_pca=X_cands_pool.astype(np.float32),
                q=min(top_n, len(cands_meta)),
                xi=xi,
            )
            seen: set[int] = set()
            n_picks = len(suggestions)
            for rank, suggestion in enumerate(suggestions):
                idx = suggestion["index"]
                if idx in seen:
                    continue
                seen.add(idx)
                start, end, g = next((s, e, grp) for s, e, grp in group_cand_offsets if s <= idx < e)
                all_picks.append(_make_pick(cands_meta[idx], _MODAL_TYPE, g.platform, g.seed_ad_id, g.text_source_id, _sort_key=float(n_picks - rank)))
        except Exception as exc:
            logger.warning("unified_cross_platform_bo: Modal failed (%s) — local GPR fallback", exc)
            use_modal = False

    if not use_modal:
        gpr, scaler = fit_gpr(X_scored_pool, y_pool)
        ei_scores = expected_improvement(gpr, scaler, X_cands_pool, y_best, xi=xi)
        remaining = list(range(len(cands_meta)))

        for pick_num in range(min(top_n, len(cands_meta))):
            best_local = int(np.argmax(ei_scores[remaining]))
            idx = remaining[best_local]
            start, end, g = next((s, e, grp) for s, e, grp in group_cand_offsets if s <= idx < e)
            mu, sigma = predict_with_std(gpr, scaler, X_cands_pool[[idx]])
            ei_val = float(ei_scores[idx])
            sel_type = _EI_TYPE if pick_num == 0 else _FANTASY_TYPE
            all_picks.append(_make_pick(cands_meta[idx], sel_type, g.platform, g.seed_ad_id, g.text_source_id, ei_score=ei_val, gpr_mean=float(mu[0]), gpr_std=float(sigma[0]), _sort_key=ei_val))
            remaining.pop(best_local)
            if remaining and pick_num < top_n - 1:
                gpr, scaler = fantasize(gpr, scaler, X_scored_pool, y_pool, X_cands_pool[[idx]])

    # ── 6. Global rank and return ─────────────────────────────────────────────
    all_picks.sort(key=lambda p: p["_sort_key"], reverse=True)
    result = all_picks[:top_n]
    for pick in result:
        pick.pop("_sort_key", None)

    return result, group_stats

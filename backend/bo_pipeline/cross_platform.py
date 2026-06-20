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
# _expl_* helpers — explainability only, never used in BO selection decisions
# ─────────────────────────────────────────────────────────────────────────────
# Functions prefixed _expl_ compute statistics purely for UI display.
# They must not influence which candidates are chosen.

def _expl_combo_label(combination: dict) -> str:
    """Short human-readable label from a combination's text slots."""
    for key in ("headline", "primary_text", "description"):
        val = combination.get(key)
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
    """Top-k scored observations nearest to pick_vec by cosine distance (pre-PCA space)."""
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
        logger.info("_expl_local_gp_stats [cross]: y_best=%.4f results=%s", y_best, out)
        return out
    except Exception:
        logger.exception("_expl_local_gp_stats [cross]: failed for indices=%s X_train.shape=%s", picked_indices, X_train.shape)
        return [{"gpr_mean": None, "gpr_std": None, "ei_score": None}] * len(picked_indices)


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
    nearest_known: list[dict] | None = None,
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
        "nearest_known": nearest_known if nearest_known is not None else [],
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
            nearest_known=_expl_nearest_known(X_cands_raw[p1_idx], group.scored, X_scored_raw),
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
            nearest_known=_expl_nearest_known(X_cands_raw[remaining_idx[p2_idx]], group.scored, X_scored_raw),
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
    Modal q-EI path for one group (single-output gpytorch path).

    Applies the shared ECDF transform client-side, PCA-reduces to
    group.pca_dims, then calls the Modal GP endpoint with q=2.
    Falls back to _run_group_bo on any API error.

    Returned picks carry ``selection_type="modal_q_ei"``.  Sort keys are
    rank-based: first suggestion > second > random (_RANDOM_SORT_KEY = -1.0).
    """
    from bo_pipeline.modal_bo import _api_url, call_modal_api

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

    suggestions = call_modal_api(
        api_url=_api_url(),
        X=X_train_pca,
        y=y,
        candidates=X_cands_pca,
        q=min(2, len(group.candidates)),
        xi=xi,
    )

    # Deduplicate Modal suggestions while preserving rank order.
    ordered_indices: list[int] = []
    seen_pgi: set[int] = set()
    for s in suggestions:
        if s["index"] not in seen_pgi:
            seen_pgi.add(s["index"])
            ordered_indices.append(s["index"])

    # EXPLAINABILITY: fit local GPR on PCA-projected data for per-pick display stats.
    # Modal q-EI already chose these candidates — the local GPR only provides display values.
    expl_stats = _expl_local_gp_stats(ordered_indices, X_train_pca, y, X_cands_pca, xi)

    n_picks = len(ordered_indices)
    picks = []
    for rank, (idx, expl) in enumerate(zip(ordered_indices, expl_stats)):
        picks.append(_make_pick(
            group.candidates[idx],
            _MODAL_TYPE,
            group.platform,
            group.seed_ad_id,
            group.text_source_id,
            ei_score=expl["ei_score"],
            gpr_mean=expl["gpr_mean"],
            gpr_std=expl["gpr_std"],
            nearest_known=_expl_nearest_known(X_cands_raw[idx], group.scored, X_scored_raw),
            _sort_key=float(n_picks - rank),
        ))

    return picks


def _run_unified_modal_bo_multioutput(
    groups: list[BOGroup],
    ecdf_transform,
    sign: float,
    top_n: int,
    xi: float,
    rho: float = 0.5,
) -> list[dict]:
    """
    Multioutput Modal path for run_unified_cross_platform_bo.

    Unlike the shared-PCA path, this builds a **separate PCA per platform**
    (no zero-padding of Google's image half), then calls the Modal endpoint
    with d and d_candidates arrays so the server uses _TorchMultiOutputGP.

    The coregionalization matrix B = [[1, rho], [rho, 1]] models the
    Meta–Google correlation.  rho=0.5 is a conservative prior; update it
    from real CTR data when available.

    Returns a list of pick dicts with _sort_key set (to be stripped by caller).
    Returns [] on any error so the caller can fall back to the shared-PCA path.
    """
    from bo_pipeline.modal_bo import (
        _api_url,
        call_modal_api_multioutput,
        fit_pca,
        pca_dims_for_platform,
    )

    K = pca_dims_for_platform("meta")  # shared output dim for all platform PCAs

    # Assign output index per platform in order of first appearance.
    seen_platforms: list[str] = []
    for g in groups:
        if g.platform not in seen_platforms:
            seen_platforms.append(g.platform)
    platform_to_d: dict[str, int] = {p: i for i, p in enumerate(seen_platforms)}

    if len(platform_to_d) > 2:
        raise ValueError(
            "_run_unified_modal_bo_multioutput: rho-based B only supports 2 platforms; "
            f"got {list(platform_to_d)}"
        )

    # ── collect raw vectors per platform ────────────────────────────────────
    per_platform_scored_raw: dict[str, list[np.ndarray]] = {p: [] for p in seen_platforms}
    per_platform_cands_raw:  dict[str, list[np.ndarray]] = {p: [] for p in seen_platforms}
    per_platform_scored_entries: dict[str, list[tuple[dict, BOGroup]]] = {p: [] for p in seen_platforms}
    per_platform_cands_entries:  dict[str, list[tuple[dict, BOGroup]]] = {p: [] for p in seen_platforms}

    for g in groups:
        p = g.platform
        if g.scored:
            per_platform_scored_raw[p].append(g.build_X(g.scored).astype(np.float32))
            for obs in g.scored:
                per_platform_scored_entries[p].append((obs, g))
        if g.candidates:
            per_platform_cands_raw[p].append(g.build_X(g.candidates).astype(np.float32))
            for cand in g.candidates:
                per_platform_cands_entries[p].append((cand, g))

    # ── fit PCA per platform, project ───────────────────────────────────────
    X_scored_by_platform: dict[str, np.ndarray] = {}
    X_cands_by_platform:  dict[str, np.ndarray] = {}

    for p in seen_platforms:
        raw_s = per_platform_scored_raw[p]
        raw_c = per_platform_cands_raw[p]
        if not raw_c:
            continue

        n_scored_p = sum(r.shape[0] for r in raw_s)
        all_raw_parts: list[np.ndarray] = []
        if raw_s:
            all_raw_parts.append(np.vstack(raw_s))
        all_raw_parts.append(np.vstack(raw_c))
        X_all_raw = np.vstack(all_raw_parts).astype(np.float32)

        n_comp = min(K, X_all_raw.shape[0], X_all_raw.shape[1])
        _, X_all_pca = fit_pca(X_all_raw, n_components=n_comp)

        if n_scored_p > 0:
            X_scored_by_platform[p] = X_all_pca[:n_scored_p]
        X_cands_by_platform[p] = X_all_pca[n_scored_p:]

    # ── pad to K dims and stack into joint arrays ────────────────────────────
    def _pad_to_K(arr: np.ndarray) -> np.ndarray:
        if arr.shape[1] == K:
            return arr
        return np.hstack([arr, np.zeros((len(arr), K - arr.shape[1]), dtype=arr.dtype)])

    scored_X_parts:   list[np.ndarray] = []
    scored_d_parts:   list[np.ndarray] = []
    scored_entries_ordered: list[tuple[dict, BOGroup]] = []
    cands_X_parts:    list[np.ndarray] = []
    cands_d_parts:    list[np.ndarray] = []
    cand_entries_ordered: list[tuple[dict, BOGroup]] = []

    for p in seen_platforms:
        d_idx = platform_to_d[p]
        if p in X_scored_by_platform:
            scored_X_parts.append(_pad_to_K(X_scored_by_platform[p]))
            scored_d_parts.append(
                np.full(len(per_platform_scored_entries[p]), d_idx, dtype=np.int32)
            )
            scored_entries_ordered.extend(per_platform_scored_entries[p])
        if p in X_cands_by_platform:
            cands_X_parts.append(_pad_to_K(X_cands_by_platform[p]))
            cands_d_parts.append(
                np.full(len(per_platform_cands_entries[p]), d_idx, dtype=np.int32)
            )
            cand_entries_ordered.extend(per_platform_cands_entries[p])

    if not cands_X_parts or not scored_X_parts:
        return []

    X_scored_pool = np.vstack(scored_X_parts).astype(np.float32)
    d_scored_pool = np.concatenate(scored_d_parts)
    X_cands_pool  = np.vstack(cands_X_parts).astype(np.float32)
    d_cands_pool  = np.concatenate(cands_d_parts)

    y_raw  = np.array([obs["score"] * sign for obs, _ in scored_entries_ordered], dtype=np.float64)
    y_pool = ecdf_transform(y_raw).astype(np.float32)

    # ── call Modal multioutput endpoint ─────────────────────────────────────
    suggestions = call_modal_api_multioutput(
        api_url=_api_url(),
        X=X_scored_pool,
        y=y_pool,
        candidates=X_cands_pool,
        d_train=d_scored_pool,
        d_cands=d_cands_pool,
        rho=rho,
        q=min(top_n, len(cand_entries_ordered)),
        xi=xi,
    )

    picks: list[dict] = []
    seen_idx: set[int] = set()
    n_picks = len(suggestions)
    for rank, suggestion in enumerate(suggestions):
        idx = suggestion["index"]
        if idx in seen_idx:
            continue
        seen_idx.add(idx)
        cand, g = cand_entries_ordered[idx]
        picks.append(_make_pick(
            cand, _MODAL_TYPE, g.platform, g.seed_ad_id, g.text_source_id,
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

    # ── 3a. Multioutput path: separate PCA per platform, joint GP call ────────
    # method="modal_multioutput" takes a completely different code path here and
    # returns early.  Falls through to the shared-PCA path on failure or when
    # there is no scored data.
    if method == "modal_multioutput":
        from bo_pipeline.modal_bo import modal_bo_enabled
        if not modal_bo_enabled():
            logger.warning("unified_cross_platform_bo: modal_multioutput requested but MODAL_BO_API_URL unset — falling back to shared-PCA path")
        elif ecdf_transform is not None:
            try:
                all_picks = _run_unified_modal_bo_multioutput(
                    groups=groups,
                    ecdf_transform=ecdf_transform,
                    sign=sign,
                    top_n=top_n,
                    xi=xi,
                )
                if all_picks:
                    all_picks.sort(key=lambda p: p["_sort_key"], reverse=True)
                    result = all_picks[:top_n]
                    for pick in result:
                        pick.pop("_sort_key", None)
                    return result, group_stats
                logger.warning(
                    "unified_cross_platform_bo: multioutput Modal returned no picks — "
                    "falling back to shared-PCA path"
                )
            except Exception as exc:
                logger.warning(
                    "unified_cross_platform_bo: multioutput Modal failed (%s) — "
                    "falling back to shared-PCA path",
                    exc,
                )

    # ── 4. Build 3072-dim raw vectors for all groups, then fit ONE shared PCA ───
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

    # Stacked raw (pre-PCA) vectors — 1:1 with scored_meta and cands_meta.
    # Used only for explainability (nearest_known cosine distances).
    X_scored_all_raw = np.vstack(scored_raw_rows).astype(np.float32) if scored_raw_rows else None
    X_cands_all_raw  = np.vstack(cands_raw_rows).astype(np.float32)

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
                X=X_scored_pool.astype(np.float32),
                y=y_pool.astype(np.float32),
                candidates=X_cands_pool.astype(np.float32),
                q=min(top_n, len(cands_meta)),
                xi=xi,
            )
            # Deduplicate Modal suggestions while preserving rank order.
            ordered_modal: list[int] = []
            seen: set[int] = set()
            for suggestion in suggestions:
                if suggestion["index"] not in seen:
                    seen.add(suggestion["index"])
                    ordered_modal.append(suggestion["index"])

            # EXPLAINABILITY: local GPR on the shared PCA space for per-pick display stats.
            # Modal q-EI already chose these candidates — the local GPR only provides display values.
            expl_stats = _expl_local_gp_stats(
                ordered_modal,
                X_scored_pool.astype(np.float32),
                y_pool.astype(np.float32),
                X_cands_pool.astype(np.float32),
                xi,
            )
            n_picks = len(ordered_modal)
            for rank, (idx, expl) in enumerate(zip(ordered_modal, expl_stats)):
                start, end, g = next((s, e, grp) for s, e, grp in group_cand_offsets if s <= idx < e)
                nearest = _expl_nearest_known(X_cands_all_raw[idx], scored_meta, X_scored_all_raw) if X_scored_all_raw is not None else []
                logger.info(
                    "unified_modal pick rank=%d idx=%d gpr_mean=%s gpr_std=%s ei_score=%s nearest_known_count=%d",
                    rank, idx, expl.get("gpr_mean"), expl.get("gpr_std"), expl.get("ei_score"), len(nearest),
                )
                all_picks.append(_make_pick(
                    cands_meta[idx], _MODAL_TYPE, g.platform, g.seed_ad_id, g.text_source_id,
                    ei_score=expl["ei_score"], gpr_mean=expl["gpr_mean"], gpr_std=expl["gpr_std"],
                    nearest_known=nearest, _sort_key=float(n_picks - rank),
                ))
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
            nearest = _expl_nearest_known(X_cands_all_raw[idx], scored_meta, X_scored_all_raw) if X_scored_all_raw is not None else []
            all_picks.append(_make_pick(cands_meta[idx], sel_type, g.platform, g.seed_ad_id, g.text_source_id, ei_score=ei_val, gpr_mean=float(mu[0]), gpr_std=float(sigma[0]), nearest_known=nearest, _sort_key=ei_val))
            remaining.pop(best_local)
            if remaining and pick_num < top_n - 1:
                gpr, scaler = fantasize(gpr, scaler, X_scored_pool, y_pool, X_cands_pool[[idx]])

    # ── 6. Global rank and return ─────────────────────────────────────────────
    all_picks.sort(key=lambda p: p["_sort_key"], reverse=True)
    result = all_picks[:top_n]
    for pick in result:
        pick.pop("_sort_key", None)

    return result, group_stats

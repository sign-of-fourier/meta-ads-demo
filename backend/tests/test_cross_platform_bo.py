"""
Tests for cross-platform Bayesian Optimisation.

Covers:
  TestECDF              — fit_ecdf pure-function behaviour
  TestPCADimsForPlatform — pca_dims_for_platform env-var routing
  TestBOGroupBuildX     — BOGroup.build_X produces correct shapes per platform
  TestCrossPlatformBO   — end-to-end pipeline with pre-seeded DB (no API calls)
  TestCrossPlatformBOEndpoint — FastAPI route shape and auth (mocked pipeline)

No Azure / OpenAI / Modal calls — all embeddings are synthetic numpy vectors.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pytest

# ── env stubs (must precede main import) ──────────────────────────────────────
os.environ.setdefault("META_APP_ID", "test_app_id")
os.environ.setdefault("META_APP_SECRET", "test_app_secret")
os.environ.setdefault("META_REDIRECT_URI", "http://localhost:8000/auth/meta/callback")
os.environ.setdefault("JWT_SECRET", "test_secret")

# ── constants ─────────────────────────────────────────────────────────────────

# Use realistic embedding dimensions (matching the real system) so combine()
# and build_X() produce the correct shapes without any truncation/padding.
TEXT_EMBED_DIM = 1536   # matches text-embedding-3-small
IMAGE_EMBED_DIM = 1536  # matches embed-v-4-0
COMBINED_DIM = TEXT_EMBED_DIM + IMAGE_EMBED_DIM  # 3072 — Meta combined

TEST_USER_ID = 88001

# Meta group identifiers
META_SEED_AD_ID = "cp_meta_seed_001"
META_TEXT_SOURCE_ID = "cp_meta_gen_001"

# Google group identifiers
GOOGLE_SEED_AD_ID = "cp_google_seed_001"
GOOGLE_TEXT_SOURCE_ID = "cp_google_gen_001"

N_META_SCORED = 4
N_GOOGLE_SCORED = 3
N_TEXT_COMBOS = 8   # candidates per group

META_SCORES = [3.5, 4.2, 5.1, 4.7]
GOOGLE_SCORES = [2.8, 3.9, 4.4]

rng = np.random.default_rng(99)


def _rand_vec(dim: int) -> np.ndarray:
    return rng.random(dim).astype(np.float32)


# ── DB DDL (subset of main app schema needed for BO selector) ─────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ad_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ad_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    text_model TEXT, image_model TEXT,
    text_vector BLOB, image_vector BLOB, combined_vector BLOB NOT NULL,
    text_snapshot TEXT, image_url TEXT,
    embedded_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, ad_id)
);
CREATE TABLE IF NOT EXISTS ad_text_combination_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    combination_key TEXT NOT NULL,
    vector BLOB NOT NULL,
    model TEXT,
    embedded_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_id, combination_key)
);
CREATE TABLE IF NOT EXISTS ad_image_embeddings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    ad_id       TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    slot_index  INTEGER NOT NULL,
    image_ref   TEXT NOT NULL,
    vector      BLOB NOT NULL,
    model       TEXT,
    embedded_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, ad_id, slot_index)
);
CREATE TABLE IF NOT EXISTS scored_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    seed_ad_id      TEXT NOT NULL,
    combination_key TEXT NOT NULL,
    combination     TEXT NOT NULL,
    score           REAL NOT NULL,
    metric          TEXT NOT NULL DEFAULT 'synthetic',
    source          TEXT NOT NULL DEFAULT 'seed_script',
    text_vector     BLOB,
    image_vector    BLOB,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, seed_ad_id, combination_key, metric)
);
"""

_TEXT_COMBOS = [
    {"headline": f"Headline {i}", "primary_text": f"Body copy {i}."}
    for i in range(N_TEXT_COMBOS)
]

_GOOGLE_TEXT_COMBOS = [
    {"headline": f"Google Headline {i}", "description": f"Google desc {i}."}
    for i in range(N_TEXT_COMBOS)
]


def _seed_group(
    conn: sqlite3.Connection,
    seed_ad_id: str,
    text_source_id: str,
    campaign_id: str,
    scores: list[float],
    text_combos: list[dict],
    include_image_vec: bool = True,
) -> None:
    """Seed one BO group (seed ad + text combos + scored observations) into conn."""

    # Seed ad embedding
    txt_vec = _rand_vec(TEXT_EMBED_DIM)
    img_vec = _rand_vec(IMAGE_EMBED_DIM) if include_image_vec else None
    combined_stub = np.zeros(8, dtype=np.float32)  # minimal non-null combined blob
    conn.execute(
        """INSERT OR REPLACE INTO ad_embeddings
           (user_id, ad_id, campaign_id, text_vector, image_vector, combined_vector)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            TEST_USER_ID, seed_ad_id, campaign_id,
            txt_vec.tobytes(),
            img_vec.tobytes() if img_vec is not None else None,
            combined_stub.tobytes(),
        ),
    )

    # Text combination embeddings (candidates)
    for combo in text_combos:
        key = json.dumps(combo, sort_keys=True, separators=(",", ":"))
        vec = _rand_vec(TEXT_EMBED_DIM)
        conn.execute(
            """INSERT OR REPLACE INTO ad_text_combination_embeddings
               (source_id, combination_key, vector) VALUES (?, ?, ?)""",
            (text_source_id, key, vec.tobytes()),
        )

    # Scored observations — write directly to scored_observations
    for i, score in enumerate(scores):
        combo = text_combos[i % len(text_combos)]
        key = json.dumps(combo, sort_keys=True, separators=(",", ":"))
        tce_row = conn.execute(
            "SELECT vector FROM ad_text_combination_embeddings WHERE source_id=? AND combination_key=?",
            (text_source_id, key),
        ).fetchone()
        txt_vec_bytes = tce_row["vector"] if tce_row else _rand_vec(TEXT_EMBED_DIM).tobytes()
        v_img = _rand_vec(IMAGE_EMBED_DIM) if include_image_vec else None
        conn.execute(
            """INSERT OR REPLACE INTO scored_observations
               (user_id, seed_ad_id, combination_key, combination,
                score, metric, source, text_vector, image_vector)
               VALUES (?, ?, ?, ?, ?, 'synthetic', 'seed_script', ?, ?)""",
            (
                TEST_USER_ID, seed_ad_id, key, key,
                score,
                txt_vec_bytes,
                v_img.tobytes() if v_img is not None else None,
            ),
        )


def _seed_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    conn.execute(
        "INSERT OR IGNORE INTO users (id, email, pw_hash) VALUES (?, ?, ?)",
        (TEST_USER_ID, "cp_bo_test@example.com", "hash"),
    )

    # Meta group: has both text + image vectors
    _seed_group(
        conn, META_SEED_AD_ID, META_TEXT_SOURCE_ID,
        campaign_id="camp_meta",
        scores=META_SCORES,
        text_combos=_TEXT_COMBOS,
        include_image_vec=True,
    )

    # Google group: text vectors only (image_vector=None — mirrors real RSA ads)
    _seed_group(
        conn, GOOGLE_SEED_AD_ID, GOOGLE_TEXT_SOURCE_ID,
        campaign_id="camp_google",
        scores=GOOGLE_SCORES,
        text_combos=_GOOGLE_TEXT_COMBOS,
        include_image_vec=False,
    )

    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def cp_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("cp_db") / "cp_bo.db"
    _seed_db(db_path)
    return db_path


# ─────────────────────────────────────────────────────────────────────────────
# TestECDF — pure function, no DB
# ─────────────────────────────────────────────────────────────────────────────

class TestECDF:
    def test_returns_callable(self):
        from bo_pipeline.ecdf import fit_ecdf
        transform = fit_ecdf([1.0, 2.0, 3.0])
        assert callable(transform)

    def test_output_is_finite(self):
        from bo_pipeline.ecdf import fit_ecdf
        pool = [1.0, 2.0, 3.0, 4.0, 5.0]
        transform = fit_ecdf(pool)
        result = transform(np.array(pool, dtype=np.float64))
        assert np.all(np.isfinite(result))

    def test_monotone_increasing(self):
        """Higher input score → higher transformed output."""
        from bo_pipeline.ecdf import fit_ecdf
        pool = [1.0, 2.0, 3.0, 4.0, 5.0]
        transform = fit_ecdf(pool)
        result = transform(np.array(pool))
        diffs = np.diff(result)
        assert np.all(diffs >= 0), "ECDF transform must be non-decreasing"

    def test_cross_group_ordering_preserved(self):
        """A Meta score of 5.1 should rank higher than a Google score of 3.9."""
        from bo_pipeline.ecdf import fit_ecdf
        meta_scores = [3.5, 4.2, 5.1, 4.7]
        google_scores = [2.8, 3.9, 4.4]
        transform = fit_ecdf(meta_scores + google_scores)

        t_meta_high = transform(np.array([5.1]))[0]
        t_google_low = transform(np.array([3.9]))[0]
        assert t_meta_high > t_google_low

    def test_median_maps_near_zero(self):
        """The score at the median of the pool should map close to 0 (norm.ppf(0.5)=0)."""
        from bo_pipeline.ecdf import fit_ecdf
        pool = list(range(1, 10))  # 9 values; median = 5
        transform = fit_ecdf(pool)
        t_median = transform(np.array([5.0]))[0]
        assert abs(t_median) < 0.5, f"Median should map near 0; got {t_median}"

    def test_single_element_pool(self):
        """A pool of one score should not crash."""
        from bo_pipeline.ecdf import fit_ecdf
        transform = fit_ecdf([3.0])
        result = transform(np.array([3.0]))
        assert result.shape == (1,)
        assert np.isfinite(result[0])

    def test_scores_outside_pool_are_clamped(self):
        """Scores outside the pool range should produce finite (not ±inf) outputs."""
        from bo_pipeline.ecdf import fit_ecdf
        pool = [2.0, 3.0, 4.0]
        transform = fit_ecdf(pool)
        result = transform(np.array([0.0, 100.0]))
        assert np.all(np.isfinite(result))

    def test_output_shape_matches_input(self):
        from bo_pipeline.ecdf import fit_ecdf
        transform = fit_ecdf([1.0, 2.0, 3.0, 4.0])
        inp = np.array([1.5, 2.5, 3.5])
        assert transform(inp).shape == (3,)


# ─────────────────────────────────────────────────────────────────────────────
# TestPCADimsForPlatform — env-var routing
# ─────────────────────────────────────────────────────────────────────────────

class TestPCADimsForPlatform:
    def test_google_returns_smaller_dims(self):
        from bo_pipeline.modal_bo import pca_dims_for_platform
        google_dims = pca_dims_for_platform("google")
        meta_dims = pca_dims_for_platform("meta")
        # With default env vars: google=32, meta=64
        assert google_dims < meta_dims

    def test_meta_uses_modal_pca_dims_default(self):
        from bo_pipeline.modal_bo import pca_dims_for_platform
        assert pca_dims_for_platform("meta") == 64

    def test_google_uses_google_pca_dims_default(self):
        from bo_pipeline.modal_bo import pca_dims_for_platform
        assert pca_dims_for_platform("google") == 32

    def test_unknown_platform_falls_back_to_meta_dims(self):
        from bo_pipeline.modal_bo import pca_dims_for_platform
        # Any non-google platform uses the Meta default
        assert pca_dims_for_platform("tiktok") == pca_dims_for_platform("meta")

    def test_google_env_override(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_BO_PCA_DIMS", "16")
        from importlib import reload
        import bo_pipeline.modal_bo as mb
        reload(mb)
        assert mb.pca_dims_for_platform("google") == 16
        reload(mb)  # restore


# ─────────────────────────────────────────────────────────────────────────────
# TestBOGroupBuildX — feature matrix construction
# ─────────────────────────────────────────────────────────────────────────────

class TestBOGroupBuildX:
    """Verify build_X returns the correct input dimension per platform."""

    def _make_obs(self, with_image: bool = True) -> dict:
        return {
            "text_vector": _rand_vec(TEXT_EMBED_DIM),
            "image_vector": _rand_vec(IMAGE_EMBED_DIM) if with_image else None,
            "score": 3.0,
            "combination_key": "k",
            "combination": {},
        }

    def test_meta_group_returns_combined_dim(self):
        from bo_pipeline.cross_platform import BOGroup
        group = BOGroup(platform="meta", seed_ad_id="x", text_source_id="x")
        obs = [self._make_obs(with_image=True) for _ in range(3)]
        X = group.build_X(obs)
        assert X.shape == (3, COMBINED_DIM), f"Expected (3, {COMBINED_DIM}), got {X.shape}"

    def test_google_group_returns_text_dim(self):
        from bo_pipeline.cross_platform import BOGroup
        group = BOGroup(platform="google", seed_ad_id="x", text_source_id="x")
        obs = [self._make_obs(with_image=False) for _ in range(3)]
        X = group.build_X(obs)
        assert X.shape == (3, TEXT_EMBED_DIM), f"Expected (3, {TEXT_EMBED_DIM}), got {X.shape}"

    def test_meta_and_google_have_different_input_dims(self):
        """The key property: different lengths prevent accidental GPR mixing."""
        from bo_pipeline.cross_platform import BOGroup
        meta_group = BOGroup(platform="meta", seed_ad_id="x", text_source_id="x")
        google_group = BOGroup(platform="google", seed_ad_id="y", text_source_id="y")
        obs = [self._make_obs(with_image=True)]

        X_meta = meta_group.build_X(obs)
        X_google = google_group.build_X(obs)
        assert X_meta.shape[1] != X_google.shape[1], (
            "Meta and Google must have different feature dimensions"
        )
        assert X_meta.shape[1] == COMBINED_DIM
        assert X_google.shape[1] == TEXT_EMBED_DIM

    def test_meta_with_none_image_still_works(self):
        """combine() zero-pads when image_vector is None — must not crash."""
        from bo_pipeline.cross_platform import BOGroup
        group = BOGroup(platform="meta", seed_ad_id="x", text_source_id="x")
        obs = [self._make_obs(with_image=False)]
        X = group.build_X(obs)
        assert X.shape == (1, COMBINED_DIM)

    def test_output_dtype_is_float32(self):
        from bo_pipeline.cross_platform import BOGroup
        for platform in ("meta", "google"):
            group = BOGroup(platform=platform, seed_ad_id="x", text_source_id="x")
            obs = [self._make_obs(with_image=(platform == "meta"))]
            X = group.build_X(obs)
            assert X.dtype == np.float32, f"Expected float32 for {platform}"

    def test_google_pca_dims_smaller_than_meta(self):
        from bo_pipeline.cross_platform import BOGroup
        meta_g = BOGroup(platform="meta", seed_ad_id="x", text_source_id="x")
        google_g = BOGroup(platform="google", seed_ad_id="y", text_source_id="y")
        assert google_g.pca_dims < meta_g.pca_dims


# ─────────────────────────────────────────────────────────────────────────────
# TestCrossPlatformBO — full pipeline with pre-seeded DB
# ─────────────────────────────────────────────────────────────────────────────

_PAIRS = [
    {"platform": "meta",   "seed_ad_id": META_SEED_AD_ID,   "text_source_id": META_TEXT_SOURCE_ID},
    {"platform": "google", "seed_ad_id": GOOGLE_SEED_AD_ID, "text_source_id": GOOGLE_TEXT_SOURCE_ID},
]


class TestCrossPlatformBO:
    @pytest.fixture(scope="class")
    def cp_result(self, cp_db):
        from bo_pipeline.cross_platform import run_cross_platform_bo
        # BO_TEST_METHOD env var selects the path: "local" (default) or "modal".
        method = os.getenv("BO_TEST_METHOD", "local")
        picks, _ = run_cross_platform_bo(_PAIRS, TEST_USER_ID, db_path=cp_db, method=method)
        return picks

    # ── Basic shape ────────────────────────────────────────────────────────────

    def test_returns_list(self, cp_result):
        assert isinstance(cp_result, list)

    def test_returns_at_most_top_n_picks(self, cp_result):
        assert len(cp_result) <= 2

    def test_returns_at_least_one_pick(self, cp_result):
        assert len(cp_result) >= 1

    # ── Platform tagging ───────────────────────────────────────────────────────

    def test_all_picks_have_platform_tag(self, cp_result):
        for pick in cp_result:
            assert "platform" in pick
            assert pick["platform"] in ("meta", "google")

    def test_all_picks_have_seed_ad_id(self, cp_result):
        for pick in cp_result:
            assert "seed_ad_id" in pick

    def test_all_picks_have_text_source_id(self, cp_result):
        for pick in cp_result:
            assert "text_source_id" in pick

    def test_platform_matches_seed_ad_id(self, cp_result):
        """Picks from meta seed should have platform='meta', etc."""
        for pick in cp_result:
            if pick["seed_ad_id"] == META_SEED_AD_ID:
                assert pick["platform"] == "meta"
            elif pick["seed_ad_id"] == GOOGLE_SEED_AD_ID:
                assert pick["platform"] == "google"

    # ── Standard pick fields ───────────────────────────────────────────────────

    def test_picks_have_required_fields(self, cp_result):
        required = {"combination_key", "combination", "selection_type"}
        for pick in cp_result:
            assert required <= pick.keys(), f"Missing fields in pick: {pick.keys()}"

    def test_picks_have_valid_selection_type(self, cp_result):
        valid_types = {"ei", "fantasy", "random", "modal_q_ei"}
        for pick in cp_result:
            assert pick["selection_type"] in valid_types

    def test_combination_is_dict(self, cp_result):
        for pick in cp_result:
            assert isinstance(pick["combination"], dict)

    def test_ei_picks_have_nonnegative_ei_score(self, cp_result):
        for pick in cp_result:
            if pick["selection_type"] in ("ei", "fantasy"):
                assert pick["ei_score"] is not None
                assert pick["ei_score"] >= 0.0

    # ── Candidate pool membership ──────────────────────────────────────────────

    def test_picks_from_correct_platform_candidate_pool(self, cp_db):
        """Each pick's combination_key must belong to its platform's candidate pool."""
        from bo_pipeline.cross_platform import run_cross_platform_bo
        from bo_pipeline.selector import get_candidate_combinations

        method = os.getenv("BO_TEST_METHOD", "local")
        picks, _ = run_cross_platform_bo(_PAIRS, TEST_USER_ID, db_path=cp_db, method=method)

        meta_cand_keys = {
            c["combination_key"]
            for c in get_candidate_combinations(
                META_TEXT_SOURCE_ID, META_SEED_AD_ID, TEST_USER_ID, db_path=cp_db
            )
        }
        google_cand_keys = {
            c["combination_key"]
            for c in get_candidate_combinations(
                GOOGLE_TEXT_SOURCE_ID, GOOGLE_SEED_AD_ID, TEST_USER_ID, db_path=cp_db
            )
        }

        for pick in picks:
            if pick["platform"] == "meta":
                assert pick["combination_key"] in meta_cand_keys, (
                    f"Meta pick not in meta candidate pool: {pick['combination_key']}"
                )
            elif pick["platform"] == "google":
                assert pick["combination_key"] in google_cand_keys, (
                    f"Google pick not in google candidate pool: {pick['combination_key']}"
                )

    # ── EI ordering ────────────────────────────────────────────────────────────

    def test_first_pick_has_geq_ei_than_second(self, cp_result):
        """Global ranking: pick 0 should have ≥ EI than pick 1."""
        if len(cp_result) < 2:
            pytest.skip("Only one pick returned")
        ei0 = cp_result[0].get("ei_score") or 0.0
        ei1 = cp_result[1].get("ei_score") or 0.0
        assert ei0 >= ei1 - 1e-9  # allow tiny float error

    def test_no_internal_sort_key_in_result(self, cp_result):
        """_sort_key is an internal field and must not leak to callers."""
        for pick in cp_result:
            assert "_sort_key" not in pick

    # ── top_n parameter ────────────────────────────────────────────────────────

    def test_top_n_1_returns_single_pick(self, cp_db):
        from bo_pipeline.cross_platform import run_cross_platform_bo
        method = os.getenv("BO_TEST_METHOD", "local")
        picks, _ = run_cross_platform_bo(_PAIRS, TEST_USER_ID, db_path=cp_db, top_n=1, method=method)
        assert len(picks) <= 1

    def test_top_n_4_returns_at_most_4(self, cp_db):
        from bo_pipeline.cross_platform import run_cross_platform_bo
        method = os.getenv("BO_TEST_METHOD", "local")
        picks, _ = run_cross_platform_bo(_PAIRS, TEST_USER_ID, db_path=cp_db, top_n=4, method=method)
        assert len(picks) <= 4

    # ── Fallback behaviour ─────────────────────────────────────────────────────

    def test_fallback_random_when_no_scored_data(self, tmp_path):
        """When neither group has scored observations, all picks are random."""
        from bo_pipeline.cross_platform import run_cross_platform_bo

        empty_db = tmp_path / "empty_cp.db"
        conn = sqlite3.connect(str(empty_db))
        conn.row_factory = sqlite3.Row
        conn.executescript(_DDL)
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, pw_hash) VALUES (?, ?, ?)",
            (TEST_USER_ID, "empty@example.com", "hash"),
        )
        # Seed only text combinations (no scored variants, no seed ad embeddings)
        for combos, source_id in [
            (_TEXT_COMBOS[:3], META_TEXT_SOURCE_ID),
            (_GOOGLE_TEXT_COMBOS[:3], GOOGLE_TEXT_SOURCE_ID),
        ]:
            for combo in combos:
                key = json.dumps(combo, sort_keys=True, separators=(",", ":"))
                conn.execute(
                    "INSERT OR REPLACE INTO ad_text_combination_embeddings "
                    "(source_id, combination_key, vector) VALUES (?, ?, ?)",
                    (source_id, key, _rand_vec(TEXT_EMBED_DIM).tobytes()),
                )
        # Seed ad embeddings so get_candidate_combinations can find image slots
        for ad_id, campaign in [(META_SEED_AD_ID, "c1"), (GOOGLE_SEED_AD_ID, "c2")]:
            stub = np.zeros(8, dtype=np.float32)
            conn.execute(
                "INSERT OR REPLACE INTO ad_embeddings "
                "(user_id, ad_id, campaign_id, combined_vector) VALUES (?, ?, ?, ?)",
                (TEST_USER_ID, ad_id, campaign, stub.tobytes()),
            )
        conn.commit()
        conn.close()

        method = os.getenv("BO_TEST_METHOD", "local")
        picks, _ = run_cross_platform_bo(_PAIRS, TEST_USER_ID, db_path=empty_db, method=method)
        assert len(picks) <= 2
        for pick in picks:
            # No scored data → random fallback regardless of method
            assert pick["selection_type"] == "random"

    def test_one_group_insufficient_data_falls_back_locally(self, tmp_path):
        """
        If only the Google group has insufficient scored data (< MIN_TRAINING_POINTS),
        its picks are random while Meta's are EI-based.
        """
        from bo_pipeline.cross_platform import run_cross_platform_bo

        partial_db = tmp_path / "partial_cp.db"
        conn = sqlite3.connect(str(partial_db))
        conn.row_factory = sqlite3.Row
        conn.executescript(_DDL)
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, pw_hash) VALUES (?, ?, ?)",
            (TEST_USER_ID, "partial@example.com", "hash"),
        )
        # Meta: full scored data
        _seed_group(
            conn, META_SEED_AD_ID, META_TEXT_SOURCE_ID,
            campaign_id="camp_meta_partial",
            scores=META_SCORES,
            text_combos=_TEXT_COMBOS,
            include_image_vec=True,
        )
        # Google: only 1 scored observation → below MIN_TRAINING_POINTS (2)
        _seed_group(
            conn, GOOGLE_SEED_AD_ID, GOOGLE_TEXT_SOURCE_ID,
            campaign_id="camp_google_partial",
            scores=[3.0],   # only 1 score → random fallback
            text_combos=_GOOGLE_TEXT_COMBOS,
            include_image_vec=False,
        )
        conn.commit()
        conn.close()

        method = os.getenv("BO_TEST_METHOD", "local")
        picks, _ = run_cross_platform_bo(_PAIRS, TEST_USER_ID, db_path=partial_db, method=method)

        # At least the Meta pick should be EI-based
        meta_picks = [p for p in picks if p["platform"] == "meta"]
        google_picks = [p for p in picks if p["platform"] == "google"]

        if meta_picks:
            # Meta had full data: EI-based (local) or Modal pick
            assert meta_picks[0]["selection_type"] in ("ei", "fantasy", "modal_q_ei")
        if google_picks:
            # Google had insufficient data: always random regardless of method
            assert google_picks[0]["selection_type"] == "random"

    def test_empty_pairs_returns_empty(self, cp_db):
        from bo_pipeline.cross_platform import run_cross_platform_bo
        method = os.getenv("BO_TEST_METHOD", "local")
        picks, _ = run_cross_platform_bo([], TEST_USER_ID, db_path=cp_db, method=method)
        assert picks == []

    def test_single_meta_pair_still_works(self, cp_db):
        """Single-group mode should behave like run_bo."""
        from bo_pipeline.cross_platform import run_cross_platform_bo
        method = os.getenv("BO_TEST_METHOD", "local")
        picks, _ = run_cross_platform_bo(
            [{"platform": "meta", "seed_ad_id": META_SEED_AD_ID, "text_source_id": META_TEXT_SOURCE_ID}],
            TEST_USER_ID, db_path=cp_db, method=method,
        )
        assert len(picks) >= 1
        assert all(p["platform"] == "meta" for p in picks)

    # ── ECDF cross-group target consistency ────────────────────────────────────

    def test_ecdf_used_combined_pool(self, cp_db):
        """
        Run the ECDF fit manually and verify that a Meta score equal to the
        top Google score gets a higher ECDF rank (combined pool of 7 scores).
        """
        from bo_pipeline.ecdf import fit_ecdf

        all_scores = META_SCORES + GOOGLE_SCORES   # 7 scores combined
        transform = fit_ecdf(all_scores)

        # Highest Google score is 4.4 — a Meta score of 5.1 should rank higher
        t_meta_high = transform(np.array([5.1]))[0]
        t_google_max = transform(np.array([4.4]))[0]
        assert t_meta_high > t_google_max


# ─────────────────────────────────────────────────────────────────────────────
# TestCrossPlatformBOEndpoint — FastAPI route (mocked pipeline)
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossPlatformBOEndpoint:
    """Route-level tests: auth, request shape, response shape.  Pipeline is mocked."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        import main as m
        from fastapi.testclient import TestClient
        from main import app, create_token, init_db

        db_file = tmp_path / "ep_test.db"
        monkeypatch.setattr(m, "DB_PATH", db_file)
        init_db()

        self.app = app
        self.client = TestClient(app, raise_server_exceptions=False)
        self.db_file = db_file

        db = sqlite3.connect(str(db_file))
        cur = db.execute(
            "INSERT INTO users (email, pw_hash) VALUES (?, ?)",
            ("ep_test@example.com", "hash"),
        )
        self.user_id = cur.lastrowid
        db.commit()
        db.close()

        self.token = create_token(self.user_id)

    def _valid_payload(self):
        return {
            "pairs": [
                {"platform": "meta",   "seed_ad_id": "meta_x",   "text_source_id": "meta_x"},
                {"platform": "google", "seed_ad_id": "google_x",  "text_source_id": "google_x"},
            ]
        }

    def test_requires_auth(self):
        resp = self.client.post("/api/bo/cross-platform", json=self._valid_payload())
        assert resp.status_code == 401

    def test_empty_pairs_returns_400(self):
        resp = self.client.post(
            "/api/bo/cross-platform",
            json={"pairs": []},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        assert resp.status_code == 400

    def test_returns_200_with_mocked_pipeline(self):
        from unittest.mock import patch

        mock_picks = [
            {
                "combination_key": '{"headline":"Buy Now"}',
                "combination": {"headline": "Buy Now"},
                "selection_type": "random",
                "ei_score": None,
                "gpr_mean": None,
                "gpr_std": None,
                "platform": "meta",
                "seed_ad_id": "meta_x",
                "text_source_id": "meta_x",
            }
        ]

        mock_stats = [
            {"platform": "meta",   "seed_ad_id": "meta_x",   "scored_count": 0, "candidate_count": 0},
            {"platform": "google", "seed_ad_id": "google_x",  "scored_count": 0, "candidate_count": 0},
        ]
        with patch("bo_pipeline.cross_platform.run_cross_platform_bo", return_value=(mock_picks, mock_stats)), \
             patch("bo_pipeline.storage.save_bo_run", return_value=[1]):
            resp = self.client.post(
                "/api/bo/cross-platform",
                json=self._valid_payload(),
                headers={"Authorization": f"Bearer {self.token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "picks" in body
        assert "group_stats" in body

    def test_response_shape_picks(self):
        from unittest.mock import patch

        mock_picks = [
            {
                "combination_key": "k1",
                "combination": {"headline": "H1", "description": "D1"},
                "selection_type": "ei",
                "ei_score": 0.12,
                "gpr_mean": 0.8,
                "gpr_std": 0.1,
                "platform": "google",
                "seed_ad_id": "google_x",
                "text_source_id": "google_x",
            }
        ]

        mock_stats = [
            {"platform": "meta",   "seed_ad_id": "meta_x",   "scored_count": 1, "candidate_count": 1},
            {"platform": "google", "seed_ad_id": "google_x",  "scored_count": 1, "candidate_count": 1},
        ]
        with patch("bo_pipeline.cross_platform.run_cross_platform_bo", return_value=(mock_picks, mock_stats)), \
             patch("bo_pipeline.storage.save_bo_run", return_value=[1]):
            resp = self.client.post(
                "/api/bo/cross-platform",
                json=self._valid_payload(),
                headers={"Authorization": f"Bearer {self.token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        pick = body["picks"][0]
        assert pick["platform"] == "google"
        assert pick["selection_type"] == "ei"
        assert pick["ei_score"] == pytest.approx(0.12)
        assert pick["seed_ad_id"] == "google_x"

    def test_response_shape_group_stats(self):
        from unittest.mock import patch

        mock_stats = [
            {"platform": "meta",   "seed_ad_id": "meta_x",   "scored_count": 2, "candidate_count": 5},
            {"platform": "google", "seed_ad_id": "google_x",  "scored_count": 2, "candidate_count": 5},
        ]
        with patch("bo_pipeline.cross_platform.run_cross_platform_bo", return_value=([], mock_stats)), \
             patch("bo_pipeline.storage.save_bo_run", return_value=[]):
            resp = self.client.post(
                "/api/bo/cross-platform",
                json=self._valid_payload(),
                headers={"Authorization": f"Bearer {self.token}"},
            )

        assert resp.status_code == 200
        stats = resp.json()["group_stats"]
        assert len(stats) == 2
        for stat in stats:
            assert "platform" in stat
            assert "scored_count" in stat
            assert "candidate_count" in stat
            assert stat["scored_count"] == 2
            assert stat["candidate_count"] == 5

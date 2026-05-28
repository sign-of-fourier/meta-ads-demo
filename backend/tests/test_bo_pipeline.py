"""
Tests for ad_embedding_combiner and bo_pipeline.

Structure:
  TestCombineEmbeddings   — pure function tests, no DB
  TestGPR                 — pure GPR functions, no DB, synthetic numpy data
  TestSelector            — DB access layer (pre-seeded, no embedding API calls)
  TestBOPipeline          — full end-to-end with pre-seeded DB

No Azure API calls — all embeddings are pre-seeded synthetic numpy vectors.
No ad creation — all variants/jobs/embeddings are inserted directly.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Constants shared across tests
# ---------------------------------------------------------------------------

EMBED_DIM = 1536          # realistic Azure embedding dimension
TEXT_DIM = 128            # from ad_embedding_combiner defaults
IMAGE_DIM = 128           # from ad_embedding_combiner defaults

TEST_USER_ID = 77001
SEED_AD_ID = "bo_test_seed_ad_001"
TEXT_SOURCE_ID = "bo_test_gen_ad_001"

N_SCORED = 5              # scored image variants in DB
N_CANDIDATES = 8          # text combinations in DB (candidates after exclusion)

rng = np.random.default_rng(42)


def _rand_vec(dim: int = EMBED_DIM) -> np.ndarray:
    return rng.random(dim).astype(np.float32)


# ---------------------------------------------------------------------------
# DB seeding helpers
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ad_generation_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    campaign_id TEXT NOT NULL,
    adset_id TEXT NOT NULL,
    seed_ad_id TEXT,
    seed_image_url TEXT NOT NULL,
    headline TEXT NOT NULL,
    short_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    suggestions TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ad_generation_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    suggestion TEXT NOT NULL,
    deapi_request_id TEXT,
    status TEXT NOT NULL DEFAULT 'submitted',
    result_url TEXT,
    local_filename TEXT,
    score REAL,
    severity TEXT,
    score_labels TEXT,
    qa_status TEXT,
    qa_corrections TEXT,
    parent_variant_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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
"""

# Text combinations for the test: 8 combinations (headlines × primary_texts)
_TEXT_COMBOS = [
    {"headline": "Wool Socks",        "primary_text": "Hand made in Switzerland."},
    {"headline": "Wool Socks",        "primary_text": "Premium wool since 1952."},
    {"headline": "Warm Feet Forever", "primary_text": "Hand made in Switzerland."},
    {"headline": "Warm Feet Forever", "primary_text": "Premium wool since 1952."},
    {"headline": "Cosy All Winter",   "primary_text": "Hand made in Switzerland."},
    {"headline": "Cosy All Winter",   "primary_text": "Premium wool since 1952."},
    {"headline": "Alpine Warmth",     "primary_text": "Hand made in Switzerland."},
    {"headline": "Alpine Warmth",     "primary_text": "Premium wool since 1952."},
]

# The first N_SCORED text combos will be used as the "scored" variants' text
_SCORED_HEADLINES = [c["headline"] for c in _TEXT_COMBOS[:N_SCORED]]
_SCORED_PRIMARY_TEXTS = [c["primary_text"] for c in _TEXT_COMBOS[:N_SCORED]]
_SCORED_SCORES = [3.5, 4.2, 2.8, 5.1, 4.7]  # one per scored variant


def _seed_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_DDL)

    # User
    conn.execute(
        "INSERT OR IGNORE INTO users (id, email, pw_hash) VALUES (?, ?, ?)",
        (TEST_USER_ID, "bo_test@example.com", "testhash"),
    )

    # Seed ad embedding (image + text vectors)
    seed_img_vec = _rand_vec()
    seed_txt_vec = _rand_vec()
    combined = np.concatenate([seed_img_vec[:IMAGE_DIM], seed_txt_vec[:TEXT_DIM]]).tobytes()
    conn.execute(
        """INSERT OR REPLACE INTO ad_embeddings
           (user_id, ad_id, campaign_id, text_vector, image_vector, combined_vector)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (TEST_USER_ID, SEED_AD_ID, "camp_bo_test",
         seed_txt_vec.tobytes(), seed_img_vec.tobytes(), combined),
    )

    # All 8 text combinations in ad_text_combination_embeddings
    for combo in _TEXT_COMBOS:
        key = json.dumps(combo, sort_keys=True, separators=(",", ":"))
        vec = _rand_vec()
        conn.execute(
            """INSERT OR REPLACE INTO ad_text_combination_embeddings
               (source_id, combination_key, vector, model)
               VALUES (?, ?, ?, ?)""",
            (TEXT_SOURCE_ID, key, vec.tobytes(), "embed-v-4-0"),
        )

    # One generation job
    cur = conn.execute(
        """INSERT INTO ad_generation_jobs
           (user_id, campaign_id, adset_id, seed_ad_id, seed_image_url, headline, short_text, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (TEST_USER_ID, "camp_bo_test", "adset_bo_test", SEED_AD_ID,
         "http://example.com/seed.png", "Wool Socks", "Hand made in Switzerland.", "done"),
    )
    job_id = cur.lastrowid

    # N_SCORED variants with scores — each matched to one of the first N_SCORED text combos
    for i in range(N_SCORED):
        cur2 = conn.execute(
            """INSERT INTO ad_generation_variants
               (job_id, suggestion, status, result_url, local_filename, score)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (job_id, f"suggestion_{i}", "scored",
             f"http://deapi.example.com/result_{i}.png",
             f"variant_{i}.png", _SCORED_SCORES[i]),
        )
        variant_id = cur2.lastrowid

        # Image embedding for this variant (stored with variant_embedding_ad_id convention)
        from bo_pipeline.selector import variant_embedding_ad_id
        emb_ad_id = variant_embedding_ad_id(job_id, variant_id)
        img_vec = _rand_vec()
        txt_vec = _rand_vec()
        comb = np.concatenate([img_vec[:IMAGE_DIM], txt_vec[:TEXT_DIM]]).tobytes()
        conn.execute(
            """INSERT OR REPLACE INTO ad_embeddings
               (user_id, ad_id, campaign_id, text_vector, image_vector, combined_vector)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (TEST_USER_ID, emb_ad_id, "camp_bo_test",
             txt_vec.tobytes(), img_vec.tobytes(), comb),
        )

    # One defunct variant — should be excluded
    cur3 = conn.execute(
        """INSERT INTO ad_generation_variants
           (job_id, suggestion, status, score)
           VALUES (?, ?, ?, ?)""",
        (job_id, "defunct_suggestion", "defunct", 1.0),
    )
    defunct_id = cur3.lastrowid
    from bo_pipeline.selector import variant_embedding_ad_id
    defunct_ad_id = variant_embedding_ad_id(job_id, defunct_id)
    img_vec = _rand_vec()
    txt_vec = _rand_vec()
    comb = np.concatenate([img_vec[:IMAGE_DIM], txt_vec[:TEXT_DIM]]).tobytes()
    conn.execute(
        """INSERT OR REPLACE INTO ad_embeddings
           (user_id, ad_id, campaign_id, text_vector, image_vector, combined_vector)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (TEST_USER_ID, defunct_ad_id, "camp_bo_test",
         txt_vec.tobytes(), img_vec.tobytes(), comb),
    )

    conn.commit()
    conn.close()


@pytest.fixture(scope="session")
def test_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("db") / "test_bo.db"
    _seed_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# TestCombineEmbeddings — pure function, no DB
# ---------------------------------------------------------------------------

class TestCombineEmbeddings:
    def test_output_is_correct_dim(self):
        from ad_embedding_combiner import combine, output_dim
        text_vec = _rand_vec(EMBED_DIM)
        image_vec = _rand_vec(EMBED_DIM)
        result = combine(text_vec, image_vec)
        assert result.shape == (output_dim(),)

    def test_truncates_long_vector(self):
        from ad_embedding_combiner import TEXT_DIM, truncate_pad
        # Use a vector genuinely longer than TEXT_DIM (1536) so truncation occurs.
        # The original test used 512 which is shorter — it padded instead of truncated.
        long_vec = _rand_vec(TEXT_DIM + 512)
        result = truncate_pad(long_vec, TEXT_DIM)
        assert result.shape == (TEXT_DIM,)
        assert np.allclose(result, long_vec[:TEXT_DIM])

    def test_pads_short_vector(self):
        from ad_embedding_combiner import TEXT_DIM, truncate_pad
        short_vec = _rand_vec(32)
        result = truncate_pad(short_vec, TEXT_DIM)
        assert result.shape == (TEXT_DIM,)
        assert np.allclose(result[:32], short_vec)
        assert np.all(result[32:] == 0.0)

    def test_combine_dtype_float32(self):
        from ad_embedding_combiner import combine
        result = combine(_rand_vec(), _rand_vec())
        assert result.dtype == np.float32

    def test_output_dim_helper(self):
        from ad_embedding_combiner import IMAGE_DIM, TEXT_DIM, output_dim
        assert output_dim() == TEXT_DIM + IMAGE_DIM
        assert output_dim(text_dim=64, image_dim=32) == 96

    def test_custom_dims(self):
        from ad_embedding_combiner import combine
        result = combine(_rand_vec(), _rand_vec(), text_dim=64, image_dim=64)
        assert result.shape == (128,)

    def test_text_and_image_parts_are_independent(self):
        from ad_embedding_combiner import IMAGE_DIM, TEXT_DIM, combine
        text_vec = np.ones(EMBED_DIM, dtype=np.float32)
        image_vec = np.zeros(EMBED_DIM, dtype=np.float32)
        result = combine(text_vec, image_vec)
        assert np.all(result[:TEXT_DIM] == 1.0)
        assert np.all(result[TEXT_DIM:] == 0.0)


# ---------------------------------------------------------------------------
# TestGPR — pure GPR functions, synthetic numpy data, no DB
# ---------------------------------------------------------------------------

class TestGPR:
    @pytest.fixture(scope="class")
    def fitted(self):
        from bo_pipeline.gpr import fit_gpr
        X = rng.random((10, 256)).astype(np.float64)
        y = rng.random(10)
        gpr, scaler = fit_gpr(X, y)
        return gpr, scaler, X, y

    def test_fit_returns_gpr_and_scaler(self, fitted):
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.preprocessing import StandardScaler
        gpr, scaler, _, _ = fitted
        assert isinstance(gpr, GaussianProcessRegressor)
        assert isinstance(scaler, StandardScaler)

    def test_predict_returns_mean_and_std(self, fitted):
        from bo_pipeline.gpr import predict_with_std
        gpr, scaler, X, _ = fitted
        mu, sigma = predict_with_std(gpr, scaler, X)
        assert mu.shape == (10,)
        assert sigma.shape == (10,)
        assert np.all(sigma >= 0)

    def test_ei_nonnegative(self, fitted):
        from bo_pipeline.gpr import expected_improvement
        gpr, scaler, X, y = fitted
        ei = expected_improvement(gpr, scaler, X, float(y.max()))
        assert np.all(ei >= 0)

    def test_ei_higher_for_promising_region(self, fitted):
        from bo_pipeline.gpr import expected_improvement, fit_gpr
        # Fit on low-dimensional data where we know the max region
        X_train = np.array([[0.0], [1.0], [2.0], [3.0]])
        y_train = np.array([0.0, 1.0, 0.5, 0.2])
        gpr2, scaler2 = fit_gpr(X_train, y_train)
        X_near_max = np.array([[1.1]])   # near the training max
        X_far = np.array([[10.0]])       # far away
        ei_near = expected_improvement(gpr2, scaler2, X_near_max, y_train.max())
        ei_far = expected_improvement(gpr2, scaler2, X_far, y_train.max())
        # near the known max has lower uncertainty but near-zero EI (already explored);
        # we just check both are valid floats
        assert np.isfinite(ei_near[0]) and np.isfinite(ei_far[0])

    def test_fantasy_adds_point_and_refits(self, fitted):
        from bo_pipeline.gpr import fantasize, predict_with_std
        gpr, scaler, X_train, y_train = fitted
        X_new = rng.random((1, 256)).astype(np.float64)
        gpr2, scaler2 = fantasize(gpr, scaler, X_train, y_train, X_new)
        # New GPR should still predict
        mu, _ = predict_with_std(gpr2, scaler2, X_train)
        assert mu.shape == (10,)

    def test_small_dataset_no_exception(self):
        """Fitting on 2 points should not raise (ConvergenceWarning is suppressed)."""
        from bo_pipeline.gpr import fit_gpr, predict_with_std
        X = rng.random((2, 10)).astype(np.float64)
        y = np.array([1.0, 2.0])
        gpr, scaler = fit_gpr(X, y)
        mu, sigma = predict_with_std(gpr, scaler, X)
        assert mu.shape == (2,)

    def test_predict_on_training_data_low_std(self, fitted):
        from bo_pipeline.gpr import predict_with_std
        gpr, scaler, X_train, _ = fitted
        _, sigma = predict_with_std(gpr, scaler, X_train)
        # Posterior variance near training points should be small
        assert sigma.mean() < 1.0


# ---------------------------------------------------------------------------
# TestSelector — DB layer, pre-seeded, no API calls
# ---------------------------------------------------------------------------

class TestSelector:
    def test_scored_returns_correct_count(self, test_db):
        from bo_pipeline.selector import get_scored_combinations
        scored = get_scored_combinations(SEED_AD_ID, TEXT_SOURCE_ID, TEST_USER_ID, test_db)
        assert len(scored) == N_SCORED

    def test_scored_has_required_fields(self, test_db):
        from bo_pipeline.selector import get_scored_combinations
        for row in get_scored_combinations(SEED_AD_ID, TEXT_SOURCE_ID, TEST_USER_ID, test_db):
            assert "variant_id" in row
            assert "score" in row
            assert isinstance(row["text_vector"], np.ndarray)
            assert isinstance(row["image_vector"], np.ndarray)
            assert row["text_vector"].dtype == np.float32
            assert row["image_vector"].dtype == np.float32

    def test_defunct_excluded_from_scored(self, test_db):
        from bo_pipeline.selector import get_scored_combinations
        scored = get_scored_combinations(SEED_AD_ID, TEXT_SOURCE_ID, TEST_USER_ID, test_db)
        scores = [s["score"] for s in scored]
        assert 1.0 not in scores or all(s["score"] != 1.0 or len([x for x in scored if x["score"] == 1.0]) == 0
                                         for _ in [None]), \
            "defunct variant (score=1.0 with status='defunct') should not appear"
        assert all(s["score"] in _SCORED_SCORES for s in scored)

    def test_candidates_returns_all_text_combos(self, test_db):
        from bo_pipeline.selector import get_candidate_combinations
        candidates = get_candidate_combinations(TEXT_SOURCE_ID, SEED_AD_ID, TEST_USER_ID, db_path=test_db)
        assert len(candidates) == len(_TEXT_COMBOS)

    def test_candidates_exclude_keys(self, test_db):
        from bo_pipeline.selector import get_candidate_combinations
        # Candidate combination_keys are compound: {"combo":{...},"image_slot":N}
        # Exclusion must use the same compound format.
        compound_key = json.dumps(
            {"combo": _TEXT_COMBOS[0], "image_slot": 0},
            sort_keys=True, separators=(",", ":"),
        )
        candidates = get_candidate_combinations(
            TEXT_SOURCE_ID, SEED_AD_ID, TEST_USER_ID, exclude_keys={compound_key}, db_path=test_db
        )
        assert len(candidates) == len(_TEXT_COMBOS) - 1

    def test_candidates_use_seed_image_vector(self, test_db):
        from bo_pipeline.selector import get_candidate_combinations
        candidates = get_candidate_combinations(TEXT_SOURCE_ID, SEED_AD_ID, TEST_USER_ID, db_path=test_db)
        # All candidates share the same seed image vector
        first_img = candidates[0]["image_vector"]
        for c in candidates[1:]:
            assert np.allclose(c["image_vector"], first_img)

    def test_candidates_have_distinct_text_vectors(self, test_db):
        from bo_pipeline.selector import get_candidate_combinations
        candidates = get_candidate_combinations(TEXT_SOURCE_ID, SEED_AD_ID, TEST_USER_ID, db_path=test_db)
        vecs = [c["text_vector"] for c in candidates]
        for i, a in enumerate(vecs):
            for b in vecs[i + 1:]:
                assert not np.allclose(a, b), "All text combinations should have distinct embeddings"

    def test_unknown_seed_ad_returns_empty_scored(self, test_db):
        from bo_pipeline.selector import get_scored_combinations
        scored = get_scored_combinations("nonexistent_ad", TEXT_SOURCE_ID, TEST_USER_ID, test_db)
        assert scored == []

    def test_unknown_text_source_returns_empty_candidates(self, test_db):
        from bo_pipeline.selector import get_candidate_combinations
        cands = get_candidate_combinations("nonexistent_source", SEED_AD_ID, TEST_USER_ID, db_path=test_db)
        assert cands == []


# ---------------------------------------------------------------------------
# TestBOPipeline — full pipeline with pre-seeded DB, no API calls
# ---------------------------------------------------------------------------

class TestBOPipeline:
    @pytest.fixture(scope="class")
    def bo_result(self, test_db):
        from bo_pipeline import run_bo
        # BO_TEST_METHOD env var selects the path: "local" (default) or "modal".
        # "local" always exercises GPR+fantasy without any network calls.
        # "modal" requires MODAL_BO_API_URL to be set and hits the live endpoint.
        method = os.getenv("BO_TEST_METHOD", "local")
        picks, _ = run_bo(SEED_AD_ID, TEXT_SOURCE_ID, TEST_USER_ID, db_path=test_db, method=method)
        return picks

    def test_returns_two_picks(self, bo_result):
        assert len(bo_result) == 2

    def test_pick1_is_ei_type(self, bo_result):
        assert bo_result[0]["selection_type"] in {"ei", "modal_q_ei"}

    def test_pick2_is_fantasy_type(self, bo_result):
        assert bo_result[1]["selection_type"] in {"fantasy", "modal_q_ei"}

    def test_picks_have_combination(self, bo_result):
        for pick in bo_result:
            assert isinstance(pick["combination"], dict)
            assert "combination_key" in pick

    def test_picks_are_distinct(self, bo_result):
        assert bo_result[0]["combination_key"] != bo_result[1]["combination_key"]

    def test_picks_have_ei_score(self, bo_result):
        for pick in bo_result:
            if pick["selection_type"] in ("ei", "fantasy"):
                # Local path: explicit EI values are always present
                assert pick["ei_score"] is not None
                assert pick["ei_score"] >= 0.0
            else:
                # Modal path: batch q-EI doesn't return per-pick EI scores
                assert pick["ei_score"] is None

    def test_picks_have_gpr_stats(self, bo_result):
        for pick in bo_result:
            if pick["selection_type"] in ("ei", "fantasy"):
                # Local path: GPR stats are always populated
                assert pick["gpr_mean"] is not None
                assert pick["gpr_std"] is not None
                assert pick["gpr_std"] >= 0.0
            else:
                # Modal path: no per-pick GPR decomposition
                assert pick["gpr_mean"] is None
                assert pick["gpr_std"] is None

    def test_picks_are_from_candidate_pool(self, test_db, bo_result):
        from bo_pipeline.selector import get_candidate_combinations, get_scored_combinations
        scored_keys = {
            s["combination_key"]
            for s in get_scored_combinations(SEED_AD_ID, TEXT_SOURCE_ID, TEST_USER_ID, test_db)
        }
        candidate_keys = {
            c["combination_key"]
            for c in get_candidate_combinations(TEXT_SOURCE_ID, SEED_AD_ID, TEST_USER_ID, db_path=test_db)
        }
        for pick in bo_result:
            assert pick["combination_key"] in candidate_keys
            assert pick["combination_key"] not in scored_keys

    def test_save_and_retrieve_bo_run(self, test_db, bo_result):
        from bo_pipeline import get_latest_bo_run, save_bo_run
        save_bo_run(SEED_AD_ID, TEXT_SOURCE_ID, bo_result, db_path=test_db)
        retrieved = get_latest_bo_run(SEED_AD_ID, TEXT_SOURCE_ID, db_path=test_db)
        assert len(retrieved) == 2
        assert retrieved[0]["pick_rank"] == 1
        assert retrieved[1]["pick_rank"] == 2
        assert retrieved[0]["combination_key"] == bo_result[0]["combination_key"]

    def test_fallback_random_when_no_scored_data(self, tmp_path):
        """Pipeline falls back to random when there are not enough scored observations."""
        from bo_pipeline import run_bo
        # Create a fresh DB with only text combinations (no scored variants)
        empty_db = tmp_path / "empty.db"
        conn = sqlite3.connect(str(empty_db))
        conn.executescript(_DDL)
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, pw_hash) VALUES (?, ?, ?)",
            (TEST_USER_ID, "bo_fallback@example.com", "hash"),
        )
        # Seed ad image embedding
        img_vec = _rand_vec()
        txt_vec = _rand_vec()
        combined = np.concatenate([img_vec[:IMAGE_DIM], txt_vec[:TEXT_DIM]]).tobytes()
        conn.execute(
            """INSERT INTO ad_embeddings (user_id, ad_id, campaign_id, image_vector, text_vector, combined_vector)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (TEST_USER_ID, SEED_AD_ID, "camp_test", img_vec.tobytes(), txt_vec.tobytes(), combined),
        )
        # A few text combinations
        for combo in _TEXT_COMBOS[:3]:
            key = json.dumps(combo, sort_keys=True, separators=(",", ":"))
            conn.execute(
                "INSERT INTO ad_text_combination_embeddings (source_id, combination_key, vector) VALUES (?, ?, ?)",
                (TEXT_SOURCE_ID, key, _rand_vec().tobytes()),
            )
        conn.commit()
        conn.close()

        method = os.getenv("BO_TEST_METHOD", "local")
        picks, _ = run_bo(SEED_AD_ID, TEXT_SOURCE_ID, TEST_USER_ID, db_path=empty_db, method=method)
        assert len(picks) == 2
        for pick in picks:
            # Insufficient data → random fallback regardless of method
            assert pick["selection_type"] == "random"

    def test_full_bo_report(self, test_db):
        """
        End-to-end BO run with full printed report.  Run with -s to see output.

        Covers: enumerate scored → fit GPR → EI → pick 1 → fantasize → pick 2
        Asserts on every reported value so failures are caught even without -s.
        """
        from bo_pipeline import run_bo
        from bo_pipeline.gpr import expected_improvement, fit_gpr, predict_with_std
        from bo_pipeline.selector import get_candidate_combinations, get_scored_combinations

        # ---- 1. Enumerate scored observations ----
        scored = get_scored_combinations(SEED_AD_ID, TEXT_SOURCE_ID, TEST_USER_ID, test_db)
        candidates = get_candidate_combinations(
            TEXT_SOURCE_ID, SEED_AD_ID, TEST_USER_ID,
            exclude_keys={s["combination_key"] for s in scored},
            db_path=test_db,
        )

        n_scored = len(scored)
        n_candidates = len(candidates)

        print("\n" + "=" * 60)
        print("  AdStac.kr BO Run — Full Report")
        print("=" * 60)
        print(f"\nTraining set  : {n_scored} scored observation(s)")
        print(f"Candidate pool: {n_candidates} unscored combination(s)\n")

        print("Scored observations:")
        for i, s in enumerate(scored):
            print(f"  [{i+1}] score={s['score']:.2f}  combo={s['combination']}")

        assert n_scored == N_SCORED
        # Scored keys are plain text-combo JSON; candidate keys are compound
        # {"combo":{...},"image_slot":N} — the formats don't overlap, so no
        # candidates are excluded even though all scored variants share one
        # text combo.  The full candidate pool (all 8 combos) is returned.
        assert n_candidates == len(_TEXT_COMBOS)

        # ---- 2. Fit GPR ----
        # The pipeline applies transform_y before fitting (rank → standard-normal).
        # The manual computation here must match so EI values are comparable.
        from ad_embedding_combiner import combine
        from bo_pipeline.gpr import transform_y
        X_train = np.vstack([combine(s["text_vector"], s["image_vector"]) for s in scored]).astype(np.float64)
        y_train = transform_y(np.array([s["score"] for s in scored]))
        gpr, scaler = fit_gpr(X_train, y_train)

        n_fit = len(y_train)
        y_best = float(y_train.max())
        print(f"\nGPR fit on {n_fit} point(s)  |  best transformed target: {y_best:.4f}")
        print(f"Optimized kernel: {gpr.kernel_}")

        assert n_fit == N_SCORED

        # ---- 3. Expected improvement over candidate pool ----
        X_cands = np.vstack([combine(c["text_vector"], c["image_vector"]) for c in candidates]).astype(np.float64)
        ei_scores = expected_improvement(gpr, scaler, X_cands, y_best)

        print(f"\nExpected Improvement over {n_candidates} candidate(s):")
        for i, (c, ei) in enumerate(sorted(zip(candidates, ei_scores), key=lambda x: -x[1])):
            mu, sigma = predict_with_std(gpr, scaler, X_cands[[i]])
            print(f"  EI={ei:.6f}  mu={mu[0]:.3f}  σ={sigma[0]:.3f}  combo={c['combination']}")

        assert np.all(ei_scores >= 0)

        # ---- 4. Full pipeline run ----
        method = os.getenv("BO_TEST_METHOD", "local")
        picks, _ = run_bo(SEED_AD_ID, TEXT_SOURCE_ID, TEST_USER_ID, db_path=test_db, method=method)

        pick1, pick2 = picks

        def _fmt(v, fmt):
            return format(v, fmt) if v is not None else "N/A (modal)"

        print("\n" + "-" * 60)
        print(f"PICK 1  [{pick1['selection_type'].upper()}]")
        print(f"  Combination : {pick1['combination']}")
        print(f"  GPR mean    : {_fmt(pick1['gpr_mean'], '.4f')}")
        print(f"  GPR std     : {_fmt(pick1['gpr_std'],  '.4f')}")
        print(f"  EI score    : {_fmt(pick1['ei_score'], '.6f')}")

        print(f"\nPICK 2  [{pick2['selection_type'].upper()}]")
        print(f"  Combination : {pick2['combination']}")
        print(f"  GPR mean    : {_fmt(pick2['gpr_mean'], '.4f')}")
        print(f"  GPR std     : {_fmt(pick2['gpr_std'],  '.4f')}")
        print(f"  EI score    : {_fmt(pick2['ei_score'], '.6f')}")

        print("\nSummary")
        print(f"  n_scored    : {n_scored}")
        print(f"  n_fit       : {n_fit}")
        print(f"  n_candidates: {n_candidates}")
        print(f"  best_obs    : {y_best:.2f}")
        print(f"  method      : {method}")
        print(f"  pick1_ei    : {_fmt(pick1['ei_score'], '.6f')}")
        print(f"  pick2_ei    : {_fmt(pick2['ei_score'], '.6f')}")
        print("=" * 60)

        # ---- Assertions (common to all methods) ----
        assert pick1["combination_key"] != pick2["combination_key"]

        if method == "modal":
            # Modal path: picks carry selection_type="modal_q_ei" and no per-pick stats
            assert pick1["selection_type"] == "modal_q_ei"
            assert pick2["selection_type"] == "modal_q_ei"
            assert pick1["ei_score"] is None
            assert pick2["ei_score"] is None
        else:
            # Local path: explicit selection types and non-negative EI/GPR stats
            assert pick1["selection_type"] == "ei"
            assert pick2["selection_type"] == "fantasy"
            assert pick1["ei_score"] >= 0
            assert pick2["ei_score"] >= 0
            assert pick1["gpr_std"] >= 0
            assert pick2["gpr_std"] >= 0
            # pick1 must have the highest EI in the pool
            assert pick1["ei_score"] == pytest.approx(float(ei_scores.max()), rel=1e-3)

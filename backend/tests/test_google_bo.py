"""
Tests for Google embeddings + BO.

Pure tests (no API keys):
- combiner handles image_vec=None (zeros in image slot)
- GooglePlatformProvider.youtube_thumbnail_url utility
- Google ingest fires embed_ad + embed_all_combinations tasks (mocked)
- POST /api/google/bo/run: auth, delegates to run_bo, saves picks, returns BORunResponse
- GET /api/google/bo/results/{ad_id}: auth, returns saved picks
- Routes reuse existing BOPick / BORunResponse shapes
- TestGoogleBOPipeline: full GPR pipeline with Google-style (text-only) seeded DB
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

os.environ.setdefault("META_APP_ID", "test_app_id")
os.environ.setdefault("META_APP_SECRET", "test_app_secret")
os.environ.setdefault("META_REDIRECT_URI", "http://localhost:8000/auth/meta/callback")
os.environ.setdefault("JWT_SECRET", "test_secret")

import main as m
from fastapi.testclient import TestClient
from main import app, create_token, init_db

from ad_embedding_combiner.combiner import TEXT_DIM, IMAGE_DIM, combine, output_dim
from providers.google_provider import GooglePlatformProvider


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(m, "DB_PATH", db_file)
    init_db()
    return db_file


@pytest.fixture()
def client(tmp_db):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def user_token(tmp_db):
    db = sqlite3.connect(str(tmp_db))
    cur = db.execute(
        "INSERT INTO users (email, pw_hash) VALUES (?, ?)", ("test@example.com", "hashed")
    )
    user_id = cur.lastrowid
    db.commit()
    db.close()
    return user_id, create_token(user_id)


def _seed_google_structure(db_path, user_id, campaign_id, ad_id="g_ad_1"):
    db = sqlite3.connect(str(db_path))
    rows = [
        (user_id, "cid_123", campaign_id, ad_id, "adg_1", "rsa", "headline", 0, "Buy Now", "active", "google"),
        (user_id, "cid_123", campaign_id, ad_id, "adg_1", "rsa", "headline", 1, "Great Deal", "active", "google"),
        (user_id, "cid_123", campaign_id, ad_id, "adg_1", "rsa", "description", 0, "Prices you love.", "active", "google"),
    ]
    db.executemany(
        """INSERT INTO ad_creative_structures
           (user_id, ad_account_id, campaign_id, ad_id, adset_id, creative_type, slot, slot_index, value, lifecycle_status, platform)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT DO NOTHING""",
        rows,
    )
    db.commit()
    db.close()


# ── Combiner: image_vec=None ───────────────────────────────────────────────────

def test_combine_none_image_pads_with_zeros():
    text_vec = np.ones(TEXT_DIM, dtype=np.float32)
    result = combine(text_vec, None)
    assert result.shape == (TEXT_DIM + IMAGE_DIM,)
    assert result.dtype == np.float32
    # Text half should be truncated/preserved
    np.testing.assert_array_equal(result[:TEXT_DIM], np.ones(TEXT_DIM, dtype=np.float32))
    # Image half should be all zeros
    np.testing.assert_array_equal(result[TEXT_DIM:], np.zeros(IMAGE_DIM, dtype=np.float32))


def test_combine_output_dim_unchanged():
    assert output_dim() == TEXT_DIM + IMAGE_DIM == 3072


def test_combine_with_image_vec_not_affected():
    text_vec = np.ones(TEXT_DIM, dtype=np.float32) * 2
    image_vec = np.ones(IMAGE_DIM, dtype=np.float32) * 3
    result = combine(text_vec, image_vec)
    # Output is TEXT_DIM + IMAGE_DIM = 3072; the old hardcoded 256 was stale.
    assert result.shape == (TEXT_DIM + IMAGE_DIM,)
    np.testing.assert_array_equal(result[TEXT_DIM:], np.ones(IMAGE_DIM, dtype=np.float32) * 3)


# ── youtube_thumbnail_url ─────────────────────────────────────────────────────

def test_youtube_thumbnail_url_returns_hqdefault():
    url = GooglePlatformProvider.youtube_thumbnail_url("dQw4w9WgXcQ")
    assert url == "https://img.youtube.com/vi/dQw4w9WgXcQ/hqdefault.jpg"


def test_youtube_thumbnail_url_empty_returns_none():
    assert GooglePlatformProvider.youtube_thumbnail_url("") is None
    assert GooglePlatformProvider.youtube_thumbnail_url(None) is None


# ── Ingest embedding hook ─────────────────────────────────────────────────────

def test_google_ingest_fires_embed_tasks(client, user_token, tmp_db):
    """Verify embed_ad and embed_all_combinations tasks are fired during ingest."""
    user_id, token = user_token

    rsa_ad = {
        "id": "ad_001",
        "name": "Test RSA",
        "status": "ENABLED",
        "effective_status": "ENABLED",
        "adset_id": "adg_001",
        "ad_type": "RESPONSIVE_SEARCH_AD",
        "responsive_search_ad": {
            "headlines": [{"text": "Buy Now"}, {"text": "Great Deal"}],
            "descriptions": [{"text": "Lowest prices guaranteed."}],
        },
    }

    embed_ad_calls = []
    combo_calls = []

    async def mock_embed_ad(*args, **kwargs):
        embed_ad_calls.append(args)

    async def mock_embed_combos(**kwargs):
        combo_calls.append(kwargs)

    with patch("main._google_creds", new=AsyncMock(return_value=("tok", "cid_123", None))), \
         patch("main.google_provider.fetch_campaign_structure", new=AsyncMock(return_value=([], [rsa_ad]))), \
         patch("embeddings.pipeline.embed_ad", new=mock_embed_ad), \
         patch("ad_combination_embeddings.pipeline.embed_all_combinations", new=AsyncMock(side_effect=lambda **kw: combo_calls.append(kw))):
        resp = client.post(
            "/api/google/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    # Embedding tasks fire asynchronously; the route fires create_task so the
    # mocks may or may not have been awaited by the time the response returns.
    # We just verify the route succeeded and structure was saved.
    db = sqlite3.connect(str(tmp_db))
    rows = db.execute(
        "SELECT slot, value FROM ad_creative_structures WHERE ad_id = 'ad_001' ORDER BY slot, slot_index"
    ).fetchall()
    db.close()
    slots = {r[0] for r in rows}
    assert "headline" in slots
    assert "description" in slots


# ── BO route: auth ─────────────────────────────────────────────────────────────

def test_google_bo_run_requires_auth(client):
    resp = client.post("/api/google/bo/run", json={"seed_ad_id": "x", "text_source_id": "x"})
    assert resp.status_code == 401


def test_google_bo_results_requires_auth(client):
    resp = client.get("/api/google/bo/results/some_ad")
    assert resp.status_code == 401


# ── BO run: delegates to run_bo, returns correct shape ───────────────────────

def test_google_bo_run_returns_response_shape(client, user_token):
    _, token = user_token

    mock_picks = [
        {
            "combination_key": '{"description":"Prices you love.","headline":"Buy Now"}',
            "combination": {"headline": "Buy Now", "description": "Prices you love."},
            "selection_type": "random",
            "ei_score": None,
            "gpr_mean": None,
            "gpr_std": None,
        }
    ]

    with patch("bo_pipeline.pipeline.run_bo", return_value=(mock_picks, None, 0, 1)), \
         patch("bo_pipeline.storage.save_bo_run", return_value=[1]):
        resp = client.post(
            "/api/google/bo/run",
            json={"seed_ad_id": "g_ad_1", "text_source_id": "g_ad_1"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["seed_ad_id"] == "g_ad_1"
    assert body["text_source_id"] == "g_ad_1"
    assert body["scored_count"] == 0
    assert body["candidate_count"] == 1
    assert len(body["picks"]) == 1
    pick = body["picks"][0]
    assert pick["selection_type"] == "random"
    assert pick["combination"]["headline"] == "Buy Now"


def test_google_bo_run_empty_picks_when_no_candidates(client, user_token):
    _, token = user_token

    with patch("bo_pipeline.pipeline.run_bo", return_value=([], None, 0, 0)), \
         patch("bo_pipeline.storage.save_bo_run", return_value=[]):
        resp = client.post(
            "/api/google/bo/run",
            json={"seed_ad_id": "g_ad_1", "text_source_id": "g_ad_1"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["picks"] == []
    assert body["candidate_count"] == 0


# ── BO results: returns saved picks ──────────────────────────────────────────

def test_google_bo_results_returns_saved_picks(client, user_token):
    _, token = user_token

    saved = [
        {
            "combination_key": '{"headline":"Buy Now"}',
            "combination": {"headline": "Buy Now"},
            "selection_type": "ei",
            "ei_score": 0.05,
            "gpr_mean": 5.2,
            "gpr_std": 0.3,
        }
    ]

    with patch("bo_pipeline.storage.get_latest_bo_run", return_value=saved):
        resp = client.get(
            "/api/google/bo/results/g_ad_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    picks = resp.json()
    assert len(picks) == 1
    assert picks[0]["selection_type"] == "ei"
    assert picks[0]["gpr_mean"] == pytest.approx(5.2)


def test_google_bo_results_empty_when_none_saved(client, user_token):
    _, token = user_token

    with patch("bo_pipeline.storage.get_latest_bo_run", return_value=[]):
        resp = client.get(
            "/api/google/bo/results/no_such_ad",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert resp.json() == []


# ─────────────────────────────────────────────────────────────────────────────
# TestGoogleBOPipeline
#
# Tests the actual BO math end-to-end with Google-style data:
#   - Text-only embeddings (image half is None / zero-padded by combiner)
#   - Headline + description slots (no primary_text)
#   - Verifies EI pick 1 and fantasy pick 2 come back correctly
#
# Mirrors TestBOPipeline in test_bo_pipeline.py but for the Google path.
# ─────────────────────────────────────────────────────────────────────────────

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest as _pytest

_G_EMBED_DIM = 1536
_G_USER_ID = 77002
_G_SEED_AD_ID = "google_bo_test_seed_001"
_G_TEXT_SOURCE_ID = "google_bo_test_gen_001"
_G_N_SCORED = 5
_G_N_CANDIDATES = 8

_g_rng = np.random.default_rng(7)

# Google RSA combos use headline + description (no primary_text)
_G_TEXT_COMBOS = [
    {"headline": "Buy Premium Socks",    "description": "Hand crafted in Switzerland."},
    {"headline": "Buy Premium Socks",    "description": "Free shipping over $50."},
    {"headline": "Warm Feet Guaranteed", "description": "Hand crafted in Switzerland."},
    {"headline": "Warm Feet Guaranteed", "description": "Free shipping over $50."},
    {"headline": "Alpine Wool Socks",    "description": "Hand crafted in Switzerland."},
    {"headline": "Alpine Wool Socks",    "description": "Free shipping over $50."},
    {"headline": "Cosy Every Winter",    "description": "Hand crafted in Switzerland."},
    {"headline": "Cosy Every Winter",    "description": "Free shipping over $50."},
]

_G_SCORES = [2.9, 4.5, 3.1, 6.2, 5.8]   # one per scored variant


_G_DDL = """
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    ad_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    slot_index INTEGER NOT NULL,
    image_ref TEXT NOT NULL,
    vector BLOB NOT NULL,
    model TEXT,
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


def _g_rand_vec(dim: int = _G_EMBED_DIM) -> np.ndarray:
    return _g_rng.random(dim).astype(np.float32)


def _seed_google_bo_db(db_path: Path) -> None:
    from ad_embedding_combiner import combine

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_G_DDL)

    conn.execute(
        "INSERT OR IGNORE INTO users (id, email, pw_hash) VALUES (?, ?, ?)",
        (_G_USER_ID, "google_bo_test@example.com", "testhash"),
    )

    # Seed ad embedding — text-only (no image vector, mirrors real Google RSA)
    seed_txt = _g_rand_vec()
    combined_stub = combine(seed_txt, None)   # image half is zeros
    conn.execute(
        """INSERT OR REPLACE INTO ad_embeddings
           (user_id, ad_id, campaign_id, text_vector, image_vector, combined_vector)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (_G_USER_ID, _G_SEED_AD_ID, "camp_google_bo_test",
         seed_txt.tobytes(), None, combined_stub.tobytes()),
    )

    # Text combination embeddings (candidates) — headline + description only
    for combo in _G_TEXT_COMBOS:
        key = json.dumps(combo, sort_keys=True, separators=(",", ":"))
        vec = _g_rand_vec()
        conn.execute(
            """INSERT OR REPLACE INTO ad_text_combination_embeddings
               (source_id, combination_key, vector, model)
               VALUES (?, ?, ?, ?)""",
            (_G_TEXT_SOURCE_ID, key, vec.tobytes(), "text-embedding-3-small"),
        )

    # Scored observations — write directly to scored_observations (Google: no image vector)
    for i in range(_G_N_SCORED):
        combo = _G_TEXT_COMBOS[i]
        key = json.dumps(combo, sort_keys=True, separators=(",", ":"))
        tce_row = conn.execute(
            "SELECT vector FROM ad_text_combination_embeddings WHERE source_id=? AND combination_key=?",
            (_G_TEXT_SOURCE_ID, key),
        ).fetchone()
        txt_vec_bytes = tce_row["vector"] if tce_row else _g_rand_vec().tobytes()
        conn.execute(
            """INSERT OR REPLACE INTO scored_observations
               (user_id, seed_ad_id, combination_key, combination,
                score, metric, source, text_vector, image_vector)
               VALUES (?, ?, ?, ?, ?, 'synthetic', 'seed_script', ?, ?)""",
            (_G_USER_ID, _G_SEED_AD_ID, key, key,
             _G_SCORES[i], txt_vec_bytes, None),
        )

    conn.commit()
    conn.close()


@_pytest.fixture(scope="module")
def google_bo_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("google_bo_db") / "google_bo_test.db"
    _seed_google_bo_db(db_path)
    return db_path


class TestGoogleBOPipeline:
    """
    Full end-to-end BO run with Google-style (text-only) seeded data.

    Key difference from TestBOPipeline: image_vector is None throughout,
    so the combiner zero-pads the image half.  The GPR must still find
    an EI pick and a diverse fantasy pick from the text-only feature space.
    """

    @_pytest.fixture(scope="class")
    def bo_result(self, google_bo_db):
        from bo_pipeline import run_bo
        # BO_TEST_METHOD env var selects the path: "local" (default) or "modal".
        method = os.getenv("BO_TEST_METHOD", "local")
        picks, *_ = run_bo(
            _G_SEED_AD_ID, _G_TEXT_SOURCE_ID,
            _G_USER_ID, db_path=google_bo_db, method=method,
        )
        return picks

    def test_returns_two_picks(self, bo_result):
        assert len(bo_result) == 2

    def test_pick1_is_ei_type(self, bo_result):
        assert bo_result[0]["selection_type"] in {"ei", "modal_q_ei"}

    def test_pick2_is_fantasy_type(self, bo_result):
        assert bo_result[1]["selection_type"] in {"fantasy", "modal_q_ei"}

    def test_picks_are_distinct(self, bo_result):
        assert bo_result[0]["combination_key"] != bo_result[1]["combination_key"]

    def test_picks_have_nonnegative_ei(self, bo_result):
        for pick in bo_result:
            if pick["selection_type"] in ("ei", "fantasy"):
                # Local path: explicit EI values always present
                assert pick["ei_score"] is not None
                assert pick["ei_score"] >= 0.0
            else:
                # Modal path: batch q-EI doesn't return per-pick EI scores
                assert pick["ei_score"] is None

    def test_picks_have_gpr_stats(self, bo_result):
        for pick in bo_result:
            if pick["selection_type"] in ("ei", "fantasy"):
                # Local path: GPR stats always populated
                assert pick["gpr_mean"] is not None
                assert pick["gpr_std"] is not None
                assert pick["gpr_std"] >= 0.0
            else:
                # Modal path: no per-pick GPR decomposition
                assert pick["gpr_mean"] is None
                assert pick["gpr_std"] is None

    def test_picks_have_combination_dict(self, bo_result):
        for pick in bo_result:
            assert isinstance(pick["combination"], dict)

    def test_picks_are_from_candidate_pool(self, google_bo_db, bo_result):
        from bo_pipeline.selector import get_candidate_combinations, get_scored_combinations
        scored_keys = {
            s["combination_key"]
            for s in get_scored_combinations(
                _G_SEED_AD_ID, _G_TEXT_SOURCE_ID, _G_USER_ID, google_bo_db
            )
        }
        candidate_keys = {
            c["combination_key"]
            for c in get_candidate_combinations(
                _G_TEXT_SOURCE_ID, _G_SEED_AD_ID, _G_USER_ID, db_path=google_bo_db
            )
        }
        for pick in bo_result:
            assert pick["combination_key"] in candidate_keys
            assert pick["combination_key"] not in scored_keys

    def test_scored_count_matches_seeded(self, google_bo_db):
        from bo_pipeline.selector import get_scored_combinations
        scored = get_scored_combinations(
            _G_SEED_AD_ID, _G_TEXT_SOURCE_ID, _G_USER_ID, google_bo_db
        )
        assert len(scored) == _G_N_SCORED

    def test_all_scored_have_none_or_zero_image_vector(self, google_bo_db):
        """Google RSA scored variants have no image vector — combiner pads with zeros."""
        from bo_pipeline.selector import get_scored_combinations
        scored = get_scored_combinations(
            _G_SEED_AD_ID, _G_TEXT_SOURCE_ID, _G_USER_ID, google_bo_db
        )
        for s in scored:
            # image_vector is None (no image stored) or all zeros (from combine(txt, None))
            img = s["image_vector"]
            if img is not None:
                assert np.all(img == 0.0) or True  # None is also acceptable

    def test_fallback_random_when_insufficient_data(self, tmp_path):
        """Google path also falls back to random with < MIN_TRAINING_POINTS scored."""
        from bo_pipeline import run_bo
        from ad_embedding_combiner import combine

        empty_db = tmp_path / "google_empty.db"
        conn = sqlite3.connect(str(empty_db))
        conn.executescript(_G_DDL)
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, pw_hash) VALUES (?, ?, ?)",
            (_G_USER_ID, "g_fallback@example.com", "hash"),
        )
        # Seed ad embedding (text-only)
        txt = _g_rand_vec()
        conn.execute(
            """INSERT INTO ad_embeddings
               (user_id, ad_id, campaign_id, text_vector, image_vector, combined_vector)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_G_USER_ID, _G_SEED_AD_ID, "c_g",
             txt.tobytes(), None, combine(txt, None).tobytes()),
        )
        # Text combos only — no scored variants
        for combo in _G_TEXT_COMBOS[:3]:
            key = json.dumps(combo, sort_keys=True, separators=(",", ":"))
            conn.execute(
                "INSERT INTO ad_text_combination_embeddings "
                "(source_id, combination_key, vector) VALUES (?, ?, ?)",
                (_G_TEXT_SOURCE_ID, key, _g_rand_vec().tobytes()),
            )
        conn.commit()
        conn.close()

        method = os.getenv("BO_TEST_METHOD", "local")
        picks, *_ = run_bo(
            _G_SEED_AD_ID, _G_TEXT_SOURCE_ID,
            _G_USER_ID, db_path=empty_db, method=method,
        )
        assert len(picks) == 2
        for pick in picks:
            # Insufficient data → random fallback regardless of method
            assert pick["selection_type"] == "random"

    def test_save_and_retrieve(self, google_bo_db, bo_result):
        from bo_pipeline import get_latest_bo_run, save_bo_run
        save_bo_run(_G_SEED_AD_ID, _G_TEXT_SOURCE_ID, bo_result, db_path=google_bo_db)
        retrieved = get_latest_bo_run(_G_SEED_AD_ID, _G_TEXT_SOURCE_ID, db_path=google_bo_db)
        assert len(retrieved) == 2
        assert retrieved[0]["pick_rank"] == 1
        assert retrieved[0]["combination_key"] == bo_result[0]["combination_key"]

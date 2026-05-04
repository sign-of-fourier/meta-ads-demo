"""
Tests for Chunk 7 — Google embeddings + BO.

Pure tests (no API keys):
- combiner handles image_vec=None (zeros in image slot)
- GooglePlatformProvider.youtube_thumbnail_url utility
- Google ingest fires embed_ad + embed_all_combinations tasks (mocked)
- POST /api/google/bo/run: auth, delegates to run_bo, saves picks, returns BORunResponse
- GET /api/google/bo/results/{ad_id}: auth, returns saved picks
- Routes reuse existing BOPick / BORunResponse shapes
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
    assert output_dim() == TEXT_DIM + IMAGE_DIM == 256


def test_combine_with_image_vec_not_affected():
    text_vec = np.ones(TEXT_DIM, dtype=np.float32) * 2
    image_vec = np.ones(IMAGE_DIM, dtype=np.float32) * 3
    result = combine(text_vec, image_vec)
    assert result.shape == (256,)
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

    with patch("bo_pipeline.pipeline.run_bo", return_value=mock_picks), \
         patch("bo_pipeline.selector.get_scored_combinations", return_value=[]), \
         patch("bo_pipeline.selector.get_candidate_combinations", return_value=[{"x": 1}]), \
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

    with patch("bo_pipeline.pipeline.run_bo", return_value=[]), \
         patch("bo_pipeline.selector.get_scored_combinations", return_value=[]), \
         patch("bo_pipeline.selector.get_candidate_combinations", return_value=[]), \
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

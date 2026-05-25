"""
Tests for Chunk 6 — Google RSA text generation.

Pure tests (no API keys):
- slots_for_platform returns correct slot sets
- generate_all_slots uses platform-aware slot filter
- POST /api/google/generate/text/{campaign_id} route: auth, 404, delegates to pipeline
- Route passes platform='google' to run_text_pipeline
- Route picks seed_ad_id from query param when supplied

API-key-required tests are marked with skipif and live in the
test_google_text_pipeline_live.py file (not in this suite).
"""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("META_APP_ID", "test_app_id")
os.environ.setdefault("META_APP_SECRET", "test_app_secret")
os.environ.setdefault("META_REDIRECT_URI", "http://localhost:8000/auth/meta/callback")
os.environ.setdefault("JWT_SECRET", "test_secret")

import main as m
from fastapi.testclient import TestClient
from main import app, create_token, init_db

from ad_text_generation.generator import GOOGLE_RSA_SLOTS, TEXT_SLOTS, slots_for_platform


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
    """Insert a minimal RSA ad structure row into ad_creative_structures."""
    db = sqlite3.connect(str(db_path))
    rows = [
        (user_id, "cid_123", campaign_id, ad_id, "adg_1", "rsa", "headline", 0, "Buy Now", "active", "google"),
        (user_id, "cid_123", campaign_id, ad_id, "adg_1", "rsa", "headline", 1, "Great Deal", "active", "google"),
        (user_id, "cid_123", campaign_id, ad_id, "adg_1", "rsa", "description", 0, "Get the best prices online.", "active", "google"),
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


# ── Unit tests: slots_for_platform ────────────────────────────────────────────

def test_slots_for_platform_google():
    assert slots_for_platform("google") == GOOGLE_RSA_SLOTS
    assert "headline" in GOOGLE_RSA_SLOTS
    assert "description" in GOOGLE_RSA_SLOTS
    assert "primary_text" not in GOOGLE_RSA_SLOTS
    assert "cta" not in GOOGLE_RSA_SLOTS


def test_slots_for_platform_meta():
    assert slots_for_platform("meta") == TEXT_SLOTS
    assert "primary_text" in TEXT_SLOTS
    assert "cta" in TEXT_SLOTS


def test_slots_for_platform_unknown_defaults_to_meta():
    # Unknown platform should fall back to Meta slots (the else branch)
    result = slots_for_platform("unknown_platform")
    assert result == TEXT_SLOTS


def test_google_rsa_slots_subset_of_meta_slots():
    # Every Google RSA slot must be a valid Meta slot (headline/description exist in both)
    for slot in GOOGLE_RSA_SLOTS:
        assert slot in TEXT_SLOTS


# ── Route tests ───────────────────────────────────────────────────────────────

def test_generate_google_text_requires_auth(client):
    resp = client.post("/api/google/generate/text/camp_1")
    assert resp.status_code == 401


def test_generate_google_text_404_when_not_ingested(client, user_token):
    _, token = user_token
    resp = client.post(
        "/api/google/generate/text/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert "Ingest" in resp.json()["detail"]


def test_generate_google_text_calls_pipeline_with_google_platform(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id, "camp_rsa", "g_ad_1")

    captured = {}

    async def mock_pipeline(seed_components, n_per_slot, source_ad_id, platform, **kwargs):
        captured["platform"] = platform
        captured["n_per_slot"] = n_per_slot
        captured["source_ad_id"] = source_ad_id
        return 1

    mock_slots = [
        {"slot": "headline", "slot_index": 0, "value": "New Headline", "source": "generated"},
        {"slot": "description", "slot_index": 0, "value": "New description text.", "source": "generated"},
    ]

    with patch("ad_text_generation.pipeline.run_text_pipeline", new=mock_pipeline), \
         patch("ad_text_generation.storage.get_generated_ad", return_value=mock_slots):
        resp = client.post(
            "/api/google/generate/text/camp_rsa",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert captured["platform"] == "google"
    assert captured["n_per_slot"] == 10
    assert captured["source_ad_id"] == "g_ad_1"

    body = resp.json()
    assert body["source_ad_id"] == "g_ad_1"
    assert len(body["slots"]) == 2
    slots_by_name = {s["slot"]: s["value"] for s in body["slots"]}
    assert "headline" in slots_by_name
    assert "description" in slots_by_name


def test_generate_google_text_respects_seed_ad_id_param(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id, "camp_multi", "g_ad_1")
    _seed_google_structure(tmp_db, user_id, "camp_multi", "g_ad_2")

    captured = {}

    async def mock_pipeline(seed_components, n_per_slot, source_ad_id, platform, **kwargs):
        captured["source_ad_id"] = source_ad_id
        return 2

    mock_slots = [{"slot": "headline", "slot_index": 0, "value": "v", "source": "generated"}]

    with patch("ad_text_generation.pipeline.run_text_pipeline", new=mock_pipeline), \
         patch("ad_text_generation.storage.get_generated_ad", return_value=mock_slots):
        resp = client.post(
            "/api/google/generate/text/camp_multi?seed_ad_id=g_ad_2",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert captured["source_ad_id"] == "g_ad_2"


def test_generate_google_text_400_for_unknown_seed_ad_id(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id, "camp_x", "g_ad_1")

    with patch("ad_text_generation.pipeline.run_text_pipeline", new=AsyncMock(return_value=1)), \
         patch("ad_text_generation.storage.get_generated_ad", return_value=[]):
        resp = client.post(
            "/api/google/generate/text/camp_x?seed_ad_id=nonexistent_id",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 400


def test_generate_google_text_platform_isolation(client, user_token, tmp_db):
    """Meta rows in ad_creative_structures are invisible to the Google route."""
    user_id, token = user_token
    # Insert a Meta row (platform='meta')
    db = sqlite3.connect(str(tmp_db))
    db.execute(
        """INSERT INTO ad_creative_structures
           (user_id, ad_account_id, campaign_id, ad_id, adset_id, creative_type, slot, slot_index, value, lifecycle_status, platform)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, "act_123", "camp_meta", "meta_ad_1", "adset_1", "static", "headline", 0, "Meta headline", "active", "meta"),
    )
    db.commit()
    db.close()

    resp = client.post(
        "/api/google/generate/text/camp_meta",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Should 404 because there are no platform='google' rows
    assert resp.status_code == 404

"""
Tests for Chunk 9 — Performance Max + Shopping stubs.

Route-level tests:
- Shopping ad → 400 at text generation endpoint
- Unknown ad type → 400 at text generation endpoint
- pMax ad → proceeds (not 400) at text generation endpoint
- Shopping ad → 400 at BO endpoint
- Unknown ad type → 400 at BO endpoint
- pMax ad → proceeds (not 400) at BO endpoint
"""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("META_APP_ID", "test_app_id")
os.environ.setdefault("META_APP_SECRET", "test_app_secret")
os.environ.setdefault("META_REDIRECT_URI", "http://localhost:8000/auth/meta/callback")
os.environ.setdefault("JWT_SECRET", "test_secret")

import main as m
from fastapi.testclient import TestClient
from main import app, create_token, init_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(m, "DB_PATH", db_file)
    init_db()
    from ad_text_generation.storage import ensure_tables as _ensure_text
    _ensure_text(db_file)
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


def _seed_structure(db_path, user_id, campaign_id, ad_id, creative_type, slot="headline", value="H1"):
    db = sqlite3.connect(str(db_path))
    db.execute(
        """INSERT OR REPLACE INTO ad_creative_structures
           (user_id, ad_account_id, campaign_id, ad_id, adset_id,
            creative_type, slot, slot_index, value, lifecycle_status, platform)
           VALUES (?, 'cid', ?, ?, 'adg_1', ?, ?, 0, ?, 'active', 'google')""",
        (user_id, campaign_id, ad_id, creative_type, slot, value),
    )
    db.commit()
    db.close()


# ── Text generation: creative type guard ──────────────────────────────────────

def test_text_gen_returns_400_for_shopping(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_structure(tmp_db, user_id, "camp_1", "shop_ad", "shopping", "final_url", "https://shop.example.com")
    resp = client.post(
        "/api/google/generate/text/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "shopping" in resp.json()["detail"].lower()


def test_text_gen_returns_400_for_unknown(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_structure(tmp_db, user_id, "camp_1", "unk_ad", "unknown", "headline", "H1")
    resp = client.post(
        "/api/google/generate/text/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "unknown" in resp.json()["detail"].lower()


def test_text_gen_proceeds_for_pmax(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_structure(tmp_db, user_id, "camp_1", "pmax_ad", "pmax", "headline", "Performance Headline")

    mock_gen_id = 42
    mock_slots = [{"slot": "headline", "slot_index": 0, "value": "New Headline", "source": "generated"}]

    with patch("ad_text_generation.pipeline.run_text_pipeline", new=AsyncMock(return_value=mock_gen_id)), \
         patch("ad_text_generation.storage.get_generated_ad", return_value=mock_slots):
        resp = client.post(
            "/api/google/generate/text/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["source_ad_id"] == "pmax_ad"


# ── BO: creative type guard ───────────────────────────────────────────────────

def test_bo_returns_400_for_shopping(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_structure(tmp_db, user_id, "camp_1", "shop_ad", "shopping", "final_url", "https://shop.example.com")
    resp = client.post(
        "/api/google/bo/run",
        json={"seed_ad_id": "shop_ad", "text_source_id": "shop_ad"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "shopping" in resp.json()["detail"].lower()


def test_bo_returns_400_for_unknown(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_structure(tmp_db, user_id, "camp_1", "unk_ad", "unknown", "headline", "H1")
    resp = client.post(
        "/api/google/bo/run",
        json={"seed_ad_id": "unk_ad", "text_source_id": "unk_ad"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "unknown" in resp.json()["detail"].lower()


def test_bo_proceeds_for_pmax(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_structure(tmp_db, user_id, "camp_1", "pmax_ad", "pmax", "headline", "Performance Headline")

    mock_picks = []
    with patch("bo_pipeline.pipeline.run_bo", return_value=(mock_picks, None)), \
         patch("bo_pipeline.selector.get_scored_combinations", return_value=[]), \
         patch("bo_pipeline.selector.get_candidate_combinations", return_value=[]):
        resp = client.post(
            "/api/google/bo/run",
            json={"seed_ad_id": "pmax_ad", "text_source_id": "pmax_ad"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["seed_ad_id"] == "pmax_ad"

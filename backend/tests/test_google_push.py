"""
Tests for Chunk 8 — Push to Google Ads (Mutate API).

Pure tests (no API keys):
- POST /api/google/push requires auth
- No unpushed BO picks → pushed=0, no Mutate call
- Successful push: correct RSA request shape (headlines/descriptions, HEADLINE_1 pinned)
- google_ad_resource_name written to bo_selections after push
- Second sync is a no-op (already pushed)
- Missing final_url → graceful error in results
- Not enough text variants → graceful error in results
- Mutate API error → graceful error, failed count incremented
- Only pushes this user's ads (not another user's)
- BO pick is first headline/description (pinned order)
- Google not connected → note in response, pushed=0
"""

from __future__ import annotations

import json
import os
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("META_APP_ID", "test_app_id")
os.environ.setdefault("META_APP_SECRET", "test_app_secret")
os.environ.setdefault("META_REDIRECT_URI", "http://localhost:8000/auth/meta/callback")
os.environ.setdefault("JWT_SECRET", "test_secret")

import main as m
from fastapi import HTTPException
from fastapi.testclient import TestClient
from main import app, create_token, init_db


# ── Fixtures ───────────────────────────────────────────────────���──────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(m, "DB_PATH", db_file)
    init_db()
    # generated_ads/generated_ad_slots are created lazily by the text pipeline;
    # create them in tmp_db so tests can seed data without hitting the real app.db.
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
        # tier='premium' — these tests exercise push mechanics, not the write-access
        # gate (free/trial/beta are read-only; see backend/permissions.py).
        "INSERT INTO users (email, pw_hash, tier) VALUES (?, ?, 'premium')", ("test@example.com", "hashed")
    )
    user_id = cur.lastrowid
    db.commit()
    db.close()
    return user_id, create_token(user_id)


def _seed_google_structure(
    db_path, user_id, campaign_id="camp_1", ad_id="g_ad_1",
    ad_group_id="adg_1", final_url="https://example.com/landing",
    n_headlines=3, n_descriptions=2,
):
    db = sqlite3.connect(str(db_path))
    rows = []
    for i in range(n_headlines):
        rows.append((
            user_id, "cid_123", campaign_id, ad_id, ad_group_id,
            "rsa", "headline", i, f"Headline {i}", "active", "google",
        ))
    for i in range(n_descriptions):
        rows.append((
            user_id, "cid_123", campaign_id, ad_id, ad_group_id,
            "rsa", "description", i, f"Description {i}", "active", "google",
        ))
    rows.append((
        user_id, "cid_123", campaign_id, ad_id, ad_group_id,
        "rsa", "final_url", 0, final_url, "active", "google",
    ))
    db.executemany(
        """INSERT OR REPLACE INTO ad_creative_structures
           (user_id, ad_account_id, campaign_id, ad_id, adset_id,
            creative_type, slot, slot_index, value, lifecycle_status, platform)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    db.commit()
    db.close()


def _seed_bo_pick(db_path, seed_ad_id="g_ad_1", combination=None, resource_name=None):
    if combination is None:
        combination = {"headline": "Best Headline", "description": "Best Description"}
    db = sqlite3.connect(str(db_path))
    db.execute(
        """INSERT INTO bo_selections
           (seed_ad_id, text_source_id, pick_rank, combination_key, combination,
            selection_type, google_ad_resource_name)
           VALUES (?, ?, 1, ?, ?, 'random', ?)""",
        (seed_ad_id, seed_ad_id, json.dumps(combination), json.dumps(combination), resource_name),
    )
    db.commit()
    db.close()


def _seed_generated_text(db_path, source_ad_id="g_ad_1", n_headlines=10, n_descriptions=10):
    db = sqlite3.connect(str(db_path))
    cur = db.execute(
        "INSERT INTO generated_ads (source_ad_id) VALUES (?)", (source_ad_id,)
    )
    gen_id = cur.lastrowid
    slots = []
    for i in range(n_headlines):
        slots.append((gen_id, "headline", i, f"Generated H{i}", "generated"))
    for i in range(n_descriptions):
        slots.append((gen_id, "description", i, f"Generated D{i}", "generated"))
    db.executemany(
        "INSERT INTO generated_ad_slots (generated_ad_id, slot, slot_index, value, source) VALUES (?, ?, ?, ?, ?)",
        slots,
    )
    db.commit()
    db.close()


def _mock_google_creds(customer_id="cid_123"):
    return patch(
        "main._google_creds",
        new=AsyncMock(return_value=("ya29.test", customer_id, None)),
    )


# ── Auth ──────────────────────────���─────────────────────────────���─────────────

def test_google_push_requires_auth(client):
    resp = client.post("/api/google/push")
    assert resp.status_code == 401


# ── No unpushed picks ─────────────────────────────────────────────────────────

def test_google_push_no_unpushed_returns_zero(client, user_token, tmp_db):
    _, token = user_token
    with _mock_google_creds():
        resp = client.post("/api/google/push", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["pushed"] == 0
    assert data["failed"] == 0


def test_google_push_already_pushed_is_noop(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id)
    _seed_bo_pick(tmp_db, resource_name="customers/123/adGroupAds/456~789")
    _seed_generated_text(tmp_db)

    with _mock_google_creds(), \
         patch("google_ads_api.create_rsa", new=AsyncMock()) as mock_create:
        resp = client.post("/api/google/push", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["pushed"] == 0
    mock_create.assert_not_called()


# ── Successful push ─────────────────────────────��─────────────────────────────

def test_google_push_correct_rsa_request_shape(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id)
    _seed_bo_pick(tmp_db, combination={"headline": "Best Headline", "description": "Best Desc"})
    _seed_generated_text(tmp_db)

    captured = {}

    async def mock_create(**kwargs):
        captured.update(kwargs)
        return "customers/cid_123/adGroupAds/111~222"

    with _mock_google_creds(), patch("google_ads_api.create_rsa", new=mock_create):
        resp = client.post("/api/google/push", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["pushed"] == 1
    assert captured["final_url"] == "https://example.com/landing"
    assert captured["ad_group_id"] == "adg_1"
    assert len(captured["headlines"]) >= 3
    assert len(captured["descriptions"]) >= 2


def test_google_push_bo_pick_is_pinned_first(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id)
    _seed_bo_pick(tmp_db, combination={"headline": "Top Pick Headline", "description": "Top Pick Desc"})
    _seed_generated_text(tmp_db)

    captured = {}

    async def mock_create(**kwargs):
        captured.update(kwargs)
        return "customers/cid_123/adGroupAds/111~333"

    with _mock_google_creds(), patch("google_ads_api.create_rsa", new=mock_create):
        client.post("/api/google/push", headers={"Authorization": f"Bearer {token}"})

    assert captured["headlines"][0] == "Top Pick Headline"
    assert captured["descriptions"][0] == "Top Pick Desc"


def test_google_push_writes_resource_name_to_db(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id)
    _seed_bo_pick(tmp_db)
    _seed_generated_text(tmp_db)

    async def mock_create(**kwargs):
        return "customers/cid_123/adGroupAds/555~666"

    with _mock_google_creds(), patch("google_ads_api.create_rsa", new=mock_create):
        client.post("/api/google/push", headers={"Authorization": f"Bearer {token}"})

    db = sqlite3.connect(str(tmp_db))
    row = db.execute(
        "SELECT google_ad_resource_name FROM bo_selections WHERE seed_ad_id = 'g_ad_1'"
    ).fetchone()
    db.close()
    assert row is not None
    assert row[0] == "customers/cid_123/adGroupAds/555~666"


# ── Graceful failures ───────────────────────────────���──────────────────────��──

def test_google_push_missing_final_url_fails_gracefully(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id, final_url="")
    # Remove the final_url slot that was just seeded
    db = sqlite3.connect(str(tmp_db))
    db.execute("DELETE FROM ad_creative_structures WHERE slot = 'final_url'")
    db.commit()
    db.close()
    _seed_bo_pick(tmp_db)
    _seed_generated_text(tmp_db)

    with _mock_google_creds(), patch("google_ads_api.create_rsa", new=AsyncMock()) as mock_create:
        resp = client.post("/api/google/push", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["pushed"] == 0
    assert data["failed"] == 1
    assert "final_url" in data["results"][0]["error"]
    mock_create.assert_not_called()


def test_google_push_not_enough_variants_fails_gracefully(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id)
    _seed_bo_pick(tmp_db, combination={"headline": "H", "description": "D"})
    # Only 1 extra headline, 0 extra descriptions → total: 2 headlines, 1 description
    _seed_generated_text(tmp_db, n_headlines=1, n_descriptions=0)

    with _mock_google_creds(), patch("google_ads_api.create_rsa", new=AsyncMock()) as mock_create:
        resp = client.post("/api/google/push", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["failed"] == 1
    assert "variants" in data["results"][0]["error"].lower() or "enough" in data["results"][0]["error"].lower()
    mock_create.assert_not_called()


def test_google_push_api_error_graceful(client, user_token, tmp_db):
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id)
    _seed_bo_pick(tmp_db)
    _seed_generated_text(tmp_db)

    async def mock_create(**kwargs):
        raise RuntimeError("Google Ads API error 403: insufficient permissions")

    with _mock_google_creds(), patch("google_ads_api.create_rsa", new=mock_create):
        resp = client.post("/api/google/push", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["pushed"] == 0
    assert data["failed"] == 1
    assert "403" in data["results"][0]["error"]


# ── User isolation ───────────────────────────────────���───────────────────────���

def test_google_push_only_pushes_own_ads(client, tmp_db):
    # Create two users
    db = sqlite3.connect(str(tmp_db))
    uid1 = db.execute("INSERT INTO users (email, pw_hash, tier) VALUES (?, ?, 'premium')", ("u1@x.com", "h")).lastrowid
    uid2 = db.execute("INSERT INTO users (email, pw_hash, tier) VALUES (?, ?, 'premium')", ("u2@x.com", "h")).lastrowid
    db.commit()
    db.close()
    token1 = create_token(uid1)

    # Seed structure for user 2's ad
    _seed_google_structure(tmp_db, uid2, ad_id="u2_ad")
    _seed_bo_pick(tmp_db, seed_ad_id="u2_ad")
    _seed_generated_text(tmp_db, source_ad_id="u2_ad")

    with _mock_google_creds(), patch("google_ads_api.create_rsa", new=AsyncMock()) as mock_create:
        resp = client.post("/api/google/push", headers={"Authorization": f"Bearer {token1}"})

    assert resp.status_code == 200
    assert resp.json()["pushed"] == 0
    mock_create.assert_not_called()


# ── Not connected ───────────────────────────────���──────────────────────────��──

def test_google_push_not_connected_returns_note(client, user_token):
    _, token = user_token
    with patch("main._google_creds", new=AsyncMock(side_effect=HTTPException(400, "not connected"))):
        resp = client.post("/api/google/push", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["pushed"] == 0
    assert "not connected" in (data.get("note") or "").lower()

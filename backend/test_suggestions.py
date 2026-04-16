"""
Tests for suggested_configurations — scaffolding and confirm-create flow.

Covers:
- POST /api/suggestions: store a suggestion linked to a source dynamic template
- GET /api/suggestions: list suggestions, optionally filtered by campaign_id
- POST /api/suggestions/{id}/confirm (action="create"):
    - calls _clone_dynamic_to_static_ad, sets created_static + static_ad_id
    - Meta failure preserves original status
- POST /api/suggestions/{id}/confirm (action="replace"):
    - transitions to pending_confirmation without Meta call
    - supports optional static_ad_id pre-linking
- auth isolation: user cannot access another user's suggestion
- invalid action and non-confirmable status still rejected
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
from main import app, create_token, init_db

from fastapi.testclient import TestClient


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(m, "DB_PATH", db_file)
    init_db()
    return db_file


@pytest.fixture()
def client(tmp_db):
    return TestClient(app)


@pytest.fixture()
def user_token(tmp_db):
    db = sqlite3.connect(str(tmp_db))
    db.row_factory = sqlite3.Row
    cur = db.execute(
        "INSERT INTO users (email, pw_hash) VALUES (?, ?)",
        ("test@example.com", "hashed"),
    )
    user_id = cur.lastrowid
    db.execute(
        "INSERT INTO meta_connections (user_id, meta_user_id, access_token, ad_account_id) "
        "VALUES (?, ?, ?, ?)",
        (user_id, "meta_123", "fake_token", "act_999"),
    )
    db.commit()
    db.close()
    return user_id, create_token(user_id)


# ── Helpers ────────────────────────────────────────────────────────────────────

SAMPLE_SUGGESTION = {
    "source_ad_id": "ad_dyn_1",
    "campaign_id": "camp_1",
    "adset_id": "adset_1",
    "components": {
        "headline": "Buy Now — Summer Sale",
        "primary_text": "Up to 50% off selected items",
        "image": "https://example.com/summer.jpg",
    },
}


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_store_suggestion_creates_record(client, user_token, tmp_db):
    """POST /api/suggestions stores the suggestion with status 'suggested'."""
    _, token = user_token

    resp = client.post(
        "/api/suggestions",
        json=SAMPLE_SUGGESTION,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["source_ad_id"] == "ad_dyn_1"
    assert body["campaign_id"] == "camp_1"
    assert body["adset_id"] == "adset_1"
    assert body["components"] == SAMPLE_SUGGESTION["components"]
    assert body["deployment_status"] == "suggested"
    assert body["static_ad_id"] is None
    assert "id" in body
    assert "created_at" in body


def test_store_suggestion_persists_to_db(client, user_token, tmp_db):
    """Stored suggestion appears in the database with correct data."""
    user_id, token = user_token

    resp = client.post(
        "/api/suggestions",
        json=SAMPLE_SUGGESTION,
        headers={"Authorization": f"Bearer {token}"},
    )
    suggestion_id = resp.json()["id"]

    db = sqlite3.connect(str(tmp_db))
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT * FROM suggested_configurations WHERE id = ?", (suggestion_id,)
    ).fetchone()
    db.close()

    assert row is not None
    assert row["user_id"] == user_id
    assert row["source_ad_id"] == "ad_dyn_1"
    assert row["ad_account_id"] == "act_999"
    assert row["deployment_status"] == "suggested"
    assert row["static_ad_id"] is None
    stored_components = json.loads(row["components"])
    assert stored_components["headline"] == "Buy Now — Summer Sale"


def test_confirm_replace_transitions_to_pending_confirmation(client, user_token, tmp_db):
    """action='replace' transitions to pending_confirmation without calling Meta."""
    _, token = user_token

    create_resp = client.post(
        "/api/suggestions",
        json=SAMPLE_SUGGESTION,
        headers={"Authorization": f"Bearer {token}"},
    )
    suggestion_id = create_resp.json()["id"]

    confirm_resp = client.post(
        f"/api/suggestions/{suggestion_id}/confirm",
        json={"action": "replace", "target_static_ad_id": "ad_existing"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert confirm_resp.status_code == 200
    body = confirm_resp.json()
    assert body["deployment_status"] == "pending_confirmation"
    assert body["id"] == suggestion_id


def test_replace_pre_links_static_ad_id(client, user_token, tmp_db):
    """action='replace' accepts an optional static_ad_id for pre-linking without Meta."""
    _, token = user_token

    create_resp = client.post(
        "/api/suggestions",
        json=SAMPLE_SUGGESTION,
        headers={"Authorization": f"Bearer {token}"},
    )
    suggestion_id = create_resp.json()["id"]

    confirm_resp = client.post(
        f"/api/suggestions/{suggestion_id}/confirm",
        json={"action": "replace", "static_ad_id": "ad_prelinked_42"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert confirm_resp.status_code == 200
    body = confirm_resp.json()
    assert body["deployment_status"] == "pending_confirmation"
    assert body["static_ad_id"] == "ad_prelinked_42"

    db = sqlite3.connect(str(tmp_db))
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT static_ad_id, deployment_status FROM suggested_configurations WHERE id = ?",
        (suggestion_id,),
    ).fetchone()
    db.close()
    assert row["static_ad_id"] == "ad_prelinked_42"
    assert row["deployment_status"] == "pending_confirmation"


def test_confirm_returns_404_for_other_users_suggestion(client, tmp_db):
    """A user cannot confirm a suggestion belonging to another user."""
    # Create user A
    db = sqlite3.connect(str(tmp_db))
    cur = db.execute(
        "INSERT INTO users (email, pw_hash) VALUES (?, ?)", ("a@test.com", "x")
    )
    uid_a = cur.lastrowid
    db.execute(
        "INSERT INTO meta_connections (user_id, meta_user_id, access_token, ad_account_id) "
        "VALUES (?, 'ma', 'tok_a', 'act_a')",
        (uid_a,),
    )
    # Create user B
    cur = db.execute(
        "INSERT INTO users (email, pw_hash) VALUES (?, ?)", ("b@test.com", "x")
    )
    uid_b = cur.lastrowid
    db.execute(
        "INSERT INTO meta_connections (user_id, meta_user_id, access_token, ad_account_id) "
        "VALUES (?, 'mb', 'tok_b', 'act_b')",
        (uid_b,),
    )
    db.commit()
    db.close()

    token_a = create_token(uid_a)
    token_b = create_token(uid_b)

    # User A stores a suggestion
    create_resp = client.post(
        "/api/suggestions",
        json=SAMPLE_SUGGESTION,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    suggestion_id = create_resp.json()["id"]

    # User B tries to confirm it
    confirm_resp = client.post(
        f"/api/suggestions/{suggestion_id}/confirm",
        json={"action": "create"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert confirm_resp.status_code == 404


def test_confirm_invalid_action_rejected(client, user_token, tmp_db):
    """Confirm with an unrecognised action returns 400."""
    _, token = user_token

    create_resp = client.post(
        "/api/suggestions",
        json=SAMPLE_SUGGESTION,
        headers={"Authorization": f"Bearer {token}"},
    )
    suggestion_id = create_resp.json()["id"]

    confirm_resp = client.post(
        f"/api/suggestions/{suggestion_id}/confirm",
        json={"action": "deploy_to_moon"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert confirm_resp.status_code == 400


def test_confirm_already_terminal_status_rejected(client, user_token, tmp_db):
    """Confirming a suggestion already in 'rejected' status returns 409."""
    user_id, token = user_token

    create_resp = client.post(
        "/api/suggestions",
        json=SAMPLE_SUGGESTION,
        headers={"Authorization": f"Bearer {token}"},
    )
    suggestion_id = create_resp.json()["id"]

    # Manually move to 'rejected' in the DB
    db = sqlite3.connect(str(tmp_db))
    db.execute(
        "UPDATE suggested_configurations SET deployment_status = 'rejected' WHERE id = ?",
        (suggestion_id,),
    )
    db.commit()
    db.close()

    confirm_resp = client.post(
        f"/api/suggestions/{suggestion_id}/confirm",
        json={"action": "create"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert confirm_resp.status_code == 409


# ── Confirm create: real Meta deployment (mocked) ─────────────────────────────

def _patch_deploy(new_ad_id: str = "ad_static_meta_999"):
    """Patch _clone_dynamic_to_static_ad to return a fixed fake Meta ad id."""
    async def _fake(*args, **kwargs):
        return new_ad_id
    return patch("main._clone_dynamic_to_static_ad", side_effect=_fake)


def _patch_deploy_fail(detail: str = "Simulated Meta failure"):
    """Patch _clone_dynamic_to_static_ad to raise HTTPException(502)."""
    from fastapi import HTTPException as _HTTPException
    async def _fail(*args, **kwargs):
        raise _HTTPException(502, detail)
    return patch("main._clone_dynamic_to_static_ad", side_effect=_fail)


def test_confirm_create_transitions_to_created_static(client, user_token, tmp_db):
    """action='create' with mocked Meta deployment → deployment_status == 'created_static'."""
    _, token = user_token

    create_resp = client.post(
        "/api/suggestions",
        json=SAMPLE_SUGGESTION,
        headers={"Authorization": f"Bearer {token}"},
    )
    suggestion_id = create_resp.json()["id"]

    with _patch_deploy("ad_static_meta_999"):
        confirm_resp = client.post(
            f"/api/suggestions/{suggestion_id}/confirm",
            json={"action": "create"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert confirm_resp.status_code == 200
    body = confirm_resp.json()
    assert body["deployment_status"] == "created_static"
    assert body["id"] == suggestion_id


def test_confirm_create_persists_static_ad_id_from_meta(client, user_token, tmp_db):
    """The Meta-returned ad id is stored as static_ad_id after a successful create."""
    _, token = user_token

    create_resp = client.post(
        "/api/suggestions",
        json=SAMPLE_SUGGESTION,
        headers={"Authorization": f"Bearer {token}"},
    )
    suggestion_id = create_resp.json()["id"]

    with _patch_deploy("ad_static_meta_abc123"):
        confirm_resp = client.post(
            f"/api/suggestions/{suggestion_id}/confirm",
            json={"action": "create"},
            headers={"Authorization": f"Bearer {token}"},
        )

    body = confirm_resp.json()
    assert body["static_ad_id"] == "ad_static_meta_abc123"

    # Verify persisted in DB
    db = sqlite3.connect(str(tmp_db))
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT static_ad_id, deployment_status FROM suggested_configurations WHERE id = ?",
        (suggestion_id,),
    ).fetchone()
    db.close()
    assert row["static_ad_id"] == "ad_static_meta_abc123"
    assert row["deployment_status"] == "created_static"


def test_confirm_create_meta_failure_preserves_status(client, user_token, tmp_db):
    """If the Meta deployment call fails, deployment_status is NOT changed."""
    _, token = user_token

    create_resp = client.post(
        "/api/suggestions",
        json=SAMPLE_SUGGESTION,
        headers={"Authorization": f"Bearer {token}"},
    )
    suggestion_id = create_resp.json()["id"]

    with _patch_deploy_fail("Simulated Meta API error"):
        confirm_resp = client.post(
            f"/api/suggestions/{suggestion_id}/confirm",
            json={"action": "create"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert confirm_resp.status_code == 502

    # Status must remain 'suggested' — the DB must not have been updated
    db = sqlite3.connect(str(tmp_db))
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT deployment_status, static_ad_id FROM suggested_configurations WHERE id = ?",
        (suggestion_id,),
    ).fetchone()
    db.close()
    assert row["deployment_status"] == "suggested"
    assert row["static_ad_id"] is None


# ── GET /api/suggestions ──────────────────────────────────────────────────────

def test_list_suggestions_filtered_by_campaign(client, user_token, tmp_db):
    """GET /api/suggestions?campaign_id=... returns only matching suggestions."""
    _, token = user_token

    # Store two suggestions for different campaigns
    client.post(
        "/api/suggestions",
        json={**SAMPLE_SUGGESTION, "campaign_id": "camp_1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    client.post(
        "/api/suggestions",
        json={**SAMPLE_SUGGESTION, "campaign_id": "camp_2"},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = client.get(
        "/api/suggestions?campaign_id=camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["campaign_id"] == "camp_1"


def test_list_suggestions_returns_all_without_filter(client, user_token, tmp_db):
    """GET /api/suggestions without filter returns all suggestions for the user."""
    _, token = user_token

    for i in range(3):
        client.post(
            "/api/suggestions",
            json={**SAMPLE_SUGGESTION, "campaign_id": f"camp_{i}"},
            headers={"Authorization": f"Bearer {token}"},
        )

    resp = client.get(
        "/api/suggestions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 3

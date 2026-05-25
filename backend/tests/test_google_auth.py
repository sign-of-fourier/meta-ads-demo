"""
Tests for Google OAuth connect flow (Chunk 3).

Covers:
- GET /me/google-status returns connected=False when no connection exists
- GET /me/google-status returns connected=True with customer info when connected
- GET /auth/google/login-url returns a Google OAuth URL
- GET /auth/google/callback writes google_connections row on success
- GET /auth/google/callback redirects with error param when OAuth error occurs
- _google_creds raises 400 when not connected
- _google_creds calls refresh_access_token and returns (access_token, customer_id)
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
from main import app, create_token, init_db
from fastapi.testclient import TestClient


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
    db.row_factory = sqlite3.Row
    cur = db.execute(
        "INSERT INTO users (email, pw_hash) VALUES (?, ?)", ("test@example.com", "hashed")
    )
    user_id = cur.lastrowid
    db.commit()
    db.close()
    return user_id, create_token(user_id)


# ── Status endpoint ────────────────────────────────────────────────────────────

def test_google_status_not_connected(client, user_token, tmp_db):
    _, token = user_token
    resp = client.get("/me/google-status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "customer_id": None, "customer_name": None}


def test_google_status_connected(client, user_token, tmp_db):
    user_id, token = user_token
    db = sqlite3.connect(str(tmp_db))
    db.execute(
        "INSERT INTO google_connections (user_id, customer_id, refresh_token, customer_name) "
        "VALUES (?, ?, ?, ?)",
        (user_id, "123456789", "refresh-tok", "My Agency"),
    )
    db.commit()
    db.close()

    resp = client.get("/me/google-status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["customer_id"] == "123456789"
    assert body["customer_name"] == "My Agency"


# ── Login URL ──────────────────────────────────────────────────────────────────

def test_google_login_url_returns_google_url(client, user_token, tmp_db):
    _, token = user_token
    resp = client.get("/auth/google/login-url", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    url = resp.json()["url"]
    assert "accounts.google.com" in url
    assert "adwords" in url
    assert "offline" in url


# ── OAuth callback ─────────────────────────────────────────────────────────────

def test_google_callback_writes_pending_connection(client, user_token, tmp_db):
    """Callback should redirect to google_pick=<key> and write to google_pending_connections."""
    user_id, _ = user_token
    db = sqlite3.connect(str(tmp_db))
    db.execute(
        "INSERT INTO oauth_states (state, user_id, provider) VALUES (?, ?, 'google')",
        ("test-state-abc", user_id),
    )
    db.commit()
    db.close()

    with (
        patch.object(
            m.google_ads_api,
            "exchange_code_for_tokens",
            new=AsyncMock(return_value={"access_token": "ya29.test", "refresh_token": "1//refresh"}),
        ),
        patch.object(
            m.google_ads_api,
            "list_accessible_customers",
            new=AsyncMock(return_value=["customers/987654321"]),
        ),
        patch.object(
            m.google_ads_api,
            "get_customer_name",
            new=AsyncMock(return_value="Test Account"),
        ),
    ):
        resp = client.get(
            "/auth/google/callback",
            params={"code": "authcode123", "state": "test-state-abc"},
            follow_redirects=False,
        )

    assert resp.status_code in (302, 307)
    assert "google_pick=" in resp.headers["location"]

    db = sqlite3.connect(str(tmp_db))
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT * FROM google_pending_connections WHERE user_id = ?", (user_id,)
    ).fetchone()
    db.close()
    assert row is not None
    assert row["refresh_token"] == "1//refresh"
    import json
    accounts = json.loads(row["accounts_json"])
    assert any(a["customer_id"] == "987654321" for a in accounts)


def test_google_callback_error_param_redirects(client, user_token, tmp_db):
    _, _ = user_token
    resp = client.get(
        "/auth/google/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "google_error" in resp.headers["location"]


# ── _google_creds helper ───────────────────────────────────────────────────────

def test_google_creds_raises_400_when_not_connected(client, user_token, tmp_db):
    _, token = user_token
    # Any route that calls _google_creds would 400; we test the helper directly
    # by calling it from within a request context via a route that uses it.
    # For now call it directly using an async runner.
    import asyncio
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            m._google_creds(user_token[0])
        )
    assert exc_info.value.status_code == 400


def test_google_creds_refreshes_and_returns_token(client, user_token, tmp_db):
    user_id, _ = user_token
    db = sqlite3.connect(str(tmp_db))
    db.execute(
        "INSERT INTO google_connections (user_id, customer_id, refresh_token) VALUES (?, ?, ?)",
        (user_id, "111222333", "stored-refresh"),
    )
    db.commit()
    db.close()

    import asyncio

    with patch.object(
        m.google_ads_api,
        "refresh_access_token",
        new=AsyncMock(return_value="fresh-access-token"),
    ):
        access_token, customer_id, login_customer_id = asyncio.run(
            m._google_creds(user_id)
        )

    assert access_token == "fresh-access-token"
    assert customer_id == "111222333"
    assert login_customer_id is None

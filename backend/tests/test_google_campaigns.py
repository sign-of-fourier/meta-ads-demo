"""
Tests for GET /api/google/campaigns (Chunk 4).

Covers:
- 400 returned when Google account not connected
- Normalization: ENABLED → ACTIVE, amountMicros → daily_budget cents
- Metrics normalization: costMicros → spend dollars, ctr decimal → percentage,
  averageCpm/averageCpc micros → dollars
- PAUSED status passes through unchanged
- Zero-delivery campaign (0 impressions) → ctr/cpm/cpc are None
- Multiple rows per campaign are aggregated correctly
- Empty GAQL results → empty list

Smoke test (skipped unless live credentials are configured):
- Requires GOOGLE_DEVELOPER_TOKEN and GOOGLE_ADS_API_VERSION in env, plus a
  google_connections row for the test user. Connect via Settings → run this test.
"""

from __future__ import annotations

import asyncio
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
    cur = db.execute(
        "INSERT INTO users (email, pw_hash) VALUES (?, ?)", ("test@example.com", "hashed")
    )
    user_id = cur.lastrowid
    db.commit()
    db.close()
    return user_id, create_token(user_id)


def _connected_user(tmp_db, user_id, customer_id="123456789"):
    """Insert a google_connections row so _google_creds succeeds."""
    db = sqlite3.connect(str(tmp_db))
    db.execute(
        "INSERT INTO google_connections (user_id, customer_id, refresh_token) VALUES (?, ?, ?)",
        (user_id, customer_id, "stored-refresh"),
    )
    db.commit()
    db.close()


def _mock_creds(customer_id="123456789"):
    return patch(
        "main._google_creds",
        new=AsyncMock(return_value=("ya29.test", customer_id, None)),
    )


# ── Connectivity guard ─────────────────────────────────────────────────────────

def test_google_campaigns_not_connected(client, user_token, tmp_db):
    _, token = user_token
    resp = client.get("/api/google/campaigns", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 400


# ── Status normalization ───────────────────────────────────────────────────────

def test_enabled_normalizes_to_active(client, user_token, tmp_db):
    _, token = user_token
    rows = [{"campaign": {"id": "1", "name": "Camp A", "status": "ENABLED"},
             "campaignBudget": {"amountMicros": "5000000"},
             "metrics": {"impressions": "1000", "clicks": "50", "costMicros": "250000",
                         "ctr": 0.05, "averageCpm": "250000", "averageCpc": "5000000"}}]
    with _mock_creds(), patch("google_ads_api.query_gaql", new=AsyncMock(return_value=rows)):
        resp = client.get("/api/google/campaigns", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()[0]["status"] == "ACTIVE"


def test_paused_passes_through(client, user_token, tmp_db):
    _, token = user_token
    rows = [{"campaign": {"id": "2", "name": "Camp B", "status": "PAUSED"},
             "campaignBudget": {}, "metrics": {}}]
    with _mock_creds(), patch("google_ads_api.query_gaql", new=AsyncMock(return_value=rows)):
        resp = client.get("/api/google/campaigns", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()[0]["status"] == "PAUSED"


# ── Budget normalization ───────────────────────────────────────────────────────

def test_budget_micros_to_cents(client, user_token, tmp_db):
    _, token = user_token
    # 5_000_000 micros = $5.00 = 500 cents
    rows = [{"campaign": {"id": "1", "name": "C", "status": "ENABLED"},
             "campaignBudget": {"amountMicros": "5000000"},
             "metrics": {}}]
    with _mock_creds(), patch("google_ads_api.query_gaql", new=AsyncMock(return_value=rows)):
        resp = client.get("/api/google/campaigns", headers={"Authorization": f"Bearer {token}"})
    assert resp.json()[0]["daily_budget"] == 500


def test_missing_budget_is_none(client, user_token, tmp_db):
    _, token = user_token
    rows = [{"campaign": {"id": "1", "name": "C", "status": "ENABLED"},
             "campaignBudget": {}, "metrics": {}}]
    with _mock_creds(), patch("google_ads_api.query_gaql", new=AsyncMock(return_value=rows)):
        resp = client.get("/api/google/campaigns", headers={"Authorization": f"Bearer {token}"})
    assert resp.json()[0]["daily_budget"] is None


# ── Metrics normalization ──────────────────────────────────────────────────────

def test_metrics_normalization(client, user_token, tmp_db):
    # Normalization contract locked down here explicitly.
    # cost_micros ÷ 1_000_000 → dollars:   2_500_000 → $2.50 spend
    # ctr recomputed as percentage:         50 / 1000 * 100 = 5.0 %
    # cpm in dollars per 1000 impressions:  2.50 / 1000 * 1000 = $2.50
    # cpc in dollars per click:             2.50 / 50 = $0.05
    # averageCpm / averageCpc from the API are NOT used — we recompute from totals.
    _, token = user_token
    rows = [{"campaign": {"id": "1", "name": "C", "status": "ENABLED"},
             "campaignBudget": {},
             "metrics": {"impressions": "1000", "clicks": "50", "costMicros": "2500000"}}]
    with _mock_creds(), patch("google_ads_api.query_gaql", new=AsyncMock(return_value=rows)):
        resp = client.get("/api/google/campaigns", headers={"Authorization": f"Bearer {token}"})
    c = resp.json()[0]
    assert c["impressions_7d"] == 1000
    assert c["clicks_7d"] == 50
    assert abs(c["spend_7d"] - 2.50) < 0.001    # 2_500_000 micros ÷ 1_000_000
    assert abs(c["ctr_7d"] - 5.0) < 0.001       # 50/1000 * 100 — decimal ratio → percent
    assert abs(c["cpm_7d"] - 2.50) < 0.001      # $2.50 / 1000 impressions * 1000
    # cpc is computed but not exposed by the Campaign model (mirrors Meta).
    # See test_cpc_computed_from_totals for explicit cpc coverage.


def test_zero_delivery_gives_none_ratios(client, user_token, tmp_db):
    _, token = user_token
    rows = [{"campaign": {"id": "1", "name": "C", "status": "ENABLED"},
             "campaignBudget": {}, "metrics": {"impressions": "0", "clicks": "0", "costMicros": "0"}}]
    with _mock_creds(), patch("google_ads_api.query_gaql", new=AsyncMock(return_value=rows)):
        resp = client.get("/api/google/campaigns", headers={"Authorization": f"Bearer {token}"})
    c = resp.json()[0]
    assert c["ctr_7d"] is None
    assert c["cpm_7d"] is None


def test_cpc_computed_from_totals():
    # cpc is not exposed by the Campaign response model (mirrors Meta), so we test
    # the provider's metrics_by_campaign dict directly.
    # spend $2.50 / 50 clicks = $0.05 cpc
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from providers.google_provider import GooglePlatformProvider

    rows = [{"campaign": {"id": "1", "name": "C", "status": "ENABLED"},
             "campaignBudget": {},
             "metrics": {"impressions": "1000", "clicks": "50", "costMicros": "2500000"}}]

    provider = GooglePlatformProvider()
    mock_client = MagicMock()

    with patch("google_ads_api.query_gaql", new=AsyncMock(return_value=rows)):
        _, metrics, _ = asyncio.run(
            provider.fetch_campaigns_and_insights(mock_client, "ya29.test", "123456789")
        )

    assert abs(metrics["1"]["cpc"] - 0.05) < 0.0001  # $2.50 / 50 clicks


# ── Aggregation ────────────────────────────────────────────────────────────────

def test_multiple_rows_same_campaign_aggregated(client, user_token, tmp_db):
    _, token = user_token
    # Two rows for the same campaign (e.g. date segmentation bleed-through)
    rows = [
        {"campaign": {"id": "1", "name": "C", "status": "ENABLED"},
         "campaignBudget": {"amountMicros": "1000000"},
         "metrics": {"impressions": "500", "clicks": "20", "costMicros": "1000000"}},
        {"campaign": {"id": "1", "name": "C", "status": "ENABLED"},
         "campaignBudget": {"amountMicros": "1000000"},
         "metrics": {"impressions": "500", "clicks": "30", "costMicros": "1500000"}},
    ]
    with _mock_creds(), patch("google_ads_api.query_gaql", new=AsyncMock(return_value=rows)):
        resp = client.get("/api/google/campaigns", headers={"Authorization": f"Bearer {token}"})
    result = resp.json()
    assert len(result) == 1
    assert result[0]["impressions_7d"] == 1000
    assert result[0]["clicks_7d"] == 50
    assert abs(result[0]["spend_7d"] - 2.50) < 0.001


# ── Empty results ──────────────────────────────────────────────────────────────

def test_empty_gaql_results(client, user_token, tmp_db):
    _, token = user_token
    with _mock_creds(), patch("google_ads_api.query_gaql", new=AsyncMock(return_value=[])):
        resp = client.get("/api/google/campaigns", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


# ── Live smoke test ────────────────────────────────────────────────────────────

_LIVE_CREDS = bool(
    os.getenv("GOOGLE_DEVELOPER_TOKEN")
    and os.getenv("GOOGLE_ADS_API_VERSION")
    and os.getenv("GOOGLE_CLIENT_ID")
    and os.getenv("GOOGLE_CLIENT_SECRET")
)


@pytest.mark.skipif(not _LIVE_CREDS, reason="Live Google credentials not configured")
def test_google_campaigns_live_smoke(client, user_token, tmp_db):
    """
    Smoke test against a real connected account.

    Prerequisites:
      - Set GOOGLE_DEVELOPER_TOKEN, GOOGLE_ADS_API_VERSION, GOOGLE_CLIENT_ID,
        GOOGLE_CLIENT_SECRET in your .env
      - Insert a google_connections row for user_id=1 with a valid refresh_token
        and customer_id (your Google Ads Test Account customer ID).

    Run with:
        python -m pytest tests/test_google_campaigns.py::test_google_campaigns_live_smoke -v -s
    """
    user_id, token = user_token
    # You must pre-seed the google_connections row with real credentials.
    # Example (run once before this test):
    #   INSERT INTO google_connections (user_id, customer_id, refresh_token)
    #   VALUES (1, '<your-customer-id>', '<your-refresh-token>');
    resp = client.get("/api/google/campaigns", headers={"Authorization": f"Bearer {token}"})
    # If not connected the test itself explains what's needed
    if resp.status_code == 400:
        pytest.skip("No google_connections row found — seed the DB with real credentials first")
    assert resp.status_code == 200
    campaigns = resp.json()
    print(f"\nLive smoke: {len(campaigns)} campaign(s) returned")
    for c in campaigns:
        print(f"  [{c['status']}] {c['name']}  impressions={c.get('impressions_7d')}  spend={c.get('spend_7d')}")

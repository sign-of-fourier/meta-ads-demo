"""
Invariant tests: login_customer_id is threaded from _google_creds() through every
Google Ads route that makes real API calls.

Context
-------
Google Ads requires a `login-customer-id` HTTP header for any call made on behalf
of a client account managed through an MCC (Manager) account.  _google_creds()
returns a 3-tuple (access_token, customer_id, login_customer_id).  If any route
silently drops the third element the MCC user gets DEVELOPER_TOKEN_NOT_APPROVED
with no other indication of what went wrong.

Routes that call the Google Ads API (must thread login_customer_id):
  POST /api/google/ingest/structure/{campaign_id}
    → google_provider.fetch_campaign_structure
    → google_ads_api.query_gaql  (called 3×: ad groups, ads, pMax assets)
    → login-customer-id request header

  POST /api/google/push
    → _create_google_rsa_ad
    → google_ads_api.create_rsa
    → login-customer-id request header

Routes that do NOT call the Google Ads API (credential-free by design):
  POST /api/google/generate/text/{campaign_id}  — DB + Azure OpenAI only
  POST /api/google/bo/run                       — DB + BO pipeline only

The credential-free status of the latter two is also locked in here so that
future changes that accidentally add a Google API call become immediately visible.
"""

from __future__ import annotations

import asyncio
import json
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

_MCC_CREDS = ("ACCESS_TOKEN", "123456789", "999888777")
_DIRECT_CREDS = ("ACCESS_TOKEN", "123456789", None)


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


# ── DB seed helpers ───────────────────────────────────────────────────────────

def _seed_google_structure(
    db_path, user_id,
    campaign_id="camp_1", ad_id="g_ad_1", ad_group_id="adg_1",
):
    db = sqlite3.connect(str(db_path))
    rows = [
        (user_id, "123456789", campaign_id, ad_id, ad_group_id,
         "rsa", "headline", 0, "H0", "active", "google"),
        (user_id, "123456789", campaign_id, ad_id, ad_group_id,
         "rsa", "headline", 1, "H1", "active", "google"),
        (user_id, "123456789", campaign_id, ad_id, ad_group_id,
         "rsa", "headline", 2, "H2", "active", "google"),
        (user_id, "123456789", campaign_id, ad_id, ad_group_id,
         "rsa", "description", 0, "D0", "active", "google"),
        (user_id, "123456789", campaign_id, ad_id, ad_group_id,
         "rsa", "description", 1, "D1", "active", "google"),
        (user_id, "123456789", campaign_id, ad_id, ad_group_id,
         "rsa", "final_url", 0, "https://example.com/landing", "active", "google"),
    ]
    db.executemany(
        """INSERT OR REPLACE INTO ad_creative_structures
           (user_id, ad_account_id, campaign_id, ad_id, adset_id,
            creative_type, slot, slot_index, value, lifecycle_status, platform)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    db.commit()
    db.close()


def _seed_bo_pick(db_path, seed_ad_id="g_ad_1"):
    combo = {"headline": "Best Headline", "description": "Best Description"}
    db = sqlite3.connect(str(db_path))
    db.execute(
        """INSERT INTO bo_selections
           (seed_ad_id, text_source_id, pick_rank, combination_key, combination, selection_type)
           VALUES (?, ?, 1, ?, ?, 'random')""",
        (seed_ad_id, seed_ad_id, json.dumps(combo), json.dumps(combo)),
    )
    db.commit()
    db.close()


def _seed_generated_text(db_path, source_ad_id="g_ad_1", n_headlines=10, n_descriptions=5):
    db = sqlite3.connect(str(db_path))
    cur = db.execute(
        "INSERT INTO generated_ads (source_ad_id) VALUES (?)", (source_ad_id,)
    )
    gen_id = cur.lastrowid
    slots = []
    for i in range(n_headlines):
        slots.append((gen_id, "headline", i, f"Gen H{i}", "generated"))
    for i in range(n_descriptions):
        slots.append((gen_id, "description", i, f"Gen D{i}", "generated"))
    db.executemany(
        "INSERT INTO generated_ad_slots (generated_ad_id, slot, slot_index, value, source)"
        " VALUES (?, ?, ?, ?, ?)",
        slots,
    )
    db.commit()
    db.close()


# ── Minimal GAQL fixture data ─────────────────────────────────────────────────

def _gaql_responses(ad_group_id="adg_1", ad_id="rsa_001"):
    """Return (adgroup_rows, ad_rows) suitable for a minimal ingest."""
    adgroup_rows = [{
        "adGroup": {"id": ad_group_id, "name": "AG", "status": "ENABLED"},
        "campaign": {"id": "camp_1"},
    }]
    ad_rows = [{
        "adGroupAd": {
            "ad": {
                "id": ad_id, "name": "RSA", "type": "RESPONSIVE_SEARCH_AD",
                "responsiveSearchAd": {
                    "headlines": [{"text": "H1"}, {"text": "H2"}],
                    "descriptions": [{"text": "D1"}],
                },
                "finalUrls": [],
            },
            "status": "ENABLED",
            "effectiveStatus": "ENABLED",
        },
        "adGroup": {"id": ad_group_id},
    }]
    return adgroup_rows, ad_rows


# ── POST /api/google/ingest/structure: login_customer_id threading ─────────────

def test_ingest_structure_threads_login_customer_id(client, user_token, tmp_db):
    """Every query_gaql call made during ingest must receive the login_customer_id
    returned by _google_creds.  Dropping it breaks MCC accounts silently."""
    _, token = user_token
    adgroup_rows, ad_rows = _gaql_responses()

    received: list[str | None] = []
    call_n = {"n": 0}

    async def fake_gaql(*args, login_customer_id=None, **kwargs):
        received.append(login_customer_id)
        call_n["n"] += 1
        # call 1 = ad groups, call 2 = ads, call 3 = pMax assets (returns empty)
        if call_n["n"] == 1:
            return adgroup_rows
        if call_n["n"] == 2:
            return ad_rows
        return []

    with patch("main._google_creds", new=AsyncMock(return_value=_MCC_CREDS)), \
         patch("google_ads_api.query_gaql", side_effect=fake_gaql):
        resp = client.post(
            "/api/google/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert len(received) >= 2, "Expected at least 2 query_gaql calls"
    # Every call must carry the MCC login_customer_id — not just the first one.
    assert all(v == "999888777" for v in received), (
        f"Some query_gaql calls dropped login_customer_id: {received}"
    )


def test_ingest_structure_passes_none_when_no_mcc(client, user_token, tmp_db):
    """When login_customer_id is None (direct account), it must be forwarded as None —
    not converted to empty string or omitted from the kwarg entirely."""
    _, token = user_token
    adgroup_rows, ad_rows = _gaql_responses()

    received: list = []
    call_n = {"n": 0}

    async def fake_gaql(*args, login_customer_id=None, **kwargs):
        received.append(login_customer_id)
        call_n["n"] += 1
        if call_n["n"] == 1:
            return adgroup_rows
        if call_n["n"] == 2:
            return ad_rows
        return []

    with patch("main._google_creds", new=AsyncMock(return_value=_DIRECT_CREDS)), \
         patch("google_ads_api.query_gaql", side_effect=fake_gaql):
        resp = client.post(
            "/api/google/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    # None must propagate as None; the GAQL layer uses `if login_customer_id:` to
    # decide whether to add the header, so any truthy non-None value would be wrong.
    assert all(v is None for v in received), (
        f"Expected None for all calls, got: {received}"
    )


# ── POST /api/google/push: login_customer_id threading ────────────────────────

def test_google_push_threads_login_customer_id(client, user_token, tmp_db):
    """create_rsa must receive the login_customer_id from _google_creds.
    An MCC user whose login_customer_id is dropped here gets a cryptic 403 from
    the Mutate API with no indication of the root cause."""
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id)
    _seed_bo_pick(tmp_db)
    _seed_generated_text(tmp_db)

    captured: dict = {}

    async def fake_create_rsa(**kwargs):
        captured.update(kwargs)
        return "customers/123456789/adGroupAds/111~222"

    with patch("main._google_creds", new=AsyncMock(return_value=_MCC_CREDS)), \
         patch("google_ads_api.create_rsa", side_effect=fake_create_rsa):
        resp = client.post(
            "/api/google/push",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["pushed"] == 1
    assert captured.get("login_customer_id") == "999888777"


def test_google_push_passes_none_when_no_mcc(client, user_token, tmp_db):
    """login_customer_id=None must be forwarded to create_rsa, not dropped."""
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id)
    _seed_bo_pick(tmp_db)
    _seed_generated_text(tmp_db)

    captured: dict = {}

    async def fake_create_rsa(**kwargs):
        captured.update(kwargs)
        return "customers/123456789/adGroupAds/111~222"

    with patch("main._google_creds", new=AsyncMock(return_value=_DIRECT_CREDS)), \
         patch("google_ads_api.create_rsa", side_effect=fake_create_rsa):
        resp = client.post(
            "/api/google/push",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert "login_customer_id" in captured, (
        "login_customer_id kwarg must be present in create_rsa call even when None"
    )
    assert captured["login_customer_id"] is None


# ── Deep GAQL header test (unit test on query_gaql itself) ────────────────────

def test_query_gaql_sets_login_customer_id_header_for_mcc():
    """The login-customer-id HTTP header must appear in the outgoing searchStream
    request when login_customer_id is non-None.  This is the header Google Ads
    requires for MCC accounts; its absence causes DEVELOPER_TOKEN_NOT_APPROVED."""
    from google_ads_api import query_gaql

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": []}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    asyncio.run(query_gaql(
        customer_id="123456789",
        access_token="ya29.test",
        developer_token="dev-tok",
        api_version="v18",
        gaql="SELECT campaign.id FROM campaign",
        client=mock_client,
        login_customer_id="999888777",
    ))

    mock_client.post.assert_called_once()
    headers = mock_client.post.call_args.kwargs["headers"]
    assert "login-customer-id" in headers, (
        "login-customer-id header missing from GAQL request"
    )
    assert headers["login-customer-id"] == "999888777"


def test_query_gaql_omits_login_customer_id_header_for_direct_accounts():
    """The login-customer-id header must NOT be sent for non-MCC accounts.
    Sending an empty or null value causes errors on direct accounts."""
    from google_ads_api import query_gaql

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": []}

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    asyncio.run(query_gaql(
        customer_id="123456789",
        access_token="ya29.test",
        developer_token="dev-tok",
        api_version="v18",
        gaql="SELECT campaign.id FROM campaign",
        client=mock_client,
        login_customer_id=None,
    ))

    mock_client.post.assert_called_once()
    headers = mock_client.post.call_args.kwargs["headers"]
    assert "login-customer-id" not in headers, (
        "login-customer-id header must be absent when login_customer_id is None"
    )


def test_create_rsa_sets_login_customer_id_header_for_mcc():
    """The login-customer-id HTTP header must appear in the Mutate API request
    when login_customer_id is provided.  Mirrors the GAQL check at the mutate path."""
    import httpx
    from google_ads_api import create_rsa

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "mutateOperationResponses": [
            {"adGroupAdResult": {"resourceName": "customers/123/adGroupAds/1~2"}}
        ]
    }

    captured_headers: dict = {}

    async def fake_post(self, url, **kwargs):
        captured_headers.update(kwargs.get("headers", {}))
        return mock_response

    with patch("httpx.AsyncClient.post", new=fake_post):
        asyncio.run(create_rsa(
            customer_id="123456789",
            access_token="ya29.test",
            developer_token="dev-tok",
            api_version="v18",
            ad_group_id="adg_1",
            headlines=["H1", "H2", "H3"],
            descriptions=["D1", "D2"],
            final_url="https://example.com",
            login_customer_id="999888777",
        ))

    assert "login-customer-id" in captured_headers, (
        "login-customer-id header missing from Mutate API request"
    )
    assert captured_headers["login-customer-id"] == "999888777"


def test_create_rsa_omits_login_customer_id_header_when_none():
    """login-customer-id must not appear in the Mutate request for direct accounts."""
    from google_ads_api import create_rsa

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "mutateOperationResponses": [
            {"adGroupAdResult": {"resourceName": "customers/123/adGroupAds/1~2"}}
        ]
    }

    captured_headers: dict = {}

    async def fake_post(self, url, **kwargs):
        captured_headers.update(kwargs.get("headers", {}))
        return mock_response

    with patch("httpx.AsyncClient.post", new=fake_post):
        asyncio.run(create_rsa(
            customer_id="123456789",
            access_token="ya29.test",
            developer_token="dev-tok",
            api_version="v18",
            ad_group_id="adg_1",
            headlines=["H1", "H2", "H3"],
            descriptions=["D1", "D2"],
            final_url="https://example.com",
            login_customer_id=None,
        ))

    assert "login-customer-id" not in captured_headers


# ── Credential-free routes: invariant lock-in ─────────────────────────────────

def test_generate_text_does_not_call_google_creds(client, user_token, tmp_db):
    """POST /api/google/generate/text is a DB + Azure OpenAI route.
    It must NOT call _google_creds — adding a Google API dependency here would
    break the text generation flow for users who have not connected Google Ads."""
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id)

    forbidden = AsyncMock(side_effect=AssertionError("_google_creds must not be called"))

    with patch("main._google_creds", new=forbidden), \
         patch("ad_text_generation.pipeline.run_text_pipeline",
               new=AsyncMock(return_value=42)), \
         patch("ad_text_generation.storage.get_generated_ad", return_value=[
             {"slot": "headline", "slot_index": 0, "value": "Test H", "source": "generated"},
         ]):
        resp = client.post(
            "/api/google/generate/text/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    forbidden.assert_not_called()


def test_bo_run_does_not_call_google_creds(client, user_token, tmp_db):
    """POST /api/google/bo/run is a pure BO + DB route.
    It must NOT call _google_creds — it should work regardless of whether
    the user has a connected Google Ads account."""
    user_id, token = user_token
    _seed_google_structure(tmp_db, user_id)

    forbidden = AsyncMock(side_effect=AssertionError("_google_creds must not be called"))

    with patch("main._google_creds", new=forbidden), \
         patch("bo_pipeline.pipeline.run_bo", return_value=([], None)), \
         patch("bo_pipeline.selector.get_scored_combinations", return_value=[]), \
         patch("bo_pipeline.selector.get_candidate_combinations", return_value=[]):
        resp = client.post(
            "/api/google/bo/run",
            json={"seed_ad_id": "g_ad_1", "text_source_id": "g_ad_1"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    forbidden.assert_not_called()

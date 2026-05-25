"""
Tests for Google structural ingest (Chunk 5).

Covers:
- normalize_creative: RSA, display, video, unknown types
- POST /api/google/ingest/structure/{campaign_id}: RSA and display ads ingested correctly
- GET /api/google/structure/{campaign_id}: returns ingested structure filtered by platform='google'
- Idempotency: reingest does not duplicate rows
- Lifecycle status: ENABLED → active, PAUSED → inactive
- Missing-ad detection: ads absent from re-fetch get lifecycle_status = 'missing'
- Platform isolation: Meta rows not returned by Google route
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("META_APP_ID", "test_app_id")
os.environ.setdefault("META_APP_SECRET", "test_app_secret")
os.environ.setdefault("META_REDIRECT_URI", "http://localhost:8000/auth/meta/callback")
os.environ.setdefault("JWT_SECRET", "test_secret")

import main as m
from main import app, create_token, init_db
from fastapi.testclient import TestClient
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


def _connected_google(tmp_db, user_id, customer_id="111222333"):
    db = sqlite3.connect(str(tmp_db))
    db.execute(
        "INSERT INTO google_connections (user_id, customer_id, refresh_token) VALUES (?, ?, ?)",
        (user_id, customer_id, "stored-refresh"),
    )
    db.commit()
    db.close()


def _mock_google_creds(customer_id="111222333"):
    return patch(
        "main._google_creds",
        new=AsyncMock(return_value=("ya29.test", customer_id, None)),
    )


# ── normalize_creative unit tests ─────────────────────────────────────────────

provider = GooglePlatformProvider()


def test_normalize_rsa():
    ad = {
        "ad_type": "RESPONSIVE_SEARCH_AD",
        "responsive_search_ad": {
            "headlines": [{"text": "Buy Now"}, {"text": "Great Deal"}],
            "descriptions": [{"text": "Shop today"}, {"text": "Free shipping"}],
        },
    }
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "rsa"
    headlines = [c for c in components if c["slot"] == "headline"]
    descriptions = [c for c in components if c["slot"] == "description"]
    assert len(headlines) == 2
    assert len(descriptions) == 2
    assert headlines[0]["value"] == "Buy Now"
    assert headlines[0]["slot_index"] == 0
    assert descriptions[1]["value"] == "Free shipping"
    assert descriptions[1]["slot_index"] == 1


def test_normalize_display():
    ad = {
        "ad_type": "RESPONSIVE_DISPLAY_AD",
        "responsive_display_ad": {
            "headlines": [{"text": "Amazing Products"}],
            "descriptions": [{"text": "Shop our catalog"}],
            "marketing_images": [
                {"asset": "customers/123/assets/456"},
                {"asset": "customers/123/assets/789"},
            ],
        },
    }
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "display"
    images = [c for c in components if c["slot"] == "image"]
    assert len(images) == 2
    assert images[0]["value"] == "customers/123/assets/456"
    assert images[1]["slot_index"] == 1


def test_normalize_video():
    ad = {
        "ad_type": "VIDEO_RESPONSIVE_AD",
        "video_responsive_ad": {
            "headlines": [{"text": "Watch Our Story"}],
            "descriptions": [{"text": "Subscribe now"}],
            "videos": [{"asset": "customers/123/assets/vid001"}],
        },
    }
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "video"
    videos = [c for c in components if c["slot"] == "video"]
    assert len(videos) == 1
    assert videos[0]["value"] == "customers/123/assets/vid001"
    assert videos[0]["slot_index"] == 0


def test_normalize_unknown():
    ad = {"ad_type": "LEGACY_AD", "some_field": "x"}
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "unknown"
    assert components == []


def test_normalize_empty_type():
    ad = {}
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "unknown"
    assert components == []


def test_normalize_rsa_empty_slots():
    ad = {
        "ad_type": "RESPONSIVE_SEARCH_AD",
        "responsive_search_ad": {"headlines": [], "descriptions": []},
    }
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "rsa"
    assert components == []


# ── Chunk 9: pMax + Shopping normalize tests ──────────────────────────────────

def test_normalize_pmax_extracts_text_assets():
    ad = {
        "ad_type": "PERFORMANCE_MAX_AD",
        "pmax_assets": [
            {"field_type": "HEADLINE", "text": "Great deals", "resource_name": "customers/1/assets/10"},
            {"field_type": "LONG_HEADLINE", "text": "Shop the best prices", "resource_name": "customers/1/assets/11"},
            {"field_type": "DESCRIPTION", "text": "Free shipping on orders", "resource_name": "customers/1/assets/12"},
        ],
    }
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "pmax"
    headlines = [c for c in components if c["slot"] == "headline"]
    descriptions = [c for c in components if c["slot"] == "description"]
    assert len(headlines) == 2
    assert headlines[0]["value"] == "Great deals"
    assert headlines[0]["slot_index"] == 0
    assert headlines[1]["value"] == "Shop the best prices"
    assert headlines[1]["slot_index"] == 1
    assert len(descriptions) == 1
    assert descriptions[0]["value"] == "Free shipping on orders"


def test_normalize_pmax_extracts_image_and_video_assets():
    ad = {
        "ad_type": "PERFORMANCE_MAX_AD",
        "pmax_assets": [
            {"field_type": "MARKETING_IMAGE", "text": "", "resource_name": "customers/1/assets/20"},
            {"field_type": "SQUARE_MARKETING_IMAGE", "text": "", "resource_name": "customers/1/assets/21"},
            {"field_type": "YOUTUBE_VIDEO", "text": "", "resource_name": "customers/1/assets/30"},
        ],
    }
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "pmax"
    images = [c for c in components if c["slot"] == "image"]
    videos = [c for c in components if c["slot"] == "video"]
    assert len(images) == 2
    assert images[0]["value"] == "customers/1/assets/20"
    assert len(videos) == 1
    assert videos[0]["value"] == "customers/1/assets/30"


def test_normalize_pmax_empty_assets():
    ad = {"ad_type": "PERFORMANCE_MAX_AD", "pmax_assets": []}
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "pmax"
    assert components == []


def test_normalize_pmax_skips_assets_with_no_value():
    ad = {
        "ad_type": "PERFORMANCE_MAX_AD",
        "pmax_assets": [
            {"field_type": "HEADLINE", "text": "", "resource_name": ""},
            {"field_type": "DESCRIPTION", "text": "Keep this", "resource_name": ""},
        ],
    }
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "pmax"
    assert len(components) == 1
    assert components[0]["slot"] == "description"


def test_normalize_shopping_with_final_url():
    ad = {
        "ad_type": "SHOPPING_PRODUCT_AD",
        "final_urls": ["https://shop.example.com/products"],
    }
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "shopping"
    assert len(components) == 1
    assert components[0]["slot"] == "final_url"
    assert components[0]["value"] == "https://shop.example.com/products"
    assert components[0]["slot_index"] == 0


def test_normalize_shopping_no_final_url():
    ad = {"ad_type": "SHOPPING_PRODUCT_AD"}
    creative_type, components = provider.normalize_creative(ad)
    assert creative_type == "shopping"
    assert components == []


# ── Ingest route tests ────────────────────────────────────────────────────────

def _make_rsa_rows(ad_id="ad_001", ad_group_id="ag_001", status="ENABLED"):
    adgroup_rows = [{"adGroup": {"id": ad_group_id, "name": "AG 1", "status": status},
                     "campaign": {"id": "camp_1"}}]
    ad_rows = [{
        "adGroupAd": {
            "ad": {
                "id": ad_id,
                "name": "My RSA",
                "type": "RESPONSIVE_SEARCH_AD",
                "responsiveSearchAd": {
                    "headlines": [{"text": "Headline A"}, {"text": "Headline B"}],
                    "descriptions": [{"text": "Desc X"}, {"text": "Desc Y"}],
                },
            },
            "status": status,
        },
        "adGroup": {"id": ad_group_id},
    }]
    return adgroup_rows, ad_rows


def _mock_fetch_structure(adgroup_rows, ad_rows):
    """Patch google_ads_api.query_gaql to return ad group rows first, ad rows second."""
    call_count = {"n": 0}
    async def fake_gaql(**kwargs):
        call_count["n"] += 1
        return adgroup_rows if call_count["n"] == 1 else ad_rows
    return patch("google_ads_api.query_gaql", side_effect=fake_gaql)


def test_ingest_rsa_stores_rows(client, user_token, tmp_db):
    _, token = user_token
    adgroup_rows, ad_rows = _make_rsa_rows()
    with _mock_google_creds(), _mock_fetch_structure(adgroup_rows, ad_rows):
        resp = client.post(
            "/api/google/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == "camp_1"
    assert body["ads_processed"] == 1
    assert body["components_saved"] == 4  # 2 headlines + 2 descriptions

    db = sqlite3.connect(str(tmp_db))
    rows = db.execute(
        "SELECT slot, value FROM ad_creative_structures WHERE ad_id = 'ad_001' ORDER BY slot, slot_index"
    ).fetchall()
    db.close()
    slots = [r[0] for r in rows]
    assert slots.count("headline") == 2
    assert slots.count("description") == 2


def test_ingest_sets_platform_google(client, user_token, tmp_db):
    _, token = user_token
    adgroup_rows, ad_rows = _make_rsa_rows()
    with _mock_google_creds(), _mock_fetch_structure(adgroup_rows, ad_rows):
        client.post(
            "/api/google/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )
    db = sqlite3.connect(str(tmp_db))
    row = db.execute(
        "SELECT platform FROM ad_creative_structures WHERE ad_id = 'ad_001' LIMIT 1"
    ).fetchone()
    db.close()
    assert row[0] == "google"


def test_ingest_enabled_sets_active_lifecycle(client, user_token, tmp_db):
    _, token = user_token
    adgroup_rows, ad_rows = _make_rsa_rows(status="ENABLED")
    with _mock_google_creds(), _mock_fetch_structure(adgroup_rows, ad_rows):
        client.post(
            "/api/google/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )
    db = sqlite3.connect(str(tmp_db))
    row = db.execute(
        "SELECT lifecycle_status FROM ad_creative_structures WHERE ad_id = 'ad_001' LIMIT 1"
    ).fetchone()
    db.close()
    assert row[0] == "active"


def test_ingest_paused_sets_inactive_lifecycle(client, user_token, tmp_db):
    _, token = user_token
    adgroup_rows, ad_rows = _make_rsa_rows(status="PAUSED")
    with _mock_google_creds(), _mock_fetch_structure(adgroup_rows, ad_rows):
        client.post(
            "/api/google/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )
    db = sqlite3.connect(str(tmp_db))
    row = db.execute(
        "SELECT lifecycle_status FROM ad_creative_structures WHERE ad_id = 'ad_001' LIMIT 1"
    ).fetchone()
    db.close()
    assert row[0] == "inactive"


def test_ingest_idempotent(client, user_token, tmp_db):
    _, token = user_token
    adgroup_rows, ad_rows = _make_rsa_rows()
    # Ingest twice
    for _ in range(2):
        with _mock_google_creds(), _mock_fetch_structure(adgroup_rows, ad_rows):
            client.post(
                "/api/google/ingest/structure/camp_1",
                headers={"Authorization": f"Bearer {token}"},
            )
    db = sqlite3.connect(str(tmp_db))
    count = db.execute(
        "SELECT COUNT(*) FROM ad_creative_structures WHERE ad_id = 'ad_001'"
    ).fetchone()[0]
    db.close()
    assert count == 4  # same 4 rows, not 8


def test_ingest_missing_detection(client, user_token, tmp_db):
    _, token = user_token
    # First ingest: two ads
    adgroup_rows = [{"adGroup": {"id": "ag1", "name": "AG", "status": "ENABLED"},
                     "campaign": {"id": "camp_1"}}]
    ad_rows_first = [
        {"adGroupAd": {"ad": {"id": "ad_A", "name": "A", "type": "RESPONSIVE_SEARCH_AD",
                               "responsiveSearchAd": {"headlines": [{"text": "H"}], "descriptions": []}},
                        "status": "ENABLED"},
         "adGroup": {"id": "ag1"}},
        {"adGroupAd": {"ad": {"id": "ad_B", "name": "B", "type": "RESPONSIVE_SEARCH_AD",
                               "responsiveSearchAd": {"headlines": [{"text": "H2"}], "descriptions": []}},
                        "status": "ENABLED"},
         "adGroup": {"id": "ag1"}},
    ]
    # Second ingest: only ad_A (ad_B is missing)
    ad_rows_second = [ad_rows_first[0]]

    for ad_rows in [ad_rows_first, ad_rows_second]:
        call_count = {"n": 0}
        async def fake_gaql(**kwargs):
            call_count["n"] += 1
            return adgroup_rows if call_count["n"] == 1 else ad_rows
        with _mock_google_creds(), patch("google_ads_api.query_gaql", side_effect=fake_gaql):
            client.post(
                "/api/google/ingest/structure/camp_1",
                headers={"Authorization": f"Bearer {token}"},
            )

    db = sqlite3.connect(str(tmp_db))
    row = db.execute(
        "SELECT lifecycle_status FROM ad_creative_structures WHERE ad_id = 'ad_B' LIMIT 1"
    ).fetchone()
    db.close()
    assert row[0] == "missing"


# ── GET /api/google/structure route ──────────────────────────────────────────

def test_get_structure_returns_ingested(client, user_token, tmp_db):
    _, token = user_token
    adgroup_rows, ad_rows = _make_rsa_rows()
    with _mock_google_creds(), _mock_fetch_structure(adgroup_rows, ad_rows):
        client.post(
            "/api/google/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )
    resp = client.get(
        "/api/google/structure/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    ads = resp.json()
    assert len(ads) == 1
    assert ads[0]["creative_type"] == "rsa"
    assert "headline" in ads[0]["components"]
    assert ads[0]["components"]["headline"] == ["Headline A", "Headline B"]


def test_get_structure_empty_before_ingest(client, user_token, tmp_db):
    _, token = user_token
    resp = client.get(
        "/api/google/structure/camp_999",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_platform_isolation(client, user_token, tmp_db):
    """Google structure route must not return Meta rows for the same campaign_id."""
    user_id, token = user_token
    # Insert a Meta row directly with the same campaign_id
    db = sqlite3.connect(str(tmp_db))
    db.execute(
        """INSERT INTO ad_creative_structures
               (user_id, ad_account_id, campaign_id, adset_id, ad_id,
                creative_type, slot, slot_index, value, ingested_at, lifecycle_status, platform)
           VALUES (?, 'act_123', 'camp_1', 'adset_1', 'meta_ad_001',
                   'dynamic', 'headline', 0, 'Meta Headline', '2024-01-01T00:00:00Z', 'active', 'meta')""",
        (user_id,),
    )
    db.commit()
    db.close()

    # Google ingest
    adgroup_rows, ad_rows = _make_rsa_rows()
    with _mock_google_creds(), _mock_fetch_structure(adgroup_rows, ad_rows):
        client.post(
            "/api/google/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    resp = client.get(
        "/api/google/structure/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    ads = resp.json()
    # Only the Google ad should appear
    assert all(a["ad_id"] != "meta_ad_001" for a in ads)
    assert any(a["ad_id"] == "ad_001" for a in ads)


def test_ingest_requires_auth(client, tmp_db):
    resp = client.post("/api/google/ingest/structure/camp_1")
    assert resp.status_code in (401, 403)


def test_get_structure_requires_auth(client, tmp_db):
    resp = client.get("/api/google/structure/camp_1")
    assert resp.status_code in (401, 403)


def test_ingest_not_connected(client, user_token, tmp_db):
    _, token = user_token
    # No google_connections row → _google_creds raises 400
    resp = client.post(
        "/api/google/ingest/structure/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400

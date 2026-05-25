"""
Tests for structural creative ingestion.

Covers:
- _normalize_creative for static ads
- _normalize_creative for dynamic ads
- ingest_campaign_structure endpoint with one ad
- ingest_campaign_structure endpoint with multiple ads
- duplicate ingest is idempotent (upsert)
- existing /api/ingest metrics snapshot behavior still works
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set required env vars before importing main
os.environ.setdefault("META_APP_ID", "test_app_id")
os.environ.setdefault("META_APP_SECRET", "test_app_secret")
os.environ.setdefault("META_REDIRECT_URI", "http://localhost:8000/auth/meta/callback")
os.environ.setdefault("JWT_SECRET", "test_secret")

import main as m
from main import _normalize_creative, app, create_token, get_db, init_db

from fastapi.testclient import TestClient


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a fresh temp file for each test."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(m, "DB_PATH", db_file)
    init_db()
    return db_file


@pytest.fixture()
def client(tmp_db):
    return TestClient(app)


@pytest.fixture()
def user_token(tmp_db):
    """Create a user in the temp DB and return a valid JWT."""
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


# ── Unit tests: _normalize_creative ───────────────────────────────────────────

def test_normalize_static_all_slots():
    ad = {
        "id": "ad_1",
        "creative": {
            "title": "Buy Now",
            "body": "Great deal today",
            "image_url": "https://example.com/img.jpg",
            "object_story_spec": {
                "link_data": {
                    "description": "Limited time offer",
                }
            },
        },
    }
    creative_type, components = _normalize_creative(ad)

    assert creative_type == "static"
    slots = {c["slot"]: c for c in components}
    assert slots["headline"]["value"] == "Buy Now"
    assert slots["headline"]["slot_index"] == 0
    assert slots["primary_text"]["value"] == "Great deal today"
    assert slots["primary_text"]["slot_index"] == 0
    assert slots["description"]["value"] == "Limited time offer"
    assert slots["description"]["slot_index"] == 0
    assert slots["image"]["value"] == "https://example.com/img.jpg"
    assert slots["image"]["slot_index"] == 0


def test_normalize_static_falls_back_to_object_story_spec():
    ad = {
        "id": "ad_2",
        "creative": {
            "object_story_spec": {
                "link_data": {
                    "name": "Story headline",
                    "message": "Story body text",
                    "description": "Story description",
                }
            },
        },
    }
    creative_type, components = _normalize_creative(ad)

    assert creative_type == "static"
    slots = {c["slot"]: c for c in components}
    assert slots["headline"]["value"] == "Story headline"
    assert slots["primary_text"]["value"] == "Story body text"
    assert slots["description"]["value"] == "Story description"
    assert "image" not in slots


def test_normalize_static_no_creative():
    ad = {"id": "ad_3"}
    creative_type, components = _normalize_creative(ad)
    assert creative_type == "static"
    assert components == []


def test_normalize_dynamic_multiple_variants():
    ad = {
        "id": "ad_4",
        "creative": {
            "asset_feed_spec": {
                "titles": [{"text": "Headline A"}, {"text": "Headline B"}],
                "bodies": [{"text": "Body 1"}, {"text": "Body 2"}, {"text": "Body 3"}],
                "descriptions": [{"text": "Desc X"}],
                "images": [{"url": "https://example.com/a.jpg"}, {"url": "https://example.com/b.jpg"}],
            }
        },
    }
    creative_type, components = _normalize_creative(ad)

    assert creative_type == "dynamic"

    headlines = [c for c in components if c["slot"] == "headline"]
    assert len(headlines) == 2
    assert headlines[0] == {"slot": "headline", "slot_index": 0, "value": "Headline A"}
    assert headlines[1] == {"slot": "headline", "slot_index": 1, "value": "Headline B"}

    bodies = [c for c in components if c["slot"] == "primary_text"]
    assert len(bodies) == 3
    assert bodies[2]["value"] == "Body 3"

    descs = [c for c in components if c["slot"] == "description"]
    assert len(descs) == 1

    images = [c for c in components if c["slot"] == "image"]
    assert len(images) == 2
    assert images[0]["value"] == "https://example.com/a.jpg"


def test_normalize_dynamic_image_hash_fallback():
    ad = {
        "id": "ad_5",
        "creative": {
            "asset_feed_spec": {
                "images": [{"hash": "abc123"}],
            }
        },
    }
    _, components = _normalize_creative(ad)
    assert components[0] == {"slot": "image", "slot_index": 0, "value": "abc123"}


# ── Endpoint tests ─────────────────────────────────────────────────────────────

def _make_meta_response(data: list | dict, status: int = 200):
    """Build a mock httpx.Response-like object."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data if isinstance(data, dict) else {"data": data}
    resp.text = json.dumps(data)
    return resp


STATIC_AD = {
    "id": "ad_s1",
    "name": "Static Ad",
    "status": "ACTIVE",
    "campaign_id": "camp_1",
    "adset_id": "adset_1",
    "creative": {
        "title": "Buy Now",
        "body": "Great deal",
        "image_url": "https://example.com/img.jpg",
    },
}

DYNAMIC_AD = {
    "id": "ad_d1",
    "name": "Dynamic Ad",
    "status": "ACTIVE",
    "campaign_id": "camp_1",
    "adset_id": "adset_1",
    "creative": {
        "asset_feed_spec": {
            "titles": [{"text": "Headline A"}, {"text": "Headline B"}],
            "bodies": [{"text": "Body 1"}],
            "images": [{"url": "https://example.com/a.jpg"}],
        }
    },
}

ADSETS = [{"id": "adset_1", "name": "Adset 1", "status": "ACTIVE", "campaign_id": "camp_1"}]


def _patch_fetch_structure(adsets, ads):
    """Return a coroutine that yields (adsets, ads)."""
    async def _fake(*args, **kwargs):
        return adsets, ads
    return patch("main._fetch_campaign_structure", side_effect=_fake)


def test_ingest_static_ad(client, user_token, tmp_db):
    user_id, token = user_token

    with _patch_fetch_structure(ADSETS, [STATIC_AD]):
        resp = client.post(
            "/api/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_id"] == "camp_1"
    assert body["ads_processed"] == 1
    assert body["components_saved"] == 3  # headline + primary_text + image

    db = sqlite3.connect(str(tmp_db))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT slot, slot_index, value, creative_type FROM ad_creative_structures "
        "WHERE ad_id = 'ad_s1' ORDER BY slot"
    ).fetchall()
    db.close()

    assert len(rows) == 3
    slots = {r["slot"]: r for r in rows}
    assert slots["headline"]["value"] == "Buy Now"
    assert slots["headline"]["creative_type"] == "static"
    assert slots["primary_text"]["value"] == "Great deal"
    assert slots["image"]["value"] == "https://example.com/img.jpg"


def test_ingest_dynamic_ad(client, user_token, tmp_db):
    user_id, token = user_token

    with _patch_fetch_structure(ADSETS, [DYNAMIC_AD]):
        resp = client.post(
            "/api/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ads_processed"] == 1
    assert body["components_saved"] == 4  # 2 headlines + 1 body + 1 image

    db = sqlite3.connect(str(tmp_db))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT slot, slot_index, value, creative_type FROM ad_creative_structures "
        "WHERE ad_id = 'ad_d1' ORDER BY slot, slot_index"
    ).fetchall()
    db.close()

    assert len(rows) == 4
    headlines = [r for r in rows if r["slot"] == "headline"]
    assert len(headlines) == 2
    assert headlines[0]["value"] == "Headline A"
    assert headlines[1]["value"] == "Headline B"
    assert headlines[0]["creative_type"] == "dynamic"


def test_ingest_multiple_ads(client, user_token, tmp_db):
    user_id, token = user_token

    with _patch_fetch_structure(ADSETS, [STATIC_AD, DYNAMIC_AD]):
        resp = client.post(
            "/api/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ads_processed"] == 2
    assert body["components_saved"] == 7  # 3 static + 4 dynamic

    db = sqlite3.connect(str(tmp_db))
    count = db.execute("SELECT COUNT(*) FROM ad_creative_structures").fetchone()[0]
    db.close()
    assert count == 7


def test_duplicate_ingest_is_idempotent(client, user_token, tmp_db):
    user_id, token = user_token

    updated_ad = {**STATIC_AD, "creative": {**STATIC_AD["creative"], "body": "Updated body"}}

    with _patch_fetch_structure(ADSETS, [STATIC_AD]):
        client.post(
            "/api/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    with _patch_fetch_structure(ADSETS, [updated_ad]):
        resp = client.post(
            "/api/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200

    db = sqlite3.connect(str(tmp_db))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT slot, value FROM ad_creative_structures WHERE ad_id = 'ad_s1'"
    ).fetchall()
    count = db.execute("SELECT COUNT(*) FROM ad_creative_structures").fetchone()[0]
    db.close()

    # Row count must not grow on re-ingest
    assert count == 3
    slots = {r["slot"]: r["value"] for r in rows}
    # Updated value replaces old one
    assert slots["primary_text"] == "Updated body"


# ── /api/ingest metrics snapshot tests ────────────────────────────────────────

def test_existing_metrics_ingest_unaffected(client, user_token, tmp_db):
    """POST /api/ingest still saves rows and returns richer summary when metrics are present."""
    user_id, token = user_token

    campaigns_raw = [
        {"id": "camp_1", "name": "Campaign 1", "status": "ACTIVE"}
    ]
    metrics_by_campaign = {
        "camp_1": {
            "impressions": 1000,
            "clicks": 50,
            "spend": 25.0,
            "ctr": 5.0,
            "cpm": 25.0,
            "cpc": 0.5,
        }
    }

    with patch(
        "main._fetch_campaigns_and_insights",
        new=AsyncMock(return_value=(campaigns_raw, metrics_by_campaign, 0)),
    ):
        resp = client.post(
            "/api/ingest",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["campaigns_saved"] == 1
    assert body["ad_account_id"] == "act_999"
    assert body["campaigns_seen"] == 1
    assert body["campaigns_with_metrics"] == 1
    assert body["insights_errors"] == 0
    assert "act_999" in body["message"]

    db = sqlite3.connect(str(tmp_db))
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT impressions, clicks, spend FROM ad_insights WHERE object_id = 'camp_1'"
    ).fetchone()
    db.close()

    assert row["impressions"] == 1000
    assert row["clicks"] == 50
    assert abs(row["spend"] - 25.0) < 0.001


def test_ingest_zero_metrics_clear_message(client, user_token, tmp_db):
    """When campaigns exist but none have delivery data, no rows are saved and message is clear."""
    _, token = user_token

    campaigns_raw = [
        {"id": "camp_1", "name": "Campaign 1", "status": "PAUSED"},
        {"id": "camp_2", "name": "Campaign 2", "status": "PAUSED"},
    ]

    with patch(
        "main._fetch_campaigns_and_insights",
        new=AsyncMock(return_value=(campaigns_raw, {}, 0)),
    ):
        resp = client.post(
            "/api/ingest",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["campaigns_saved"] == 0
    assert body["campaigns_seen"] == 2
    assert body["campaigns_with_metrics"] == 0
    assert body["insights_errors"] == 0
    # Message should explain no delivery data rather than being cryptically empty
    assert "no" in body["message"].lower() or "none" in body["message"].lower()

    db = sqlite3.connect(str(tmp_db))
    count = db.execute("SELECT COUNT(*) FROM ad_insights").fetchone()[0]
    db.close()
    assert count == 0  # Nothing written


def test_ingest_insights_error_surfaces_in_response(client, user_token, tmp_db):
    """When the insights API call fails, insights_errors > 0 and the message says so."""
    _, token = user_token

    campaigns_raw = [
        {"id": "camp_1", "name": "Campaign 1", "status": "ACTIVE"},
    ]

    with patch(
        "main._fetch_campaigns_and_insights",
        new=AsyncMock(return_value=(campaigns_raw, {}, 1)),
    ):
        resp = client.post(
            "/api/ingest",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["campaigns_saved"] == 0
    assert body["campaigns_seen"] == 1
    assert body["insights_errors"] == 1
    assert "failed" in body["message"].lower() or "error" in body["message"].lower()

    db = sqlite3.connect(str(tmp_db))
    count = db.execute("SELECT COUNT(*) FROM ad_insights").fetchone()[0]
    db.close()
    assert count == 0  # No rows written despite insights error


def test_ingest_partial_metrics_only_saves_campaigns_with_data(client, user_token, tmp_db):
    """Campaigns without metrics are still not saved (existing behavior preserved)."""
    _, token = user_token

    campaigns_raw = [
        {"id": "camp_with_data", "name": "Active", "status": "ACTIVE"},
        {"id": "camp_no_data", "name": "Paused", "status": "PAUSED"},
    ]
    metrics_by_campaign = {
        "camp_with_data": {
            "impressions": 500, "clicks": 20, "spend": 10.0,
            "ctr": 4.0, "cpm": 20.0, "cpc": 0.5,
        }
    }

    with patch(
        "main._fetch_campaigns_and_insights",
        new=AsyncMock(return_value=(campaigns_raw, metrics_by_campaign, 0)),
    ):
        resp = client.post(
            "/api/ingest",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["campaigns_saved"] == 1
    assert body["campaigns_seen"] == 2
    assert body["campaigns_with_metrics"] == 1
    assert body["insights_errors"] == 0

    db = sqlite3.connect(str(tmp_db))
    rows = db.execute("SELECT object_id FROM ad_insights").fetchall()
    db.close()
    assert len(rows) == 1
    assert rows[0][0] == "camp_with_data"


# ── GET /api/structure/{campaign_id} ──────────────────────────────────────────

def _seed_structure(tmp_db, user_id, rows):
    """Insert rows directly into ad_creative_structures for read-endpoint tests."""
    db = sqlite3.connect(str(tmp_db))
    for r in rows:
        db.execute(
            """
            INSERT INTO ad_creative_structures
                (user_id, ad_account_id, campaign_id, adset_id, ad_id,
                 creative_type, slot, slot_index, value, ingested_at, lifecycle_status)
            VALUES (?, 'act_999', ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
            """,
            (user_id, r["campaign_id"], r["adset_id"], r["ad_id"],
             r["creative_type"], r["slot"], r["slot_index"], r["value"],
             r.get("lifecycle_status", "active")),
        )
    db.commit()
    db.close()


def test_get_structure_groups_by_ad(client, user_token, tmp_db):
    user_id, token = user_token

    _seed_structure(tmp_db, user_id, [
        {"campaign_id": "camp_1", "adset_id": "adset_1", "ad_id": "ad_s1",
         "creative_type": "static", "slot": "headline", "slot_index": 0, "value": "Buy Now"},
        {"campaign_id": "camp_1", "adset_id": "adset_1", "ad_id": "ad_s1",
         "creative_type": "static", "slot": "primary_text", "slot_index": 0, "value": "Great deal"},
        {"campaign_id": "camp_1", "adset_id": "adset_1", "ad_id": "ad_d1",
         "creative_type": "dynamic", "slot": "headline", "slot_index": 0, "value": "Headline A"},
        {"campaign_id": "camp_1", "adset_id": "adset_1", "ad_id": "ad_d1",
         "creative_type": "dynamic", "slot": "headline", "slot_index": 1, "value": "Headline B"},
    ])

    resp = client.get(
        "/api/structure/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert len(body) == 2
    by_id = {a["ad_id"]: a for a in body}

    static_ad = by_id["ad_s1"]
    assert static_ad["creative_type"] == "static"
    assert static_ad["campaign_id"] == "camp_1"
    assert static_ad["adset_id"] == "adset_1"
    assert static_ad["components"]["headline"] == ["Buy Now"]
    assert static_ad["components"]["primary_text"] == ["Great deal"]
    assert static_ad["lifecycle_status"] == "active"

    dynamic_ad = by_id["ad_d1"]
    assert dynamic_ad["creative_type"] == "dynamic"
    assert dynamic_ad["components"]["headline"] == ["Headline A", "Headline B"]
    assert dynamic_ad["lifecycle_status"] == "active"


def test_get_structure_empty_returns_list(client, user_token, tmp_db):
    """Campaign with no ingested structure returns empty list, not 404."""
    _, token = user_token
    resp = client.get(
        "/api/structure/nonexistent_campaign",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_structure_isolates_by_user(client, user_token, tmp_db):
    """Structure rows for a different user are not returned."""
    user_id, token = user_token

    # Seed rows for a different user_id (999)
    db = sqlite3.connect(str(tmp_db))
    db.execute(
        """
        INSERT INTO users (id, email, pw_hash) VALUES (999, 'other@test.com', 'x')
        """
    )
    db.commit()
    db.close()

    _seed_structure(tmp_db, 999, [
        {"campaign_id": "camp_1", "adset_id": "adset_1", "ad_id": "ad_other",
         "creative_type": "static", "slot": "headline", "slot_index": 0, "value": "Other"},
    ])

    resp = client.get(
        "/api/structure/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_ingest_then_read_roundtrip(client, user_token, tmp_db):
    """Ingest a campaign and immediately read back the structure."""
    _, token = user_token

    with _patch_fetch_structure(ADSETS, [STATIC_AD, DYNAMIC_AD]):
        ingest_resp = client.post(
            "/api/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert ingest_resp.status_code == 200
    summary = ingest_resp.json()
    assert summary["ads_processed"] == 2

    read_resp = client.get(
        "/api/structure/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert read_resp.status_code == 200
    ads = read_resp.json()
    assert len(ads) == 2

    by_id = {a["ad_id"]: a for a in ads}
    assert by_id["ad_s1"]["creative_type"] == "static"
    assert by_id["ad_d1"]["creative_type"] == "dynamic"
    assert len(by_id["ad_d1"]["components"]["headline"]) == 2
    assert by_id["ad_s1"]["lifecycle_status"] == "active"
    assert by_id["ad_d1"]["lifecycle_status"] == "active"


# ── Lifecycle status tests ─────────────────────────────────────────────────────

def test_lifecycle_active_when_present_and_active(client, user_token, tmp_db):
    """Ad in latest fetch with ACTIVE status → lifecycle_status == 'active'."""
    _, token = user_token

    active_ad = {**STATIC_AD, "status": "ACTIVE", "effective_status": "ACTIVE"}

    with _patch_fetch_structure(ADSETS, [active_ad]):
        resp = client.post(
            "/api/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200

    read_resp = client.get(
        "/api/structure/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert read_resp.status_code == 200
    ads = read_resp.json()
    assert len(ads) == 1
    assert ads[0]["lifecycle_status"] == "active"


def test_lifecycle_inactive_when_present_but_not_active(client, user_token, tmp_db):
    """Ad in latest fetch with non-ACTIVE effective_status → lifecycle_status == 'inactive'."""
    _, token = user_token

    paused_ad = {**STATIC_AD, "status": "PAUSED", "effective_status": "PAUSED"}

    with _patch_fetch_structure(ADSETS, [paused_ad]):
        resp = client.post(
            "/api/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200

    read_resp = client.get(
        "/api/structure/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert read_resp.status_code == 200
    ads = read_resp.json()
    assert len(ads) == 1
    assert ads[0]["lifecycle_status"] == "inactive"


def test_lifecycle_missing_when_previously_ingested_but_not_in_latest_fetch(
    client, user_token, tmp_db
):
    """Ad with existing structural rows but absent from latest Meta fetch → lifecycle_status == 'missing'."""
    _, token = user_token

    # First ingest: ad_s1 is present
    with _patch_fetch_structure(ADSETS, [STATIC_AD]):
        client.post(
            "/api/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )

    # Second ingest: ad_s1 is gone, only dynamic ad remains
    with _patch_fetch_structure(ADSETS, [DYNAMIC_AD]):
        resp = client.post(
            "/api/ingest/structure/camp_1",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200

    read_resp = client.get(
        "/api/structure/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert read_resp.status_code == 200
    ads = read_resp.json()
    by_id = {a["ad_id"]: a for a in ads}

    # ad_s1 is still returned (historical rows remain) but marked missing
    assert by_id["ad_s1"]["lifecycle_status"] == "missing"
    # ad_d1 is still present and active
    assert by_id["ad_d1"]["lifecycle_status"] == "active"


def test_get_structure_includes_lifecycle_status(client, user_token, tmp_db):
    """GET /api/structure/{campaign_id} always includes lifecycle_status per ad."""
    user_id, token = user_token

    _seed_structure(tmp_db, user_id, [
        {"campaign_id": "camp_1", "adset_id": "adset_1", "ad_id": "ad_x",
         "creative_type": "static", "slot": "headline", "slot_index": 0,
         "value": "Test", "lifecycle_status": "inactive"},
    ])

    resp = client.get(
        "/api/structure/camp_1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    ads = resp.json()
    assert len(ads) == 1
    assert ads[0]["lifecycle_status"] == "inactive"

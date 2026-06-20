"""
fake_ad_server/state.py

Shared in-memory state for the fake ad server.  All pushed ads and creatives
live here for the lifetime of the server process.  Everything is keyed by the
platform account identifier so multiple accounts in the same demo session don't
collide.

Also owns the deterministic metrics functions used by both route modules.
"""

import hashlib
import json
import os
import random
import time
import uuid
from pathlib import Path
from threading import Lock

_lock = Lock()

# FAST_RAMP=true — treat every pushed clone as if it has been running for 24 hours.
# Use alongside COLD_START so that after the user pushes a clone, the convergence
# checker immediately writes real CTR to scored_observations, transitioning to Case 1.
_FAST_RAMP = os.getenv("FAST_RAMP", "").lower() in ("1", "true", "yes")

# COLD_START=true — fixture campaign and ad metrics return zeros.
# Simulates a new account with no delivery history: ingest sees no impressions,
# so scored_observations stays empty and warm-start fires on the first BO run.
# Switch to Case 1 by restarting without COLD_START and reingesting.
_COLD_START = os.getenv("COLD_START", "").lower() in ("1", "true", "yes")

# ── Meta state ────────────────────────────────────────────────────────────────

# creative_id → {id, name, object_story_spec}
_meta_creatives: dict[str, dict] = {}

# act_id → list of ad dicts (each carries "_pushed_at" internal key)
_meta_ads: dict[str, list[dict]] = {}

# ad_id → Qwen-derived CTR percentage (cached after first Qwen call)
_meta_ad_qwen_ctr: dict[str, float] = {}

# ── Google state ──────────────────────────────────────────────────────────────

# customer_id → list of ad-group-ad rows (each carries "_pushed_at", "_campaign_id")
_google_ads: dict[str, list[dict]] = {}

# ── Lookup maps built from fixtures ───────────────────────────────────────────

_adset_to_campaign: dict[str, str] = {}   # Meta adset_id → campaign_id
_adgroup_to_campaign: dict[str, str] = {} # Google adgroup_id → campaign_id

_FIXTURES = Path(__file__).parent / "fixtures"


def _build_lookup_maps() -> None:
    try:
        meta_adsets = json.loads((_FIXTURES / "meta_adsets.json").read_text())
        for campaign_id, adsets in meta_adsets.items():
            for adset in adsets:
                _adset_to_campaign[str(adset["id"])] = str(campaign_id)
    except Exception:
        pass

    try:
        google_adgroups = json.loads((_FIXTURES / "google_adgroups.json").read_text())
        for campaign_id, groups in google_adgroups.items():
            for row in groups:
                ag_id = str((row.get("adGroup") or {}).get("id", ""))
                if ag_id:
                    _adgroup_to_campaign[ag_id] = str(campaign_id)
    except Exception:
        pass


_build_lookup_maps()

# ── Meta operations ───────────────────────────────────────────────────────────

def store_meta_creative(name: str, object_story_spec_raw: str) -> str:
    creative_id = f"fake_creative_{uuid.uuid4().hex[:10]}"
    try:
        oss = json.loads(object_story_spec_raw)
    except Exception:
        oss = {}
    with _lock:
        _meta_creatives[creative_id] = {
            "id": creative_id,
            "name": name,
            "object_story_spec": oss,
        }
    return creative_id


def store_meta_ad(act_id: str, name: str, adset_id: str, creative_id: str,
                  status: str = "PAUSED") -> str:
    ad_id = f"fake_ad_{uuid.uuid4().hex[:10]}"
    campaign_id = _adset_to_campaign.get(str(adset_id), "unknown")
    with _lock:
        creative = dict(_meta_creatives.get(creative_id, {"id": creative_id}))
        ad = {
            "id": ad_id,
            "name": name,
            "status": status,
            "effective_status": status,
            "campaign_id": campaign_id,
            "adset_id": adset_id,
            "creative": creative,
            "_pushed_at": time.time(),
        }
        _meta_ads.setdefault(act_id, []).append(ad)
    return ad_id


def get_meta_ads(act_id: str, campaign_id: str | None = None) -> list[dict]:
    with _lock:
        ads = list(_meta_ads.get(act_id, []))
    if campaign_id:
        ads = [a for a in ads if str(a.get("campaign_id")) == str(campaign_id)]
    # Strip internal keys before returning
    return [{k: v for k, v in a.items() if not k.startswith("_")} for a in ads]


def get_meta_ad_by_id(ad_id: str) -> dict | None:
    with _lock:
        for ads in _meta_ads.values():
            for ad in ads:
                if ad["id"] == ad_id:
                    return {k: v for k, v in ad.items() if not k.startswith("_")}
    return None


def pushed_meta_ad_metrics(ad_id: str) -> dict:
    """Return live metrics for a pushed Meta clone (starts at zero, ramps up)."""
    with _lock:
        pushed_at = None
        for ads in _meta_ads.values():
            for ad in ads:
                if ad["id"] == ad_id:
                    pushed_at = ad.get("_pushed_at")
                    break
    if pushed_at is None:
        return {}
    return _ramp_metrics(ad_id, pushed_at)


def update_meta_ad_status(ad_id: str, status: str) -> bool:
    with _lock:
        for ads in _meta_ads.values():
            for ad in ads:
                if ad["id"] == ad_id:
                    ad["status"] = status
                    ad["effective_status"] = status
                    return True
    return False


def get_creative_content_for_ad(ad_id: str) -> dict | None:
    """Return {title, body, image_url} extracted from a pushed ad's creative, or None."""
    with _lock:
        for ads in _meta_ads.values():
            for ad in ads:
                if ad["id"] == ad_id:
                    oss = (ad.get("creative") or {}).get("object_story_spec") or {}
                    link_data = oss.get("link_data") or {}
                    return {
                        "title": link_data.get("name", ""),
                        "body": link_data.get("message", ""),
                        "image_url": link_data.get("picture", ""),
                    }
    return None


def get_ad_qwen_ctr(ad_id: str) -> float | None:
    return _meta_ad_qwen_ctr.get(ad_id)


def set_ad_qwen_ctr(ad_id: str, ctr: float) -> None:
    with _lock:
        _meta_ad_qwen_ctr[ad_id] = ctr

# ── Google operations ─────────────────────────────────────────────────────────

def store_google_ad(customer_id: str, body: dict) -> str:
    resource_name = f"customers/{customer_id}/adGroupAds/{uuid.uuid4().int % 10 ** 10}"

    ops = body.get("mutateOperations", [])
    ad_group_path = ""
    headlines: list[str] = []
    descriptions: list[str] = []
    final_urls: list[str] = []

    if ops:
        create = (ops[0].get("adGroupAdOperation") or {}).get("create") or {}
        ad_group_path = create.get("adGroup", "")
        ad = create.get("ad") or {}
        rsa = ad.get("responsiveSearchAd") or {}
        headlines = [h.get("text", "") for h in rsa.get("headlines", [])]
        descriptions = [d.get("text", "") for d in rsa.get("descriptions", [])]
        final_urls = ad.get("finalUrls", [])

    # Resolve campaign from adgroup path
    ag_id = ad_group_path.split("/")[-1] if "/" in ad_group_path else ad_group_path
    campaign_id = _adgroup_to_campaign.get(ag_id, "unknown")

    with _lock:
        row = {
            "adGroupAd": {
                "resourceName": resource_name,
                "ad": {
                    "resourceName": resource_name,
                    "id": str(uuid.uuid4().int % 10 ** 9),
                    "name": "Pushed RSA",
                    "type": "RESPONSIVE_SEARCH_AD",
                    "finalUrls": final_urls,
                    "responsiveSearchAd": {
                        "headlines": [{"text": h} for h in headlines],
                        "descriptions": [{"text": d} for d in descriptions],
                    },
                },
                "status": "PAUSED",
            },
            "adGroup": {"id": ag_id, "resourceName": ad_group_path},
            "campaign": {"id": campaign_id},
            "_campaign_id": campaign_id,
            "_pushed_at": time.time(),
            "_resource_name": resource_name,
        }
        _google_ads.setdefault(customer_id, []).append(row)

    return resource_name


def get_google_ads(customer_id: str, campaign_id: str | None = None) -> list[dict]:
    with _lock:
        rows = list(_google_ads.get(customer_id, []))
    if campaign_id:
        rows = [r for r in rows if str(r.get("_campaign_id")) == str(campaign_id)]
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]


def pushed_google_ad_metrics(resource_name: str) -> dict | None:
    """Return live ramp metrics for a pushed Google RSA clone, or None if not found."""
    with _lock:
        for rows in _google_ads.values():
            for row in rows:
                if row.get("_resource_name") == resource_name:
                    return _ramp_metrics(resource_name, row["_pushed_at"])
    return None


def is_fast_ramp() -> bool:
    return _FAST_RAMP


def update_google_ad_status(resource_name: str, status: str) -> bool:
    with _lock:
        for rows in _google_ads.values():
            for row in rows:
                if row.get("_resource_name") == resource_name:
                    (row.get("adGroupAd") or {})["status"] = status
                    return True
    return False

# ── Metrics ───────────────────────────────────────────────────────────────────

# Base impressions (7-day) for each fixture campaign
_META_BASES = {
    "120210001": 42000,
    "120210002": 18500,
    "120210003": 3200,
}
_GOOGLE_BASES = {
    "9876543210": 95000,
    "9876543211": 210000,
    "9876543212": 145000,
}


def campaign_metrics(campaign_id: str, platform: str = "meta") -> dict:
    """
    Deterministic, slowly evolving metrics for a fixture campaign.
    Returns zeros when COLD_START=true to simulate a new account with no history.
    """
    if _COLD_START:
        return {"impressions": 0, "clicks": 0, "spend": 0.0, "ctr": 0.0, "cpm": 0.0, "cpc": 0.0}

    bases = _META_BASES if platform == "meta" else _GOOGLE_BASES
    base = bases.get(str(campaign_id), 10000)

    hour = int(time.time()) // 3600
    hour_of_week = hour % 168          # 0-167, resets weekly
    seed = int(hashlib.md5(campaign_id.encode()).hexdigest(), 16) % (2 ** 32)
    rng = random.Random(seed ^ hour)   # XOR with hour: different seed every hour

    growth = 1.0 + hour_of_week * rng.uniform(0.004, 0.012)
    impressions = max(50, int(base * growth) + rng.randint(-int(base * 0.02), int(base * 0.02)))
    ctr = rng.uniform(0.025, 0.065)
    cpc = rng.uniform(0.35, 1.90)
    clicks = max(1, int(impressions * ctr))
    spend = round(clicks * cpc, 2)

    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend": spend,
        "ctr": round(ctr * 100, 4),
        "cpm": round(spend / impressions * 1000, 2),
        "cpc": round(spend / clicks, 2),
    }


def _ramp_metrics(entity_id: str, pushed_at: float) -> dict:
    """
    Metrics for a pushed clone: zero at push time, ramping up each hour.
    Deterministic per (entity_id, hour).
    With FAST_RAMP=true, treats every pushed ad as already 24 hours old so the
    convergence checker sees data immediately.
    """
    if _FAST_RAMP:
        hours_running = 24.0
    else:
        hours_running = max(0.0, (time.time() - pushed_at) / 3600)
    seed = int(hashlib.md5(entity_id.encode()).hexdigest(), 16) % (2 ** 32)
    rng = random.Random(seed + int(hours_running))

    impr_per_hour = rng.uniform(15, 65)
    impressions = int(impr_per_hour * hours_running) + rng.randint(0, 10)
    if impressions < 1:
        return {"impressions": 0, "clicks": 0, "spend": 0.0,
                "ctr": 0.0, "cpm": 0.0, "cpc": 0.0}

    ctr = rng.uniform(0.02, 0.055)
    cpc = rng.uniform(0.45, 2.10)
    clicks = max(0, int(impressions * ctr))
    spend = round(clicks * cpc, 2)

    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend": spend,
        "ctr": round(ctr * 100, 4),
        "cpm": round(spend / impressions * 1000, 2),
        "cpc": round(spend / max(1, clicks), 2),
    }

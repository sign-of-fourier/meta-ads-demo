"""
Read/write the fake_ad_server fixture JSON files.

All write operations are atomic at the JSON level: load → mutate → save.
Because the fake server reads fixture files fresh on every request, any
change written here is visible immediately without a server restart.

Public API:
  get_or_create_meta_campaign(fs_dir, campaign_id, campaign_name) → campaign_id str
  get_or_create_meta_adset(fs_dir, campaign_id, adset_id, adset_name) → adset_id str
  inject_meta_ad(fs_dir, ad_dict) → None

  get_or_create_google_campaign(fs_dir, campaign_id, campaign_name) → campaign_id str
  get_or_create_google_adgroup(fs_dir, campaign_id, adgroup_id, adgroup_name) → adgroup_id str
  inject_google_ad(fs_dir, ad_row) → None
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


# ── low-level helpers ─────────────────────────────────────────────────────────

def _load(fs_dir: Path, name: str) -> Any:
    return json.loads((fs_dir / "fixtures" / name).read_text())


def _save(fs_dir: Path, name: str, data: Any) -> None:
    path = fs_dir / "fixtures" / name
    path.write_text(json.dumps(data, indent=2))


def _fresh_id() -> str:
    """Monotonically increasing ID based on current time (microseconds)."""
    return str(int(time.time() * 1_000) % 10 ** 12)


# ── Meta ──────────────────────────────────────────────────────────────────────

def get_or_create_meta_campaign(
    fs_dir: Path,
    campaign_id: str | None,
    campaign_name: str,
) -> str:
    campaigns: list[dict] = _load(fs_dir, "meta_campaigns.json")

    if campaign_id:
        if any(str(c["id"]) == str(campaign_id) for c in campaigns):
            return str(campaign_id)
        # ID supplied but absent → create with that ID
    else:
        # No ID supplied → use first existing campaign
        if campaigns:
            return str(campaigns[0]["id"])
        campaign_id = _fresh_id()

    new_campaign = {
        "id": str(campaign_id),
        "name": campaign_name,
        "status": "ACTIVE",
        "daily_budget": "5000",
    }
    campaigns.append(new_campaign)
    _save(fs_dir, "meta_campaigns.json", campaigns)

    # Add a baseline insights row so the campaign appears in the insights feed
    insights: list[dict] = _load(fs_dir, "meta_insights.json")
    if not any(r["campaign_id"] == str(campaign_id) for r in insights):
        insights.append({
            "campaign_id": str(campaign_id),
            "impressions": "0",
            "clicks": "0",
            "spend": "0.00",
            "date_start": "2026-05-01",
            "date_stop": "2026-05-31",
        })
        _save(fs_dir, "meta_insights.json", insights)

    print(f"  Created Meta campaign {campaign_id!r} ({campaign_name!r})")
    return str(campaign_id)


def get_or_create_meta_adset(
    fs_dir: Path,
    campaign_id: str,
    adset_id: str | None,
    adset_name: str,
) -> str:
    adsets: dict[str, list[dict]] = _load(fs_dir, "meta_adsets.json")

    if adset_id:
        for sets in adsets.values():
            if any(str(s["id"]) == str(adset_id) for s in sets):
                return str(adset_id)
        # ID supplied but absent → create with that ID
    else:
        existing = adsets.get(str(campaign_id), [])
        if existing:
            return str(existing[0]["id"])
        adset_id = _fresh_id()

    new_adset = {
        "id": str(adset_id),
        "name": adset_name,
        "status": "ACTIVE",
        "campaign_id": str(campaign_id),
        "targeting": {"publisher_platforms": ["facebook", "instagram"]},
    }
    adsets.setdefault(str(campaign_id), []).append(new_adset)
    _save(fs_dir, "meta_adsets.json", adsets)

    print(f"  Created Meta adset {adset_id!r} ({adset_name!r}) under campaign {campaign_id!r}")
    return str(adset_id)


def inject_meta_ad(fs_dir: Path, ad_dict: dict) -> None:
    """Append ad_dict to the meta_ads.json under its campaign_id key."""
    ads: dict[str, list[dict]] = _load(fs_dir, "meta_ads.json")
    cid = str(ad_dict["campaign_id"])
    ads.setdefault(cid, []).append(ad_dict)
    _save(fs_dir, "meta_ads.json", ads)


# ── Google ────────────────────────────────────────────────────────────────────

def get_or_create_google_campaign(
    fs_dir: Path,
    campaign_id: str | None,
    campaign_name: str,
) -> str:
    campaigns: list[dict] = _load(fs_dir, "google_campaigns.json")

    def _cid(row: dict) -> str:
        return str((row.get("campaign") or {}).get("id", ""))

    if campaign_id:
        if any(_cid(c) == str(campaign_id) for c in campaigns):
            return str(campaign_id)
    else:
        if campaigns:
            return _cid(campaigns[0])
        campaign_id = _fresh_id()

    new_campaign = {
        "campaign": {
            "id": str(campaign_id),
            "name": campaign_name,
            "status": "ENABLED",
        },
        "campaignBudget": {"amountMicros": "5000000000"},
        "metrics": {"impressions": "0", "clicks": "0", "costMicros": "0"},
    }
    campaigns.append(new_campaign)
    _save(fs_dir, "google_campaigns.json", campaigns)

    print(f"  Created Google campaign {campaign_id!r} ({campaign_name!r})")
    return str(campaign_id)


def get_or_create_google_adgroup(
    fs_dir: Path,
    campaign_id: str,
    adgroup_id: str | None,
    adgroup_name: str,
) -> str:
    adgroups: dict[str, list[dict]] = _load(fs_dir, "google_adgroups.json")

    def _agid(row: dict) -> str:
        return str((row.get("adGroup") or {}).get("id", ""))

    if adgroup_id:
        for groups in adgroups.values():
            if any(_agid(g) == str(adgroup_id) for g in groups):
                return str(adgroup_id)
    else:
        existing = adgroups.get(str(campaign_id), [])
        if existing:
            return _agid(existing[0])
        adgroup_id = _fresh_id()

    new_adgroup = {
        "adGroup": {
            "id": str(adgroup_id),
            "name": adgroup_name,
            "status": "ENABLED",
        },
        "campaign": {"id": str(campaign_id)},
    }
    adgroups.setdefault(str(campaign_id), []).append(new_adgroup)
    _save(fs_dir, "google_adgroups.json", adgroups)

    print(f"  Created Google ad group {adgroup_id!r} ({adgroup_name!r}) under campaign {campaign_id!r}")
    return str(adgroup_id)


def inject_google_ad(fs_dir: Path, ad_row: dict) -> None:
    """Append ad_row to google_ads.json under its campaign id key."""
    ads: dict[str, list[dict]] = _load(fs_dir, "google_ads.json")
    cid = str((ad_row.get("campaign") or {}).get("id", "unknown"))
    ads.setdefault(cid, []).append(ad_row)
    _save(fs_dir, "google_ads.json", ads)

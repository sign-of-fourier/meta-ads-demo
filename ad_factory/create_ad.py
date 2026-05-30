"""
ad_factory/create_ad.py — CLI entry point.

Usage:
    python ad_factory/create_ad.py <config.json>
    python -m ad_factory <config.json>

Config keys:
    concept         (required) plain-English description of the ad / product
    platform        (required) "meta" or "google"
    campaign_id     existing campaign ID; omit to use first fixture campaign
    campaign_name   name for a new campaign (only used when creating)
    adset_id        Meta adset ID or Google ad_group ID; omit to use first in campaign
    adset_name      name for a new adset/ad_group (only used when creating)
    ad_name         override the ad name (defaults to truncated concept)
    final_url       landing page URL (default "https://example.com")
    status          "PAUSED" or "ACTIVE" (default "PAUSED")
    generate_image  true → call deAPI FLUX img2img (needs DEAPI_API_KEY)
                    false (default) → deterministic picsum placeholder
    image_url       explicit image URL, overrides generate_image
    customer_id     Google Ads customer ID (default "1234567890")
    fake_server_dir path to fake_ad_server directory (default "../fake_ad_server")

Environment (loaded automatically from backend/.env if present):
    AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION
    AZURE_TEXT_GEN_DEPLOYMENT
    DEAPI_API_KEY (only if generate_image: true)
    IMAGES_SERVE_BASE_URL (only if generate_image: true)
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path(__file__).parent.parent / "backend" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=False)
        except ImportError:
            pass


_load_dotenv()

# Local imports after env is loaded
from ad_factory import fixtures, image_gen, text_gen  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _resolve_fs_dir(config: dict) -> Path:
    raw = config.get("fake_server_dir", "fake_ad_server")
    p = Path(raw)
    if not p.is_absolute():
        # Resolve relative to the project root (grandparent of ad_factory/)
        project_root = Path(__file__).parent.parent
        p = (project_root / raw).resolve()
    if not (p / "fixtures").is_dir():
        raise FileNotFoundError(
            f"fake_ad_server fixtures not found at {p}/fixtures. "
            "Set fake_server_dir in your config."
        )
    return p


def _pick_image(config: dict) -> str:
    if config.get("image_url"):
        return config["image_url"]
    if config.get("generate_image"):
        return image_gen.generate_with_deapi(config["concept"])
    return image_gen.placeholder(config["concept"])


# ── Meta ──────────────────────────────────────────────────────────────────────

def _create_meta_ad(config: dict) -> None:
    concept = config["concept"]
    final_url = config.get("final_url", "https://example.com")
    fs_dir = _resolve_fs_dir(config)

    campaign_id = fixtures.get_or_create_meta_campaign(
        fs_dir,
        campaign_id=config.get("campaign_id"),
        campaign_name=config.get("campaign_name", f"{concept[:50]} Campaign"),
    )

    adset_id = fixtures.get_or_create_meta_adset(
        fs_dir,
        campaign_id=campaign_id,
        adset_id=config.get("adset_id"),
        adset_name=config.get("adset_name", "AI Factory Ad Set"),
    )

    print("Generating Meta ad copy …")
    text = text_gen.generate_meta_ad_text(concept, final_url)

    print("Resolving image …")
    img_url = _pick_image(config)

    ad_id = f"adgen_{uuid.uuid4().hex[:10]}"
    ad_name = config.get("ad_name", f"{concept[:50]} — AI Ad")
    status = config.get("status", "PAUSED")

    ad = {
        "id": ad_id,
        "name": ad_name,
        "status": status,
        "effective_status": status,
        "campaign_id": campaign_id,
        "adset_id": adset_id,
        "creative": {
            "id": f"creative_{ad_id}",
            "title": text["headline"],
            "body": text["primary_text"],
            "description": text["description"],
            "image_url": img_url,
            "thumbnail_url": img_url,
            "object_story_spec": {
                "page_id": "fake_page_999",
                "link_data": {
                    "name": text["headline"],
                    "description": text["description"],
                    "message": text["primary_text"],
                    "link": final_url,
                    "call_to_action": {
                        "type": text["cta_type"],
                        "value": {"link": final_url},
                    },
                },
            },
        },
    }

    fixtures.inject_meta_ad(fs_dir, ad)

    print(f"""
Meta ad written to fixtures:
  ID:           {ad_id}
  Name:         {ad_name}
  Campaign:     {campaign_id}
  Adset:        {adset_id}
  Status:       {status}
  Headline:     {text['headline']}
  Body:         {text['primary_text']}
  Description:  {text['description']}
  CTA:          {text['cta_type']}
  Image:        {img_url}

Visible at (fake server running on :9000):
  GET /meta/v19.0/act_<your_act_id>/ads?filtering=[{{"field":"campaign.id","operator":"EQUAL","value":"{campaign_id}"}}]
""")


# ── Google ────────────────────────────────────────────────────────────────────

def _create_google_ad(config: dict) -> None:
    concept = config["concept"]
    final_url = config.get("final_url", "https://example.com")
    fs_dir = _resolve_fs_dir(config)
    customer_id = config.get("customer_id", "1234567890")

    campaign_id = fixtures.get_or_create_google_campaign(
        fs_dir,
        campaign_id=config.get("campaign_id"),
        campaign_name=config.get("campaign_name", f"{concept[:50]} Campaign"),
    )

    adgroup_id = fixtures.get_or_create_google_adgroup(
        fs_dir,
        campaign_id=campaign_id,
        adgroup_id=config.get("adset_id") or config.get("ad_group_id"),
        adgroup_name=config.get("adset_name") or config.get("ad_group_name", "AI Factory Ad Group"),
    )

    print("Generating Google RSA copy …")
    text = text_gen.generate_google_rsa_text(concept, final_url)

    headlines = text["headlines"]
    descriptions = text["descriptions"]
    ad_numeric_id = str(uuid.uuid4().int % 10 ** 9)
    resource_name = f"customers/{customer_id}/adGroupAds/{ad_numeric_id}"
    ad_group_rn = f"customers/{customer_id}/adGroups/{adgroup_id}"
    ad_name = config.get("ad_name", f"{concept[:50]} — AI RSA")
    status = config.get("status", "PAUSED")

    ad_row = {
        "adGroupAd": {
            "resourceName": resource_name,
            "ad": {
                "resourceName": resource_name,
                "id": ad_numeric_id,
                "name": ad_name,
                "type": "RESPONSIVE_SEARCH_AD",
                "finalUrls": [final_url],
                "responsiveSearchAd": {
                    "headlines": [{"text": h} for h in headlines],
                    "descriptions": [{"text": d} for d in descriptions],
                },
            },
            "status": status,
        },
        "adGroup": {"id": adgroup_id, "resourceName": ad_group_rn},
        "campaign": {"id": campaign_id},
    }

    fixtures.inject_google_ad(fs_dir, ad_row)

    hl_preview = "\n".join(f"    {i+1}. {h}" for i, h in enumerate(headlines[:5]))
    if len(headlines) > 5:
        hl_preview += f"\n    … and {len(headlines) - 5} more"
    desc_preview = "\n".join(f"    - {d}" for d in descriptions)

    print(f"""
Google RSA written to fixtures:
  Resource:     {resource_name}
  Name:         {ad_name}
  Campaign:     {campaign_id}
  Ad Group:     {adgroup_id}
  Status:       {status}
  Headlines ({len(headlines)}):
{hl_preview}
  Descriptions:
{desc_preview}

Visible via fake server GAQL:
  SELECT ad_group_ad.ad.id FROM ad_group_ad
  WHERE campaign.id = {campaign_id}
""")


# ── main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("Usage: python -m ad_factory <config.json>")
        print("       python ad_factory/create_ad.py <config.json>")
        sys.exit(1)

    config_path = Path(args[0])
    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        sys.exit(1)

    config = json.loads(config_path.read_text())
    platform = config.get("platform", "").lower()

    if not platform:
        print('Config must include "platform": "meta" or "google"')
        sys.exit(1)

    if not config.get("concept"):
        print('Config must include "concept": "<ad description>"')
        sys.exit(1)

    print(f"ad_factory: platform={platform}  concept={config['concept']!r}")

    if platform == "meta":
        _create_meta_ad(config)
    elif platform == "google":
        _create_google_ad(config)
    else:
        print(f'Unknown platform: {platform!r}. Use "meta" or "google".')
        sys.exit(1)


if __name__ == "__main__":
    main()

# providers/meta_live.py
import json
import logging
import os
from typing import Any, Dict, List, Tuple

import httpx
from fastapi import HTTPException

from .meta_provider import PlatformProvider

_META_API_VERSION = os.getenv("META_API_VERSION", "v19.0")
META_GRAPH = f"https://graph.facebook.com/{_META_API_VERSION}"

logger = logging.getLogger(__name__)


class LiveMetaProvider(PlatformProvider):
    @property
    def platform_name(self) -> str:
        return "meta"

    def normalize_creative(self, ad: dict) -> tuple[str, list[dict]]:
        creative = ad.get("creative") or {}
        asset_feed = creative.get("asset_feed_spec")

        if asset_feed:
            components: list[dict] = []

            for i, item in enumerate(asset_feed.get("titles", [])):
                components.append({"slot": "headline", "slot_index": i, "value": item.get("text")})

            for i, item in enumerate(asset_feed.get("descriptions", [])):
                components.append({"slot": "description", "slot_index": i, "value": item.get("text")})

            for i, item in enumerate(asset_feed.get("bodies", [])):
                components.append({"slot": "primary_text", "slot_index": i, "value": item.get("text")})

            for i, item in enumerate(asset_feed.get("images", [])):
                value = item.get("url") or item.get("hash")
                components.append({"slot": "image", "slot_index": i, "value": value})

            has_url_image = any(
                c["slot"] == "image" and c["value"] and c["value"].startswith("http")
                for c in components
            )
            if not has_url_image:
                thumbnail = creative.get("thumbnail_url") or creative.get("image_url")
                if thumbnail:
                    components.append({"slot": "image", "slot_index": 9999, "value": thumbnail})

            return "dynamic", components

        components = []
        link_data = (creative.get("object_story_spec") or {}).get("link_data") or {}

        headline = creative.get("title") or link_data.get("name")
        if headline:
            components.append({"slot": "headline", "slot_index": 0, "value": headline})

        description = link_data.get("description")
        if description:
            components.append({"slot": "description", "slot_index": 0, "value": description})

        primary_text = creative.get("body") or link_data.get("message")
        if primary_text:
            components.append({"slot": "primary_text", "slot_index": 0, "value": primary_text})

        image = creative.get("image_url") or creative.get("thumbnail_url")
        if image:
            components.append({"slot": "image", "slot_index": 0, "value": image})

        return "static", components

    async def fetch_campaigns_and_insights(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], int]:
        camp_resp = await client.get(
            f"{META_GRAPH}/{ad_account_id}/campaigns",
            params={
                "access_token": access_token,
                "fields": "id,name,status,daily_budget",
                "effective_status": '["ACTIVE","PAUSED","ARCHIVED","WITH_ISSUES"]',
                "limit": 100,
            },
        )
        if camp_resp.status_code != 200:
            raise HTTPException(502, f"Meta API error: {camp_resp.text}")
        campaigns_raw = camp_resp.json().get("data", [])

        metrics_by_campaign: Dict[str, Dict[str, Any]] = {}
        insights_error_count = 0

        try:
            insights_resp = await client.get(
                f"{META_GRAPH}/{ad_account_id}/insights",
                params={
                    "access_token": access_token,
                    "level": "campaign",
                    "date_preset": "last_7d",
                    "fields": "campaign_id,impressions,clicks,spend,ctr,cpm,cpc",
                    "limit": 5000,
                },
            )
            if insights_resp.status_code == 200:
                for row in insights_resp.json().get("data", []):
                    cid = row.get("campaign_id")
                    if not cid:
                        continue
                    m = metrics_by_campaign.setdefault(
                        cid, {"impressions": 0, "clicks": 0, "spend": 0.0}
                    )
                    m["impressions"] += int(row.get("impressions", 0))
                    m["clicks"] += int(row.get("clicks", 0))
                    m["spend"] += float(row.get("spend", 0.0))
            else:
                insights_error_count += 1
                logger.warning(
                    "Insights fetch returned non-200 for account %s: status=%d body=%s",
                    ad_account_id,
                    insights_resp.status_code,
                    insights_resp.text[:500],
                )
        except httpx.HTTPError as exc:
            insights_error_count += 1
            logger.warning(
                "Insights fetch raised HTTP error for account %s: %s",
                ad_account_id,
                exc,
            )

        for m in metrics_by_campaign.values():
            imp = m["impressions"] or 0
            clk = m["clicks"] or 0
            spend = m["spend"] or 0.0
            m["ctr"] = (clk / imp * 100.0) if imp > 0 else None
            m["cpm"] = (spend / imp * 1000.0) if imp > 0 else None
            m["cpc"] = (spend / clk) if clk > 0 else None

        return campaigns_raw, metrics_by_campaign, insights_error_count

    async def fetch_ads(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> List[Dict[str, Any]]:
        resp = await client.get(
            f"{META_GRAPH}/{ad_account_id}/ads",
            params={
                "access_token": access_token,
                "fields": "id,name,status,campaign_id,adset_id,creative{body,image_url,thumbnail_url}",
                "limit": 200,
            },
        )
        if resp.status_code != 200:
            raise HTTPException(502, f"Failed to fetch ads: {resp.text}")
        return resp.json().get("data", [])

    async def fetch_campaign_structure(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
        campaign_id: str,
    ):
        from fastapi import HTTPException
        adsets_resp = await client.get(
            f"{META_GRAPH}/{ad_account_id}/adsets",
            params={
                "access_token": access_token,
                "fields": "id,name,status,campaign_id",
                "filtering": f'[{{"field":"campaign.id","operator":"EQUAL","value":"{campaign_id}"}}]',
                "limit": 500,
            },
        )
        if adsets_resp.status_code != 200:
            raise HTTPException(502, f"Failed to fetch adsets: {adsets_resp.text}")
        adsets = adsets_resp.json().get("data", [])

        ads_resp = await client.get(
            f"{META_GRAPH}/{ad_account_id}/ads",
            params={
                "access_token": access_token,
                "fields": (
                    "id,name,status,effective_status,campaign_id,adset_id,"
                    "creative{"
                    "id,name,body,title,image_url,thumbnail_url,"
                    "asset_feed_spec,object_story_spec"
                    "}"
                ),
                "filtering": f'[{{"field":"campaign.id","operator":"EQUAL","value":"{campaign_id}"}}]',
                "limit": 500,
            },
        )
        if ads_resp.status_code != 200:
            raise HTTPException(502, f"Failed to fetch ads: {ads_resp.text}")
        ads = ads_resp.json().get("data", [])

        # Resolve image hashes → URLs via adimages endpoint
        hashes = []
        for ad in ads:
            feed = (ad.get("creative") or {}).get("asset_feed_spec") or {}
            for img in feed.get("images", []):
                h = img.get("hash")
                if h and not img.get("url"):
                    hashes.append(h)

        hash_to_url: dict[str, str] = {}
        if hashes:
            img_resp = await client.get(
                f"{META_GRAPH}/{ad_account_id}/adimages",
                params={
                    "access_token": access_token,
                    "hashes": json.dumps(hashes),
                    "fields": "hash,url",
                },
            )
            if img_resp.status_code == 200:
                for row in img_resp.json().get("data", []):
                    if row.get("hash") and row.get("url"):
                        hash_to_url[row["hash"]] = row["url"]

        # Patch image URLs back into each ad's asset_feed_spec
        for ad in ads:
            feed = (ad.get("creative") or {}).get("asset_feed_spec") or {}
            for img in feed.get("images", []):
                h = img.get("hash")
                if h and h in hash_to_url:
                    img["url"] = hash_to_url[h]

        return adsets, ads

    async def pause_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        resp = await client.post(
            f"{META_GRAPH}/{campaign_id}",
            params={"access_token": access_token},
            data={"status": "PAUSED"},
        )
        if resp.status_code != 200:
            raise HTTPException(502, f"Failed to pause campaign: {resp.text}")

    async def resume_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        resp = await client.post(
            f"{META_GRAPH}/{campaign_id}",
            params={"access_token": access_token},
            data={"status": "ACTIVE"},
        )
        if resp.status_code != 200:
            raise HTTPException(502, f"Failed to resume campaign: {resp.text}")

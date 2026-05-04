# providers/meta_demo.py
from typing import Any, Dict, List, Tuple
from datetime import datetime, timedelta

import httpx

from .meta_provider import PlatformProvider

class DemoMetaProvider(PlatformProvider):
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
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        # Completely synthetic or seeded from a small fixture
        campaigns_raw: List[Dict[str, Any]] = [
            {
                "id": "demo_camp_1",
                "name": "Demo Prospecting Campaign",
                "status": "ACTIVE",
                "daily_budget": 5000,
            },
            {
                "id": "demo_camp_2",
                "name": "Demo Retargeting Campaign",
                "status": "ACTIVE",
                "daily_budget": 3000,
            },
        ]
        metrics_by_campaign: Dict[str, Dict[str, Any]] = {
            "demo_camp_1": {
                "impressions": 120_000,
                "clicks": 2_400,
                "spend": 850.0,
            },
            "demo_camp_2": {
                "impressions": 45_000,
                "clicks": 900,
                "spend": 320.0,
            },
        }
        # compute ctr/cpm/cpc same way as in live helper
        for m in metrics_by_campaign.values():
            imp = m["impressions"] or 0
            clk = m["clicks"] or 0
            spend = m["spend"] or 0.0
            m["ctr"] = (clk / imp * 100.0) if imp > 0 else None
            m["cpm"] = (spend / imp * 1000.0) if imp > 0 else None
            m["cpc"] = (spend / clk) if clk > 0 else None
        return campaigns_raw, metrics_by_campaign, 0

    async def fetch_ads(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> List[Dict[str, Any]]:
        # Return ads linked to the demo campaigns above
        return [
            {
                "id": "demo_ad_1",
                "name": "Demo Ad 1",
                "status": "ACTIVE",
                "campaign_id": "demo_camp_1",
                "adset_id": "demo_adset_1",
                "creative": {
                    "body": "Try AdStac.kr demo now.",
                    "image_url": "https://via.placeholder.com/600x315?text=Demo+Ad",
                    "thumbnail_url": "https://via.placeholder.com/120x120?text=Demo",
                },
            },
            # add a couple more
        ]

    async def fetch_campaign_structure(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
        campaign_id: str,
    ):
        demo_structure = {
            "demo_camp_1": (
                [{"id": "demo_adset_1", "name": "Demo Adset 1", "status": "ACTIVE", "campaign_id": "demo_camp_1"}],
                [
                    {
                        "id": "demo_ad_1",
                        "name": "Demo Ad 1",
                        "status": "ACTIVE",
                        "effective_status": "ACTIVE",
                        "campaign_id": "demo_camp_1",
                        "adset_id": "demo_adset_1",
                        "creative": {
                            "title": "Try AdStac.kr",
                            "body": "Try AdStac.kr demo now.",
                            "image_url": "https://via.placeholder.com/600x315?text=Demo+Ad",
                            "thumbnail_url": "https://via.placeholder.com/120x120?text=Demo",
                        },
                    }
                ],
            ),
            "demo_camp_2": (
                [{"id": "demo_adset_2", "name": "Demo Adset 2", "status": "ACTIVE", "campaign_id": "demo_camp_2"}],
                [
                    {
                        "id": "demo_ad_2",
                        "name": "Demo Retargeting Ad",
                        "status": "ACTIVE",
                        "effective_status": "ACTIVE",
                        "campaign_id": "demo_camp_2",
                        "adset_id": "demo_adset_2",
                        "creative": {
                            "title": "Come Back",
                            "body": "You left something behind — finish what you started.",
                            "image_url": "https://via.placeholder.com/600x315?text=Retargeting+Ad",
                            "thumbnail_url": "https://via.placeholder.com/120x120?text=RT",
                        },
                    }
                ],
            ),
        }
        return demo_structure.get(campaign_id, ([], []))

    async def pause_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        # No-op in demo mode: pretend it succeeded
        return

    async def resume_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        # No-op in demo mode
        return


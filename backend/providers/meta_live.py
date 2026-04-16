# providers/meta_live.py
import logging
import os
from typing import Any, Dict, List, Tuple

import httpx
from fastapi import HTTPException

from .meta_provider import MetaProvider

_META_API_VERSION = os.getenv("META_API_VERSION", "v19.0")
META_GRAPH = f"https://graph.facebook.com/{_META_API_VERSION}"

logger = logging.getLogger(__name__)


class LiveMetaProvider(MetaProvider):
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

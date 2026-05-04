# providers/google_provider.py
import asyncio
import logging
import os
from typing import Any, Dict, List, Tuple

import httpx
from fastapi import HTTPException

import google_ads_api
from .meta_provider import PlatformProvider

logger = logging.getLogger(__name__)

_CAMPAIGNS_GAQL = """
SELECT campaign.id, campaign.name, campaign.status,
       campaign_budget.amount_micros,
       metrics.impressions, metrics.clicks, metrics.cost_micros,
       metrics.ctr, metrics.average_cpm, metrics.average_cpc
FROM campaign
WHERE campaign.status IN ('ENABLED', 'PAUSED')
  AND segments.date DURING LAST_7_DAYS
"""

_STATUS_MAP = {"ENABLED": "ACTIVE", "PAUSED": "PAUSED", "REMOVED": "REMOVED"}

_AD_GROUPS_GAQL = """
SELECT ad_group.id, ad_group.name, ad_group.status, campaign.id
FROM ad_group
WHERE campaign.id = {campaign_id}
"""

_PMAX_ASSETS_GAQL = """
SELECT
  asset_group.id,
  asset_group.name,
  asset_group.status,
  asset_group_asset.field_type,
  asset.resource_name,
  asset.text_asset.text
FROM asset_group_asset
WHERE campaign.id = {campaign_id}
  AND asset_group_asset.status != 'REMOVED'
"""

_ADS_GAQL = """
SELECT
  ad_group_ad.ad.id,
  ad_group_ad.ad.name,
  ad_group_ad.status,
  ad_group_ad.ad.type,
  ad_group_ad.ad.final_urls,
  ad_group_ad.ad.responsive_search_ad.headlines,
  ad_group_ad.ad.responsive_search_ad.descriptions,
  ad_group_ad.ad.responsive_display_ad.headlines,
  ad_group_ad.ad.responsive_display_ad.descriptions,
  ad_group_ad.ad.responsive_display_ad.marketing_images,
  ad_group_ad.ad.video_responsive_ad.headlines,
  ad_group_ad.ad.video_responsive_ad.descriptions,
  ad_group_ad.ad.video_responsive_ad.videos,
  ad_group.id
FROM ad_group_ad
WHERE campaign.id = {campaign_id}
  AND ad_group_ad.status != 'REMOVED'
"""


def _int(val) -> int:
    try:
        return int(float(str(val)))
    except (TypeError, ValueError):
        return 0


def _float(val) -> float:
    try:
        return float(str(val))
    except (TypeError, ValueError):
        return 0.0


class GooglePlatformProvider(PlatformProvider):
    @property
    def platform_name(self) -> str:
        return "google"

    async def fetch_campaigns_and_insights(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,  # customer_id for Google
        login_customer_id: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], int]:
        api_version = os.getenv("GOOGLE_ADS_API_VERSION", "")
        developer_token = os.getenv("GOOGLE_DEVELOPER_TOKEN", "")

        try:
            rows = await google_ads_api.query_gaql(
                customer_id=ad_account_id,
                access_token=access_token,
                developer_token=developer_token,
                api_version=api_version,
                gaql=_CAMPAIGNS_GAQL,
                client=client,
                login_customer_id=login_customer_id,
            )
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))

        campaigns_raw: List[Dict[str, Any]] = []
        metrics_by_campaign: Dict[str, Dict[str, Any]] = {}
        seen: set = set()

        for row in rows:
            camp = row.get("campaign") or {}
            budget = row.get("campaignBudget") or {}
            metrics = row.get("metrics") or {}

            cid = str(camp.get("id", ""))
            if not cid:
                continue

            if cid not in seen:
                seen.add(cid)
                status_raw = camp.get("status", "")
                amount_micros = _int(budget.get("amountMicros", 0))
                campaigns_raw.append({
                    "id": cid,
                    "name": camp.get("name", ""),
                    "status": _STATUS_MAP.get(status_raw, status_raw),
                    "daily_budget": int(amount_micros // 10_000) if amount_micros > 0 else None,
                })

            m = metrics_by_campaign.setdefault(cid, {"impressions": 0, "clicks": 0, "spend": 0.0})
            m["impressions"] += _int(metrics.get("impressions", 0))
            m["clicks"] += _int(metrics.get("clicks", 0))
            # cost_micros → dollars: 1 micro = 0.000001 currency units  ÷ 1_000_000
            m["spend"] += _float(metrics.get("costMicros", 0)) / 1_000_000

        # Normalization contract (three distinct rules):
        #
        #   amount_micros  → integer cents  (daily_budget)  ÷ 10_000
        #     1 cent = 10_000 micros  →  e.g. 5_000_000 micros = 500 cents = $5.00
        #     Matches Meta's integer-cents budget field. Different divisor from spend.
        #
        #   cost_micros    → dollars        (spend)         ÷ 1_000_000
        #     1 dollar = 1_000_000 micros  →  e.g. 2_500_000 micros = $2.50
        #     cpm and cpc are then derived from aggregated spend/impressions/clicks.
        #
        #   API ctr        → percentage     (ctr_7d)        × 100
        #     Google returns ctr as a decimal ratio (0.05 = 5 %).
        #     We recompute from aggregated totals (clk / imp * 100.0) rather than
        #     using the API's ctr field directly, so the formula is explicit here.
        for m in metrics_by_campaign.values():
            imp = m["impressions"]
            clk = m["clicks"]
            spend = m["spend"]
            m["ctr"] = (clk / imp * 100.0) if imp > 0 else None   # decimal ratio → percent
            m["cpm"] = (spend / imp * 1000.0) if imp > 0 else None  # dollars per 1000 impressions
            m["cpc"] = (spend / clk) if clk > 0 else None           # dollars per click

        return campaigns_raw, metrics_by_campaign, 0

    def normalize_creative(self, ad: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
        ad_type = ad.get("ad_type", "")
        components: List[Dict[str, Any]] = []

        def _texts(items: list, key: str = "text") -> list[str]:
            out = []
            for item in (items or []):
                text = item.get(key, "") if isinstance(item, dict) else str(item)
                if text:
                    out.append(text)
            return out

        if ad_type == "RESPONSIVE_SEARCH_AD":
            rsa = ad.get("responsive_search_ad") or {}
            for i, t in enumerate(_texts(rsa.get("headlines", []))):
                components.append({"slot": "headline", "slot_index": i, "value": t})
            for i, t in enumerate(_texts(rsa.get("descriptions", []))):
                components.append({"slot": "description", "slot_index": i, "value": t})
            final_urls = ad.get("final_urls") or []
            if final_urls:
                components.append({"slot": "final_url", "slot_index": 0, "value": final_urls[0]})
            return "rsa", components

        if ad_type == "RESPONSIVE_DISPLAY_AD":
            rda = ad.get("responsive_display_ad") or {}
            for i, t in enumerate(_texts(rda.get("headlines", []))):
                components.append({"slot": "headline", "slot_index": i, "value": t})
            for i, t in enumerate(_texts(rda.get("descriptions", []))):
                components.append({"slot": "description", "slot_index": i, "value": t})
            for i, img in enumerate(rda.get("marketing_images", []) or []):
                value = img.get("asset", "") if isinstance(img, dict) else str(img)
                if value:
                    components.append({"slot": "image", "slot_index": i, "value": value})
            return "display", components

        if ad_type == "VIDEO_RESPONSIVE_AD":
            vra = ad.get("video_responsive_ad") or {}
            for i, t in enumerate(_texts(vra.get("headlines", []))):
                components.append({"slot": "headline", "slot_index": i, "value": t})
            for i, t in enumerate(_texts(vra.get("descriptions", []))):
                components.append({"slot": "description", "slot_index": i, "value": t})
            for i, v in enumerate(vra.get("videos", []) or []):
                value = v.get("asset", "") if isinstance(v, dict) else str(v)
                if value:
                    components.append({"slot": "video", "slot_index": i, "value": value})
            return "video", components

        if ad_type == "PERFORMANCE_MAX_AD":
            _HEADLINE_FIELDS = frozenset({"HEADLINE", "LONG_HEADLINE"})
            _DESC_FIELDS = frozenset({"DESCRIPTION"})
            _IMAGE_FIELDS = frozenset({
                "MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "PORTRAIT_MARKETING_IMAGE",
                "LOGO", "LANDSCAPE_LOGO",
            })
            _VIDEO_FIELDS = frozenset({"YOUTUBE_VIDEO"})
            headline_i = description_i = image_i = video_i = 0
            for asset in (ad.get("pmax_assets") or []):
                ft = asset.get("field_type", "").upper()
                text = asset.get("text", "") or ""
                resource = asset.get("resource_name", "") or ""
                value = text or resource
                if not value:
                    continue
                if ft in _HEADLINE_FIELDS:
                    components.append({"slot": "headline", "slot_index": headline_i, "value": value})
                    headline_i += 1
                elif ft in _DESC_FIELDS:
                    components.append({"slot": "description", "slot_index": description_i, "value": value})
                    description_i += 1
                elif ft in _IMAGE_FIELDS:
                    components.append({"slot": "image", "slot_index": image_i, "value": resource or value})
                    image_i += 1
                elif ft in _VIDEO_FIELDS:
                    components.append({"slot": "video", "slot_index": video_i, "value": resource or value})
                    video_i += 1
            return "pmax", components

        if ad_type == "SHOPPING_PRODUCT_AD":
            final_urls = ad.get("final_urls") or []
            if final_urls:
                components.append({"slot": "final_url", "slot_index": 0, "value": final_urls[0]})
            return "shopping", components

        return "unknown", []

    async def fetch_campaign_structure(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
        campaign_id: str,
        login_customer_id: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        api_version = os.getenv("GOOGLE_ADS_API_VERSION", "")
        developer_token = os.getenv("GOOGLE_DEVELOPER_TOKEN", "")

        common = dict(
            customer_id=ad_account_id,
            access_token=access_token,
            developer_token=developer_token,
            api_version=api_version,
            client=client,
            login_customer_id=login_customer_id,
        )

        try:
            adgroup_rows, ad_rows = await asyncio.gather(
                google_ads_api.query_gaql(
                    **common,
                    gaql=_AD_GROUPS_GAQL.format(campaign_id=campaign_id),
                ),
                google_ads_api.query_gaql(
                    **common,
                    gaql=_ADS_GAQL.format(campaign_id=campaign_id),
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))

        # pMax campaigns have no ad_group_ad rows; query asset_group_asset instead.
        # Runs separately so a failure here (e.g. non-pMax campaign) doesn't block
        # the main ingest.
        try:
            pmax_rows = await google_ads_api.query_gaql(
                **common,
                gaql=_PMAX_ASSETS_GAQL.format(campaign_id=campaign_id),
            )
        except Exception:
            pmax_rows = []

        adsets: List[Dict[str, Any]] = []
        for row in adgroup_rows:
            ag = row.get("adGroup") or {}
            ag_id = str(ag.get("id", ""))
            if ag_id:
                adsets.append({
                    "id": ag_id,
                    "name": ag.get("name", ""),
                    "status": ag.get("status", ""),
                    "campaign_id": str((row.get("campaign") or {}).get("id", "")),
                })

        ads: List[Dict[str, Any]] = []
        for row in ad_rows:
            aga = row.get("adGroupAd") or {}
            ad_data = aga.get("ad") or {}
            ag = row.get("adGroup") or {}

            ad_id = str(ad_data.get("id", ""))
            if not ad_id:
                continue

            ad_type = ad_data.get("type", "")
            normalized: Dict[str, Any] = {
                "id": ad_id,
                "name": ad_data.get("name", ""),
                "status": aga.get("status", ""),
                "effective_status": aga.get("status", ""),
                "adset_id": str(ag.get("id", "")),
                "ad_type": ad_type,
                "final_urls": ad_data.get("finalUrls", []),
            }

            if ad_type == "RESPONSIVE_SEARCH_AD":
                rsa = ad_data.get("responsiveSearchAd") or {}
                normalized["responsive_search_ad"] = {
                    "headlines": rsa.get("headlines", []),
                    "descriptions": rsa.get("descriptions", []),
                }
            elif ad_type == "RESPONSIVE_DISPLAY_AD":
                rda = ad_data.get("responsiveDisplayAd") or {}
                normalized["responsive_display_ad"] = {
                    "headlines": rda.get("headlines", []),
                    "descriptions": rda.get("descriptions", []),
                    "marketing_images": rda.get("marketingImages", []),
                }
            elif ad_type == "VIDEO_RESPONSIVE_AD":
                vra = ad_data.get("videoResponsiveAd") or {}
                normalized["video_responsive_ad"] = {
                    "headlines": vra.get("headlines", []),
                    "descriptions": vra.get("descriptions", []),
                    "videos": vra.get("videos", []),
                }

            ads.append(normalized)

        # Build virtual ads from pMax asset groups (one "ad" per asset group)
        _pmax_groups: Dict[str, Dict[str, Any]] = {}
        for row in pmax_rows:
            ag_data = row.get("assetGroup") or {}
            ag_id = str(ag_data.get("id", ""))
            if not ag_id:
                continue
            if ag_id not in _pmax_groups:
                _pmax_groups[ag_id] = {
                    "id": f"pmax_{ag_id}",
                    "name": ag_data.get("name", ""),
                    "status": ag_data.get("status", ""),
                    "effective_status": ag_data.get("status", ""),
                    "adset_id": ag_id,
                    "ad_type": "PERFORMANCE_MAX_AD",
                    "pmax_assets": [],
                }
            aga_data = row.get("assetGroupAsset") or {}
            asset_data = row.get("asset") or {}
            field_type = aga_data.get("fieldType", "")
            resource_name = asset_data.get("resourceName", "")
            text = (asset_data.get("textAsset") or {}).get("text", "")
            if field_type:
                _pmax_groups[ag_id]["pmax_assets"].append({
                    "field_type": field_type,
                    "text": text or "",
                    "resource_name": resource_name or "",
                })
        ads.extend(_pmax_groups.values())

        return adsets, ads

    @staticmethod
    def youtube_thumbnail_url(video_id: str) -> str | None:
        """
        Return the hqdefault thumbnail URL for a YouTube video ID.
        Requires the actual video ID (e.g. 'dQw4w9WgXcQ'), not an asset resource name.
        Returns None if video_id is empty.
        """
        vid = (video_id or "").strip()
        if not vid:
            return None
        return f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"

    async def fetch_ads(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError("fetch_ads not yet implemented for Google (Chunk 5)")

    async def pause_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        raise NotImplementedError("pause_campaign not yet implemented for Google")

    async def resume_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        raise NotImplementedError("resume_campaign not yet implemented for Google")

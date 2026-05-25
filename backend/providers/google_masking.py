# providers/google_masking.py
import hashlib
import logging
from typing import Any, Dict, List, Tuple

import httpx

from .google_mask_policy import GoogleMaskPolicy
from .meta_provider import PlatformProvider

logger = logging.getLogger(__name__)

_BUDGET_SEEDS = [5000, 7500, 10000, 12500, 15000, 8000, 6000, 20000]

_PROFILES: Dict[str, Dict[str, Any]] = {
    "healthy": {
        "imp_base": 80_000, "imp_range": 70_000,
        "ctr_base": 1.8,   "ctr_range": 0.7,
        "cpc_base": 0.40,  "cpc_range": 0.30,
    },
    "stable": {
        "imp_base": 30_000, "imp_range": 30_000,
        "ctr_base": 1.2,   "ctr_range": 0.6,
        "cpc_base": 0.70,  "cpc_range": 0.50,
    },
    "weak": {
        "imp_base": 5_000,  "imp_range": 15_000,
        "ctr_base": 0.5,   "ctr_range": 0.5,
        "cpc_base": 1.50,  "cpc_range": 1.50,
    },
}


def _campaign_seed(campaign_id: str) -> float:
    digest = hashlib.md5(campaign_id.encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFF_FFFF


def _synthetic_metrics(campaign_id: str, profile: str) -> Dict[str, Any]:
    p = _PROFILES.get(profile, _PROFILES["healthy"])
    digest = hashlib.md5(campaign_id.encode()).digest()
    s_imp = int.from_bytes(digest[0:4], "big") / 0xFFFF_FFFF
    s_ctr = int.from_bytes(digest[4:8], "big") / 0xFFFF_FFFF
    s_cpc = int.from_bytes(digest[8:12], "big") / 0xFFFF_FFFF

    impressions = int(p["imp_base"] + s_imp * p["imp_range"])
    ctr = p["ctr_base"] + s_ctr * p["ctr_range"]
    cpc = p["cpc_base"] + s_cpc * p["cpc_range"]
    clicks = max(1, int(impressions * ctr / 100.0))
    spend = round(clicks * cpc, 2)

    return {
        "impressions": impressions,
        "clicks": clicks,
        "spend": spend,
        "ctr": round(clicks / impressions * 100.0, 4),
        "cpm": round(spend / impressions * 1000.0, 4),
        "cpc": round(spend / clicks, 4),
    }


def _synthetic_budget(campaign_id: str) -> int:
    s = _campaign_seed(campaign_id)
    idx = int(s * len(_BUDGET_SEEDS)) % len(_BUDGET_SEEDS)
    return _BUDGET_SEEDS[idx]


class GoogleMaskingProvider(PlatformProvider):
    """
    Wraps a live Google provider and selectively overrides fields per GoogleMaskPolicy.
    Real API calls are made first; masks are applied on the way out.
    """

    def __init__(self, live_provider: PlatformProvider, policy: GoogleMaskPolicy) -> None:
        self._live = live_provider
        self._policy = policy

    @property
    def platform_name(self) -> str:
        return self._live.platform_name

    def normalize_creative(self, ad: Dict[str, Any]):
        return self._live.normalize_creative(ad)

    async def fetch_campaigns_and_insights(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
        login_customer_id: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], int]:
        campaigns_raw, metrics_by_campaign, insights_error_count = (
            await self._live.fetch_campaigns_and_insights(
                client, access_token, ad_account_id, login_customer_id=login_customer_id
            )
        )

        p = self._policy
        masks_applied: List[str] = []

        for camp in campaigns_raw:
            cid = camp.get("id")
            if not cid:
                continue

            if p.mask_status:
                camp["status"] = "ACTIVE"

            if p.mask_budgets:
                camp["daily_budget"] = _synthetic_budget(cid)

            if p.mask_metrics:
                real = metrics_by_campaign.get(cid)
                if not real or real.get("impressions", 0) < 100:
                    metrics_by_campaign[cid] = _synthetic_metrics(cid, p.metric_profile)

        if p.mask_status:
            masks_applied.append("status")
        if p.mask_budgets:
            masks_applied.append("budgets")
        if p.mask_metrics:
            masks_applied.append(f"metrics({p.metric_profile})")

        if masks_applied:
            logger.info(
                "google masking applied to %d campaign(s) [%s]",
                len(campaigns_raw),
                ", ".join(masks_applied),
            )

        return campaigns_raw, metrics_by_campaign, insights_error_count

    async def fetch_campaign_structure(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
        campaign_id: str,
        login_customer_id: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        return await self._live.fetch_campaign_structure(
            client, access_token, ad_account_id, campaign_id,
            login_customer_id=login_customer_id,
        )

    async def fetch_ads(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> List[Dict[str, Any]]:
        return await self._live.fetch_ads(client, access_token, ad_account_id)

    async def pause_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        if self._policy.mask_pause_resume:
            logger.info("google masking: pause_campaign no-op for campaign_id=%s", campaign_id)
            return
        await self._live.pause_campaign(client, access_token, campaign_id)

    async def resume_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        if self._policy.mask_pause_resume:
            logger.info("google masking: resume_campaign no-op for campaign_id=%s", campaign_id)
            return
        await self._live.resume_campaign(client, access_token, campaign_id)

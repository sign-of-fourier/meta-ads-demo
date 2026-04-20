# providers/meta_masking.py
import hashlib
import logging
from typing import Any, Dict, List, Tuple

import httpx

from .mask_policy import MaskPolicy
from .meta_provider import MetaProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Budget seeds (cents) — realistic demo daily budgets by slot
# ---------------------------------------------------------------------------
_BUDGET_SEEDS = [5000, 7500, 10000, 12500, 15000, 8000, 6000, 20000]

# ---------------------------------------------------------------------------
# Metric profile definitions
# Each profile is (impressions_base, impressions_range, ctr_pct, cpc_dollars)
# where the actual value = base + (seed_fraction * range).
# All values are "per 7-day window" totals.
# ---------------------------------------------------------------------------
_PROFILES: Dict[str, Dict[str, Any]] = {
    "healthy": {
        "imp_base": 80_000,
        "imp_range": 70_000,
        "ctr_base": 1.8,
        "ctr_range": 0.7,   # ctr in percent
        "cpc_base": 0.40,
        "cpc_range": 0.30,  # cpc in dollars
    },
    "stable": {
        "imp_base": 30_000,
        "imp_range": 30_000,
        "ctr_base": 1.2,
        "ctr_range": 0.6,
        "cpc_base": 0.70,
        "cpc_range": 0.50,
    },
    "weak": {
        "imp_base": 5_000,
        "imp_range": 15_000,
        "ctr_base": 0.5,
        "ctr_range": 0.5,
        "cpc_base": 1.50,
        "cpc_range": 1.50,
    },
}


def _campaign_seed(campaign_id: str) -> float:
    """Return a stable float in [0, 1) derived deterministically from campaign_id."""
    digest = hashlib.md5(campaign_id.encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFF_FFFF


def _synthetic_metrics(campaign_id: str, profile: str) -> Dict[str, Any]:
    """Generate internally-consistent synthetic metrics for a campaign."""
    p = _PROFILES.get(profile, _PROFILES["healthy"])
    s = _campaign_seed(campaign_id)

    # Use different byte slices for independent variance on each dimension
    digest = hashlib.md5(campaign_id.encode()).digest()
    s_imp = int.from_bytes(digest[0:4], "big") / 0xFFFF_FFFF
    s_ctr = int.from_bytes(digest[4:8], "big") / 0xFFFF_FFFF
    s_cpc = int.from_bytes(digest[8:12], "big") / 0xFFFF_FFFF

    impressions = int(p["imp_base"] + s_imp * p["imp_range"])
    ctr = p["ctr_base"] + s_ctr * p["ctr_range"]           # percent
    cpc = p["cpc_base"] + s_cpc * p["cpc_range"]           # dollars

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
    """Return a plausible daily budget (cents) seeded from campaign_id."""
    s = _campaign_seed(campaign_id)
    idx = int(s * len(_BUDGET_SEEDS)) % len(_BUDGET_SEEDS)
    return _BUDGET_SEEDS[idx]


class MaskingMetaProvider(MetaProvider):
    """
    Wraps a live provider and selectively overrides fields according to MaskPolicy.
    Real API calls are made first; masks are applied on the way out.
    """

    def __init__(self, live_provider: MetaProvider, policy: MaskPolicy) -> None:
        self._live = live_provider
        self._policy = policy

    # ------------------------------------------------------------------
    # fetch_campaigns_and_insights
    # ------------------------------------------------------------------
    async def fetch_campaigns_and_insights(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], int]:
        campaigns_raw, metrics_by_campaign, insights_error_count = (
            await self._live.fetch_campaigns_and_insights(client, access_token, ad_account_id)
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
                "masking applied to %d campaign(s) [%s] account=%s",
                len(campaigns_raw),
                ", ".join(masks_applied),
                ad_account_id,
            )
        else:
            logger.debug(
                "fetch_campaigns_and_insights pass-through for account=%s (%d campaigns)",
                ad_account_id,
                len(campaigns_raw),
            )

        return campaigns_raw, metrics_by_campaign, insights_error_count

    # ------------------------------------------------------------------
    # fetch_ads
    # ------------------------------------------------------------------
    async def fetch_ads(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> List[Dict[str, Any]]:
        ads = await self._live.fetch_ads(client, access_token, ad_account_id)

        p = self._policy
        if p.mask_ad_statuses or p.mask_status:
            for ad in ads:
                ad["status"] = "ACTIVE"
            logger.info(
                "masking applied: ad statuses forced ACTIVE for %d ad(s) account=%s",
                len(ads),
                ad_account_id,
            )
        else:
            logger.debug(
                "fetch_ads pass-through for account=%s (%d ads)", ad_account_id, len(ads)
            )

        return ads

    # ------------------------------------------------------------------
    # pause_campaign / resume_campaign
    # ------------------------------------------------------------------
    async def fetch_campaign_structure(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
        campaign_id: str,
    ):
        return await self._live.fetch_campaign_structure(client, access_token, ad_account_id, campaign_id)

    async def pause_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        if self._policy.mask_pause_resume:
            logger.info("masking: pause_campaign no-op for campaign_id=%s", campaign_id)
            return
        await self._live.pause_campaign(client, access_token, campaign_id)

    async def resume_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        if self._policy.mask_pause_resume:
            logger.info("masking: resume_campaign no-op for campaign_id=%s", campaign_id)
            return
        await self._live.resume_campaign(client, access_token, campaign_id)

# providers/google_demo.py
from typing import Any, Dict, List, Tuple

import httpx

from .meta_provider import PlatformProvider

# ---------------------------------------------------------------------------
# Synthetic campaign fixtures
# ---------------------------------------------------------------------------

_CAMPAIGNS: List[Dict[str, Any]] = [
    {
        "id": "demo_g_camp_rsa",
        "name": "Demo Search Campaign (RSA)",
        "status": "ACTIVE",
        "daily_budget": 5000,
    },
    {
        "id": "demo_g_camp_display",
        "name": "Demo Display Campaign",
        "status": "ACTIVE",
        "daily_budget": 3000,
    },
    {
        "id": "demo_g_camp_pmax",
        "name": "Demo Performance Max",
        "status": "ACTIVE",
        "daily_budget": 8000,
    },
]

_METRICS: Dict[str, Dict[str, Any]] = {
    "demo_g_camp_rsa": {
        "impressions": 95_000,
        "clicks": 2_850,
        "spend": 1_140.0,
    },
    "demo_g_camp_display": {
        "impressions": 210_000,
        "clicks": 1_470,
        "spend": 588.0,
    },
    "demo_g_camp_pmax": {
        "impressions": 145_000,
        "clicks": 3_625,
        "spend": 1_813.0,
    },
}

# ---------------------------------------------------------------------------
# Synthetic creative structures (adsets + ads per campaign)
# ---------------------------------------------------------------------------

_STRUCTURE: Dict[str, Tuple[List[Dict], List[Dict]]] = {
    "demo_g_camp_rsa": (
        [
            {
                "id": "demo_g_ag_rsa",
                "name": "Demo Ad Group — Search",
                "status": "ACTIVE",
                "campaign_id": "demo_g_camp_rsa",
            }
        ],
        [
            {
                "id": "demo_g_ad_rsa",
                "name": "Demo RSA Ad",
                "status": "ENABLED",
                "effective_status": "ENABLED",
                "adset_id": "demo_g_ag_rsa",
                "ad_type": "RESPONSIVE_SEARCH_AD",
                "final_urls": ["https://demo.example.com/search-landing"],
                "responsive_search_ad": {
                    "headlines": [
                        {"text": "Shop the Best Deals"},
                        {"text": "Save Big Today"},
                        {"text": "Top-Rated Products"},
                        {"text": "Free Shipping on Orders"},
                        {"text": "Limited Time Offer"},
                    ],
                    "descriptions": [
                        {"text": "Explore our full catalog of premium products at unbeatable prices."},
                        {"text": "Fast delivery, easy returns, and 24/7 customer support."},
                    ],
                },
            }
        ],
    ),
    "demo_g_camp_display": (
        [
            {
                "id": "demo_g_ag_display",
                "name": "Demo Ad Group — Display",
                "status": "ACTIVE",
                "campaign_id": "demo_g_camp_display",
            }
        ],
        [
            {
                "id": "demo_g_ad_display",
                "name": "Demo Responsive Display Ad",
                "status": "ENABLED",
                "effective_status": "ENABLED",
                "adset_id": "demo_g_ag_display",
                "ad_type": "RESPONSIVE_DISPLAY_AD",
                "final_urls": ["https://demo.example.com/display-landing"],
                "responsive_display_ad": {
                    "headlines": [
                        {"text": "Amazing Products"},
                        {"text": "Shop Now and Save"},
                    ],
                    "descriptions": [
                        {"text": "Discover our seasonal collection."},
                        {"text": "New arrivals every week."},
                    ],
                    "marketing_images": [
                        {"asset": "customers/demo/assets/img_001"},
                        {"asset": "customers/demo/assets/img_002"},
                    ],
                },
            }
        ],
    ),
    "demo_g_camp_pmax": (
        [],
        [
            {
                "id": "pmax_demo_ag_1",
                "name": "Demo Asset Group",
                "status": "ENABLED",
                "effective_status": "ENABLED",
                "adset_id": "demo_ag_1",
                "ad_type": "PERFORMANCE_MAX_AD",
                "pmax_assets": [
                    {"field_type": "HEADLINE", "text": "Boost Your Business", "resource_name": "customers/demo/assets/h1"},
                    {"field_type": "HEADLINE", "text": "Reach New Customers", "resource_name": "customers/demo/assets/h2"},
                    {"field_type": "LONG_HEADLINE", "text": "Grow your customer base with our proven solutions", "resource_name": "customers/demo/assets/lh1"},
                    {"field_type": "DESCRIPTION", "text": "Comprehensive marketing tools to drive growth.", "resource_name": "customers/demo/assets/d1"},
                    {"field_type": "DESCRIPTION", "text": "Trusted by thousands of businesses worldwide.", "resource_name": "customers/demo/assets/d2"},
                    {"field_type": "MARKETING_IMAGE", "text": "", "resource_name": "customers/demo/assets/img1"},
                ],
            }
        ],
    ),
}


class GoogleDemoProvider(PlatformProvider):
    """Fully synthetic Google Ads provider for demo mode. No API calls made."""

    @property
    def platform_name(self) -> str:
        return "google"

    def normalize_creative(self, ad: Dict[str, Any]):
        from .google_provider import GooglePlatformProvider
        return GooglePlatformProvider().normalize_creative(ad)

    async def fetch_campaigns_and_insights(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
        login_customer_id: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], int]:
        metrics = {}
        for cid, m in _METRICS.items():
            imp = m["impressions"]
            clk = m["clicks"]
            spend = m["spend"]
            metrics[cid] = {
                **m,
                "ctr": round(clk / imp * 100.0, 4) if imp > 0 else None,
                "cpm": round(spend / imp * 1000.0, 4) if imp > 0 else None,
                "cpc": round(spend / clk, 4) if clk > 0 else None,
            }
        return list(_CAMPAIGNS), metrics, 0

    async def fetch_campaign_structure(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
        campaign_id: str,
        login_customer_id: str | None = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        return _STRUCTURE.get(campaign_id, ([], []))

    async def fetch_ads(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> List[Dict[str, Any]]:
        ads = []
        for _adsets, campaign_ads in _STRUCTURE.values():
            ads.extend(campaign_ads)
        return ads

    async def pause_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        pass

    async def resume_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        pass

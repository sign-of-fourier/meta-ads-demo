# providers/meta_provider.py
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple
import httpx


class MetaProvider(ABC):
    @abstractmethod
    async def fetch_campaigns_and_insights(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], int]:
        """Fetch campaigns and 7-day insights.

        Returns:
            campaigns_raw: list of raw campaign dicts from Meta
            metrics_by_campaign: campaign_id → insight metric dict
            insights_errors: count of campaigns whose insights fetch failed
        """
        ...

    @abstractmethod
    async def fetch_ads(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> List[Dict[str, Any]]:
        """Fetch all ads under the given ad account."""
        ...

    @abstractmethod
    async def pause_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        """Pause a campaign. Should be a no-op (not raise) if already paused."""
        ...

    @abstractmethod
    async def resume_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        """Resume a paused campaign."""
        ...

    @abstractmethod
    async def fetch_campaign_structure(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
        campaign_id: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Fetch adsets and ads with creative fields for a single campaign.

        Returns:
            adsets: list of adset dicts
            ads: list of ad dicts with expanded creative fields
        """
        ...

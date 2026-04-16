# providers/meta_provider.py
from typing import Any, Dict, List, Tuple
import httpx

class MetaProvider:
    async def fetch_campaigns_and_insights(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], int]:
        raise NotImplementedError

    async def fetch_ads(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        ad_account_id: str,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def pause_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        raise NotImplementedError

    async def resume_campaign(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        campaign_id: str,
    ) -> None:
        raise NotImplementedError

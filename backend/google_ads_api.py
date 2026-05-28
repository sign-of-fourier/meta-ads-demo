# google_ads_api.py — raw Google Ads HTTP calls
import os

import httpx

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_ADS_BASE = os.getenv("FAKE_GOOGLE_BASE_URL", "https://googleads.googleapis.com")


async def refresh_access_token(refresh_token: str) -> str:
    """Exchange a refresh token for a fresh access token."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Google token refresh failed: {resp.text}")
    return resp.json()["access_token"]


async def exchange_code_for_tokens(code: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Google token exchange failed: {resp.text}")
    return resp.json()


async def list_accessible_customers(
    access_token: str, developer_token: str, api_version: str
) -> list[str]:
    """Return resource names of all Google Ads customers accessible to this token."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_GOOGLE_ADS_BASE}/{api_version}/customers:listAccessibleCustomers",
            headers={
                "Authorization": f"Bearer {access_token}",
                "developer-token": developer_token,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to list Google Ads customers: {resp.text}")
    return resp.json().get("resourceNames", [])


async def get_customer_name(
    customer_id: str, access_token: str, developer_token: str, api_version: str
) -> str | None:
    """Fetch the descriptive name for a Google Ads customer."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_GOOGLE_ADS_BASE}/{api_version}/customers/{customer_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "developer-token": developer_token,
            },
        )
    if resp.status_code != 200:
        return None
    data = resp.json()
    return data.get("descriptiveName") or data.get("id")


async def create_rsa(
    customer_id: str,
    access_token: str,
    developer_token: str,
    api_version: str,
    ad_group_id: str,
    headlines: list[str],
    descriptions: list[str],
    final_url: str,
    login_customer_id: str | None = None,
) -> str:
    """Create a new PAUSED Responsive Search Ad via the Mutate API.

    The first headline and first description are pinned (HEADLINE_1, DESCRIPTION_1).
    Google RSA requires at least 3 headlines and 2 descriptions.
    Returns the resource name of the created ad group ad.
    """
    url = f"{_GOOGLE_ADS_BASE}/{api_version}/customers/{customer_id}:mutate"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": developer_token,
        "Content-Type": "application/json",
    }
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id

    headline_assets = [{"text": h} for h in headlines]
    if headline_assets:
        headline_assets[0]["pinnedField"] = "HEADLINE_1"

    description_assets = [{"text": d} for d in descriptions]
    if description_assets:
        description_assets[0]["pinnedField"] = "DESCRIPTION_1"

    body = {
        "mutateOperations": [
            {
                "adGroupAdOperation": {
                    "create": {
                        "ad": {
                            "responsiveSearchAd": {
                                "headlines": headline_assets,
                                "descriptions": description_assets,
                            },
                            "finalUrls": [final_url],
                        },
                        "adGroup": f"customers/{customer_id}/adGroups/{ad_group_id}",
                        "status": "PAUSED",
                    }
                }
            }
        ]
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=body, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"Google RSA create failed ({resp.status_code}): {resp.text}")

    data = resp.json()
    responses = data.get("mutateOperationResponses", [])
    if not responses:
        raise RuntimeError(f"No mutateOperationResponses in: {data}")
    resource_name = (responses[0].get("adGroupAdResult") or {}).get("resourceName", "")
    if not resource_name:
        raise RuntimeError(f"No resourceName in Google Mutate response: {data}")
    return resource_name


async def query_gaql(
    customer_id: str,
    access_token: str,
    developer_token: str,
    api_version: str,
    gaql: str,
    client: httpx.AsyncClient,
    login_customer_id: str | None = None,
) -> list[dict]:
    """Execute a GAQL query via searchStream and return a flat list of row dicts.

    The searchStream endpoint returns a JSON array of batch response objects, each
    containing a 'results' list. This function flattens them into a single list.
    """
    url = f"{_GOOGLE_ADS_BASE}/{api_version}/customers/{customer_id}/googleAds:searchStream"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": developer_token,
    }
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id
    resp = await client.post(url, json={"query": gaql}, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"GAQL query failed ({resp.status_code}): {resp.text}")

    data = resp.json()
    rows: list[dict] = []
    if isinstance(data, list):
        for batch in data:
            rows.extend(batch.get("results", []))
    elif isinstance(data, dict):
        rows.extend(data.get("results", []))
    return rows

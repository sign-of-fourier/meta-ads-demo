# google_ads_api.py — raw Google Ads HTTP calls
import os

import httpx

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_ADS_BASE = os.getenv("FAKE_GOOGLE_BASE_URL", "https://googleads.googleapis.com")


_USING_FAKE_SERVER = bool(os.getenv("FAKE_GOOGLE_BASE_URL", ""))


async def refresh_access_token(refresh_token: str) -> str:
    """Exchange a refresh token for a fresh access token."""
    if _USING_FAKE_SERVER:
        return "fake_google_access_token"
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
    ad_name: str | None = None,
    pin_all: bool = False,
) -> str:
    """Create a new PAUSED Responsive Search Ad via the Mutate API.

    When pin_all=False (default): pins only HEADLINE_1 and DESCRIPTION_1.
    When pin_all=True: pins all 5 slots so every impression shows the same
    combination — required for test clones where CTR must reflect exactly one
    (headline, description) pair.
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

    _HEADLINE_PINS = ["HEADLINE_1", "HEADLINE_2", "HEADLINE_3"]
    _DESC_PINS = ["DESCRIPTION_1", "DESCRIPTION_2"]

    headline_assets = []
    for i, h in enumerate(headlines):
        asset: dict = {"text": h}
        if pin_all and i < len(_HEADLINE_PINS):
            asset["pinnedField"] = _HEADLINE_PINS[i]
        elif i == 0:
            asset["pinnedField"] = "HEADLINE_1"
        headline_assets.append(asset)

    description_assets = []
    for i, d in enumerate(descriptions):
        asset = {"text": d}
        if pin_all and i < len(_DESC_PINS):
            asset["pinnedField"] = _DESC_PINS[i]
        elif i == 0:
            asset["pinnedField"] = "DESCRIPTION_1"
        description_assets.append(asset)

    body = {
        "mutateOperations": [
            {
                "adGroupAdOperation": {
                    "create": {
                        "ad": {
                            **({"name": ad_name} if ad_name else {}),
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

# routes/meta.py
#
# Mimics the Meta Graph API for the endpoints called by meta_live.py.
# All routes are handled by a single catch-all that dispatches on path parts.
# Access tokens and account IDs are accepted but ignored — fixtures are returned
# regardless of which real account is connected.

import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load(name: str):
    with open(_FIXTURES / name) as f:
        return json.load(f)


def _paging():
    return {"cursors": {"before": "fake_before", "after": "fake_after"}}


def _fake_id(prefix: str = "fake") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Handlers — each returns a JSONResponse
# ---------------------------------------------------------------------------

def _get_campaigns() -> JSONResponse:
    campaigns = _load("meta_campaigns.json")
    return JSONResponse({"data": campaigns, "paging": _paging()})


def _get_insights() -> JSONResponse:
    insights = _load("meta_insights.json")
    return JSONResponse({"data": insights, "paging": _paging()})


def _get_adsets(request: Request) -> JSONResponse:
    """Return adsets filtered by campaign.id from the `filtering` query param."""
    all_adsets = _load("meta_adsets.json")  # dict keyed by campaign_id

    filtering_str = request.query_params.get("filtering", "")
    campaign_id = None
    if filtering_str:
        try:
            filters = json.loads(filtering_str)
            for f in filters:
                if f.get("field") == "campaign.id":
                    campaign_id = str(f.get("value", ""))
                    break
        except (json.JSONDecodeError, TypeError):
            pass

    if campaign_id and campaign_id in all_adsets:
        data = all_adsets[campaign_id]
    else:
        # No filter — return all adsets flat
        data = [adset for adsets in all_adsets.values() for adset in adsets]

    return JSONResponse({"data": data, "paging": _paging()})


def _get_ads(request: Request) -> JSONResponse:
    """Return ads filtered by campaign.id from the `filtering` query param."""
    all_ads = _load("meta_ads.json")  # dict keyed by campaign_id

    filtering_str = request.query_params.get("filtering", "")
    campaign_id = None
    if filtering_str:
        try:
            filters = json.loads(filtering_str)
            for f in filters:
                if f.get("field") == "campaign.id":
                    campaign_id = str(f.get("value", ""))
                    break
        except (json.JSONDecodeError, TypeError):
            pass

    if campaign_id and campaign_id in all_ads:
        data = all_ads[campaign_id]
    else:
        data = [ad for ads in all_ads.values() for ad in ads]

    return JSONResponse({"data": data, "paging": _paging()})


def _get_adimages(request: Request) -> JSONResponse:
    """Resolve image hashes to URLs. Returns placeholder URLs for any hash."""
    hashes_raw = request.query_params.get("hashes", "[]")
    try:
        hashes = json.loads(hashes_raw)
    except (json.JSONDecodeError, TypeError):
        hashes = []

    # Also check fixture ads for pre-seeded hashes
    all_ads = _load("meta_ads.json")
    hash_to_url: dict[str, str] = {}
    for ads in all_ads.values():
        for ad in ads:
            feed = (ad.get("creative") or {}).get("asset_feed_spec") or {}
            for img in feed.get("images", []):
                h = img.get("hash")
                u = img.get("url")
                if h and u:
                    hash_to_url[h] = u

    data = []
    for h in hashes:
        url = hash_to_url.get(h, f"https://picsum.photos/seed/{h}/600/315")
        data.append({"hash": h, "url": url})

    return JSONResponse({"data": data, "paging": _paging()})


def _post_adimages() -> JSONResponse:
    """Fake image upload — returns a synthetic hash."""
    fake_hash = _fake_id("imghash")
    fake_name = "uploaded.png"
    return JSONResponse({"images": {fake_name: {"hash": fake_hash, "url": ""}}})


def _post_adcreatives() -> JSONResponse:
    """Fake creative creation — returns a synthetic ID."""
    return JSONResponse({"id": _fake_id("creative")})


def _post_ads() -> JSONResponse:
    """Fake ad creation — returns a synthetic ID."""
    return JSONResponse({"id": _fake_id("ad")})


def _entity_action(entity_id: str, request: Request) -> JSONResponse:
    """
    Handles:
      POST /{campaign_id}           → pause / resume (status in form body)
      GET  /{ad_id}?fields=creative{...}  → fetch creative for push flow
    """
    method = request.method.upper()
    if method == "GET":
        # Backend fetches creative details for a specific ad before push.
        # Find the ad in fixtures and return it wrapped as Meta would.
        all_ads = _load("meta_ads.json")
        for ads in all_ads.values():
            for ad in ads:
                if str(ad.get("id")) == entity_id:
                    creative = ad.get("creative", {})
                    return JSONResponse({
                        "id": entity_id,
                        "creative": creative,
                    })
        # Not found — return minimal stub
        return JSONResponse({"id": entity_id, "creative": {}})

    # POST → pause or resume
    return JSONResponse({"success": True})


# ---------------------------------------------------------------------------
# Catch-all router
# ---------------------------------------------------------------------------

@router.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
async def meta_catch_all(path: str, request: Request) -> JSONResponse:
    """
    Dispatches Meta Graph API calls based on URL structure.

    Path arrives without the leading /meta prefix (already stripped by mount).
    Examples after stripping version segment:
      act_123456/campaigns         → GET campaigns
      act_123456/insights          → GET insights
      act_123456/adsets            → GET adsets (with filtering param)
      act_123456/ads               → GET ads | POST create ad
      act_123456/adimages          → GET resolve hashes | POST upload image
      act_123456/adcreatives       → POST create creative
      123456789                    → POST pause/resume | GET entity detail
    """
    method = request.method.upper()
    parts = [p for p in path.split("/") if p]

    if not parts:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Strip version segment if present (e.g. "v19.0")
    if parts[0].startswith("v") and "." in parts[0]:
        parts = parts[1:]

    if not parts:
        return JSONResponse({"error": "not found"}, status_code=404)

    resource_id = parts[0]
    endpoint = parts[1] if len(parts) > 1 else None

    logger.debug("Meta fake: method=%s resource=%s endpoint=%s", method, resource_id, endpoint)

    # Account-level endpoints (account IDs start with "act_")
    if resource_id.startswith("act_"):
        if endpoint == "campaigns":
            return _get_campaigns()
        elif endpoint == "insights":
            return _get_insights()
        elif endpoint == "adsets":
            return _get_adsets(request)
        elif endpoint == "ads":
            if method == "GET":
                return _get_ads(request)
            else:  # POST
                return _post_ads()
        elif endpoint == "adimages":
            if method == "GET":
                return _get_adimages(request)
            else:  # POST
                return _post_adimages()
        elif endpoint == "adcreatives":
            return _post_adcreatives()
        else:
            logger.warning("Meta fake: unhandled account endpoint '%s'", endpoint)
            return JSONResponse({"error": f"unknown endpoint: {endpoint}"}, status_code=404)

    # Entity-level: campaign_id or ad_id (pure numeric or fixture IDs)
    return _entity_action(resource_id, request)

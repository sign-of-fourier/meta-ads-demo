# routes/google.py
#
# Mimics the Google Ads REST API for the endpoints called by google_ads_api.py
# and google_provider.py. All auth headers are accepted but ignored.
#
# Key Google patterns we handle:
#   POST /{version}/customers/{cid}/googleAds:searchStream  → GAQL dispatch
#   POST /{version}/customers/{cid}:mutate                  → RSA create (push)
#   GET  /{version}/customers/{cid}                         → customer info
#   GET  /{version}/customers:listAccessibleCustomers        → list customers
#
# GAQL dispatch inspects the FROM clause and optional campaign.id filter.

import json
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_DEFAULT_CUSTOMER_ID = "1234567890"


def _load(name: str):
    with open(_FIXTURES / name) as f:
        return json.load(f)


def _fake_resource(customer_id: str, kind: str = "adGroupAds") -> str:
    return f"customers/{customer_id}/{kind}/{uuid.uuid4().int % 10**10}"


def _searchstream_response(rows: list[dict]) -> JSONResponse:
    """Wrap rows in the searchStream batch envelope."""
    return JSONResponse([{"results": rows, "fieldMask": "", "requestId": "fake"}])


def _extract_campaign_id(gaql: str) -> str | None:
    """Pull campaign.id value from a WHERE clause like `campaign.id = 9876543210`."""
    m = re.search(r"campaign\.id\s*=\s*(\d+)", gaql)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# GAQL handlers
# ---------------------------------------------------------------------------

def _handle_searchstream(customer_id: str, body: dict) -> JSONResponse:
    gaql = body.get("query", "")
    logger.debug("Google fake GAQL: %s", gaql[:120])

    # Dispatch on FROM clause keyword
    if "FROM asset_group_asset" in gaql:
        all_pmax = _load("google_pmax.json")
        cid = _extract_campaign_id(gaql)
        rows = all_pmax.get(cid, []) if cid else []
        return _searchstream_response(rows)

    if "FROM ad_group_ad" in gaql:
        all_ads = _load("google_ads.json")
        cid = _extract_campaign_id(gaql)
        rows = all_ads.get(cid, []) if cid else []
        return _searchstream_response(rows)

    if "FROM ad_group" in gaql:
        # Must check before "FROM campaign" since ad_group starts with 'a'
        all_adgroups = _load("google_adgroups.json")
        cid = _extract_campaign_id(gaql)
        rows = all_adgroups.get(cid, []) if cid else []
        return _searchstream_response(rows)

    if "FROM campaign" in gaql:
        rows = _load("google_campaigns.json")
        return _searchstream_response(rows)

    logger.warning("Google fake: unrecognised GAQL FROM clause — returning empty")
    return _searchstream_response([])


def _handle_mutate(customer_id: str, body: dict) -> JSONResponse:
    """Accept any mutate operation and return a synthetic resource name."""
    resource_name = _fake_resource(customer_id, "adGroupAds")
    return JSONResponse({
        "mutateOperationResponses": [
            {"adGroupAdResult": {"resourceName": resource_name}}
        ]
    })


def _handle_customer_info(customer_id: str) -> JSONResponse:
    return JSONResponse({
        "id": customer_id,
        "descriptiveName": f"Fake Ads Account ({customer_id})",
        "resourceName": f"customers/{customer_id}",
    })


def _handle_list_customers(customer_id: str) -> JSONResponse:
    return JSONResponse({
        "resourceNames": [f"customers/{customer_id}"]
    })


# ---------------------------------------------------------------------------
# Catch-all router
# ---------------------------------------------------------------------------

@router.api_route("/{path:path}", methods=["GET", "POST"])
async def google_catch_all(path: str, request: Request) -> JSONResponse:
    """
    Dispatches Google Ads REST calls based on URL path inspection.

    Path arrives without the leading /google prefix.
    Examples:
      v18/customers/1234567890/googleAds:searchStream
      v18/customers/1234567890:mutate
      v18/customers/1234567890
      v18/customers:listAccessibleCustomers
    """
    method = request.method.upper()
    logger.debug("Google fake: method=%s path=%s", method, path)

    # Extract customer_id from path (best-effort; falls back to default)
    customer_id = _DEFAULT_CUSTOMER_ID
    cid_match = re.search(r"customers/(\d+)", path)
    if cid_match:
        customer_id = cid_match.group(1)

    # Route on path suffix
    if path.endswith("/googleAds:searchStream"):
        body = await request.json()
        return _handle_searchstream(customer_id, body)

    if ":mutate" in path and method == "POST":
        body = await request.json()
        return _handle_mutate(customer_id, body)

    if "customers:listAccessibleCustomers" in path:
        return _handle_list_customers(customer_id)

    # GET customers/{customer_id} — customer info lookup
    if re.search(r"customers/\d+$", path) and method == "GET":
        return _handle_customer_info(customer_id)

    logger.warning("Google fake: unhandled path '%s'", path)
    return JSONResponse({"error": f"unhandled path: {path}"}, status_code=404)

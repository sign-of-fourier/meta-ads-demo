# routes/google.py
#
# Mimics the Google Ads REST API for the endpoints called by google_ads_api.py
# and google_provider.py.  All auth headers are accepted but ignored.
#
# Pushed ad state and evolving metrics live in state.py.

import json
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import state

logger = logging.getLogger(__name__)

router = APIRouter()

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_DEFAULT_CUSTOMER_ID = "1234567890"


def _load(name: str):
    with open(_FIXTURES / name) as f:
        return json.load(f)


def _searchstream_response(rows: list[dict]) -> JSONResponse:
    return JSONResponse([{"results": rows, "fieldMask": "", "requestId": "fake"}])


def _extract_campaign_id(gaql: str) -> str | None:
    m = re.search(r"campaign\.id\s*=\s*(\d+)", gaql)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# GAQL handlers
# ---------------------------------------------------------------------------

def _handle_campaigns(customer_id: str) -> JSONResponse:
    """Return fixture campaign rows with live evolving metrics."""
    fixture_rows = _load("google_campaigns.json")
    result = []
    for row in fixture_rows:
        cid = str((row.get("campaign") or {}).get("id", ""))
        m = state.campaign_metrics(cid, platform="google")
        updated = dict(row)
        updated["metrics"] = {
            "impressions": str(m["impressions"]),
            "clicks": str(m["clicks"]),
            "costMicros": str(int(m["spend"] * 1_000_000)),
            "ctr": str(m["ctr"] / 100),   # Google uses decimal fraction, not percent
        }
        result.append(updated)
    return _searchstream_response(result)


def _handle_adgroups(customer_id: str, campaign_id: str | None) -> JSONResponse:
    all_adgroups = _load("google_adgroups.json")
    if campaign_id and campaign_id in all_adgroups:
        rows = all_adgroups[campaign_id]
    else:
        rows = [r for group in all_adgroups.values() for r in group]
    return _searchstream_response(rows)


def _handle_ads(customer_id: str, campaign_id: str | None, include_metrics: bool = False) -> JSONResponse:
    """Return fixture ads + pushed clones, filtered by campaign_id if provided.

    When include_metrics=True (GAQL selects metrics.*), each row gets a 'metrics'
    dict.  Fixture ads return zeros; pushed clones return live ramp metrics.
    """
    all_fixture_ads = _load("google_ads.json")

    if campaign_id and campaign_id in all_fixture_ads:
        rows = list(all_fixture_ads[campaign_id])
    elif campaign_id:
        rows = []
    else:
        rows = [r for ads in all_fixture_ads.values() for r in ads]

    # Append pushed clones from state
    pushed = state.get_google_ads(customer_id, campaign_id=campaign_id)
    rows = rows + pushed

    if not include_metrics:
        return _searchstream_response(rows)

    enriched = []
    for row in rows:
        rn = (row.get("adGroupAd") or {}).get("resourceName", "")
        m = state.pushed_google_ad_metrics(rn) if rn else None
        enriched.append({**row, "metrics": {
            # Google returns CTR as decimal fraction (e.g. 0.045 = 4.5%)
            "impressions": str(m["impressions"]) if m else "0",
            "ctr": str(round(m["ctr"] / 100, 6)) if m else "0",
        }})
    return _searchstream_response(enriched)


def _handle_pmax(customer_id: str, campaign_id: str | None) -> JSONResponse:
    all_pmax = _load("google_pmax.json")
    rows = all_pmax.get(campaign_id, []) if campaign_id else []
    return _searchstream_response(rows)


def _handle_searchstream(customer_id: str, body: dict) -> JSONResponse:
    gaql = body.get("query", "")
    logger.debug("Google fake GAQL: %s", gaql[:120])
    campaign_id = _extract_campaign_id(gaql)

    if "FROM asset_group_asset" in gaql:
        return _handle_pmax(customer_id, campaign_id)
    if "FROM ad_group_ad" in gaql:
        return _handle_ads(customer_id, campaign_id, include_metrics="metrics." in gaql)
    if "FROM ad_group" in gaql:
        return _handle_adgroups(customer_id, campaign_id)
    if "FROM campaign" in gaql:
        return _handle_campaigns(customer_id)

    logger.warning("Google fake: unrecognised GAQL FROM clause — returning empty")
    return _searchstream_response([])


def _handle_mutate(customer_id: str, body: dict) -> JSONResponse:
    """
    Accept any mutate operation.  If it looks like an RSA creation, store it
    in state so it comes back on the next searchStream call.  All other mutate
    operations (pause/resume/budget) just return success.
    """
    ops = body.get("mutateOperations", [])
    is_rsa_create = any(
        "adGroupAdOperation" in op and "create" in (op.get("adGroupAdOperation") or {})
        for op in ops
    )

    if is_rsa_create:
        resource_name = state.store_google_ad(customer_id, body)
        logger.debug("Google fake: stored pushed RSA %s", resource_name)
    else:
        resource_name = f"customers/{customer_id}/adGroupAds/{uuid.uuid4().int % 10 ** 10}"
        logger.debug("Google fake: non-RSA mutate — returning stub resource name")

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
    return JSONResponse({"resourceNames": [f"customers/{customer_id}"]})


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

    customer_id = _DEFAULT_CUSTOMER_ID
    cid_match = re.search(r"customers/(\d+)", path)
    if cid_match:
        customer_id = cid_match.group(1)

    if path.endswith("/googleAds:searchStream"):
        body = await request.json()
        return _handle_searchstream(customer_id, body)

    if ":mutate" in path and method == "POST":
        body = await request.json()
        return _handle_mutate(customer_id, body)

    if "customers:listAccessibleCustomers" in path:
        return _handle_list_customers(customer_id)

    if re.search(r"customers/\d+$", path) and method == "GET":
        return _handle_customer_info(customer_id)

    logger.warning("Google fake: unhandled path '%s'", path)
    return JSONResponse({"error": f"unhandled path: {path}"}, status_code=404)

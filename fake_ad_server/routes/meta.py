# routes/meta.py
#
# Mimics the Meta Graph API for the endpoints called by meta_live.py.
# All routes are handled by a single catch-all that dispatches on path parts.
# Access tokens and account IDs are accepted but ignored.
#
# Pushed ad state and evolving metrics live in state.py.

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import state

logger = logging.getLogger(__name__)

router = APIRouter()


def _paging():
    return {"cursors": {"before": "fake_before", "after": "fake_after"}}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _get_campaigns() -> JSONResponse:
    import json as _json
    from pathlib import Path
    campaigns = _json.loads((Path(__file__).parent.parent / "fixtures" / "meta_campaigns.json").read_text())
    return JSONResponse({"data": campaigns, "paging": _paging()})


def _get_insights() -> JSONResponse:
    """Return live, slowly-evolving 7-day insights for each fixture campaign."""
    from pathlib import Path
    import json as _json
    base_rows = _json.loads((Path(__file__).parent.parent / "fixtures" / "meta_insights.json").read_text())

    data = []
    for row in base_rows:
        cid = row["campaign_id"]
        m = state.campaign_metrics(cid, platform="meta")
        data.append({
            "campaign_id": cid,
            "impressions": str(m["impressions"]),
            "clicks": str(m["clicks"]),
            "spend": str(m["spend"]),
            "ctr": str(m["ctr"]),
            "cpm": str(m["cpm"]),
            "cpc": str(m["cpc"]),
            "date_start": row.get("date_start", ""),
            "date_stop": row.get("date_stop", ""),
        })
    return JSONResponse({"data": data, "paging": _paging()})


def _get_adsets(request: Request) -> JSONResponse:
    from pathlib import Path
    import json as _json
    all_adsets = _json.loads((Path(__file__).parent.parent / "fixtures" / "meta_adsets.json").read_text())

    filtering_str = request.query_params.get("filtering", "")
    campaign_id = None
    if filtering_str:
        try:
            for f in json.loads(filtering_str):
                if f.get("field") == "campaign.id":
                    campaign_id = str(f.get("value", ""))
                    break
        except (json.JSONDecodeError, TypeError):
            pass

    if campaign_id and campaign_id in all_adsets:
        data = all_adsets[campaign_id]
    else:
        data = [adset for adsets in all_adsets.values() for adset in adsets]

    return JSONResponse({"data": data, "paging": _paging()})


def _get_ads(request: Request) -> JSONResponse:
    from pathlib import Path
    import json as _json
    all_fixture_ads = _json.loads((Path(__file__).parent.parent / "fixtures" / "meta_ads.json").read_text())

    filtering_str = request.query_params.get("filtering", "")
    campaign_id = None
    if filtering_str:
        try:
            for f in json.loads(filtering_str):
                if f.get("field") == "campaign.id":
                    campaign_id = str(f.get("value", ""))
                    break
        except (json.JSONDecodeError, TypeError):
            pass

    # Fixture ads
    if campaign_id and campaign_id in all_fixture_ads:
        data = list(all_fixture_ads[campaign_id])
    elif campaign_id:
        data = []
    else:
        data = [ad for ads in all_fixture_ads.values() for ad in ads]

    # Pushed ads from state — extract act_id from the request path
    act_id = _extract_act_id(request)
    if act_id:
        pushed = state.get_meta_ads(act_id, campaign_id=campaign_id)
        data = data + pushed

    return JSONResponse({"data": data, "paging": _paging()})


def _get_adimages(request: Request) -> JSONResponse:
    from pathlib import Path
    import json as _json
    all_ads = _json.loads((Path(__file__).parent.parent / "fixtures" / "meta_ads.json").read_text())

    hashes_raw = request.query_params.get("hashes", "[]")
    try:
        hashes = json.loads(hashes_raw)
    except (json.JSONDecodeError, TypeError):
        hashes = []

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


async def _post_adimages() -> JSONResponse:
    from state import _fake_id  # not in state, use local
    import uuid
    fake_hash = f"imghash_{uuid.uuid4().hex[:12]}"
    return JSONResponse({"images": {"uploaded.png": {"hash": fake_hash, "url": ""}}})


async def _post_adcreatives(request: Request) -> JSONResponse:
    """Store the creative and return a deterministic fake ID."""
    try:
        form = await request.form()
        name = form.get("name", "unnamed creative")
        oss_raw = form.get("object_story_spec", "{}")
        creative_id = state.store_meta_creative(name, oss_raw)
        logger.debug("Meta fake: stored creative %s (%s)", creative_id, name)
    except Exception as exc:
        logger.warning("Meta fake: adcreatives parse error: %s", exc)
        import uuid
        creative_id = f"fake_creative_{uuid.uuid4().hex[:10]}"
    return JSONResponse({"id": creative_id})


async def _post_ads(request: Request) -> JSONResponse:
    """Store the ad in state (linking to its creative) and return a fake ID."""
    try:
        form = await request.form()
        name = form.get("name", "Unnamed pushed ad")
        adset_id = form.get("adset_id", "unknown")
        creative_raw = form.get("creative", "{}")
        status = form.get("status", "PAUSED")
        creative_obj = json.loads(creative_raw) if isinstance(creative_raw, str) else creative_raw
        creative_id = creative_obj.get("creative_id", "")
        act_id = _extract_act_id(request)
        ad_id = state.store_meta_ad(act_id or "unknown", name, adset_id, creative_id, status)
        logger.debug("Meta fake: stored ad %s (adset=%s creative=%s)", ad_id, adset_id, creative_id)
    except Exception as exc:
        logger.warning("Meta fake: ads parse error: %s", exc)
        import uuid
        ad_id = f"fake_ad_{uuid.uuid4().hex[:10]}"
    return JSONResponse({"id": ad_id})


def _get_entity_insights(entity_id: str) -> JSONResponse:
    """GET /{ad_id}/insights — lifetime metrics for a pushed clone."""
    m = state.pushed_meta_ad_metrics(entity_id)
    if not m:
        return JSONResponse({"data": [], "paging": _paging()})
    return JSONResponse({
        "data": [{
            "impressions": str(m["impressions"]),
            "clicks": str(m["clicks"]),
            "spend": str(m["spend"]),
            # Meta returns CTR as a percentage string (e.g. "4.5123")
            "ctr": str(m["ctr"]),
            "date_start": "2020-01-01",
            "date_stop": "9999-12-31",
        }],
        "paging": _paging(),
    })


async def _entity_action(entity_id: str, request: Request) -> JSONResponse:
    """
    GET  /{ad_id}?fields=creative{...}  — fetch creative for push / reingest
    POST /{entity_id}                   — pause / resume / status update
    """
    method = request.method.upper()

    if method == "GET":
        # Check fixture ads first
        from pathlib import Path
        import json as _json
        all_ads = _json.loads((Path(__file__).parent.parent / "fixtures" / "meta_ads.json").read_text())
        for ads in all_ads.values():
            for ad in ads:
                if str(ad.get("id")) == entity_id:
                    return JSONResponse({"id": entity_id, "creative": ad.get("creative", {})})

        # Check pushed clones in state
        pushed_ad = state.get_meta_ad_by_id(entity_id)
        if pushed_ad:
            return JSONResponse({"id": entity_id, "creative": pushed_ad.get("creative", {})})

        return JSONResponse({"id": entity_id, "creative": {}})

    # POST — pause / resume / status update
    try:
        form = await request.form()
        new_status = form.get("status")
        if new_status:
            updated = state.update_meta_ad_status(entity_id, new_status)
            logger.debug("Meta fake: status update %s → %s (found=%s)", entity_id, new_status, updated)
    except Exception as exc:
        logger.warning("Meta fake: entity action parse error: %s", exc)

    return JSONResponse({"success": True})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_act_id(request: Request) -> str | None:
    """Pull the act_XXXX identifier out of the request path."""
    for part in request.url.path.split("/"):
        if part.startswith("act_"):
            return part
    return None


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
      act_123456/adsets            → GET adsets
      act_123456/ads               → GET ads | POST create ad
      act_123456/adimages          → GET resolve hashes | POST upload image
      act_123456/adcreatives       → POST create creative
      123456789                    → POST pause/resume | GET entity detail
    """
    method = request.method.upper()
    parts = [p for p in path.split("/") if p]

    if not parts:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Strip version segment (e.g. "v19.0")
    if parts[0].startswith("v") and "." in parts[0]:
        parts = parts[1:]

    if not parts:
        return JSONResponse({"error": "not found"}, status_code=404)

    resource_id = parts[0]
    endpoint = parts[1] if len(parts) > 1 else None

    logger.debug("Meta fake: method=%s resource=%s endpoint=%s", method, resource_id, endpoint)

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
            else:
                return await _post_ads(request)
        elif endpoint == "adimages":
            if method == "GET":
                return _get_adimages(request)
            else:
                return await _post_adimages()
        elif endpoint == "adcreatives":
            return await _post_adcreatives(request)
        else:
            logger.warning("Meta fake: unhandled account endpoint '%s'", endpoint)
            return JSONResponse({"error": f"unknown endpoint: {endpoint}"}, status_code=404)

    # Entity-level (ad_id or campaign_id)
    if endpoint == "insights" and method == "GET":
        return _get_entity_insights(resource_id)
    return await _entity_action(resource_id, request)

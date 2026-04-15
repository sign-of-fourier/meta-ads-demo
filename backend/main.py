"""
Meta Ads Demo – FastAPI backend
================================
Minimal SaaS control-plane that:
  1. Authenticates local users (JWT)
  2. Connects a Meta ad account via OAuth
  3. Lists campaigns from the Marketing API (with 7d metrics)
  4. Pauses / resumes campaigns
  5. Fetches ads/creatives for ingestion
  6. Stores metrics snapshots for dashboard / optimization
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

# ── Load .env ──────────────────────────────────────────────────────────────────
load_dotenv()

META_APP_ID = os.environ["META_APP_ID"]
META_APP_SECRET = os.environ["META_APP_SECRET"]
META_REDIRECT_URI = os.environ["META_REDIRECT_URI"]
META_API_VERSION = os.getenv("META_API_VERSION", "v19.0")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

META_GRAPH = f"https://graph.facebook.com/{META_API_VERSION}"

DB_PATH = Path(__file__).parent / "app.db"

# ── Helpers ────────────────────────────────────────────────────────────────────
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT UNIQUE NOT NULL,
            pw_hash     TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS meta_connections (
            user_id         INTEGER PRIMARY KEY REFERENCES users(id),
            meta_user_id    TEXT,
            access_token    TEXT NOT NULL,
            ad_account_id   TEXT NOT NULL,
            connected_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS oauth_states (
            state       TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id),
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ad_insights (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id),
            ad_account_id   TEXT NOT NULL,
            level           TEXT NOT NULL,          -- 'campaign', 'adset', 'ad'
            object_id       TEXT NOT NULL,           -- campaign_id / adset_id / ad_id
            date            TEXT NOT NULL,            -- YYYY-MM-DD
            impressions     INTEGER,
            clicks          INTEGER,
            spend           REAL,
            ctr             REAL,
            cpm             REAL,
            cpc             REAL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()


# ── App lifecycle ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Meta Ads Demo", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── JWT auth helpers ───────────────────────────────────────────────────────────
def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user_id(request: Request) -> int:
    """Extract user_id from Bearer token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = auth.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid token")


# ── Pydantic models ───────────────────────────────────────────────────────────
class SignupLogin(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    token: str


class MetaStatus(BaseModel):
    connected: bool
    ad_account_id: str | None = None


class LoginUrlResponse(BaseModel):
    url: str


class Campaign(BaseModel):
    id: str
    name: str
    status: str
    daily_budget: int | None = None
    spend_7d: float | None = None
    impressions_7d: int | None = None
    clicks_7d: int | None = None
    ctr_7d: float | None = None
    cpm_7d: float | None = None


class CampaignActionResponse(BaseModel):
    id: str
    status: str


class AdCreative(BaseModel):
    id: str
    name: str | None = None
    body: str | None = None
    image_url: str | None = None
    thumbnail_url: str | None = None
    status: str | None = None
    campaign_id: str | None = None
    adset_id: str | None = None


class CampaignHistoryPoint(BaseModel):
    date: str
    impressions: int | None = None
    clicks: int | None = None
    spend: float | None = None
    ctr: float | None = None
    cpm: float | None = None
    cpc: float | None = None


class IngestPreviewCampaign(BaseModel):
    id: str
    name: str
    status: str
    daily_budget: int | None = None
    impressions_7d: int | None = None
    clicks_7d: int | None = None
    spend_7d: float | None = None
    ctr_7d: float | None = None
    cpm_7d: float | None = None
    cpc_7d: float | None = None


class IngestPreview(BaseModel):
    ad_account_id: str
    campaigns: list[IngestPreviewCampaign]
    ads: list[AdCreative]


class IngestResult(BaseModel):
    campaigns_saved: int
    ad_account_id: str


# ── Auth routes (local) ───────────────────────────────────────────────────────
@app.post("/auth/signup", response_model=TokenResponse)
def signup(body: SignupLogin):
    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (body.email,)).fetchone()
    if existing:
        raise HTTPException(409, "Email already registered")
    cur = db.execute(
        "INSERT INTO users (email, pw_hash) VALUES (?, ?)",
        (body.email, pwd_ctx.hash(body.password)),
    )
    db.commit()
    user_id = cur.lastrowid
    db.close()
    return TokenResponse(token=create_token(user_id))


@app.post("/auth/login", response_model=TokenResponse)
def login(body: SignupLogin):
    db = get_db()
    row = db.execute("SELECT id, pw_hash FROM users WHERE email = ?", (body.email,)).fetchone()
    db.close()
    if not row or not pwd_ctx.verify(body.password, row["pw_hash"]):
        raise HTTPException(401, "Invalid email or password")
    return TokenResponse(token=create_token(row["id"]))


# ── Meta connection routes ─────────────────────────────────────────────────────
@app.get("/me/meta-status", response_model=MetaStatus)
def meta_status(user_id: int = Depends(get_current_user_id)):
    db = get_db()
    row = db.execute(
        "SELECT ad_account_id FROM meta_connections WHERE user_id = ?", (user_id,)
    ).fetchone()
    db.close()
    if row:
        return MetaStatus(connected=True, ad_account_id=row["ad_account_id"])
    return MetaStatus(connected=False)


@app.get("/auth/meta/login-url", response_model=LoginUrlResponse)
def meta_login_url(user_id: int = Depends(get_current_user_id)):
    state = secrets.token_urlsafe(32)
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO oauth_states (state, user_id) VALUES (?, ?)",
        (state, user_id),
    )
    db.commit()
    db.close()

    scopes = "ads_read,ads_management,business_management"
    url = (
        f"https://www.facebook.com/{META_API_VERSION}/dialog/oauth"
        f"?client_id={META_APP_ID}"
        f"&redirect_uri={META_REDIRECT_URI}"
        f"&scope={scopes}"
        f"&state={state}"
        f"&response_type=code"
    )
    return LoginUrlResponse(url=url)


@app.get("/auth/meta/callback")
async def meta_callback(
    code: str = Query(None),
    state: str = Query(None),
    error_code: str = Query(None),
    error_message: str = Query(None),
):
    """
    Meta redirects the browser here after the user approves.
    Exchange code → token, fetch ad accounts, store connection, redirect to frontend.
    """
    # Handle Meta error redirects (e.g. insecure login blocked, user denied)
    if error_code or not code or not state:
        msg = error_message or "Meta login failed or was cancelled"
        return RedirectResponse(
            f"{FRONTEND_URL}/app/settings?meta_error={msg}"
        )

    # 1. Validate state
    db = get_db()
    row = db.execute(
        "SELECT user_id FROM oauth_states WHERE state = ?", (state,)
    ).fetchone()
    if not row:
        raise HTTPException(400, "Invalid or expired OAuth state")
    user_id = row["user_id"]
    db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    db.commit()

    # 2. Exchange code for access token
    async with httpx.AsyncClient(timeout=30) as client:
        token_resp = await client.get(
            f"{META_GRAPH}/oauth/access_token",
            params={
                "client_id": META_APP_ID,
                "client_secret": META_APP_SECRET,
                "redirect_uri": META_REDIRECT_URI,
                "code": code,
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(502, f"Meta token exchange failed: {token_resp.text}")
        token_data = token_resp.json()
        access_token: str = token_data["access_token"]

        # 3. Get Meta user id
        me_resp = await client.get(
            f"{META_GRAPH}/me", params={"access_token": access_token}
        )
        meta_user_id = me_resp.json().get("id")

        # 4. Fetch ad accounts the user can access
        accts_resp = await client.get(
            f"{META_GRAPH}/me/adaccounts",
            params={
                "access_token": access_token,
                "fields": "id,name,account_status",
                "limit": 25,
            },
        )
        if accts_resp.status_code != 200:
            raise HTTPException(502, f"Failed to fetch ad accounts: {accts_resp.text}")

        accounts = accts_resp.json().get("data", [])
        if not accounts:
            raise HTTPException(
                400,
                "No ad accounts found. Make sure the Meta user has access to at least one ad account.",
            )

        # Pick the first account (or you can hard-code a specific test account id)
        ad_account_id: str = accounts[0]["id"]  # e.g. "act_123456789"

    # 5. Persist connection
    db.execute(
        """
        INSERT OR REPLACE INTO meta_connections
            (user_id, meta_user_id, access_token, ad_account_id)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, meta_user_id, access_token, ad_account_id),
    )
    db.commit()
    db.close()

    # 6. Redirect back to the frontend settings page
    return RedirectResponse(f"{FRONTEND_URL}/app/settings?meta_connected=true")


# ── Helper: load user's Meta creds ────────────────────────────────────────────
def _meta_creds(user_id: int) -> tuple[str, str]:
    """Return (access_token, ad_account_id) or raise 400."""
    db = get_db()
    row = db.execute(
        "SELECT access_token, ad_account_id FROM meta_connections WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    db.close()
    if not row:
        raise HTTPException(400, "Meta account not connected")
    return row["access_token"], row["ad_account_id"]


# ── Campaign routes ────────────────────────────────────────────────────────────
@app.get("/api/campaigns", response_model=list[Campaign])
async def list_campaigns(user_id: int = Depends(get_current_user_id)):
    access_token, ad_account_id = _meta_creds(user_id)

    async with httpx.AsyncClient(timeout=30) as client:
        campaigns_raw, metrics_by_campaign = await _fetch_campaigns_and_insights(
            client, access_token, ad_account_id
        )

    return [
        Campaign(
            id=c["id"],
            name=c.get("name", ""),
            status=c.get("status", ""),
            daily_budget=int(c["daily_budget"]) if c.get("daily_budget") else None,
            spend_7d=metrics_by_campaign.get(c["id"], {}).get("spend"),
            impressions_7d=metrics_by_campaign.get(c["id"], {}).get("impressions"),
            clicks_7d=metrics_by_campaign.get(c["id"], {}).get("clicks"),
            ctr_7d=metrics_by_campaign.get(c["id"], {}).get("ctr"),
            cpm_7d=metrics_by_campaign.get(c["id"], {}).get("cpm"),
        )
        for c in campaigns_raw
    ]


# ── Ingest preview + explicit ingest ──────────────────────────────────────────

async def _fetch_campaigns_and_insights(
    client: httpx.AsyncClient, access_token: str, ad_account_id: str
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Shared helper: fetch campaigns + 7d insights from Meta. Returns (campaigns_raw, metrics_by_campaign)."""
    camp_resp = await client.get(
        f"{META_GRAPH}/{ad_account_id}/campaigns",
        params={
            "access_token": access_token,
            "fields": "id,name,status,daily_budget",
            "limit": 100,
        },
    )
    if camp_resp.status_code != 200:
        raise HTTPException(502, f"Meta API error: {camp_resp.text}")
    campaigns_raw = camp_resp.json().get("data", [])

    metrics_by_campaign: dict[str, dict[str, Any]] = {}
    try:
        insights_resp = await client.get(
            f"{META_GRAPH}/{ad_account_id}/insights",
            params={
                "access_token": access_token,
                "level": "campaign",
                "date_preset": "last_7d",
                "fields": "campaign_id,impressions,clicks,spend,ctr,cpm,cpc",
                "limit": 5000,
            },
        )
        if insights_resp.status_code == 200:
            for row in insights_resp.json().get("data", []):
                cid = row.get("campaign_id")
                if not cid:
                    continue
                m = metrics_by_campaign.setdefault(
                    cid, {"impressions": 0, "clicks": 0, "spend": 0.0}
                )
                m["impressions"] += int(row.get("impressions", 0))
                m["clicks"] += int(row.get("clicks", 0))
                m["spend"] += float(row.get("spend", 0.0))
    except Exception:
        pass

    for m in metrics_by_campaign.values():
        imp = m["impressions"] or 0
        clk = m["clicks"] or 0
        spend = m["spend"] or 0.0
        m["ctr"] = (clk / imp * 100.0) if imp > 0 else None
        m["cpm"] = (spend / imp * 1000.0) if imp > 0 else None
        m["cpc"] = (spend / clk) if clk > 0 else None

    return campaigns_raw, metrics_by_campaign


async def _fetch_ads(
    client: httpx.AsyncClient, access_token: str, ad_account_id: str
) -> list[dict[str, Any]]:
    resp = await client.get(
        f"{META_GRAPH}/{ad_account_id}/ads",
        params={
            "access_token": access_token,
            "fields": "id,name,status,campaign_id,adset_id,creative{body,image_url,thumbnail_url}",
            "limit": 200,
        },
    )
    if resp.status_code != 200:
        raise HTTPException(502, f"Failed to fetch ads: {resp.text}")
    return resp.json().get("data", [])


@app.get("/api/ingest/preview", response_model=IngestPreview)
async def ingest_preview(user_id: int = Depends(get_current_user_id)):
    """Fetch campaigns + ads from Meta without writing to DB — shows what an ingest would save."""
    access_token, ad_account_id = _meta_creds(user_id)

    async with httpx.AsyncClient(timeout=30) as client:
        campaigns_raw, metrics_by_campaign = await _fetch_campaigns_and_insights(
            client, access_token, ad_account_id
        )
        ads_raw = await _fetch_ads(client, access_token, ad_account_id)

    campaigns_out = [
        IngestPreviewCampaign(
            id=c["id"],
            name=c.get("name", ""),
            status=c.get("status", ""),
            daily_budget=int(c["daily_budget"]) if c.get("daily_budget") else None,
            **{
                k: metrics_by_campaign.get(c["id"], {}).get(v)
                for k, v in [
                    ("impressions_7d", "impressions"),
                    ("clicks_7d", "clicks"),
                    ("spend_7d", "spend"),
                    ("ctr_7d", "ctr"),
                    ("cpm_7d", "cpm"),
                    ("cpc_7d", "cpc"),
                ]
            },
        )
        for c in campaigns_raw
    ]

    ads_out = [
        AdCreative(
            id=a["id"],
            name=a.get("name"),
            status=a.get("status"),
            campaign_id=a.get("campaign_id"),
            adset_id=a.get("adset_id"),
            body=(a.get("creative") or {}).get("body"),
            image_url=(a.get("creative") or {}).get("image_url"),
            thumbnail_url=(a.get("creative") or {}).get("thumbnail_url"),
        )
        for a in ads_raw
    ]

    return IngestPreview(
        ad_account_id=ad_account_id,
        campaigns=campaigns_out,
        ads=ads_out,
    )


@app.post("/api/ingest", response_model=IngestResult)
async def run_ingest(user_id: int = Depends(get_current_user_id)):
    """Fetch campaigns + insights from Meta and persist snapshots to ad_insights."""
    access_token, ad_account_id = _meta_creds(user_id)

    async with httpx.AsyncClient(timeout=30) as client:
        campaigns_raw, metrics_by_campaign = await _fetch_campaigns_and_insights(
            client, access_token, ad_account_id
        )

    db = get_db()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    saved = 0

    for c in campaigns_raw:
        cid = c["id"]
        mm = metrics_by_campaign.get(cid, {})
        if mm:
            db.execute(
                """
                INSERT INTO ad_insights
                    (user_id, ad_account_id, level, object_id, date,
                     impressions, clicks, spend, ctr, cpm, cpc)
                VALUES (?, ?, 'campaign', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, ad_account_id, cid, today,
                    mm.get("impressions"), mm.get("clicks"), mm.get("spend"),
                    mm.get("ctr"), mm.get("cpm"), mm.get("cpc"),
                ),
            )
            saved += 1

    db.commit()
    db.close()
    return IngestResult(campaigns_saved=saved, ad_account_id=ad_account_id)


# ── Ads / creatives (ingestion) ────────────────────────────────────────────────
@app.get("/api/ads", response_model=list[AdCreative])
async def list_ads(user_id: int = Depends(get_current_user_id)):
    """Fetch ads + creative bodies/images from the connected ad account."""
    access_token, ad_account_id = _meta_creds(user_id)

    async with httpx.AsyncClient(timeout=30) as client:
        ads_raw = await _fetch_ads(client, access_token, ad_account_id)

    return [
        AdCreative(
            id=a["id"],
            name=a.get("name"),
            status=a.get("status"),
            campaign_id=a.get("campaign_id"),
            adset_id=a.get("adset_id"),
            body=(a.get("creative") or {}).get("body"),
            image_url=(a.get("creative") or {}).get("image_url"),
            thumbnail_url=(a.get("creative") or {}).get("thumbnail_url"),
        )
        for a in ads_raw
    ]


# ── Raw Meta explorer ─────────────────────────────────────────────────────────

@app.get("/api/explore")
async def explore(user_id: int = Depends(get_current_user_id)):
    """
    Returns raw Meta API data for the connected account:
    campaigns → adsets → ads (with expanded creative fields).
    Nothing is saved to the DB. Use this to inspect what Meta is sending back.
    """
    access_token, ad_account_id = _meta_creds(user_id)

    async with httpx.AsyncClient(timeout=60) as client:

        # 1. Campaigns
        camp_resp = await client.get(
            f"{META_GRAPH}/{ad_account_id}/campaigns",
            params={
                "access_token": access_token,
                "fields": "id,name,status,objective,daily_budget,lifetime_budget,start_time,stop_time,created_time,updated_time",
                "limit": 100,
            },
        )
        if camp_resp.status_code != 200:
            raise HTTPException(502, f"Meta campaigns error: {camp_resp.text}")
        campaigns_raw = camp_resp.json().get("data", [])

        # 2. Adsets for all campaigns in one call
        adsets_resp = await client.get(
            f"{META_GRAPH}/{ad_account_id}/adsets",
            params={
                "access_token": access_token,
                "fields": "id,name,status,campaign_id,daily_budget,lifetime_budget,optimization_goal,billing_event,bid_amount,targeting,start_time,end_time,created_time,updated_time",
                "limit": 500,
            },
        )
        adsets_raw = adsets_resp.json().get("data", []) if adsets_resp.status_code == 200 else []

        # 3. Ads with expanded creative fields (important for dynamic ads)
        ads_resp = await client.get(
            f"{META_GRAPH}/{ad_account_id}/ads",
            params={
                "access_token": access_token,
                "fields": (
                    "id,name,status,campaign_id,adset_id,created_time,updated_time,"
                    "creative{"
                    "id,name,body,title,image_url,thumbnail_url,"
                    "object_type,effective_object_story_id,"
                    "asset_feed_spec,object_story_spec"
                    "}"
                ),
                "limit": 200,
            },
        )
        ads_raw_json = ads_resp.json()
        ads_raw = ads_raw_json.get("data", []) if ads_resp.status_code == 200 else []
        ads_fetch_error = None if ads_resp.status_code == 200 else ads_raw_json

    # Build nested structure: campaign → adsets → ads
    adsets_by_campaign: dict[str, list] = {}
    for adset in adsets_raw:
        cid = adset.get("campaign_id", "__unknown__")
        adsets_by_campaign.setdefault(cid, []).append(adset)

    ads_by_adset: dict[str, list] = {}
    ads_by_campaign: dict[str, list] = {}
    for ad in ads_raw:
        asid = ad.get("adset_id", "__unknown__")
        cid = ad.get("campaign_id", "__unknown__")
        ads_by_adset.setdefault(asid, []).append(ad)
        ads_by_campaign.setdefault(cid, []).append(ad)

    for adset in adsets_raw:
        adset["_ads"] = ads_by_adset.get(adset["id"], [])

    for campaign in campaigns_raw:
        campaign["_adsets"] = adsets_by_campaign.get(campaign["id"], [])
        campaign["_ads_flat"] = ads_by_campaign.get(campaign["id"], [])

    result: dict[str, Any] = {
        "ad_account_id": ad_account_id,
        "campaigns": campaigns_raw,
        "_all_adsets": adsets_raw,
        "_all_ads": ads_raw,
    }
    if ads_fetch_error:
        result["_ads_fetch_error"] = ads_fetch_error
    return result


# ── Dashboard: campaign history from stored snapshots ──────────────────────────
@app.get(
    "/api/campaigns/{campaign_id}/history",
    response_model=list[CampaignHistoryPoint],
)
def campaign_history(
    campaign_id: str,
    days: int = 30,
    user_id: int = Depends(get_current_user_id),
):
    """Return stored metric snapshots for a campaign (last N days)."""
    db = get_db()
    rows = db.execute(
        """
        SELECT date, impressions, clicks, spend, ctr, cpm, cpc
        FROM ad_insights
        WHERE user_id = ?
          AND level = 'campaign'
          AND object_id = ?
          AND date >= date('now', ? || ' days')
        ORDER BY date ASC
        """,
        (user_id, campaign_id, str(-abs(days))),
    ).fetchall()
    db.close()
    return [
        CampaignHistoryPoint(
            date=r["date"],
            impressions=r["impressions"],
            clicks=r["clicks"],
            spend=r["spend"],
            ctr=r["ctr"],
            cpm=r["cpm"],
            cpc=r["cpc"],
        )
        for r in rows
    ]


@app.post("/api/campaigns/{campaign_id}/pause", response_model=CampaignActionResponse)
async def pause_campaign(
    campaign_id: str, user_id: int = Depends(get_current_user_id)
):
    access_token, _ = _meta_creds(user_id)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{META_GRAPH}/{campaign_id}",
            params={"access_token": access_token},
            data={"status": "PAUSED"},
        )
        if resp.status_code != 200:
            raise HTTPException(502, f"Failed to pause campaign: {resp.text}")

    return CampaignActionResponse(id=campaign_id, status="PAUSED")


@app.post("/api/campaigns/{campaign_id}/resume", response_model=CampaignActionResponse)
async def resume_campaign(
    campaign_id: str, user_id: int = Depends(get_current_user_id)
):
    access_token, _ = _meta_creds(user_id)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{META_GRAPH}/{campaign_id}",
            params={"access_token": access_token},
            data={"status": "ACTIVE"},
        )
        if resp.status_code != 200:
            raise HTTPException(502, f"Failed to resume campaign: {resp.text}")

    return CampaignActionResponse(id=campaign_id, status="ACTIVE")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

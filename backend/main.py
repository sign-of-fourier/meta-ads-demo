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

import json
import logging
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
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

# ── Load .env ──────────────────────────────────────────────────────────────────
load_dotenv()

from providers.factory import get_meta_provider  # noqa: E402 – must follow load_dotenv
from providers.mask_policy import MaskPolicy  # noqa: E402 – must follow load_dotenv
meta_provider = get_meta_provider()
APP_MODE = os.getenv("APP_MODE", "live").lower()

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

logger = logging.getLogger(__name__)

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
            data_source     TEXT NOT NULL DEFAULT 'real',  -- 'real' | 'masked' | 'demo'
            mask_profile    TEXT,                          -- 'healthy' | 'stable' | 'weak' | NULL
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ad_creative_structures (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id),
            ad_account_id   TEXT NOT NULL,
            campaign_id     TEXT NOT NULL,
            adset_id        TEXT NOT NULL,
            ad_id           TEXT NOT NULL,
            creative_type   TEXT NOT NULL,  -- 'static' or 'dynamic'
            slot            TEXT NOT NULL,  -- 'headline', 'description', 'primary_text', 'image'
            slot_index      INTEGER NOT NULL DEFAULT 0,
            value           TEXT,
            ingested_at     TEXT NOT NULL DEFAULT (datetime('now')),
            lifecycle_status TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'inactive' | 'missing'
            data_source     TEXT NOT NULL DEFAULT 'real',     -- 'real' | 'masked' | 'demo'
            mask_profile    TEXT,                             -- 'healthy' | 'stable' | 'weak' | NULL
            UNIQUE (user_id, ad_id, slot, slot_index)
        );
        CREATE TABLE IF NOT EXISTS suggested_configurations (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER NOT NULL REFERENCES users(id),
            ad_account_id     TEXT NOT NULL,
            campaign_id       TEXT NOT NULL,
            adset_id          TEXT NOT NULL,
            source_ad_id      TEXT NOT NULL,   -- the dynamic template ad_id
            components        TEXT NOT NULL,   -- JSON: {slot: chosen_value, ...}
            deployment_status TEXT NOT NULL DEFAULT 'suggested',
            -- 'suggested' | 'pending_confirmation' | 'created_static' |
            -- 'active_static' | 'replaced_static' | 'rejected'
            static_ad_id      TEXT,            -- nullable; set after Meta deployment
            created_at        TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    # Migration: add lifecycle_status column to existing databases that predate this column
    try:
        conn.execute(
            "ALTER TABLE ad_creative_structures ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'active'"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE ad_insights ADD COLUMN data_source TEXT NOT NULL DEFAULT 'real'"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE ad_insights ADD COLUMN mask_profile TEXT"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE ad_creative_structures ADD COLUMN data_source TEXT NOT NULL DEFAULT 'real'"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE ad_creative_structures ADD COLUMN mask_profile TEXT"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
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

_GENERATED_IMAGES_DIR = Path(__file__).parent / "generated_images"
_GENERATED_IMAGES_DIR.mkdir(exist_ok=True)
app.mount("/images", StaticFiles(directory=str(_GENERATED_IMAGES_DIR)), name="generated_images")


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
    campaigns_seen: int = 0
    campaigns_with_metrics: int = 0
    insights_errors: int = 0
    message: str = ""


class StructureIngestResult(BaseModel):
    campaign_id: str
    ads_processed: int
    components_saved: int


class AdStructure(BaseModel):
    ad_id: str
    adset_id: str
    campaign_id: str
    creative_type: str
    lifecycle_status: str | None = None  # 'active' | 'inactive' | 'missing'
    components: dict[str, list[str | None]]  # slot → values ordered by slot_index


# ── Suggestion domain models ───────────────────────────────────────────────────

DEPLOYMENT_STATUSES = frozenset({
    "suggested",
    "pending_confirmation",
    "created_static",
    "active_static",
    "replaced_static",
    "rejected",
})


class StoreSuggestionRequest(BaseModel):
    source_ad_id: str           # the dynamic template ad_id
    campaign_id: str
    adset_id: str
    components: dict[str, str]  # slot → single chosen value


class ConfirmSuggestionRequest(BaseModel):
    action: str                         # "create" | "replace"
    target_static_ad_id: str | None = None  # for "replace": which existing ad to target
    static_ad_id: str | None = None    # optionally pre-link the new Meta ad id


class SuggestionResponse(BaseModel):
    id: int
    source_ad_id: str
    campaign_id: str
    adset_id: str
    components: dict[str, str]
    deployment_status: str
    static_ad_id: str | None = None
    created_at: str


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
    if APP_MODE == "demo":
        return "demo-token", "demo-account"
    db = get_db()
    row = db.execute(
        "SELECT access_token, ad_account_id FROM meta_connections WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    db.close()
    if not row:
        raise HTTPException(400, "Meta account not connected")
    return row["access_token"], row["ad_account_id"]


def _current_data_provenance() -> tuple[str, str | None]:
    """
    Returns (data_source, mask_profile)

    data_source:
      - 'demo'   when APP_MODE=demo
      - 'masked' when live mode uses masking
      - 'real'   otherwise
    """
    if APP_MODE == "demo":
        return "demo", None

    policy = MaskPolicy()
    if policy.enabled and (
        policy.mask_status
        or policy.mask_budgets
        or policy.mask_metrics
        or policy.mask_pause_resume
        or policy.mask_ad_statuses
    ):
        return "masked", policy.metric_profile

    return "real", None


# ── Campaign routes ────────────────────────────────────────────────────────────
@app.get("/api/campaigns", response_model=list[Campaign])
async def list_campaigns(user_id: int = Depends(get_current_user_id)):
    access_token, ad_account_id = _meta_creds(user_id)

    async with httpx.AsyncClient(timeout=30) as client:
        campaigns_raw, metrics_by_campaign, _ = await _fetch_campaigns_and_insights(
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
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
    return await meta_provider.fetch_campaigns_and_insights(client, access_token, ad_account_id)


async def _fetch_ads(
    client: httpx.AsyncClient, access_token: str, ad_account_id: str
) -> list[dict[str, Any]]:
    return await meta_provider.fetch_ads(client, access_token, ad_account_id)


@app.get("/api/ingest/preview", response_model=IngestPreview)
async def ingest_preview(user_id: int = Depends(get_current_user_id)):
    """Fetch campaigns + ads from Meta without writing to DB — shows what an ingest would save."""
    access_token, ad_account_id = _meta_creds(user_id)

    async with httpx.AsyncClient(timeout=30) as client:
        campaigns_raw, metrics_by_campaign, _ = await _fetch_campaigns_and_insights(
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
        campaigns_raw, metrics_by_campaign, insights_errors = await _fetch_campaigns_and_insights(
            client, access_token, ad_account_id
        )

    data_source, mask_profile = _current_data_provenance()
    campaigns_seen = len(campaigns_raw)
    campaigns_with_metrics = len(metrics_by_campaign)

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
                     impressions, clicks, spend, ctr, cpm, cpc,
                     data_source, mask_profile)
                VALUES (?, ?, 'campaign', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, ad_account_id, cid, today,
                    mm.get("impressions"), mm.get("clicks"), mm.get("spend"),
                    mm.get("ctr"), mm.get("cpm"), mm.get("cpc"),
                    data_source, mask_profile,
                ),
            )
            saved += 1

    db.commit()
    db.close()

    if insights_errors:
        message = (
            f"Insights fetch failed for {insights_errors} request(s); "
            f"{saved} of {campaigns_seen} campaign snapshots saved."
        )
        logger.warning(
            "Ingest completed with insights errors: account=%s seen=%d with_metrics=%d "
            "saved=%d insights_errors=%d",
            ad_account_id, campaigns_seen, campaigns_with_metrics, saved, insights_errors,
        )
    elif saved == 0:
        message = (
            f"No campaign metrics found in the last 7 days "
            f"({campaigns_seen} campaigns seen, none had delivery data)."
        )
        logger.info(
            "Ingest completed, no metrics data: account=%s seen=%d",
            ad_account_id, campaigns_seen,
        )
    else:
        message = (
            f"Ingested {saved} campaign snapshot(s) for account {ad_account_id}."
        )
        logger.info(
            "Ingest completed: account=%s seen=%d with_metrics=%d saved=%d",
            ad_account_id, campaigns_seen, campaigns_with_metrics, saved,
        )

    return IngestResult(
        campaigns_saved=saved,
        ad_account_id=ad_account_id,
        campaigns_seen=campaigns_seen,
        campaigns_with_metrics=campaigns_with_metrics,
        insights_errors=insights_errors,
        message=message,
    )


# ── Structural ingest ─────────────────────────────────────────────────────────

_SUPPORTED_SLOTS = ("headline", "description", "primary_text", "image")


def _normalize_creative(ad: dict) -> tuple[str, list[dict]]:
    """
    Derive creative_type and component list from a raw Meta ad dict.

    Returns (creative_type, components) where each component is:
        {"slot": str, "slot_index": int, "value": str | None}

    Dynamic detection: presence of "asset_feed_spec" in the creative.
    Supported slots: headline, description, primary_text, image.
    """
    creative = ad.get("creative") or {}
    asset_feed = creative.get("asset_feed_spec")

    if asset_feed:
        creative_type = "dynamic"
        components: list[dict] = []

        for i, item in enumerate(asset_feed.get("titles", [])):
            components.append({"slot": "headline", "slot_index": i, "value": item.get("text")})

        for i, item in enumerate(asset_feed.get("descriptions", [])):
            components.append({"slot": "description", "slot_index": i, "value": item.get("text")})

        for i, item in enumerate(asset_feed.get("bodies", [])):
            components.append({"slot": "primary_text", "slot_index": i, "value": item.get("text")})

        for i, item in enumerate(asset_feed.get("images", [])):
            value = item.get("url") or item.get("hash")
            components.append({"slot": "image", "slot_index": i, "value": value})

        return creative_type, components

    # Static: pull from top-level creative fields or object_story_spec
    creative_type = "static"
    components = []

    link_data = (creative.get("object_story_spec") or {}).get("link_data") or {}

    headline = creative.get("title") or link_data.get("name")
    if headline:
        components.append({"slot": "headline", "slot_index": 0, "value": headline})

    description = link_data.get("description")
    if description:
        components.append({"slot": "description", "slot_index": 0, "value": description})

    primary_text = creative.get("body") or link_data.get("message")
    if primary_text:
        components.append({"slot": "primary_text", "slot_index": 0, "value": primary_text})

    image = creative.get("image_url") or creative.get("thumbnail_url")
    if image:
        components.append({"slot": "image", "slot_index": 0, "value": image})

    return creative_type, components


async def _fetch_campaign_structure(
    client: httpx.AsyncClient,
    access_token: str,
    ad_account_id: str,
    campaign_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Fetch adsets and ads (with expanded creative fields) for a single campaign.
    Returns (adsets, ads).
    """
    adsets_resp = await client.get(
        f"{META_GRAPH}/{ad_account_id}/adsets",
        params={
            "access_token": access_token,
            "fields": "id,name,status,campaign_id",
            "filtering": f'[{{"field":"campaign.id","operator":"EQUAL","value":"{campaign_id}"}}]',
            "limit": 500,
        },
    )
    if adsets_resp.status_code != 200:
        raise HTTPException(502, f"Failed to fetch adsets: {adsets_resp.text}")
    adsets = adsets_resp.json().get("data", [])

    ads_resp = await client.get(
        f"{META_GRAPH}/{ad_account_id}/ads",
        params={
            "access_token": access_token,
            "fields": (
                "id,name,status,effective_status,campaign_id,adset_id,"
                "creative{"
                "id,name,body,title,image_url,thumbnail_url,"
                "asset_feed_spec,object_story_spec"
                "}"
            ),
            "filtering": f'[{{"field":"campaign.id","operator":"EQUAL","value":"{campaign_id}"}}]',
            "limit": 500,
        },
    )
    if ads_resp.status_code != 200:
        raise HTTPException(502, f"Failed to fetch ads: {ads_resp.text}")
    ads = ads_resp.json().get("data", [])

    return adsets, ads


@app.post("/api/ingest/structure/{campaign_id}", response_model=StructureIngestResult)
async def ingest_campaign_structure(
    campaign_id: str,
    user_id: int = Depends(get_current_user_id),
):
    """
    Fetch campaign → adset → ad → creative structure from Meta and persist to
    ad_creative_structures. Idempotent: existing rows for the same (user_id, ad_id,
    slot, slot_index) are replaced.
    """
    access_token, ad_account_id = _meta_creds(user_id)

    async with httpx.AsyncClient(timeout=30) as client:
        _adsets, ads = await _fetch_campaign_structure(
            client, access_token, ad_account_id, campaign_id
        )

    data_source, mask_profile = _current_data_provenance()
    db = get_db()

    # Collect ad_ids previously ingested for this campaign so we can detect missing ones
    existing_ad_ids: set[str] = set(
        row[0]
        for row in db.execute(
            "SELECT DISTINCT ad_id FROM ad_creative_structures WHERE user_id = ? AND campaign_id = ?",
            (user_id, campaign_id),
        ).fetchall()
    )

    ingested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ads_processed = 0
    components_saved = 0
    current_ad_ids: set[str] = set()
    ad_components_map: dict[str, list[dict]] = {}  # ad_id → components for embedding hook

    for ad in ads:
        ad_id = ad["id"]
        current_ad_ids.add(ad_id)
        adset_id = ad.get("adset_id", "")

        # Lifecycle: prefer effective_status; fall back to status
        raw_status = ad.get("effective_status") or ad.get("status", "")
        lifecycle_status = "active" if raw_status == "ACTIVE" else "inactive"

        creative_type, components = _normalize_creative(ad)
        ad_components_map[ad_id] = components

        for comp in components:
            db.execute(
                """
                INSERT INTO ad_creative_structures
                    (user_id, ad_account_id, campaign_id, adset_id, ad_id,
                     creative_type, slot, slot_index, value, ingested_at,
                     lifecycle_status, data_source, mask_profile)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id, ad_id, slot, slot_index)
                DO UPDATE SET
                    creative_type    = excluded.creative_type,
                    value            = excluded.value,
                    ingested_at      = excluded.ingested_at,
                    lifecycle_status = excluded.lifecycle_status,
                    data_source      = excluded.data_source,
                    mask_profile     = excluded.mask_profile
                """,
                (
                    user_id, ad_account_id, campaign_id, adset_id, ad_id,
                    creative_type, comp["slot"], comp["slot_index"],
                    comp["value"], ingested_at, lifecycle_status,
                    data_source, mask_profile,
                ),
            )
            components_saved += 1

        ads_processed += 1

    # Mark previously ingested ads that are no longer in this fetch as "missing"
    missing_ad_ids = existing_ad_ids - current_ad_ids
    if missing_ad_ids:
        placeholders = ",".join("?" * len(missing_ad_ids))
        db.execute(
            f"UPDATE ad_creative_structures SET lifecycle_status = 'missing' "
            f"WHERE user_id = ? AND campaign_id = ? AND ad_id IN ({placeholders})",
            (user_id, campaign_id, *missing_ad_ids),
        )

    db.commit()
    db.close()

    # ── Embedding hook (fire-and-forget) ──────────────────────────────────────
    # Failures here are logged and swallowed; ingest result is already committed.
    try:
        import asyncio as _asyncio
        from embeddings.pipeline import embed_ad
        for _ad_id, _comps in ad_components_map.items():
            _asyncio.create_task(embed_ad(user_id, _ad_id, campaign_id, _comps))
    except Exception:
        logger.warning("embedding hook unavailable — skipping", exc_info=True)

    return StructureIngestResult(
        campaign_id=campaign_id,
        ads_processed=ads_processed,
        components_saved=components_saved,
    )


@app.get("/api/structure/{campaign_id}", response_model=list[AdStructure])
def get_campaign_structure(
    campaign_id: str,
    user_id: int = Depends(get_current_user_id),
):
    """Return persisted creative structures for a campaign, grouped by ad."""
    db = get_db()
    rows = db.execute(
        """
        SELECT ad_id, adset_id, creative_type, slot, slot_index, value, lifecycle_status
        FROM ad_creative_structures
        WHERE user_id = ? AND campaign_id = ?
        ORDER BY ad_id, slot, slot_index
        """,
        (user_id, campaign_id),
    ).fetchall()
    db.close()

    ads_map: dict[str, dict] = {}
    for row in rows:
        ad_id = row["ad_id"]
        if ad_id not in ads_map:
            ads_map[ad_id] = {
                "ad_id": ad_id,
                "adset_id": row["adset_id"],
                "campaign_id": campaign_id,
                "creative_type": row["creative_type"],
                "lifecycle_status": row["lifecycle_status"],
                "components": {},
            }
        slot = row["slot"]
        ads_map[ad_id]["components"].setdefault(slot, []).append(row["value"])

    return list(ads_map.values())


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
        await meta_provider.pause_campaign(client, access_token, campaign_id)
    return CampaignActionResponse(id=campaign_id, status="PAUSED")


@app.post("/api/campaigns/{campaign_id}/resume", response_model=CampaignActionResponse)
async def resume_campaign(
    campaign_id: str, user_id: int = Depends(get_current_user_id)
):
    access_token, _ = _meta_creds(user_id)
    async with httpx.AsyncClient(timeout=30) as client:
        await meta_provider.resume_campaign(client, access_token, campaign_id)
    return CampaignActionResponse(id=campaign_id, status="ACTIVE")


# ── Suggestion routes ─────────────────────────────────────────────────────────

async def _clone_dynamic_to_static_ad(
    client: httpx.AsyncClient,
    access_token: str,
    ad_account_id: str,
    adset_id: str,
    source_ad_id: str,
    components: dict[str, str],
    suggestion_id: int,
) -> str:
    """
    Create a new static ad in Meta from a chosen set of component values.

    Steps:
      1. Fetch the source dynamic ad to inherit page_id and destination link URL.
      2. Build an object_story_spec.link_data creative from the chosen components.
      3. POST to /{ad_account_id}/adcreatives to create the creative.
      4. POST to /{ad_account_id}/ads to create the ad in the target adset (PAUSED).

    Returns the new Meta ad id.
    Raises HTTPException(502) on any Meta API failure — never swallows errors.
    """
    # 1. Fetch source ad to get page_id and the destination link URL
    src_resp = await client.get(
        f"{META_GRAPH}/{source_ad_id}",
        params={
            "access_token": access_token,
            "fields": "creative{page_id,object_story_spec}",
        },
    )
    if src_resp.status_code != 200:
        raise HTTPException(502, f"Failed to fetch source ad {source_ad_id}: {src_resp.text}")

    src_creative = (src_resp.json().get("creative") or {})
    oss = src_creative.get("object_story_spec") or {}
    page_id = src_creative.get("page_id") or oss.get("page_id")
    if not page_id:
        raise HTTPException(502, "Could not determine page_id from source ad creative")

    # Inherit the destination link URL from the source so the static ad is valid
    src_link_data = oss.get("link_data") or {}
    link_url = (
        src_link_data.get("link")
        or (src_link_data.get("call_to_action") or {}).get("value", {}).get("link")
    )

    # 2. Build static link_data from chosen components
    link_data: dict[str, Any] = {}
    if link_url:
        link_data["link"] = link_url
    if components.get("primary_text"):
        link_data["message"] = components["primary_text"]
    if components.get("headline"):
        link_data["name"] = components["headline"]
    if components.get("description"):
        link_data["description"] = components["description"]
    if components.get("image"):
        link_data["picture"] = components["image"]

    # 3. Create the ad creative
    creative_resp = await client.post(
        f"{META_GRAPH}/{ad_account_id}/adcreatives",
        data={
            "name": f"Static clone – suggestion {suggestion_id}",
            "object_story_spec": json.dumps({"page_id": page_id, "link_data": link_data}),
            "access_token": access_token,
        },
    )
    if creative_resp.status_code != 200:
        raise HTTPException(502, f"Meta creative creation failed: {creative_resp.text}")

    new_creative_id = creative_resp.json().get("id")
    if not new_creative_id:
        raise HTTPException(502, "Meta did not return a creative id")

    # 4. Create the static ad in the adset (starts PAUSED — user activates manually)
    ad_resp = await client.post(
        f"{META_GRAPH}/{ad_account_id}/ads",
        data={
            "name": f"Static ad – suggestion {suggestion_id}",
            "adset_id": adset_id,
            "creative": json.dumps({"creative_id": new_creative_id}),
            "status": "PAUSED",
            "access_token": access_token,
        },
    )
    if ad_resp.status_code != 200:
        raise HTTPException(502, f"Meta ad creation failed: {ad_resp.text}")

    new_ad_id = ad_resp.json().get("id")
    if not new_ad_id:
        raise HTTPException(502, "Meta did not return an ad id")

    return new_ad_id


def _suggestion_from_row(row: sqlite3.Row) -> SuggestionResponse:
    return SuggestionResponse(
        id=row["id"],
        source_ad_id=row["source_ad_id"],
        campaign_id=row["campaign_id"],
        adset_id=row["adset_id"],
        components=json.loads(row["components"]),
        deployment_status=row["deployment_status"],
        static_ad_id=row["static_ad_id"],
        created_at=row["created_at"],
    )


@app.get("/api/suggestions", response_model=list[SuggestionResponse])
def list_suggestions(
    campaign_id: str = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    """List stored suggestions for the current user, optionally filtered by campaign_id."""
    db = get_db()
    if campaign_id:
        rows = db.execute(
            """
            SELECT * FROM suggested_configurations
            WHERE user_id = ? AND campaign_id = ?
            ORDER BY created_at DESC
            """,
            (user_id, campaign_id),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM suggested_configurations WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    db.close()
    return [_suggestion_from_row(r) for r in rows]


@app.post("/api/suggestions", response_model=SuggestionResponse, status_code=201)
def store_suggestion(
    body: StoreSuggestionRequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    Store a suggested configuration for a dynamic ad (e.g. returned by an external
    suggestion API). The suggestion starts in 'suggested' status and is linked to the
    source dynamic template via source_ad_id.
    """
    _, ad_account_id = _meta_creds(user_id)
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO suggested_configurations
            (user_id, ad_account_id, campaign_id, adset_id, source_ad_id, components)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            user_id, ad_account_id, body.campaign_id, body.adset_id,
            body.source_ad_id, json.dumps(body.components),
        ),
    )
    row_id = cur.lastrowid
    db.commit()
    row = db.execute(
        "SELECT * FROM suggested_configurations WHERE id = ?", (row_id,)
    ).fetchone()
    db.close()
    return _suggestion_from_row(row)


@app.post(
    "/api/suggestions/{suggestion_id}/confirm",
    response_model=SuggestionResponse,
)
async def confirm_suggestion(
    suggestion_id: int,
    body: ConfirmSuggestionRequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    Confirm a suggested configuration.

    action="create":
      - Calls _clone_dynamic_to_static_ad to create a new static ad in Meta.
      - Persists the returned static_ad_id.
      - Transitions deployment_status → "created_static".
      - If Meta call fails, deployment_status is NOT changed.

    action="replace":
      - Validated but Meta deployment not yet implemented.
      - Transitions deployment_status → "pending_confirmation".
      - Optionally pre-links static_ad_id (for manual / test use).
    """
    db = get_db()
    row = db.execute(
        "SELECT * FROM suggested_configurations WHERE id = ? AND user_id = ?",
        (suggestion_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Suggestion not found")
    if row["deployment_status"] not in ("suggested", "pending_confirmation"):
        raise HTTPException(
            409,
            f"Cannot confirm suggestion in status '{row['deployment_status']}'",
        )
    if body.action not in ("create", "replace"):
        raise HTTPException(400, "action must be 'create' or 'replace'")

    if body.action == "create":
        access_token, ad_account_id = _meta_creds(user_id)
        # Meta call happens outside the DB transaction — if it raises, we never
        # reach the UPDATE so the stored status is preserved.
        async with httpx.AsyncClient(timeout=30) as client:
            new_ad_id = await _clone_dynamic_to_static_ad(
                client=client,
                access_token=access_token,
                ad_account_id=ad_account_id,
                adset_id=row["adset_id"],
                source_ad_id=row["source_ad_id"],
                components=json.loads(row["components"]),
                suggestion_id=suggestion_id,
            )
        db.execute(
            """
            UPDATE suggested_configurations
            SET deployment_status = 'created_static',
                static_ad_id      = ?,
                updated_at        = datetime('now')
            WHERE id = ?
            """,
            (new_ad_id, suggestion_id),
        )
    else:
        # action == "replace": hold at pending_confirmation until replacement
        # semantics are implemented in a future phase.
        db.execute(
            """
            UPDATE suggested_configurations
            SET deployment_status = 'pending_confirmation',
                static_ad_id      = COALESCE(?, static_ad_id),
                updated_at        = datetime('now')
            WHERE id = ?
            """,
            (body.static_ad_id, suggestion_id),
        )

    db.commit()
    row = db.execute(
        "SELECT * FROM suggested_configurations WHERE id = ?", (suggestion_id,)
    ).fetchone()
    db.close()
    return _suggestion_from_row(row)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

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

import asyncio
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
from pydantic import BaseModel, Field

# ── Load .env ──────────────────────────────────────────────────────────────────
load_dotenv()
_app_env = os.getenv("APP_ENV", "")
if _app_env:
    load_dotenv(Path(__file__).parent / f".env.{_app_env}", override=True)

from providers.factory import get_meta_provider, get_google_provider  # noqa: E402 – must follow load_dotenv
from providers.mask_policy import MaskPolicy  # noqa: E402 – must follow load_dotenv
import google_ads_api  # noqa: E402 – must follow load_dotenv
meta_provider = get_meta_provider()
google_provider = get_google_provider()
APP_MODE = os.getenv("APP_MODE", "live").lower()

META_APP_ID = os.environ["META_APP_ID"]
META_APP_SECRET = os.environ["META_APP_SECRET"]
META_REDIRECT_URI = os.environ["META_REDIRECT_URI"]
META_API_VERSION = os.getenv("META_API_VERSION", "v19.0")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
GOOGLE_DEVELOPER_TOKEN = os.getenv("GOOGLE_DEVELOPER_TOKEN", "")
GOOGLE_ADS_API_VERSION = os.getenv("GOOGLE_ADS_API_VERSION", "")
JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24

_FAKE_META_BASE_URL = os.getenv("FAKE_META_BASE_URL", "")
META_GRAPH = _FAKE_META_BASE_URL if _FAKE_META_BASE_URL else f"https://graph.facebook.com/{META_API_VERSION}"

DB_PATH = Path(__file__).parent / os.getenv("DATABASE_PATH", "app.db")

MIN_CONVERGENCE_IMPRESSIONS = int(os.getenv("MIN_CONVERGENCE_IMPRESSIONS", "500"))
MIN_CONVERGENCE_DAYS = int(os.getenv("MIN_CONVERGENCE_DAYS", "3"))

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
        CREATE TABLE IF NOT EXISTS dynamic_generation_jobs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL REFERENCES users(id),
            campaign_id      TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'running',
            ad_id            TEXT,
            images_generated INTEGER,
            error            TEXT,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at     TEXT
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
        CREATE TABLE IF NOT EXISTS google_connections (
            user_id             INTEGER PRIMARY KEY REFERENCES users(id),
            customer_id         TEXT NOT NULL,
            login_customer_id   TEXT,
            refresh_token       TEXT NOT NULL,
            customer_name       TEXT,
            connected_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS google_pending_connections (
            key          TEXT PRIMARY KEY,
            user_id      INTEGER NOT NULL,
            refresh_token TEXT NOT NULL,
            accounts_json TEXT NOT NULL,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS bo_selections (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            seed_ad_id              TEXT NOT NULL,
            text_source_id          TEXT NOT NULL,
            pick_rank               INTEGER NOT NULL,
            combination_key         TEXT NOT NULL,
            combination             TEXT NOT NULL,
            selection_type          TEXT NOT NULL,
            ei_score                REAL,
            gpr_mean                REAL,
            gpr_std                 REAL,
            google_ad_resource_name TEXT,
            created_at              TEXT NOT NULL DEFAULT (datetime('now'))
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
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN tier TEXT NOT NULL DEFAULT 'free'"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE dynamic_generation_jobs ADD COLUMN images_generated INTEGER"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE dynamic_generation_jobs ADD COLUMN seed_ad_id TEXT"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE dynamic_generation_jobs ADD COLUMN adset_id TEXT"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE dynamic_generation_jobs ADD COLUMN meta_ad_id TEXT"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE ad_insights ADD COLUMN platform TEXT NOT NULL DEFAULT 'meta'"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE ad_creative_structures ADD COLUMN platform TEXT NOT NULL DEFAULT 'meta'"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE oauth_states ADD COLUMN provider TEXT NOT NULL DEFAULT 'meta'"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE bo_selections ADD COLUMN google_ad_resource_name TEXT"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE ad_creative_structures ADD COLUMN is_pushed_clone INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    for _col in [
        "platform_ad_numeric_id TEXT",
        "current_impressions INTEGER NOT NULL DEFAULT 0",
        "days_running INTEGER NOT NULL DEFAULT 0",
    ]:
        try:
            conn.execute(f"ALTER TABLE pushed_ad_combos ADD COLUMN {_col}")
            conn.commit()
        except Exception:
            pass  # Column already exists
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pushed_ad_combos (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id                 INTEGER NOT NULL,
            platform                TEXT NOT NULL,
            seed_ad_id              TEXT NOT NULL,
            ad_name                 TEXT NOT NULL,
            combination_key         TEXT NOT NULL,
            combination             TEXT NOT NULL,
            platform_ad_id          TEXT,
            platform_ad_numeric_id  TEXT,
            push_status             TEXT NOT NULL DEFAULT 'paused',
            converged               INTEGER NOT NULL DEFAULT 0,
            converged_at            TEXT,
            convergence_metric      REAL,
            current_impressions     INTEGER NOT NULL DEFAULT 0,
            days_running            INTEGER NOT NULL DEFAULT 0,
            pushed_at               TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, platform, seed_ad_id, combination_key)
        )
        """
    )
    conn.commit()
    conn.close()
    # Ensure tables for modules not yet wired into HTTP routes (needed by BO)
    from ad_generation.storage import ensure_tables as _ensure_ad_tables
    _ensure_ad_tables()
    from ad_combination_embeddings.storage import ensure_table as _ensure_combo_table
    _ensure_combo_table()
    from bo_pipeline.storage import ensure_scored_observations_table as _ensure_scored_obs
    _ensure_scored_obs()


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

_AD_IMAGES_DIR = Path(__file__).parent / "ad_images"
_AD_IMAGES_DIR.mkdir(exist_ok=True)
app.mount("/ad-images", StaticFiles(directory=str(_AD_IMAGES_DIR)), name="ad_images")


_PLACEMENT_RATIOS: dict[tuple[str, str], tuple[int, int]] = {
    ("facebook", "feed"):              (1, 1),
    ("facebook", "right_hand_column"): (191, 100),
    ("facebook", "story"):             (9, 16),
    ("facebook", "reels"):             (9, 16),
    ("facebook", "video_feeds"):       (16, 9),
    ("facebook", "marketplace"):       (1, 1),
    ("facebook", "instant_article"):   (191, 100),
    ("instagram", "stream"):           (1, 1),
    ("instagram", "story"):            (9, 16),
    ("instagram", "explore"):          (1, 1),
    ("instagram", "reels"):            (9, 16),
}

_PLACEMENT_LABELS: dict[tuple[str, str], str] = {
    ("facebook", "feed"):              "FB Feed",
    ("facebook", "right_hand_column"): "FB Right Column",
    ("facebook", "story"):             "FB Story",
    ("facebook", "reels"):             "FB Reels",
    ("facebook", "video_feeds"):       "FB Video",
    ("facebook", "marketplace"):       "FB Marketplace",
    ("facebook", "instant_article"):   "FB Article",
    ("instagram", "stream"):           "IG Feed",
    ("instagram", "story"):            "IG Story",
    ("instagram", "explore"):          "IG Explore",
    ("instagram", "reels"):            "IG Reels",
}


def _parse_placements(targeting: dict) -> list[dict]:
    """Convert a Meta targeting dict into a deduplicated list of placement descriptors."""
    seen_ratios: set[tuple[int, int]] = set()
    result = []
    for platform in targeting.get("publisher_platforms", []):
        if platform == "facebook":
            positions = targeting.get("facebook_positions", ["feed"])
        elif platform == "instagram":
            positions = targeting.get("instagram_positions", ["stream"])
        else:
            continue
        for pos in positions:
            key = (platform, pos)
            ratio = _PLACEMENT_RATIOS.get(key)
            if ratio is None:
                continue
            if ratio not in seen_ratios:
                seen_ratios.add(ratio)
                result.append({
                    "platform": platform,
                    "position": pos,
                    "label": _PLACEMENT_LABELS.get(key, f"{platform} {pos}"),
                    "ratio_w": ratio[0],
                    "ratio_h": ratio[1],
                })
    return result


def _image_dimensions(image_url: str) -> tuple[int | None, int | None]:
    """Return (width, height) for a local image URL, or (None, None) if unresolvable."""
    if not image_url:
        return None, None
    try:
        if "/ad-images/" in image_url:
            filename = image_url.split("/ad-images/")[-1]
            path = _AD_IMAGES_DIR / filename
        elif "/images/" in image_url:
            filename = image_url.split("/images/")[-1]
            path = _GENERATED_IMAGES_DIR / filename
        else:
            return None, None
        if not path.exists():
            return None, None
        # Read PNG dimensions from IHDR chunk (bytes 16–24) without PIL dependency
        import struct
        with open(path, "rb") as f:
            header = f.read(24)
        if header[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", header[16:24])
            return w, h
        # JPEG: scan for SOF marker
        with open(path, "rb") as f:
            data = f.read()
        i = 0
        while i < len(data) - 9:
            if data[i] == 0xFF and data[i + 1] in (0xC0, 0xC1, 0xC2):
                h = (data[i + 5] << 8) | data[i + 6]
                w = (data[i + 7] << 8) | data[i + 8]
                return w, h
            i += 1
        return None, None
    except Exception:
        return None, None


def _resolve_combination_image_url(combination: dict) -> dict:
    """Replace expired CDN image URLs with local URLs; add image dimensions."""
    image_url = combination.get("image_url")
    if not image_url:
        return combination
    out = dict(combination)
    if "localhost" not in image_url:
        filename = image_url.split("?")[0].rsplit("/", 1)[-1]
        if (_AD_IMAGES_DIR / filename).exists():
            backend_base = os.getenv("IMAGES_SERVE_BASE_URL", "http://localhost:8000/images").replace("/images", "")
            out["image_url"] = f"{backend_base}/ad-images/{filename}"
    w, h = _image_dimensions(out["image_url"])
    if w and h:
        out["image_width"] = w
        out["image_height"] = h
    return out


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


class UserInfo(BaseModel):
    email: str
    tier: str


class MetaStatus(BaseModel):
    connected: bool
    ad_account_id: str | None = None


class GoogleStatus(BaseModel):
    connected: bool
    customer_id: str | None = None
    customer_name: str | None = None


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


class LocalAdSlot(BaseModel):
    slot: str
    slot_index: int
    value: str | None = None


class LocalAd(BaseModel):
    ad_id: str
    campaign_id: str
    adset_id: str
    creative_type: str
    lifecycle_status: str
    data_source: str
    ingested_at: str
    headline: str | None = None
    image_url: str | None = None
    slots: list[LocalAdSlot] = []


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
    if len(body.password.encode()) > 72:
        raise HTTPException(400, "Password must be 72 characters or fewer")
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
    try:
        password_ok = row and pwd_ctx.verify(body.password, row["pw_hash"])
    except ValueError:
        # bcrypt rejects passwords > 72 bytes — treat as wrong password
        password_ok = False
    if not password_ok:
        raise HTTPException(401, "Invalid email or password")
    return TokenResponse(token=create_token(row["id"]))


# ── User info ─────────────────────────────────────────────────────────────────
@app.get("/me", response_model=UserInfo)
def get_me(user_id: int = Depends(get_current_user_id)):
    db = get_db()
    row = db.execute("SELECT email, tier FROM users WHERE id = ?", (user_id,)).fetchone()
    db.close()
    return UserInfo(email=row["email"], tier=row["tier"])


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


# ── Google connection routes ───────────────────────────────────────────────────
@app.get("/me/google-status", response_model=GoogleStatus)
def google_status(user_id: int = Depends(get_current_user_id)):
    db = get_db()
    row = db.execute(
        "SELECT customer_id, customer_name FROM google_connections WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    db.close()
    if row:
        return GoogleStatus(connected=True, customer_id=row["customer_id"], customer_name=row["customer_name"])
    return GoogleStatus(connected=False)


@app.get("/auth/google/login-url", response_model=LoginUrlResponse)
def google_login_url(user_id: int = Depends(get_current_user_id)):
    state = secrets.token_urlsafe(32)
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO oauth_states (state, user_id, provider) VALUES (?, ?, 'google')",
        (state, user_id),
    )
    db.commit()
    db.close()
    scopes = "https://www.googleapis.com/auth/adwords"
    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={GOOGLE_REDIRECT_URI}"
        f"&scope={scopes}"
        f"&state={state}"
        f"&response_type=code"
        f"&access_type=offline"
        f"&prompt=consent"
    )
    return LoginUrlResponse(url=url)


@app.get("/auth/google/callback")
async def google_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    if error or not code or not state:
        msg = error or "Google login failed or was cancelled"
        return RedirectResponse(f"{FRONTEND_URL}/app/settings?google_error={msg}")

    db = get_db()
    row = db.execute(
        "SELECT user_id FROM oauth_states WHERE state = ? AND provider = 'google'",
        (state,),
    ).fetchone()
    if not row:
        raise HTTPException(400, "Invalid or expired OAuth state")
    user_id = row["user_id"]
    db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    db.commit()

    try:
        token_data = await google_ads_api.exchange_code_for_tokens(code, GOOGLE_REDIRECT_URI)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))

    refresh_token: str = token_data.get("refresh_token", "")
    access_token: str = token_data.get("access_token", "")
    if not refresh_token:
        raise HTTPException(400, "No refresh token returned — re-authorize with offline access.")

    try:
        resource_names = await google_ads_api.list_accessible_customers(
            access_token, GOOGLE_DEVELOPER_TOKEN, GOOGLE_ADS_API_VERSION
        )
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))

    if not resource_names:
        raise HTTPException(400, "No Google Ads accounts found for this Google account.")

    customer_ids = [r.split("/")[-1] for r in resource_names]
    names = await asyncio.gather(
        *[
            google_ads_api.get_customer_name(cid, access_token, GOOGLE_DEVELOPER_TOKEN, GOOGLE_ADS_API_VERSION)
            for cid in customer_ids
        ]
    )
    accounts = [
        {"customer_id": cid, "name": name or cid}
        for cid, name in zip(customer_ids, names)
    ]

    pending_key = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO google_pending_connections (key, user_id, refresh_token, accounts_json) VALUES (?, ?, ?, ?)",
        (pending_key, user_id, refresh_token, json.dumps(accounts)),
    )
    db.commit()
    db.close()

    return RedirectResponse(f"{FRONTEND_URL}/app/settings?google_pick={pending_key}")


@app.get("/auth/google/pending/{key}")
async def google_pending_accounts(key: str, user_id: int = Depends(get_current_user_id)):
    db = get_db()
    row = db.execute(
        "SELECT user_id, accounts_json FROM google_pending_connections WHERE key = ?", (key,)
    ).fetchone()
    db.close()
    if not row or row["user_id"] != user_id:
        raise HTTPException(404, "Pending connection not found or expired.")
    return {"accounts": json.loads(row["accounts_json"])}


class SelectAccountRequest(BaseModel):
    key: str
    customer_id: str
    login_customer_id: str | None = None


@app.post("/auth/google/select-account")
async def google_select_account(body: SelectAccountRequest, user_id: int = Depends(get_current_user_id)):
    db = get_db()
    row = db.execute(
        "SELECT user_id, refresh_token, accounts_json FROM google_pending_connections WHERE key = ?",
        (body.key,),
    ).fetchone()
    if not row or row["user_id"] != user_id:
        raise HTTPException(404, "Pending connection not found or expired.")

    clean_id = body.customer_id.replace("-", "").strip()
    clean_login = body.login_customer_id.replace("-", "").strip() if body.login_customer_id else None

    accounts = json.loads(row["accounts_json"])
    match = next((a for a in accounts if a["customer_id"] == clean_id), None)
    name = match["name"] if match else clean_id

    db.execute(
        "INSERT OR REPLACE INTO google_connections (user_id, customer_id, login_customer_id, refresh_token, customer_name) VALUES (?, ?, ?, ?, ?)",
        (user_id, clean_id, clean_login, row["refresh_token"], name),
    )
    db.execute("DELETE FROM google_pending_connections WHERE key = ?", (body.key,))
    db.commit()
    db.close()
    return {"success": True}


# ── Helper: load user's Google creds ──────────────────────────────────────────
async def _google_creds(user_id: int) -> tuple[str, str, str | None]:
    """Return (access_token, customer_id, login_customer_id) or raise 400. Always refreshes."""
    db = get_db()
    row = db.execute(
        "SELECT refresh_token, customer_id, login_customer_id FROM google_connections WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    db.close()
    if not row:
        raise HTTPException(400, "Google Ads account not connected")
    access_token = await google_ads_api.refresh_access_token(row["refresh_token"])
    return access_token, row["customer_id"], row["login_customer_id"]


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


# ── Google campaigns ───────────────────────────────────────────────────────────
@app.get("/api/google/campaigns", response_model=list[Campaign])
async def list_google_campaigns(user_id: int = Depends(get_current_user_id)):
    access_token, customer_id, login_customer_id = await _google_creds(user_id)
    async with httpx.AsyncClient(timeout=30) as client:
        campaigns_raw, metrics_by_campaign, _ = await google_provider.fetch_campaigns_and_insights(
            client, access_token, customer_id, login_customer_id=login_customer_id
        )
    return [
        Campaign(
            id=c["id"],
            name=c.get("name", ""),
            status=c.get("status", ""),
            daily_budget=c.get("daily_budget"),
            spend_7d=metrics_by_campaign.get(c["id"], {}).get("spend"),
            impressions_7d=metrics_by_campaign.get(c["id"], {}).get("impressions"),
            clicks_7d=metrics_by_campaign.get(c["id"], {}).get("clicks"),
            ctr_7d=metrics_by_campaign.get(c["id"], {}).get("ctr"),
            cpm_7d=metrics_by_campaign.get(c["id"], {}).get("cpm"),
        )
        for c in campaigns_raw
    ]


# ── Google structural ingest ──────────────────────────────────────────────────

@app.post("/api/google/ingest/structure/{campaign_id}", response_model=StructureIngestResult)
async def ingest_google_campaign_structure(
    campaign_id: str,
    user_id: int = Depends(get_current_user_id),
):
    """Fetch Google campaign → ad group → ad → creative structure and persist to
    ad_creative_structures with platform='google'. Idempotent: existing rows for the
    same (user_id, ad_id, slot, slot_index) are replaced."""
    access_token, customer_id, login_customer_id = await _google_creds(user_id)

    async with httpx.AsyncClient(timeout=30) as client:
        _adsets, ads = await google_provider.fetch_campaign_structure(
            client, access_token, customer_id, campaign_id,
            login_customer_id=login_customer_id,
        )

    db = get_db()
    existing_ad_ids: set[str] = set(
        row[0]
        for row in db.execute(
            "SELECT DISTINCT ad_id FROM ad_creative_structures "
            "WHERE user_id = ? AND campaign_id = ? AND platform = 'google'",
            (user_id, campaign_id),
        ).fetchall()
    )

    ingested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ads_processed = 0
    components_saved = 0
    current_ad_ids: set[str] = set()
    _ads_for_embedding: list[tuple[str, str, list[dict]]] = []

    for ad in ads:
        ad_id = ad["id"]
        current_ad_ids.add(ad_id)
        adset_id = ad.get("adset_id", "")

        raw_status = ad.get("effective_status") or ad.get("status", "")
        lifecycle_status = "active" if raw_status in ("ENABLED", "ACTIVE") else "inactive"

        creative_type, components = google_provider.normalize_creative(ad)
        _ads_for_embedding.append((ad_id, creative_type, components))

        for comp in components:
            db.execute(
                """
                INSERT INTO ad_creative_structures
                    (user_id, ad_account_id, campaign_id, adset_id, ad_id,
                     creative_type, slot, slot_index, value, ingested_at,
                     lifecycle_status, data_source, mask_profile, platform)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'real', NULL, 'google')
                ON CONFLICT (user_id, ad_id, slot, slot_index)
                DO UPDATE SET
                    creative_type    = excluded.creative_type,
                    value            = excluded.value,
                    ingested_at      = excluded.ingested_at,
                    lifecycle_status = excluded.lifecycle_status,
                    platform         = 'google'
                """,
                (
                    user_id, customer_id, campaign_id, adset_id, ad_id,
                    creative_type, comp["slot"], comp["slot_index"],
                    comp["value"], ingested_at, lifecycle_status,
                ),
            )
            components_saved += 1

        ads_processed += 1

    missing_ad_ids = existing_ad_ids - current_ad_ids
    if missing_ad_ids:
        placeholders = ",".join("?" * len(missing_ad_ids))
        db.execute(
            f"UPDATE ad_creative_structures SET lifecycle_status = 'missing' "
            f"WHERE user_id = ? AND campaign_id = ? AND platform = 'google' "
            f"AND ad_id IN ({placeholders})",
            (user_id, campaign_id, *missing_ad_ids),
        )

    # ── Google clone detection ────────────────────────────────────────────────
    # Match on platform_ad_numeric_id (last segment of resource name) because
    # pushed_ad_combos.platform_ad_id is a full resource name string which does
    # not equal the numeric ad_id stored in ad_creative_structures.
    if current_ad_ids:
        pushed_rows_g = db.execute(
            "SELECT platform_ad_numeric_id FROM pushed_ad_combos "
            "WHERE user_id = ? AND platform = 'google' AND platform_ad_numeric_id IS NOT NULL",
            (user_id,),
        ).fetchall()
        pushed_numeric_ids = {r["platform_ad_numeric_id"] for r in pushed_rows_g if r["platform_ad_numeric_id"]}
        clone_ids_g = current_ad_ids & pushed_numeric_ids
        if clone_ids_g:
            placeholders = ",".join("?" * len(clone_ids_g))
            db.execute(
                f"UPDATE ad_creative_structures SET is_pushed_clone = 1 "
                f"WHERE user_id = ? AND platform = 'google' AND ad_id IN ({placeholders})",
                (user_id, *clone_ids_g),
            )

    db.commit()
    db.close()

    # ── Convergence check (fire-and-forget) ───────────────────────────────────
    asyncio.create_task(_check_google_convergence(
        user_id, campaign_id, access_token, customer_id, login_customer_id
    ))

    # ── Embedding hook (fire-and-forget) ──────────────────────────────────────
    # RSA and video/display get text-only embeddings via embed_ad (image_url=None
    # is handled gracefully — zeros in image slot of combined vector).
    # Combinations use headline × description slots for all Google creative types.
    # Per-image embeddings (embed_images) are deferred until URL resolution for
    # display/video assets is implemented.
    _GOOGLE_COMBO_SLOTS = ("headline", "description")
    try:
        from embeddings.pipeline import embed_ad
        from ad_combination_embeddings.pipeline import embed_all_combinations
        for _ad_id, _creative_type, _comps in _ads_for_embedding:
            asyncio.create_task(embed_ad(user_id, _ad_id, campaign_id, _comps))
            asyncio.create_task(
                embed_all_combinations(source_id=_ad_id, components=_comps, slots=_GOOGLE_COMBO_SLOTS)
            )
    except Exception:
        logger.warning("google ingest: embedding hook unavailable — skipping", exc_info=True)

    return StructureIngestResult(
        campaign_id=campaign_id,
        ads_processed=ads_processed,
        components_saved=components_saved,
    )


@app.get("/api/google/structure/{campaign_id}", response_model=list[AdStructure])
def get_google_campaign_structure(
    campaign_id: str,
    user_id: int = Depends(get_current_user_id),
):
    """Return persisted Google creative structures for a campaign, grouped by ad."""
    db = get_db()
    rows = db.execute(
        """
        SELECT ad_id, adset_id, creative_type, slot, slot_index, value, lifecycle_status
        FROM ad_creative_structures
        WHERE user_id = ? AND campaign_id = ? AND platform = 'google'
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
                "adset_id": row["adset_id"] or "",
                "campaign_id": campaign_id,
                "creative_type": row["creative_type"],
                "lifecycle_status": row["lifecycle_status"],
                "components": {},
            }
        ads_map[ad_id]["components"].setdefault(row["slot"], []).append(row["value"])

    return list(ads_map.values())


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
    return meta_provider.normalize_creative(ad)


async def _fetch_campaign_structure(
    client: httpx.AsyncClient,
    access_token: str,
    ad_account_id: str,
    campaign_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return await meta_provider.fetch_campaign_structure(client, access_token, ad_account_id, campaign_id)


async def _download_ad_images(components: list[dict]) -> list[dict]:
    """
    For each image-slot component with a Meta CDN URL, download the image to
    ad_images/ and return a new components list with local serve URLs substituted.
    Skips download if the file already exists (idempotent).
    """
    from urllib.parse import urlparse as _urlparse

    _backend_base = os.getenv("IMAGES_SERVE_BASE_URL", "http://localhost:8000/images").replace("/images", "")

    result = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for comp in components:
            if comp.get("slot") == "image" and comp.get("value", "").startswith("http"):
                url = comp["value"]
                filename = Path(_urlparse(url).path).name
                dest = _AD_IMAGES_DIR / filename
                if not dest.exists():
                    try:
                        r = await client.get(url)
                        r.raise_for_status()
                        dest.write_bytes(r.content)
                    except Exception:
                        logger.warning("Failed to download ad image %s", url, exc_info=True)
                        result.append(comp)
                        continue
                local_url = f"{_backend_base}/ad-images/{filename}"
                result.append({**comp, "value": local_url})
            else:
                result.append(comp)
    return result


def _write_convergence_observation(
    db: sqlite3.Connection,
    user_id: int,
    seed_ad_id: str,
    combination_key: str,
    combination_json: str,
    score: float,
    metric: str,
) -> None:
    """Write a converged real-metric score to scored_observations."""
    tce = db.execute(
        "SELECT vector FROM ad_text_combination_embeddings WHERE source_id=? AND combination_key=?",
        (seed_ad_id, combination_key),
    ).fetchone()
    text_vec_bytes = tce["vector"] if tce else None

    img_row = db.execute(
        "SELECT vector FROM ad_image_embeddings WHERE user_id=? AND ad_id=? ORDER BY slot_index LIMIT 1",
        (user_id, seed_ad_id),
    ).fetchone()
    image_vec_bytes = img_row["vector"] if img_row else None
    if not image_vec_bytes:
        emb_row = db.execute(
            "SELECT image_vector FROM ad_embeddings WHERE ad_id=? AND user_id=?",
            (seed_ad_id, user_id),
        ).fetchone()
        image_vec_bytes = emb_row["image_vector"] if emb_row else None

    try:
        db.execute(
            """INSERT OR REPLACE INTO scored_observations
               (user_id, seed_ad_id, combination_key, combination,
                score, metric, source, text_vector, image_vector)
               VALUES (?, ?, ?, ?, ?, ?, 'convergence', ?, ?)""",
            (user_id, seed_ad_id, combination_key, combination_json,
             score, metric, text_vec_bytes, image_vec_bytes),
        )
    except Exception as exc:
        logger.warning("_write_convergence_observation failed for %s: %s", seed_ad_id, exc)


async def _check_meta_convergence(
    user_id: int, campaign_id: str, access_token: str
) -> None:
    """Fetch lifetime impressions for each active, unconverged pushed Meta clone in
    this campaign and flip converged=1 when both thresholds are met."""
    db = get_db()
    rows = db.execute(
        """
        SELECT pac.id, pac.platform_ad_id, pac.pushed_at,
               pac.seed_ad_id, pac.combination_key, pac.combination
        FROM pushed_ad_combos pac
        JOIN ad_creative_structures acs
            ON acs.ad_id = pac.seed_ad_id AND acs.user_id = pac.user_id
        WHERE pac.user_id = ? AND pac.platform = 'meta'
          AND pac.push_status = 'active' AND pac.converged = 0
          AND acs.campaign_id = ?
        GROUP BY pac.id
        """,
        (user_id, campaign_id),
    ).fetchall()
    db.close()

    if not rows:
        return

    async with httpx.AsyncClient(timeout=15) as client:
        for row in rows:
            try:
                resp = await client.get(
                    f"{META_GRAPH}/{row['platform_ad_id']}/insights",
                    params={
                        "access_token": access_token,
                        "fields": "impressions,ctr",
                        "date_preset": "lifetime",
                    },
                )
                if resp.status_code != 200:
                    continue
                data = resp.json().get("data", [])
                if not data:
                    continue

                impressions = int(data[0].get("impressions", 0) or 0)
                # Meta returns CTR as a percentage string (e.g. "4.52"); store as decimal
                ctr = float(data[0].get("ctr", 0) or 0) / 100

                try:
                    pushed_at = datetime.strptime(row["pushed_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    pushed_at = datetime.now(timezone.utc)
                days_running = max(0, (datetime.now(timezone.utc) - pushed_at).days)

                db = get_db()
                db.execute(
                    "UPDATE pushed_ad_combos SET current_impressions = ?, days_running = ? WHERE id = ?",
                    (impressions, days_running, row["id"]),
                )
                if impressions >= MIN_CONVERGENCE_IMPRESSIONS and days_running >= MIN_CONVERGENCE_DAYS:
                    db.execute(
                        "UPDATE pushed_ad_combos SET converged = 1, converged_at = datetime('now'), "
                        "convergence_metric = ? WHERE id = ?",
                        (ctr, row["id"]),
                    )
                    logger.info(
                        "convergence: Meta ad=%s converged (%d impr, %d days, ctr=%.4f)",
                        row["platform_ad_id"], impressions, days_running, ctr,
                    )
                    _write_convergence_observation(
                        db, user_id,
                        row["seed_ad_id"], row["combination_key"], row["combination"],
                        ctr, "ctr",
                    )
                db.commit()
                db.close()
            except Exception as exc:
                logger.warning("convergence check failed for Meta ad=%s: %s", row["platform_ad_id"], exc)


async def _check_google_convergence(
    user_id: int,
    campaign_id: str,
    access_token: str,
    customer_id: str,
    login_customer_id: str | None,
) -> None:
    """Fetch aggregate impressions for active, unconverged pushed Google RSA clones
    in this campaign and flip converged=1 when both thresholds are met."""
    from google_ads_api import run_gaql as _run_gaql

    db = get_db()
    rows = db.execute(
        """
        SELECT pac.id, pac.platform_ad_id, pac.pushed_at,
               pac.seed_ad_id, pac.combination_key, pac.combination
        FROM pushed_ad_combos pac
        JOIN ad_creative_structures acs
            ON acs.ad_id = pac.seed_ad_id AND acs.user_id = pac.user_id
        WHERE pac.user_id = ? AND pac.platform = 'google'
          AND pac.push_status = 'active' AND pac.converged = 0
          AND acs.campaign_id = ?
        GROUP BY pac.id
        """,
        (user_id, campaign_id),
    ).fetchall()
    db.close()

    if not rows:
        return

    resource_name_map = {r["platform_ad_id"]: r for r in rows if r["platform_ad_id"]}
    if not resource_name_map:
        return

    gaql = f"""
        SELECT ad_group_ad.resource_name, metrics.impressions, metrics.ctr
        FROM ad_group_ad
        WHERE campaign.id = '{campaign_id}'
    """
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            ad_rows = await _run_gaql(
                client=client,
                gaql=gaql,
                api_version=GOOGLE_ADS_API_VERSION,
                customer_id=customer_id,
                access_token=access_token,
                developer_token=GOOGLE_DEVELOPER_TOKEN,
                login_customer_id=login_customer_id,
            )
        except Exception as exc:
            logger.warning("google convergence: GAQL failed: %s", exc)
            return

    metrics_by_resource: dict[str, dict] = {}
    for ar in ad_rows:
        rn = (ar.get("adGroupAd") or {}).get("resourceName", "")
        if rn and ar.get("metrics"):
            metrics_by_resource[rn] = ar["metrics"]

    for resource_name, row in resource_name_map.items():
        m = metrics_by_resource.get(resource_name)
        if not m:
            continue

        impressions = int(m.get("impressions", 0) or 0)
        # Google returns CTR as decimal fraction (e.g. 0.045 = 4.5%); store as-is
        ctr = float(m.get("ctr", 0) or 0)

        try:
            pushed_at = datetime.strptime(row["pushed_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            pushed_at = datetime.now(timezone.utc)
        days_running = max(0, (datetime.now(timezone.utc) - pushed_at).days)

        db = get_db()
        db.execute(
            "UPDATE pushed_ad_combos SET current_impressions = ?, days_running = ? WHERE id = ?",
            (impressions, days_running, row["id"]),
        )
        if impressions >= MIN_CONVERGENCE_IMPRESSIONS and days_running >= MIN_CONVERGENCE_DAYS:
            db.execute(
                "UPDATE pushed_ad_combos SET converged = 1, converged_at = datetime('now'), "
                "convergence_metric = ? WHERE id = ?",
                (ctr, row["id"]),
            )
            logger.info(
                "convergence: Google ad=%s converged (%d impr, %d days, ctr=%.4f)",
                resource_name, impressions, days_running, ctr,
            )
            _write_convergence_observation(
                db, user_id,
                row["seed_ad_id"], row["combination_key"], row["combination"],
                ctr, "ctr",
            )
        db.commit()
        db.close()


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

    # Build adset_id → placements map from targeting data
    adset_placements: dict[str, list[dict]] = {}
    for adset in _adsets:
        targeting = adset.get("targeting") or {}
        parsed = _parse_placements(targeting)
        if parsed:
            adset_placements[adset["id"]] = parsed

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

        # Store placement data for this ad if available from its adset's targeting
        if adset_id in adset_placements:
            db.execute(
                """
                INSERT INTO ad_creative_structures
                    (user_id, ad_account_id, campaign_id, adset_id, ad_id,
                     creative_type, slot, slot_index, value, ingested_at,
                     lifecycle_status, data_source, mask_profile)
                VALUES (?, ?, ?, ?, ?, ?, '_placements', 0, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id, ad_id, slot, slot_index)
                DO UPDATE SET value = excluded.value, ingested_at = excluded.ingested_at
                """,
                (
                    user_id, ad_account_id, campaign_id, adset_id, ad_id,
                    creative_type, json.dumps(adset_placements[adset_id]),
                    ingested_at, lifecycle_status, data_source, mask_profile,
                ),
            )

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

    # ── Clone detection: mark pushed clones so they're excluded from BO seeds ──
    if current_ad_ids:
        pushed_rows = db.execute(
            "SELECT platform_ad_id FROM pushed_ad_combos WHERE user_id = ? AND platform = 'meta'",
            (user_id,),
        ).fetchall()
        pushed_platform_ids = {r["platform_ad_id"] for r in pushed_rows if r["platform_ad_id"]}
        clone_ids = current_ad_ids & pushed_platform_ids
        if clone_ids:
            placeholders = ",".join("?" * len(clone_ids))
            db.execute(
                f"UPDATE ad_creative_structures SET is_pushed_clone = 1 "
                f"WHERE user_id = ? AND ad_id IN ({placeholders})",
                (user_id, *clone_ids),
            )

    db.commit()
    db.close()

    # ── Convergence check (fire-and-forget) ───────────────────────────────────
    asyncio.create_task(_check_meta_convergence(user_id, campaign_id, access_token))

    # ── Embedding hook (fire-and-forget) ──────────────────────────────────────
    # embed_ad       → 1 seed embedding per ad (slot[0] text + image[0]) → ad_embeddings
    # embed_all_combinations → N×M×K text combos per ad → ad_text_combination_embeddings
    # source_id = ad_id links both tables back to the ad and its campaign.
    # Failures are logged and swallowed; ingest result is already committed.
    try:
        import sqlite3 as _sqlite3
        from embeddings.pipeline import embed_ad, embed_images, DB_PATH as _EMB_DB
        from embeddings.extractor import extract_fields as _extract_fields, text_as_json as _text_as_json
        from ad_combination_embeddings.pipeline import embed_all_combinations

        for _ad_id, _comps in ad_components_map.items():
            # Download Meta CDN images to local storage while URLs are still fresh
            _local_comps = await _download_ad_images(_comps)

            # Skip embed_ad if text snapshot unchanged and image_vector already exists
            _need_seed_embed = True
            try:
                _snap = _text_as_json(_extract_fields(_local_comps))
                _c = _sqlite3.connect(str(_EMB_DB))
                _row = _c.execute(
                    "SELECT text_snapshot, image_vector, text_vector FROM ad_embeddings WHERE user_id = ? AND ad_id = ?",
                    (user_id, _ad_id),
                ).fetchone()
                _c.close()
                if _row and _row[0] == _snap and _row[1] is not None and _row[2] is not None:
                    _need_seed_embed = False
                    logger.info("embed_ad: skipping ad=%s (unchanged, already embedded)", _ad_id)
            except Exception:
                pass  # if check fails, proceed with embedding

            if _need_seed_embed:
                asyncio.create_task(embed_ad(user_id, _ad_id, campaign_id, _local_comps))
            # embed_images skips slot_indexes that already have vectors
            asyncio.create_task(embed_images(user_id, _ad_id, campaign_id, _local_comps))
            # embed_all_combinations is text-only — no URL expiry concern
            asyncio.create_task(embed_all_combinations(source_id=_ad_id, components=_comps))
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


# ── Local ad library ──────────────────────────────────────────────────────────

@app.get("/api/ads/local", response_model=list[LocalAd])
def list_local_ads(user_id: int = Depends(get_current_user_id)):
    """Return all ads stored locally (ingested + generated), grouped by ad_id."""
    with get_db() as db:
        rows = db.execute(
            """
            SELECT ad_id, campaign_id, adset_id, creative_type,
                   lifecycle_status, data_source, ingested_at,
                   slot, slot_index, value
            FROM ad_creative_structures
            WHERE user_id = ?
            ORDER BY ingested_at DESC, ad_id, slot, slot_index
            """,
            (user_id,),
        ).fetchall()

    ads: dict[str, LocalAd] = {}
    for r in rows:
        ad_id = r["ad_id"]
        if ad_id not in ads:
            ads[ad_id] = LocalAd(
                ad_id=ad_id,
                campaign_id=r["campaign_id"],
                adset_id=r["adset_id"],
                creative_type=r["creative_type"],
                lifecycle_status=r["lifecycle_status"],
                data_source=r["data_source"],
                ingested_at=r["ingested_at"],
            )
        ads[ad_id].slots.append(
            LocalAdSlot(slot=r["slot"], slot_index=r["slot_index"], value=r["value"])
        )
        if r["slot"] == "headline" and r["slot_index"] == 0 and not ads[ad_id].headline:
            ads[ad_id].headline = r["value"]
        if r["slot"] == "image" and r["slot_index"] == 0 and not ads[ad_id].image_url:
            ads[ad_id].image_url = r["value"]

    return list(ads.values())


@app.delete("/api/ads/local/{ad_id}", status_code=204)
def delete_local_ad(ad_id: str, user_id: int = Depends(get_current_user_id)):
    """Delete a locally stored ad and all related embedding rows."""
    with get_db() as db:
        db.execute(
            "DELETE FROM ad_creative_structures WHERE user_id = ? AND ad_id = ?",
            (user_id, ad_id),
        )
        db.execute(
            "DELETE FROM ad_embeddings WHERE user_id = ? AND ad_id = ?",
            (user_id, ad_id),
        )
        db.execute(
            "DELETE FROM ad_image_embeddings WHERE user_id = ? AND ad_id = ?",
            (user_id, ad_id),
        )
        db.execute(
            "DELETE FROM ad_text_combination_embeddings WHERE source_id = ?",
            (ad_id,),
        )
        db.commit()


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

async def _upload_image_to_meta(
    client: httpx.AsyncClient,
    access_token: str,
    ad_account_id: str,
    image_url: str,
) -> str | None:
    """
    Upload a locally-served image to Meta adimages API and return its hash.
    Handles both /images/ (generated) and /ad-images/ (downloaded Meta CDN) paths.
    Returns None if the file can't be found on disk or the upload fails.
    """
    local_path: Path | None = None
    _backend_dir = Path(__file__).parent

    generated_base = os.getenv("IMAGES_SERVE_BASE_URL", "http://localhost:8000/images").rstrip("/")
    if image_url.startswith(generated_base + "/"):
        filename = image_url[len(generated_base) + 1:]
        candidate = _backend_dir / "generated_images" / filename
        if candidate.exists():
            local_path = candidate
    elif image_url.startswith("/images/"):
        # Relative URL stored when IMAGES_SERVE_BASE_URL was not set
        filename = image_url[len("/images/"):]
        candidate = _backend_dir / "generated_images" / filename
        if candidate.exists():
            local_path = candidate
    elif "/ad-images/" in image_url:
        filename = image_url.split("/ad-images/")[-1]
        candidate = _backend_dir / "ad_images" / filename
        if candidate.exists():
            local_path = candidate

    if not local_path:
        logger.warning("_upload_image_to_meta: local file not found for %s", image_url)
        return None

    suffix = local_path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"

    with open(local_path, "rb") as f:
        image_bytes = f.read()

    resp = await client.post(
        f"{META_GRAPH}/{ad_account_id}/adimages",
        data={"access_token": access_token},
        files={"filename": (local_path.name, image_bytes, mime)},
        timeout=60,
    )
    if resp.status_code != 200:
        logger.warning("adimages upload failed (%s): %s", resp.status_code, resp.text)
        return None

    for img_data in resp.json().get("images", {}).values():
        h = img_data.get("hash")
        if h:
            return h

    return None


async def _clone_dynamic_to_static_ad(
    client: httpx.AsyncClient,
    access_token: str,
    ad_account_id: str,
    adset_id: str,
    source_ad_id: str,
    components: dict[str, str],
    suggestion_id: int,
    image_hash: str | None = None,
    ad_name: str | None = None,
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
            "fields": "creative{object_story_spec,asset_feed_spec}",
        },
    )
    if src_resp.status_code != 200:
        raise HTTPException(502, f"Failed to fetch source ad {source_ad_id}: {src_resp.text}")

    src_creative = (src_resp.json().get("creative") or {})
    oss = src_creative.get("object_story_spec") or {}
    page_id = oss.get("page_id")
    if not page_id:
        raise HTTPException(502, "Could not determine page_id from source ad creative")

    # Inherit the destination link URL — static ads use link_data.link,
    # dynamic ads store it in asset_feed_spec.link_urls[0].website_url
    src_link_data = oss.get("link_data") or {}
    link_url = (
        src_link_data.get("link")
        or (src_link_data.get("call_to_action") or {}).get("value", {}).get("link")
    )
    if not link_url:
        link_urls = (src_creative.get("asset_feed_spec") or {}).get("link_urls") or []
        if link_urls:
            link_url = link_urls[0].get("website_url")

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
    if image_hash:
        link_data["image_hash"] = image_hash
    elif components.get("image"):
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
            "name": ad_name or f"Static ad – suggestion {suggestion_id}",
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


# ── Bayesian Optimisation routes ──────────────────────────────────────────────

class BOPick(BaseModel):
    combination_key: str
    combination: dict
    selection_type: str
    ei_score: float | None = None
    gpr_mean: float | None = None
    gpr_std: float | None = None
    placements: list[dict] = []
    # Lifecycle — populated by _enrich_pick after checking pushed_ad_combos
    ad_name: str | None = None
    already_pushed: bool = False
    push_status: str | None = None
    converged: bool = False
    platform_ad_id: str | None = None
    current_impressions: int = 0


class BORunRequest(BaseModel):
    seed_ad_id: str
    text_source_id: str  # usually same as seed_ad_id
    target_metric: str | None = None


class BORunResponse(BaseModel):
    seed_ad_id: str
    text_source_id: str
    picks: list[BOPick]
    scored_count: int
    candidate_count: int
    warning: str | None = None


def _get_placements_for_ad(ad_id: str, user_id: int) -> list[dict]:
    """Return parsed placements for an ad by reading its stored _placements slot."""
    db = get_db()
    try:
        row = db.execute(
            "SELECT value FROM ad_creative_structures WHERE user_id=? AND ad_id=? AND slot='_placements' LIMIT 1",
            (user_id, ad_id),
        ).fetchone()
        if row and row["value"]:
            return json.loads(row["value"])
    except Exception:
        pass
    finally:
        db.close()
    return []


def _enrich_pick(p: dict, seed_ad_id: str, user_id: int) -> BOPick:
    combo = _resolve_combination_image_url(p["combination"])
    placements = _get_placements_for_ad(seed_ad_id, user_id)
    db = get_db()
    pushed_row = db.execute(
        "SELECT ad_name, push_status, converged, platform_ad_id, current_impressions "
        "FROM pushed_ad_combos "
        "WHERE user_id = ? AND seed_ad_id = ? AND combination_key = ?",
        (user_id, seed_ad_id, p["combination_key"]),
    ).fetchone()
    db.close()
    lifecycle: dict = {}
    if pushed_row:
        lifecycle = {
            "ad_name": pushed_row["ad_name"],
            "already_pushed": True,
            "push_status": pushed_row["push_status"],
            "converged": bool(pushed_row["converged"]),
            "platform_ad_id": pushed_row["platform_ad_id"],
            "current_impressions": pushed_row["current_impressions"] or 0,
        }
    return BOPick(**{**p, "combination": combo, "placements": placements, **lifecycle})


def _get_pushed_exclude_keys(user_id: int, seed_ad_id: str) -> set[str]:
    """Return combination_keys already pushed for this seed ad — excluded from BO candidates."""
    db = get_db()
    rows = db.execute(
        "SELECT combination_key FROM pushed_ad_combos WHERE user_id = ? AND seed_ad_id = ?",
        (user_id, seed_ad_id),
    ).fetchall()
    db.close()
    return {r["combination_key"] for r in rows}


@app.post("/api/bo/run", response_model=BORunResponse)
def run_bo_endpoint(body: BORunRequest, user_id: int = Depends(get_current_user_id)):
    """
    Run Bayesian Optimisation for an ad and return up to 2 recommended combinations.
    seed_ad_id and text_source_id are normally the same (both = the ingested ad_id).
    """
    from bo_pipeline.pipeline import run_bo
    from bo_pipeline.storage import DB_PATH as BO_DB_PATH, save_bo_run

    pushed_keys = _get_pushed_exclude_keys(user_id, body.seed_ad_id)
    picks, warning, scored_count, candidate_count = run_bo(
        body.seed_ad_id, body.text_source_id, user_id, BO_DB_PATH,
        additional_exclude_keys=pushed_keys,
        target_metric=body.target_metric,
    )

    if picks:
        save_bo_run(body.seed_ad_id, body.text_source_id, picks, BO_DB_PATH)

    return BORunResponse(
        seed_ad_id=body.seed_ad_id,
        text_source_id=body.text_source_id,
        picks=[_enrich_pick(p, body.seed_ad_id, user_id) for p in picks],
        scored_count=scored_count,
        candidate_count=candidate_count,
        warning=warning,
    )


@app.get("/api/bo/results/{ad_id}", response_model=list[BOPick])
def get_bo_results(ad_id: str, user_id: int = Depends(get_current_user_id)):
    """Return the most recent BO picks for an ad."""
    from bo_pipeline.storage import DB_PATH as BO_DB_PATH, get_latest_bo_run
    return [_enrich_pick(p, ad_id, user_id) for p in get_latest_bo_run(ad_id, ad_id, BO_DB_PATH)]


@app.post("/api/google/bo/run", response_model=BORunResponse)
def run_google_bo_endpoint(body: BORunRequest, user_id: int = Depends(get_current_user_id)):
    """
    Run Bayesian Optimisation for a Google RSA ad and return up to 2 recommended combinations.
    seed_ad_id and text_source_id should both be the ingested Google ad_id.
    Falls back to random when fewer than MIN_TRAINING_POINTS scored variants exist.
    """
    from bo_pipeline.pipeline import run_bo
    from bo_pipeline.storage import DB_PATH as BO_DB_PATH, save_bo_run

    db = get_db()
    ct_row = db.execute(
        "SELECT creative_type FROM ad_creative_structures "
        "WHERE ad_id = ? AND platform = 'google' LIMIT 1",
        (body.seed_ad_id,),
    ).fetchone()
    db.close()
    if ct_row and ct_row["creative_type"] in _UNSUPPORTED_FOR_OPTIMIZATION:
        raise HTTPException(
            400,
            f"Creative type '{ct_row['creative_type']}' is not supported for optimization.",
        )

    pushed_keys = _get_pushed_exclude_keys(user_id, body.seed_ad_id)
    picks, warning, scored_count, candidate_count = run_bo(
        body.seed_ad_id, body.text_source_id, user_id, BO_DB_PATH,
        platform="google", additional_exclude_keys=pushed_keys,
        target_metric=body.target_metric,
    )

    if picks:
        save_bo_run(body.seed_ad_id, body.text_source_id, picks, BO_DB_PATH)

    return BORunResponse(
        seed_ad_id=body.seed_ad_id,
        text_source_id=body.text_source_id,
        picks=[_enrich_pick(p, body.seed_ad_id, user_id) for p in picks],
        scored_count=scored_count,
        candidate_count=candidate_count,
        warning=warning,
    )


@app.get("/api/google/bo/results/{ad_id}", response_model=list[BOPick])
def get_google_bo_results(ad_id: str, user_id: int = Depends(get_current_user_id)):
    """Return the most recent BO picks for a Google ad."""
    from bo_pipeline.storage import DB_PATH as BO_DB_PATH, get_latest_bo_run
    return [_enrich_pick(p, ad_id, user_id) for p in get_latest_bo_run(ad_id, ad_id, BO_DB_PATH)]


# ── Cross-platform BO ─────────────────────────────────────────────────────────

class CrossPlatformBOPick(BaseModel):
    """A single BO pick from the cross-platform run, tagged with its originating platform."""
    combination_key: str
    combination: dict
    selection_type: str
    ei_score: float | None = None
    gpr_mean: float | None = None
    gpr_std: float | None = None
    platform: str               # "meta" | "google"
    seed_ad_id: str
    text_source_id: str
    placements: list[dict] = []


class CrossPlatformBOPair(BaseModel):
    platform: str       # "meta" | "google"
    seed_ad_id: str
    text_source_id: str


class CrossPlatformBORequest(BaseModel):
    pairs: list[CrossPlatformBOPair]


class CrossPlatformBOGroupStat(BaseModel):
    platform: str
    seed_ad_id: str
    scored_count: int
    candidate_count: int


class CrossPlatformBOResponse(BaseModel):
    picks: list[CrossPlatformBOPick]
    group_stats: list[CrossPlatformBOGroupStat]


def _enrich_cross_platform_pick(p: dict, user_id: int) -> CrossPlatformBOPick:
    """Resolve image URLs and attach placements for a cross-platform pick."""
    combo = _resolve_combination_image_url(p["combination"])
    placements = _get_placements_for_ad(p["seed_ad_id"], user_id)
    return CrossPlatformBOPick(**{**p, "combination": combo, "placements": placements})


@app.post("/api/bo/cross-platform", response_model=CrossPlatformBOResponse)
def run_cross_platform_bo_endpoint(
    body: CrossPlatformBORequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    Run Bayesian Optimisation jointly across Meta and Google (or any platform mix).

    Accepts a list of (platform, seed_ad_id, text_source_id) pairs.  A shared
    ECDF normalises scores across platforms before per-platform GPRs compute EI.
    Returns up to 2 globally-ranked picks, each tagged with its originating
    platform.

    Requires at least one pair; each pair must have ingested structure and text
    combinations.  Falls back to random selection per platform when fewer than 2
    scored observations exist for that platform.
    """
    from bo_pipeline.cross_platform import run_cross_platform_bo
    from bo_pipeline.storage import DB_PATH as BO_DB_PATH, save_bo_run

    if not body.pairs:
        raise HTTPException(400, "At least one pair is required.")

    pairs_dicts = [p.model_dump() for p in body.pairs]
    picks, raw_stats = run_cross_platform_bo(pairs_dicts, user_id, BO_DB_PATH)

    # Persist each pick under its own (seed_ad_id, text_source_id) via save_bo_run
    from itertools import groupby
    for (seed_ad_id, text_source_id), group_picks in groupby(
        picks, key=lambda p: (p["seed_ad_id"], p["text_source_id"])
    ):
        group_list = list(group_picks)
        if group_list:
            save_bo_run(seed_ad_id, text_source_id, group_list, BO_DB_PATH)

    return CrossPlatformBOResponse(
        picks=[_enrich_cross_platform_pick(p, user_id) for p in picks],
        group_stats=[CrossPlatformBOGroupStat(**s) for s in raw_stats],
    )


class UnifiedCrossPlatformBORequest(BaseModel):
    pairs: list[CrossPlatformBOPair]
    top_n: int = 4


@app.post("/api/bo/cross-platform/unified", response_model=CrossPlatformBOResponse)
def run_unified_cross_platform_bo_endpoint(
    body: UnifiedCrossPlatformBORequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    Unified cross-platform BO: per-group PCA each to the same K-dim output,
    pooled into a single GP/Modal call.  Returns top_n globally-ranked picks.

    Supports arbitrary mixes of platforms, campaigns, and ad types in one batch.
    top_n defaults to 4 and is configurable via the request body.
    """
    from bo_pipeline.cross_platform import run_unified_cross_platform_bo
    from bo_pipeline.storage import DB_PATH as BO_DB_PATH, save_bo_run

    if not body.pairs:
        raise HTTPException(400, "At least one pair is required.")

    pairs_dicts = [p.model_dump() for p in body.pairs]
    picks, raw_stats = run_unified_cross_platform_bo(pairs_dicts, user_id, BO_DB_PATH, top_n=body.top_n)

    from itertools import groupby
    for (seed_ad_id, text_source_id), group_picks in groupby(
        picks, key=lambda p: (p["seed_ad_id"], p["text_source_id"])
    ):
        group_list = list(group_picks)
        if group_list:
            save_bo_run(seed_ad_id, text_source_id, group_list, BO_DB_PATH)

    return CrossPlatformBOResponse(
        picks=[_enrich_cross_platform_pick(p, user_id) for p in picks],
        group_stats=[CrossPlatformBOGroupStat(**s) for s in raw_stats],
    )


# ── Seed fake scored variants for BO testing ─────────────────────────────────

class SeedScoredVariantsRequest(BaseModel):
    seed_ad_id: str
    text_source_id: str | None = None
    platform: str = "meta"          # "meta" | "google"
    n: int = Field(default=5, ge=1, le=50)


class SeedScoredVariantsResponse(BaseModel):
    seeded: int
    warning: str | None = None


@app.post("/api/bo/seed-scored-variants", response_model=SeedScoredVariantsResponse)
def seed_scored_variants_endpoint(
    body: SeedScoredVariantsRequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    Seed synthetic scored observations for BO testing without API keys.

    Picks up to n existing text combination embeddings for text_source_id
    and writes them directly to scored_observations with random scores in [2.0, 9.0].

    Requires text combination embeddings to already exist for the ad —
    run Ingest then wait ~10 s for the embedding pipeline, or run
    Generate Text first.
    """
    import numpy as np
    from bo_pipeline.storage import ensure_scored_observations_table

    ensure_scored_observations_table(DB_PATH)

    text_source_id = body.text_source_id or body.seed_ad_id
    include_image = body.platform != "google"
    IMAGE_DIM = 1536
    rng = np.random.default_rng()

    db = get_db()
    try:
        rows = db.execute(
            """SELECT combination_key, vector
               FROM ad_text_combination_embeddings
               WHERE source_id = ?
               ORDER BY RANDOM()
               LIMIT ?""",
            (text_source_id, body.n),
        ).fetchall()

        if not rows:
            raise HTTPException(
                400,
                "No text combination embeddings found for this ad. "
                "Run Ingest, then Generate Text (or wait ~10 s for embeddings to finish), "
                "then try again.",
            )

        # Build image vector pool (Meta only)
        image_pool: list = []
        if include_image:
            per_img = db.execute(
                "SELECT vector FROM ad_image_embeddings WHERE user_id=? AND ad_id=? ORDER BY slot_index",
                (user_id, body.seed_ad_id),
            ).fetchall()
            image_pool = [
                np.frombuffer(r["vector"], dtype=np.float32)
                for r in per_img if r["vector"]
            ]
            if not image_pool:
                seed_emb = db.execute(
                    "SELECT image_vector FROM ad_embeddings WHERE ad_id=? AND user_id=?",
                    (body.seed_ad_id, user_id),
                ).fetchone()
                if seed_emb and seed_emb["image_vector"]:
                    image_pool = [np.frombuffer(seed_emb["image_vector"], dtype=np.float32)]

        seeded = 0
        for i, row in enumerate(rows):
            text_vec = np.frombuffer(row["vector"], dtype=np.float32)
            if not include_image:
                image_vec = None
            elif image_pool:
                image_vec = image_pool[i % len(image_pool)]
            else:
                image_vec = rng.random(IMAGE_DIM).astype(np.float32)

            score = round(float(rng.uniform(2.0, 9.0)), 2)
            db.execute(
                """INSERT OR REPLACE INTO scored_observations
                   (user_id, seed_ad_id, combination_key, combination,
                    score, metric, source, text_vector, image_vector)
                   VALUES (?, ?, ?, ?, ?, 'synthetic', 'seed_script', ?, ?)""",
                (
                    user_id, body.seed_ad_id,
                    row["combination_key"],
                    row["combination_key"],  # combination_key IS the JSON combination for text combos
                    score,
                    text_vec.tobytes(),
                    image_vec.tobytes() if image_vec is not None else None,
                ),
            )
            seeded += 1

        db.commit()
        warning = None
        if len(rows) < body.n:
            warning = (
                f"Only {len(rows)} combination embeddings found; seeded {seeded}. "
                "Run Generate Text to create more."
            )
        return SeedScoredVariantsResponse(seeded=seeded, warning=warning)

    finally:
        db.close()


# ── Push to Meta ─────────────────────────────────────────────────────────────

class PushAdResult(BaseModel):
    ad_id: str
    meta_ad_id: str | None = None
    error: str | None = None


class PushResponse(BaseModel):
    pushed: int
    failed: int
    results: list[PushAdResult] = []


@app.post("/api/push", response_model=PushResponse)
async def push_generated_ads(user_id: int = Depends(get_current_user_id)):
    """Push all unpushed completed generated ads to Meta as static ads (PAUSED)."""
    try:
        access_token, ad_account_id = _meta_creds(user_id)
    except HTTPException:
        return PushResponse(pushed=0, failed=0)

    db = get_db()
    jobs = db.execute(
        """
        SELECT id, campaign_id, ad_id, seed_ad_id, adset_id
        FROM dynamic_generation_jobs
        WHERE user_id = ? AND status = 'complete'
          AND ad_id IS NOT NULL AND meta_ad_id IS NULL
          AND adset_id IS NOT NULL AND seed_ad_id IS NOT NULL
        """,
        (user_id,),
    ).fetchall()
    db.close()

    if not jobs:
        logger.info("push: no pushable jobs found for user %d", user_id)
        return PushResponse(pushed=0, failed=0)

    logger.info("push: found %d job(s) to push for user %d", len(jobs), user_id)
    pushed = 0
    failed = 0
    results: list[PushAdResult] = []

    async with httpx.AsyncClient(timeout=30) as client:
        for job in jobs:
            try:
                db = get_db()
                slot_rows = db.execute(
                    """
                    SELECT slot, value FROM ad_creative_structures
                    WHERE user_id = ? AND ad_id = ? AND slot_index = 0
                    """,
                    (user_id, job["ad_id"]),
                ).fetchall()
                db.close()

                components = {r["slot"]: r["value"] for r in slot_rows if r["value"]}
                if not components:
                    results.append(PushAdResult(ad_id=job["ad_id"], error="No components found"))
                    failed += 1
                    continue

                # Upload image to Meta first so we get a hash (local URLs aren't reachable by Meta)
                image_hash: str | None = None
                if components.get("image"):
                    image_hash = await _upload_image_to_meta(
                        client, access_token, ad_account_id, components["image"]
                    )

                meta_ad_id = await _clone_dynamic_to_static_ad(
                    client=client,
                    access_token=access_token,
                    ad_account_id=ad_account_id,
                    adset_id=job["adset_id"],
                    source_ad_id=job["seed_ad_id"],
                    components=components,
                    suggestion_id=job["id"],
                    image_hash=image_hash,
                )

                db = get_db()
                db.execute(
                    "UPDATE dynamic_generation_jobs SET meta_ad_id = ? WHERE id = ?",
                    (meta_ad_id, job["id"]),
                )
                db.commit()
                db.close()

                pushed += 1
                results.append(PushAdResult(ad_id=job["ad_id"], meta_ad_id=meta_ad_id))

            except Exception as exc:
                logger.warning("push: job %d failed: %s", job["id"], exc)
                failed += 1
                results.append(PushAdResult(ad_id=job["ad_id"], error=str(exc)))

    logger.info("push: done — pushed=%d failed=%d", pushed, failed)
    return PushResponse(pushed=pushed, failed=failed, results=results)


# ── Push to Google ────────────────────────────────────────────────────────────

class GooglePushAdResult(BaseModel):
    seed_ad_id: str
    ad_resource_name: str | None = None
    error: str | None = None


class GooglePushResponse(BaseModel):
    pushed: int
    failed: int
    results: list[GooglePushAdResult] = []
    note: str | None = None


async def _create_google_rsa_ad(
    user_id: int,
    seed_ad_id: str,
    bo_combination: dict,
    customer_id: str,
    access_token: str,
    login_customer_id: str | None,
    ad_name: str | None = None,
) -> str:
    """Look up ad group + final_url + generated text, create a PAUSED RSA.

    The BO-picked headline and description are placed first (pinned). Returns
    the resource name of the created ad group ad.
    """
    db = get_db()
    struct_row = db.execute(
        "SELECT adset_id FROM ad_creative_structures "
        "WHERE user_id = ? AND ad_id = ? AND platform = 'google' LIMIT 1",
        (user_id, seed_ad_id),
    ).fetchone()
    final_url_row = db.execute(
        "SELECT value FROM ad_creative_structures "
        "WHERE user_id = ? AND ad_id = ? AND slot = 'final_url' AND slot_index = 0 LIMIT 1",
        (user_id, seed_ad_id),
    ).fetchone()
    gen_rows = db.execute(
        """
        SELECT gs.slot, gs.value
        FROM generated_ad_slots gs
        JOIN generated_ads ga ON ga.id = gs.generated_ad_id
        WHERE ga.source_ad_id = ?
        ORDER BY gs.slot, gs.slot_index
        """,
        (seed_ad_id,),
    ).fetchall()
    db.close()

    if not struct_row:
        raise ValueError(f"No ingested structure for ad {seed_ad_id}")

    ad_group_id = struct_row["adset_id"]
    final_url = final_url_row["value"] if final_url_row else ""
    if not final_url:
        raise ValueError(f"No final_url in ingested structure for ad {seed_ad_id}")

    bo_headline = bo_combination.get("headline", "")
    bo_description = bo_combination.get("description", "")

    extra_headlines = [
        r["value"] for r in gen_rows
        if r["slot"] == "headline" and r["value"] != bo_headline
    ][:14]
    extra_descriptions = [
        r["value"] for r in gen_rows
        if r["slot"] == "description" and r["value"] != bo_description
    ][:3]

    headlines = ([bo_headline] if bo_headline else []) + extra_headlines
    descriptions = ([bo_description] if bo_description else []) + extra_descriptions

    if len(headlines) < 3 or len(descriptions) < 2:
        raise ValueError(
            f"Not enough variants: {len(headlines)} headlines, {len(descriptions)} descriptions "
            f"(need ≥3 and ≥2). Generate RSA text before pushing."
        )

    api_version = os.getenv("GOOGLE_ADS_API_VERSION", "")
    developer_token = os.getenv("GOOGLE_DEVELOPER_TOKEN", "")

    return await google_ads_api.create_rsa(
        customer_id=customer_id,
        access_token=access_token,
        developer_token=developer_token,
        api_version=api_version,
        ad_group_id=ad_group_id,
        headlines=headlines,
        descriptions=descriptions,
        final_url=final_url,
        login_customer_id=login_customer_id,
        ad_name=ad_name,
    )


@app.post("/api/google/push", response_model=GooglePushResponse)
async def push_google_ads(user_id: int = Depends(get_current_user_id)):
    """Push all unpushed BO-recommended RSA configurations to Google Ads (PAUSED)."""
    try:
        access_token, customer_id, login_customer_id = await _google_creds(user_id)
    except HTTPException:
        return GooglePushResponse(pushed=0, failed=0, note="Google Ads not connected")

    db = get_db()
    rows = db.execute(
        """
        SELECT bs.id, bs.seed_ad_id, bs.combination
        FROM bo_selections bs
        INNER JOIN (
            SELECT seed_ad_id, MAX(created_at) AS max_ts
            FROM bo_selections
            WHERE pick_rank = 1 AND google_ad_resource_name IS NULL
            GROUP BY seed_ad_id
        ) latest ON bs.seed_ad_id = latest.seed_ad_id AND bs.created_at = latest.max_ts
        WHERE bs.pick_rank = 1 AND bs.google_ad_resource_name IS NULL
          AND bs.seed_ad_id IN (
              SELECT DISTINCT ad_id FROM ad_creative_structures
              WHERE user_id = ? AND platform = 'google'
          )
        """,
        (user_id,),
    ).fetchall()
    db.close()

    if not rows:
        return GooglePushResponse(pushed=0, failed=0)

    pushed = 0
    failed = 0
    results: list[GooglePushAdResult] = []

    for row in rows:
        seed_ad_id = row["seed_ad_id"]
        try:
            combination = json.loads(row["combination"])
            resource_name = await _create_google_rsa_ad(
                user_id=user_id,
                seed_ad_id=seed_ad_id,
                bo_combination=combination,
                customer_id=customer_id,
                access_token=access_token,
                login_customer_id=login_customer_id,
            )
            db = get_db()
            db.execute(
                "UPDATE bo_selections SET google_ad_resource_name = ? WHERE id = ?",
                (resource_name, row["id"]),
            )
            db.commit()
            db.close()
            pushed += 1
            results.append(GooglePushAdResult(seed_ad_id=seed_ad_id, ad_resource_name=resource_name))
        except Exception as exc:
            logger.warning("google push: seed_ad %s failed: %s", seed_ad_id, exc)
            failed += 1
            results.append(GooglePushAdResult(seed_ad_id=seed_ad_id, error=str(exc)))

    logger.info("google push: done — pushed=%d failed=%d", pushed, failed)
    return GooglePushResponse(pushed=pushed, failed=failed, results=results)


# ── Per-pick push (named, explicit) ──────────────────────────────────────────

class PushPickRequest(BaseModel):
    platform: str          # 'meta' | 'google'
    seed_ad_id: str
    combination_key: str   # compound JSON key from the BO pipeline
    combination: dict      # actual slot values
    name: str              # user-supplied name


class PushPickResponse(BaseModel):
    platform_ad_id: str
    ad_name: str


@app.post("/api/push/pick", response_model=PushPickResponse)
async def push_pick(body: PushPickRequest, user_id: int = Depends(get_current_user_id)):
    """
    Push a specific BO-recommended combination as a new PAUSED ad.

    Meta:  creates a static ad via _clone_dynamic_to_static_ad.
    Google: creates a PAUSED RSA via _create_google_rsa_ad.

    Records the push in pushed_ad_combos so future BO runs exclude this
    combination and the UI can show lifecycle state.
    """
    db = get_db()
    struct_row = db.execute(
        "SELECT ad_account_id, adset_id FROM ad_creative_structures "
        "WHERE user_id = ? AND ad_id = ? LIMIT 1",
        (user_id, body.seed_ad_id),
    ).fetchone()
    db.close()
    if not struct_row:
        raise HTTPException(400, f"No ingested structure for ad {body.seed_ad_id}")

    if body.platform == "meta":
        access_token, ad_account_id = _meta_creds(user_id)
        adset_id = struct_row["adset_id"]
        if not adset_id:
            raise HTTPException(400, "No adset_id found for this ad — reingest the campaign")

        image_hash: str | None = None
        async with httpx.AsyncClient(timeout=30) as client:
            if body.combination.get("image"):
                image_hash = await _upload_image_to_meta(
                    client, access_token, ad_account_id, body.combination["image"]
                )
            platform_ad_id = await _clone_dynamic_to_static_ad(
                client=client,
                access_token=access_token,
                ad_account_id=ad_account_id,
                adset_id=adset_id,
                source_ad_id=body.seed_ad_id,
                components=body.combination,
                suggestion_id=0,
                image_hash=image_hash,
                ad_name=body.name,
            )

    elif body.platform == "google":
        access_token, customer_id, login_customer_id = await _google_creds(user_id)
        platform_ad_id = await _create_google_rsa_ad(
            user_id=user_id,
            seed_ad_id=body.seed_ad_id,
            bo_combination=body.combination,
            customer_id=customer_id,
            access_token=access_token,
            login_customer_id=login_customer_id,
            ad_name=body.name,
        )
    else:
        raise HTTPException(400, f"Unsupported platform: {body.platform}")

    # For Google, extract the numeric ad ID from the resource name
    # (e.g. "customers/123/adGroupAds/456" → "456") for clone detection during ingest.
    numeric_id = str(platform_ad_id).split("/")[-1] if body.platform == "google" else platform_ad_id

    db = get_db()
    db.execute(
        """
        INSERT OR REPLACE INTO pushed_ad_combos
            (user_id, platform, seed_ad_id, ad_name, combination_key, combination,
             platform_ad_id, platform_ad_numeric_id, push_status, pushed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'paused', datetime('now'))
        """,
        (user_id, body.platform, body.seed_ad_id, body.name,
         body.combination_key, json.dumps(body.combination), platform_ad_id, numeric_id),
    )
    db.commit()
    db.close()

    logger.info("push/pick: platform=%s seed=%s ad_id=%s name=%s",
                body.platform, body.seed_ad_id, platform_ad_id, body.name)
    return PushPickResponse(platform_ad_id=platform_ad_id, ad_name=body.name)


# ── Activate a pushed clone ───────────────────────────────────────────────────

class ActivateRequest(BaseModel):
    platform: str
    platform_ad_id: str


@app.post("/api/activate")
async def activate_ad(body: ActivateRequest, user_id: int = Depends(get_current_user_id)):
    """Enable a PAUSED pushed clone on the platform."""
    if body.platform == "meta":
        access_token, ad_account_id = _meta_creds(user_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{META_GRAPH}/{body.platform_ad_id}",
                data={"status": "ACTIVE", "access_token": access_token},
            )
        if resp.status_code != 200:
            raise HTTPException(502, f"Meta activate failed: {resp.text}")

    elif body.platform == "google":
        access_token, customer_id, login_customer_id = await _google_creds(user_id)
        api_version = os.getenv("GOOGLE_ADS_API_VERSION", "")
        developer_token = os.getenv("GOOGLE_DEVELOPER_TOKEN", "")
        url = f"{os.getenv('GOOGLE_ADS_BASE_URL', 'https://googleads.googleapis.com')}/{api_version}/customers/{customer_id}:mutate"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "developer-token": developer_token,
            "Content-Type": "application/json",
        }
        if login_customer_id:
            headers["login-customer-id"] = login_customer_id
        mutate_body = {
            "mutateOperations": [{
                "adGroupAdOperation": {
                    "update": {
                        "resourceName": body.platform_ad_id,
                        "status": "ENABLED",
                    },
                    "updateMask": "status",
                }
            }]
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=mutate_body, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(502, f"Google activate failed: {resp.text}")
    else:
        raise HTTPException(400, f"Unsupported platform: {body.platform}")

    db = get_db()
    db.execute(
        "UPDATE pushed_ad_combos SET push_status = 'active' "
        "WHERE user_id = ? AND platform = ? AND platform_ad_id = ?",
        (user_id, body.platform, body.platform_ad_id),
    )
    db.commit()
    db.close()
    return {"status": "active", "platform_ad_id": body.platform_ad_id}


# ── Ad text generation routes ─────────────────────────────────────────────────

class GeneratedAdSlot(BaseModel):
    slot: str
    slot_index: int
    value: str
    source: str


class GenerateTextResponse(BaseModel):
    generated_ad_id: int
    source_ad_id: str
    slots: list[GeneratedAdSlot]


@app.post("/api/generate/text/{campaign_id}", response_model=GenerateTextResponse)
async def generate_text_ads(
    campaign_id: str,
    seed_ad_id: str = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    """Generate 10 new text variants per slot from an ingested ad in the campaign."""
    from ad_text_generation.pipeline import run_text_pipeline
    from ad_text_generation.storage import get_generated_ad

    db = get_db()
    rows = db.execute(
        """
        SELECT ad_id, slot, slot_index, value
        FROM ad_creative_structures
        WHERE user_id = ? AND campaign_id = ?
        ORDER BY ad_id, slot, slot_index
        """,
        (user_id, campaign_id),
    ).fetchall()
    db.close()

    if not rows:
        raise HTTPException(404, "No ingested creative structure found for this campaign. Run Ingest first.")

    available_ids = list(dict.fromkeys(r["ad_id"] for r in rows))
    if seed_ad_id and seed_ad_id not in available_ids:
        raise HTTPException(400, f"seed_ad_id not found in this campaign. Available: {available_ids}")
    if not seed_ad_id:
        seed_ad_id = available_ids[0]
    seed_components = [
        {"slot": r["slot"], "slot_index": r["slot_index"], "value": r["value"]}
        for r in rows
        if r["ad_id"] == seed_ad_id
    ]

    generated_ad_id = await run_text_pipeline(
        seed_components=seed_components,
        n_per_slot=10,
        source_ad_id=seed_ad_id,
    )

    slots = get_generated_ad(generated_ad_id)

    # Fire embed_all_combinations under the seed ad's ID so BO automatically
    # sees both original ingested combinations AND these new generated variants
    # as one expanded candidate pool (idempotent — existing combos are skipped).
    _gen_components = [
        {"slot": s["slot"], "slot_index": s["slot_index"], "value": s["value"]}
        for s in slots
        if s.get("source") == "generated"
    ]
    _merged_meta = seed_components + _gen_components
    from ad_combination_embeddings.pipeline import embed_all_combinations as _embed_combos
    asyncio.create_task(_embed_combos(source_id=seed_ad_id, components=_merged_meta))

    return GenerateTextResponse(
        generated_ad_id=generated_ad_id,
        source_ad_id=seed_ad_id,
        slots=[GeneratedAdSlot(**s) for s in slots],
    )


_UNSUPPORTED_FOR_OPTIMIZATION = frozenset({"shopping", "unknown"})


@app.post("/api/google/generate/text/{campaign_id}", response_model=GenerateTextResponse)
async def generate_google_text_ads(
    campaign_id: str,
    seed_ad_id: str = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    """Generate 10 new RSA headline and description variants from an ingested Google ad."""
    from ad_text_generation.pipeline import run_text_pipeline
    from ad_text_generation.storage import get_generated_ad

    db = get_db()
    rows = db.execute(
        """
        SELECT ad_id, slot, slot_index, value, creative_type
        FROM ad_creative_structures
        WHERE user_id = ? AND campaign_id = ? AND platform = 'google'
        ORDER BY ad_id, slot, slot_index
        """,
        (user_id, campaign_id),
    ).fetchall()
    db.close()

    if not rows:
        raise HTTPException(404, "No ingested Google creative structure found for this campaign. Run Ingest first.")

    available_ids = list(dict.fromkeys(r["ad_id"] for r in rows))
    if seed_ad_id and seed_ad_id not in available_ids:
        raise HTTPException(400, f"seed_ad_id not found in this campaign. Available: {available_ids}")
    if not seed_ad_id:
        seed_ad_id = available_ids[0]

    seed_rows = [r for r in rows if r["ad_id"] == seed_ad_id]
    if seed_rows:
        creative_type = seed_rows[0]["creative_type"]
        if creative_type in _UNSUPPORTED_FOR_OPTIMIZATION:
            raise HTTPException(
                400,
                f"Creative type '{creative_type}' is not supported for text optimization.",
            )

    seed_components = [
        {"slot": r["slot"], "slot_index": r["slot_index"], "value": r["value"]}
        for r in seed_rows
    ]

    generated_ad_id = await run_text_pipeline(
        seed_components=seed_components,
        n_per_slot=10,
        source_ad_id=seed_ad_id,
        platform="google",
    )

    slots = get_generated_ad(generated_ad_id)

    # Fire embed_all_combinations under the seed ad's ID (Google RSA slots only)
    # so BO sees ingested + generated variants as one expanded candidate pool.
    _gen_components_g = [
        {"slot": s["slot"], "slot_index": s["slot_index"], "value": s["value"]}
        for s in slots
        if s.get("source") == "generated"
    ]
    _merged_google = seed_components + _gen_components_g
    from ad_combination_embeddings.pipeline import embed_all_combinations as _embed_combos_g
    asyncio.create_task(
        _embed_combos_g(
            source_id=seed_ad_id,
            components=_merged_google,
            slots=("headline", "description"),
        )
    )

    return GenerateTextResponse(
        generated_ad_id=generated_ad_id,
        source_ad_id=seed_ad_id,
        slots=[GeneratedAdSlot(**s) for s in slots],
    )


# ── Dynamic ad generation routes ──────────────────────────────────────────────

class DynamicGenJob(BaseModel):
    job_id: int
    status: str


class DynamicGenSlot(BaseModel):
    slot: str
    slot_index: int
    value: str


class DynamicGenResult(BaseModel):
    job_id: int
    status: str
    ad_id: str | None = None
    error: str | None = None
    images_generated: int | None = None
    slots: list[DynamicGenSlot] = []
    image_urls: list[str] = []


async def _run_dynamic_generation(
    dyn_job_id: int,
    campaign_id: str,
    user_id: int,
    seed_ad_id: str,
    seed_components: list[dict],
    seed_image_url: str | None,
    seed_adset_id: str,
) -> None:
    """Background task: generate 4×4×4×4 dynamic ad with AI images and fire embeddings."""
    import uuid as _uuid
    from ad_text_generation.generator import TEXT_SLOTS, generate_all_slots
    from embeddings.pipeline import embed_ad, embed_images
    from ad_combination_embeddings.pipeline import embed_all_combinations

    db = get_db()
    try:
        # ── Step 1: Generate 4 text variants per slot ─────────────────────────
        generated_text = await generate_all_slots(
            seed_components=seed_components,
            n_per_slot=4,
            slots=list(TEXT_SLOTS),
        )

        # ── Step 2: Generate 4 images via image pipeline ──────────────────────
        image_urls: list[str] = []
        if seed_image_url:
            try:
                from ad_generation.pipeline import (
                    create_job as _create_img_job,
                    get_active_variants,
                    run_generation_job,
                )
                headline = next(
                    (c["value"] for c in seed_components if c["slot"] == "headline"), ""
                )
                short_text = next(
                    (c["value"] for c in seed_components if c["slot"] == "primary_text"), ""
                )
                gen_job_id = _create_img_job(
                    user_id=user_id,
                    campaign_id=campaign_id,
                    adset_id=seed_adset_id,
                    seed_image_url=seed_image_url,
                    headline=headline,
                    short_text=short_text,
                    seed_ad_id=seed_ad_id,
                )
                await run_generation_job(gen_job_id)
                variants = get_active_variants(gen_job_id)
                usable = [v for v in variants if v.get("local_filename")]
                scored = sorted(
                    [v for v in usable if v.get("score") is not None],
                    key=lambda v: v["score"],
                    reverse=True,
                )
                unscored = [v for v in usable if v.get("score") is None]
                base_url = os.getenv("IMAGES_SERVE_BASE_URL", "http://localhost:8000/images")
                seen_filenames: set[str] = set()
                unique_variants: list[dict] = []
                for v in scored + unscored:
                    if v["local_filename"] not in seen_filenames:
                        seen_filenames.add(v["local_filename"])
                        unique_variants.append(v)
                image_urls = [
                    f"{base_url}/{v['local_filename']}"
                    for v in unique_variants[:4]
                ]
            except Exception:
                logger.warning("dynamic generation: image pipeline failed", exc_info=True)

        # ── Step 3: Assemble components ───────────────────────────────────────
        new_ad_id = f"gen_dyn_{_uuid.uuid4().hex[:12]}"
        now_ts = datetime.utcnow().isoformat()
        components: list[dict] = []

        for slot_name, variants_list in generated_text.items():
            for idx, value in enumerate(variants_list[:4]):
                components.append({"slot": slot_name, "slot_index": idx, "value": value})

        images_generated = len(image_urls)
        if image_urls:
            for idx, url in enumerate(image_urls[:4]):
                components.append({"slot": "image", "slot_index": idx, "value": url})
        else:
            # No generated images — carry seed images through as-is (deduplicated)
            seen_image_urls: set[str] = set()
            for comp in seed_components:
                if comp["slot"] == "image" and comp.get("value") and comp["value"] not in seen_image_urls:
                    seen_image_urls.add(comp["value"])
                    components.append(comp)
            logger.warning("dynamic generation job %d: no images generated; using %d seed image(s)", dyn_job_id, len(seen_image_urls))

        # ── Step 4: Persist into ad_creative_structures ───────────────────────
        for comp in components:
            db.execute(
                """
                INSERT INTO ad_creative_structures
                    (user_id, ad_account_id, campaign_id, adset_id, ad_id,
                     creative_type, slot, slot_index, value,
                     ingested_at, lifecycle_status, data_source)
                VALUES (?, 'generated', ?, 'generated', ?,
                        'dynamic', ?, ?, ?,
                        ?, 'generated', 'generated')
                ON CONFLICT (user_id, ad_id, slot, slot_index) DO UPDATE SET
                    value = excluded.value,
                    ingested_at = excluded.ingested_at
                """,
                (
                    user_id, campaign_id, new_ad_id,
                    comp["slot"], comp["slot_index"], comp.get("value"),
                    now_ts,
                ),
            )
        db.commit()

        # ── Step 5: Fire embeddings (fire-and-forget) ─────────────────────────
        asyncio.create_task(embed_ad(user_id, new_ad_id, campaign_id, components))
        asyncio.create_task(embed_images(user_id, new_ad_id, campaign_id, components))
        asyncio.create_task(
            embed_all_combinations(
                source_id=new_ad_id,
                components=components,
                slots=("headline", "primary_text", "description"),
            )
        )

        # ── Step 6: Mark job complete ─────────────────────────────────────────
        db.execute(
            """UPDATE dynamic_generation_jobs
               SET status = 'complete', ad_id = ?, images_generated = ?, completed_at = datetime('now')
               WHERE id = ?""",
            (new_ad_id, images_generated, dyn_job_id),
        )
        db.commit()
        logger.info("dynamic generation job %d complete: ad_id=%s images_generated=%d", dyn_job_id, new_ad_id, images_generated)

    except Exception as exc:
        logger.exception("dynamic generation job %d failed", dyn_job_id)
        try:
            db.execute(
                "UPDATE dynamic_generation_jobs SET status = 'failed', error = ? WHERE id = ?",
                (str(exc), dyn_job_id),
            )
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


@app.post("/api/generate/dynamic/{campaign_id}", response_model=DynamicGenJob)
async def start_dynamic_generation(
    campaign_id: str,
    seed_ad_id: str = Query(None),
    user_id: int = Depends(get_current_user_id),
):
    """Start async dynamic ad generation: 4 text variants per slot + 4 AI images."""
    db = get_db()
    rows = db.execute(
        """
        SELECT ad_id, adset_id, slot, slot_index, value
        FROM ad_creative_structures
        WHERE user_id = ? AND campaign_id = ?
        ORDER BY ad_id, slot, slot_index
        """,
        (user_id, campaign_id),
    ).fetchall()

    if not rows:
        db.close()
        raise HTTPException(
            404, "No ingested creative structure found. Run Ingest first."
        )

    available_ids = list(dict.fromkeys(r["ad_id"] for r in rows))
    if seed_ad_id and seed_ad_id not in available_ids:
        db.close()
        raise HTTPException(400, f"seed_ad_id not found in this campaign. Available: {available_ids}")
    if not seed_ad_id:
        seed_ad_id = available_ids[0]

    seed_adset_id = next(r["adset_id"] for r in rows if r["ad_id"] == seed_ad_id)
    seed_components = [
        {"slot": r["slot"], "slot_index": r["slot_index"], "value": r["value"]}
        for r in rows
        if r["ad_id"] == seed_ad_id
    ]
    _image_urls = [c["value"] for c in seed_components if c["slot"] == "image" and c["value"]]
    seed_image_url = secrets.choice(_image_urls) if _image_urls else None

    db.execute(
        "INSERT INTO dynamic_generation_jobs (user_id, campaign_id, seed_ad_id, adset_id) VALUES (?, ?, ?, ?)",
        (user_id, campaign_id, seed_ad_id, seed_adset_id),
    )
    db.commit()
    dyn_job_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.close()

    asyncio.create_task(
        _run_dynamic_generation(
            dyn_job_id=dyn_job_id,
            campaign_id=campaign_id,
            user_id=user_id,
            seed_ad_id=seed_ad_id,
            seed_components=seed_components,
            seed_image_url=seed_image_url,
            seed_adset_id=seed_adset_id,
        )
    )

    return DynamicGenJob(job_id=dyn_job_id, status="running")


@app.get("/api/generate/dynamic/status/{job_id}", response_model=DynamicGenResult)
def get_dynamic_gen_status(
    job_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Poll status of a dynamic generation job; returns slots + images when complete."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM dynamic_generation_jobs WHERE id = ? AND user_id = ?",
        (job_id, user_id),
    ).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Job not found")

    result = DynamicGenResult(
        job_id=row["id"],
        status=row["status"],
        ad_id=row["ad_id"],
        error=row["error"],
        images_generated=row["images_generated"],
    )

    if row["status"] == "complete" and row["ad_id"]:
        slot_rows = db.execute(
            """SELECT slot, slot_index, value FROM ad_creative_structures
               WHERE user_id = ? AND ad_id = ? ORDER BY slot, slot_index""",
            (user_id, row["ad_id"]),
        ).fetchall()
        result.slots = [
            DynamicGenSlot(slot=r["slot"], slot_index=r["slot_index"], value=r["value"] or "")
            for r in slot_rows
            if r["slot"] != "image"
        ]
        raw_image_urls = [r["value"] for r in slot_rows if r["slot"] == "image" and r["value"]]
        result.image_urls = [
            u.split("localhost:8000", 1)[-1] if "localhost:8000" in u else u
            for u in raw_image_urls
        ]

    db.close()
    return result


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)

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

from bo_pipeline.config import CONVERGENCE_FLOOR, CONVERGENCE_MARGIN

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
        CREATE TABLE IF NOT EXISTS native_ad_insights (
            user_id     INTEGER NOT NULL,
            ad_id       TEXT NOT NULL,
            platform    TEXT NOT NULL DEFAULT 'meta',
            impressions INTEGER NOT NULL DEFAULT 0,
            clicks      INTEGER,
            spend       REAL,
            ctr         REAL,
            cpm         REAL,
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, ad_id)
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
    try:
        conn.execute(
            "ALTER TABLE ad_creative_structures ADD COLUMN effective_status TEXT NOT NULL DEFAULT 'UNKNOWN'"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    for _col in [
        "platform_ad_numeric_id TEXT",
        "current_impressions INTEGER NOT NULL DEFAULT 0",
        "days_running INTEGER NOT NULL DEFAULT 0",
        "clone_status TEXT NOT NULL DEFAULT 'clone_paused'",
    ]:
        try:
            conn.execute(f"ALTER TABLE pushed_ad_combos ADD COLUMN {_col}")
            conn.commit()
        except Exception:
            pass  # Column already exists
    try:
        conn.execute(
            "ALTER TABLE ad_creative_structures ADD COLUMN role TEXT NOT NULL DEFAULT 'parent'"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ad_parent_fillers (
            parent_ad_id        TEXT NOT NULL PRIMARY KEY,
            filler_headline_1   TEXT NOT NULL,
            filler_headline_2   TEXT NOT NULL,
            filler_description_1 TEXT NOT NULL,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ad_generators (
            id         TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ad_generator_members (
            generator_id      TEXT NOT NULL REFERENCES ad_generators(id),
            ad_id             TEXT NOT NULL,
            contribution_mode TEXT NOT NULL DEFAULT 'dynamic',
            PRIMARY KEY (generator_id, ad_id)
        )
        """
    )
    conn.commit()
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
            clone_status            TEXT NOT NULL DEFAULT 'clone_paused',
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
    from ad_text_generation.storage import ensure_tables as _ensure_text_gen_tables
    _ensure_text_gen_tables()


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
    parent_should_pause: bool = False


class AdStructure(BaseModel):
    ad_id: str
    ad_name: str | None = None  # human name for pushed clones; None for template/native ads
    adset_id: str
    campaign_id: str
    creative_type: str
    lifecycle_status: str | None = None  # 'active' | 'inactive' | 'missing'
    components: dict[str, list[str | None]]  # slot → values ordered by slot_index
    is_pushed_clone: bool = False
    effective_status: str | None = None  # ACTIVE, PAUSED, DISAPPROVED, etc.
    clone_stats: dict | None = None  # impressions/days/ctr for pushed clones or ingested native ads


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
        if "access_token" not in token_data:
            raise HTTPException(502, f"Meta token exchange returned no access_token: {token_data}")
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
    try:
        access_token = await google_ads_api.refresh_access_token(row["refresh_token"])
    except RuntimeError as exc:
        raise HTTPException(401, f"Google token refresh failed — reconnect Google Ads: {exc}") from exc
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
        try:
            campaigns_raw, metrics_by_campaign, _ = await _fetch_campaigns_and_insights(
                client, access_token, ad_account_id
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise HTTPException(502, f"Could not reach Meta API: {exc}")

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
        try:
            campaigns_raw, metrics_by_campaign, _ = await google_provider.fetch_campaigns_and_insights(
                client, access_token, customer_id, login_customer_id=login_customer_id
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise HTTPException(502, f"Could not reach Google Ads API: {exc}")
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
                     lifecycle_status, effective_status, data_source, mask_profile, platform)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'real', NULL, 'google')
                ON CONFLICT (user_id, ad_id, slot, slot_index)
                DO UPDATE SET
                    creative_type    = excluded.creative_type,
                    value            = excluded.value,
                    ingested_at      = excluded.ingested_at,
                    lifecycle_status = excluded.lifecycle_status,
                    effective_status = excluded.effective_status,
                    platform         = 'google'
                """,
                (
                    user_id, customer_id, campaign_id, adset_id, ad_id,
                    creative_type, comp["slot"], comp["slot_index"],
                    comp["value"], ingested_at, lifecycle_status, raw_status,
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
    pushed_rows_g = db.execute(
        "SELECT id, platform_ad_numeric_id, combination FROM pushed_ad_combos "
        "WHERE user_id = ? AND platform = 'google' AND platform_ad_numeric_id IS NOT NULL",
        (user_id,),
    ).fetchall()
    pushed_numeric_ids = {r["platform_ad_numeric_id"] for r in pushed_rows_g if r["platform_ad_numeric_id"]}
    if current_ad_ids:
        clone_ids_g = current_ad_ids & pushed_numeric_ids
        if clone_ids_g:
            placeholders = ",".join("?" * len(clone_ids_g))
            db.execute(
                f"UPDATE ad_creative_structures SET is_pushed_clone = 1, role = 'test_clone' "
                f"WHERE user_id = ? AND platform = 'google' AND ad_id IN ({placeholders})",
                (user_id, *clone_ids_g),
            )
            # Invalidation detection: compare ingested values against stored combination
            pac_by_numeric = {r["platform_ad_numeric_id"]: r for r in pushed_rows_g}
            for clone_id in clone_ids_g:
                pac = pac_by_numeric.get(clone_id)
                if not pac:
                    continue
                combination = json.loads(pac["combination"] or "{}")
                mismatch = False
                for slot in ("headline", "description"):
                    expected = combination.get(slot)
                    if expected is None:
                        continue
                    cur = db.execute(
                        "SELECT value FROM ad_creative_structures "
                        "WHERE user_id = ? AND ad_id = ? AND slot = ? AND slot_index = 0",
                        (user_id, clone_id, slot),
                    ).fetchone()
                    if cur and cur["value"] != expected:
                        mismatch = True
                        break
                if mismatch:
                    db.execute(
                        "UPDATE pushed_ad_combos SET clone_status = 'clone_invalidated' WHERE id = ?",
                        (pac["id"],),
                    )

    # ── Filler extraction: write ad_parent_fillers for new Google parent RSAs ──
    parent_ad_ids = current_ad_ids - pushed_numeric_ids
    for pad_id in parent_ad_ids:
        existing = db.execute(
            "SELECT 1 FROM ad_parent_fillers WHERE parent_ad_id = ?", (pad_id,)
        ).fetchone()
        if existing:
            continue
        # Only extract for RSA ads
        rsa_check = db.execute(
            "SELECT 1 FROM ad_creative_structures "
            "WHERE user_id = ? AND ad_id = ? AND creative_type = 'rsa' LIMIT 1",
            (user_id, pad_id),
        ).fetchone()
        if not rsa_check:
            continue
        h1 = db.execute(
            "SELECT value FROM ad_creative_structures "
            "WHERE user_id = ? AND ad_id = ? AND slot = 'headline' AND slot_index = 1",
            (user_id, pad_id),
        ).fetchone()
        h2 = db.execute(
            "SELECT value FROM ad_creative_structures "
            "WHERE user_id = ? AND ad_id = ? AND slot = 'headline' AND slot_index = 2",
            (user_id, pad_id),
        ).fetchone()
        d1 = db.execute(
            "SELECT value FROM ad_creative_structures "
            "WHERE user_id = ? AND ad_id = ? AND slot = 'description' AND slot_index = 1",
            (user_id, pad_id),
        ).fetchone()
        filler_h1 = (h1["value"] if h1 and h1["value"] else "Learn More")
        filler_h2 = (h2["value"] if h2 and h2["value"] else "Get Started")
        filler_d1 = (d1["value"] if d1 and d1["value"] else "Find out more today.")
        db.execute(
            """
            INSERT OR IGNORE INTO ad_parent_fillers
                (parent_ad_id, filler_headline_1, filler_headline_2, filler_description_1)
            VALUES (?, ?, ?, ?)
            """,
            (pad_id, filler_h1, filler_h2, filler_d1),
        )

    # ── parent_should_pause: any clone_paused/clone_active rows for this campaign ──
    parent_should_pause = bool(
        db.execute(
            """
            SELECT 1 FROM pushed_ad_combos pac
            JOIN ad_creative_structures acs
                ON acs.ad_id = pac.seed_ad_id AND acs.user_id = pac.user_id
            WHERE pac.user_id = ? AND pac.platform = 'google'
              AND acs.campaign_id = ?
              AND pac.clone_status IN ('clone_paused', 'clone_active')
            LIMIT 1
            """,
            (user_id, campaign_id),
        ).fetchone()
    )

    db.commit()
    db.close()

    # ── Convergence check + native ad observations (fire-and-forget) ─────────
    asyncio.create_task(_check_google_convergence(
        user_id, campaign_id, access_token, customer_id, login_customer_id
    ))
    asyncio.create_task(_write_native_google_ad_observations(
        user_id, campaign_id, access_token, customer_id, login_customer_id
    ))
    asyncio.create_task(_store_native_google_display_metrics(
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
        parent_should_pause=parent_should_pause,
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
        SELECT acs.ad_id, acs.adset_id, acs.creative_type, acs.slot, acs.slot_index,
               acs.value, acs.lifecycle_status, acs.is_pushed_clone, acs.effective_status,
               pac.current_impressions, pac.days_running, pac.converged,
               pac.convergence_metric, pac.push_status, pac.clone_status,
               pac.platform_ad_id, pac.id AS combo_id, pac.ad_name AS ad_name,
               nai.impressions AS native_impressions, nai.clicks AS native_clicks,
               nai.spend AS native_spend, nai.ctr AS native_ctr, nai.cpm AS native_cpm
        FROM ad_creative_structures acs
        LEFT JOIN pushed_ad_combos pac
               ON pac.platform_ad_numeric_id = acs.ad_id AND pac.user_id = acs.user_id
        LEFT JOIN native_ad_insights nai
               ON nai.ad_id = acs.ad_id AND nai.user_id = acs.user_id
        WHERE acs.user_id = ? AND acs.campaign_id = ? AND acs.platform = 'google'
        ORDER BY acs.ad_id, acs.slot, acs.slot_index
        """,
        (user_id, campaign_id),
    ).fetchall()
    db.close()

    ads_map: dict[str, dict] = {}
    for row in rows:
        ad_id = row["ad_id"]
        if ad_id not in ads_map:
            if row["is_pushed_clone"]:
                clone_stats = {
                    "impressions": row["current_impressions"] or 0,
                    "days_running": row["days_running"] or 0,
                    "converged": bool(row["converged"]),
                    "ctr": row["convergence_metric"],
                    "push_status": row["push_status"],
                    "clone_status": row["clone_status"],
                    "platform_ad_id": row["platform_ad_id"],
                    "combo_id": row["combo_id"],
                }
            elif row["native_impressions"] is not None:
                clone_stats = {
                    "impressions": row["native_impressions"],
                    "clicks": row["native_clicks"],
                    "spend": row["native_spend"],
                    "ctr": row["native_ctr"],
                    "cpm": row["native_cpm"],
                }
            else:
                clone_stats = None
            ads_map[ad_id] = {
                "ad_id": ad_id,
                "ad_name": row["ad_name"] or None,
                "adset_id": row["adset_id"] or "",
                "campaign_id": campaign_id,
                "creative_type": row["creative_type"],
                "lifecycle_status": row["lifecycle_status"],
                "effective_status": row["effective_status"] or None,
                "is_pushed_clone": bool(row["is_pushed_clone"]),
                "clone_stats": clone_stats,
                "components": {},
            }
        else:
            cur = ads_map[ad_id]["effective_status"]
            new = row["effective_status"]
            if new and new != "UNKNOWN" and (not cur or cur == "UNKNOWN"):
                ads_map[ad_id]["effective_status"] = new
        slot = row["slot"]
        if not slot.startswith("_"):
            ads_map[ad_id]["components"].setdefault(slot, []).append(row["value"])

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
    source: str = "convergence",
) -> None:
    """Write a real-metric score to scored_observations."""
    # combination_key is the compound key {"combo":{...},"image_slot":N};
    # ad_text_combination_embeddings uses the text-only inner key
    try:
        _parsed = json.loads(combination_key)
        text_embed_key = json.dumps(
            _parsed["combo"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
    except (ValueError, KeyError):
        text_embed_key = combination_key
    tce = db.execute(
        "SELECT vector FROM ad_text_combination_embeddings WHERE source_id=? AND combination_key=?",
        (seed_ad_id, text_embed_key),
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
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, seed_ad_id, combination_key, combination_json,
             score, metric, source, text_vec_bytes, image_vec_bytes),
        )
    except Exception as exc:
        logger.warning("_write_convergence_observation failed for %s: %s", seed_ad_id, exc)


def _required_impressions(db: sqlite3.Connection, user_id: int, campaign_id: str) -> int:
    """
    Compute the impressions needed before a clone's CTR is reliable enough to trust.

    Uses the campaign's observed baseline CTR to set the bar dynamically:
      n = z² × (1-p) / (f² × p)
    where p = baseline CTR (decimal), f = CONVERGENCE_MARGIN_FRACTION, z = 1.96.

    A 3.5% baseline with f=0.20 requires ~2,650 impressions.
    A 0.5% baseline requires ~19,000. A 10% baseline requires ~850.
    Set CONVERGENCE_MARGIN_FRACTION=0.80 to converge quickly in demo mode.

    Falls back to CONVERGENCE_FLOOR when no baseline CTR is on record yet.
    """
    _Z2 = 3.8416  # 1.96²
    row = db.execute(
        """SELECT ctr FROM ad_insights
           WHERE user_id = ? AND object_id = ? AND level = 'campaign'
           ORDER BY created_at DESC LIMIT 1""",
        (user_id, campaign_id),
    ).fetchone()
    if not row or not row["ctr"] or float(row["ctr"]) <= 0:
        return CONVERGENCE_FLOOR
    p = min(float(row["ctr"]) / 100, 0.9999)  # ad_insights stores CTR as percentage
    n = _Z2 * (1 - p) / (CONVERGENCE_MARGIN ** 2 * p)
    return max(CONVERGENCE_FLOOR, int(n) + 1)


async def _check_meta_convergence(
    user_id: int, campaign_id: str, access_token: str
) -> None:
    """Fetch lifetime impressions for each non-settled pushed Meta clone in this
    campaign; advance clone_status and flip converged=1 when thresholds are met."""
    db = get_db()
    rows = db.execute(
        """
        SELECT pac.id, pac.platform_ad_id, pac.pushed_at,
               pac.seed_ad_id, pac.combination_key, pac.combination,
               pac.clone_status
        FROM pushed_ad_combos pac
        JOIN ad_creative_structures acs
            ON acs.ad_id = pac.seed_ad_id AND acs.user_id = pac.user_id
        WHERE pac.user_id = ? AND pac.platform = 'meta'
          AND pac.converged = 0
          AND pac.clone_status NOT IN ('clone_invalidated', 'clone_converged', 'clone_retained')
          AND acs.campaign_id = ?
        GROUP BY pac.id
        """,
        (user_id, campaign_id),
    ).fetchall()

    if not rows:
        db.close()
        return

    required_n = _required_impressions(db, user_id, campaign_id)
    db.close()

    async with httpx.AsyncClient(timeout=15) as client:
        for row in rows:
            try:
                # Fetch metrics and ad status in parallel — we need both.
                # Status must come from the platform (not inferred from impressions)
                # so a PAUSED clone that never got activated doesn't get promoted.
                insights_resp, status_resp = await asyncio.gather(
                    client.get(
                        f"{META_GRAPH}/{row['platform_ad_id']}/insights",
                        params={
                            "access_token": access_token,
                            "fields": "impressions,ctr",
                            "date_preset": "lifetime",
                        },
                    ),
                    client.get(
                        f"{META_GRAPH}/{row['platform_ad_id']}",
                        params={
                            "access_token": access_token,
                            "fields": "effective_status,status",
                        },
                    ),
                )
                if insights_resp.status_code != 200:
                    continue
                data = insights_resp.json().get("data", [])
                if not data:
                    continue

                impressions = int(data[0].get("impressions", 0) or 0)
                # Meta returns CTR as a percentage string (e.g. "4.52"); store as decimal
                ctr = float(data[0].get("ctr", 0) or 0) / 100

                # Learn activation from the platform, not from impressions proxy.
                effective_status = ""
                if status_resp.status_code == 200:
                    sj = status_resp.json()
                    effective_status = sj.get("effective_status") or sj.get("status") or ""

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
                if effective_status in ("ACTIVE", "ENABLED") and row["clone_status"] == "clone_paused":
                    db.execute(
                        "UPDATE pushed_ad_combos SET push_status = 'active', clone_status = 'clone_active' WHERE id = ?",
                        (row["id"],),
                    )
                if impressions >= required_n:
                    db.execute(
                        "UPDATE pushed_ad_combos SET converged = 1, converged_at = datetime('now'), "
                        "convergence_metric = ?, clone_status = 'clone_converged' WHERE id = ?",
                        (ctr, row["id"]),
                    )
                    logger.warning(
                        "convergence: Meta ad=%s converged (%d/%d impr, ctr=%.4f)",
                        row["platform_ad_id"], impressions, required_n, ctr,
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
    """Fetch status + impressions for non-settled pushed Google RSA clones in this
    campaign; advance clone_status and flip converged=1 when thresholds are met."""
    from google_ads_api import query_gaql as _query_gaql

    db = get_db()
    rows = db.execute(
        """
        SELECT pac.id, pac.platform_ad_id, pac.pushed_at,
               pac.seed_ad_id, pac.combination_key, pac.combination,
               pac.clone_status
        FROM pushed_ad_combos pac
        JOIN ad_creative_structures acs
            ON acs.ad_id = pac.seed_ad_id AND acs.user_id = pac.user_id
        WHERE pac.user_id = ? AND pac.platform = 'google'
          AND pac.converged = 0
          AND pac.clone_status NOT IN ('clone_invalidated', 'clone_converged', 'clone_retained')
          AND acs.campaign_id = ?
        GROUP BY pac.id
        """,
        (user_id, campaign_id),
    ).fetchall()

    if not rows:
        db.close()
        return

    required_n = _required_impressions(db, user_id, campaign_id)
    db.close()

    resource_name_map = {r["platform_ad_id"]: r for r in rows if r["platform_ad_id"]}
    if not resource_name_map:
        return

    gaql = f"""
        SELECT ad_group_ad.resource_name, ad_group_ad.status,
               metrics.impressions, metrics.ctr
        FROM ad_group_ad
        WHERE campaign.id = '{campaign_id}'
    """
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            ad_rows = await _query_gaql(
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

    by_resource: dict[str, dict] = {}
    for ar in ad_rows:
        rn = (ar.get("adGroupAd") or {}).get("resourceName", "")
        if rn:
            by_resource[rn] = {
                "status": (ar.get("adGroupAd") or {}).get("status", ""),
                "metrics": ar.get("metrics") or {},
            }

    for resource_name, row in resource_name_map.items():
        entry = by_resource.get(resource_name)
        if not entry:
            continue

        ad_status = entry["status"]  # "ENABLED" | "PAUSED" | "REMOVED"
        m = entry["metrics"]
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
        # ENABLED on platform → clone_active (even at 0 impressions)
        if ad_status == "ENABLED" and row["clone_status"] == "clone_paused":
            db.execute(
                "UPDATE pushed_ad_combos SET push_status = 'active', clone_status = 'clone_active' WHERE id = ?",
                (row["id"],),
            )
        if impressions >= required_n:
            db.execute(
                "UPDATE pushed_ad_combos SET converged = 1, converged_at = datetime('now'), "
                "convergence_metric = ?, clone_status = 'clone_converged' WHERE id = ?",
                (ctr, row["id"]),
            )
            logger.warning(
                "convergence: Google ad=%s converged (%d/%d impr, ctr=%.4f)",
                resource_name, impressions, required_n, ctr,
            )
            _write_convergence_observation(
                db, user_id,
                row["seed_ad_id"], row["combination_key"], row["combination"],
                ctr, "ctr",
            )
        db.commit()
        db.close()


async def _write_native_ad_observations(
    user_id: int, campaign_id: str, access_token: str
) -> None:
    """Fetch lifetime CTR for non-clone static Meta ads and write to scored_observations.
    Fires idempotently at every structural ingest so warm-start skips when native ads
    already have real metrics — no clone history required."""
    from ad_combination_embeddings.combinations import combination_key as _make_key

    db = get_db()
    ad_rows = db.execute(
        """SELECT DISTINCT ad_id FROM ad_creative_structures
           WHERE user_id=? AND campaign_id=? AND is_pushed_clone=0
             AND creative_type='static'""",
        (user_id, campaign_id),
    ).fetchall()
    db.close()

    if not ad_rows:
        return

    async with httpx.AsyncClient(timeout=15) as client:
        for row in ad_rows:
            ad_id = row["ad_id"]
            try:
                resp = await client.get(
                    f"{META_GRAPH}/{ad_id}/insights",
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
                if impressions <= 0:
                    continue
                ctr = float(data[0].get("ctr", 0) or 0) / 100

                db = get_db()
                # Only write if text combination embeddings already exist for this ad.
                # On first ingest the embedding hook runs concurrently; if it hasn't
                # finished yet the text_vector would be NULL and the BO would skip the
                # row while get_real_observation_count would still suppress warm-start.
                # Defer to the next structural ingest once embeddings are present.
                has_embedding = db.execute(
                    "SELECT 1 FROM ad_text_combination_embeddings WHERE source_id=? LIMIT 1",
                    (ad_id,),
                ).fetchone()
                if not has_embedding:
                    db.close()
                    logger.debug("native ad %s: embeddings not ready, deferring CTR write", ad_id)
                    continue

                slot_rows = db.execute(
                    """SELECT slot, value FROM ad_creative_structures
                       WHERE user_id=? AND ad_id=? AND slot_index=0
                         AND slot IN ('headline','primary_text','description')""",
                    (user_id, ad_id),
                ).fetchall()
                combo = {r["slot"]: r["value"] for r in slot_rows if r["value"]}
                if not combo:
                    db.close()
                    continue

                text_key = _make_key(combo)
                compound_key = json.dumps(
                    {"combo": json.loads(text_key), "image_slot": 0},
                    sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                )
                _write_convergence_observation(
                    db, user_id, ad_id, compound_key, json.dumps(combo),
                    ctr, "ctr", source="ingest_native",
                )
                db.commit()
                db.close()
                logger.info("native Meta ad observation: ad=%s ctr=%.4f impr=%d", ad_id, ctr, impressions)
            except Exception as exc:
                logger.warning("native Meta ad CTR ingest failed for %s: %s", ad_id, exc)


async def _write_native_google_ad_observations(
    user_id: int, campaign_id: str,
    access_token: str, customer_id: str, login_customer_id: str | None,
) -> None:
    """Fetch lifetime CTR for non-clone static Google ads and write to scored_observations."""
    from google_ads_api import query_gaql as _query_gaql
    from ad_combination_embeddings.combinations import combination_key as _make_key

    db = get_db()
    ad_rows = db.execute(
        """SELECT DISTINCT ad_id FROM ad_creative_structures
           WHERE user_id=? AND campaign_id=? AND is_pushed_clone=0
             AND creative_type='static' AND platform='google'""",
        (user_id, campaign_id),
    ).fetchall()
    db.close()

    if not ad_rows:
        return

    gaql = f"""
        SELECT ad_group_ad.resource_name, metrics.impressions, metrics.ctr
        FROM ad_group_ad
        WHERE campaign.id = '{campaign_id}'
          AND ad_group_ad.status != 'REMOVED'
    """
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            gaql_rows = await _query_gaql(
                client=client, gaql=gaql,
                api_version=GOOGLE_ADS_API_VERSION,
                customer_id=customer_id, access_token=access_token,
                developer_token=GOOGLE_DEVELOPER_TOKEN,
                login_customer_id=login_customer_id,
            )
        except Exception as exc:
            logger.warning("native Google ad CTR: GAQL failed: %s", exc)
            return

    native_ad_ids = {row["ad_id"] for row in ad_rows}
    metrics_by_rn = {
        (gr.get("adGroupAd") or {}).get("resourceName", ""): gr.get("metrics") or {}
        for gr in gaql_rows
    }

    db = get_db()
    for ad_id in native_ad_ids:
        m = metrics_by_rn.get(ad_id, {})
        impressions = int(m.get("impressions", 0) or 0)
        if impressions <= 0:
            continue
        ctr = float(m.get("ctr", 0) or 0)

        has_embedding = db.execute(
            "SELECT 1 FROM ad_text_combination_embeddings WHERE source_id=? LIMIT 1",
            (ad_id,),
        ).fetchone()
        if not has_embedding:
            logger.debug("native Google ad %s: embeddings not ready, deferring CTR write", ad_id)
            continue

        slot_rows = db.execute(
            """SELECT slot, value FROM ad_creative_structures
               WHERE user_id=? AND ad_id=? AND slot_index=0
                 AND slot IN ('headline','description')""",
            (user_id, ad_id),
        ).fetchall()
        combo = {r["slot"]: r["value"] for r in slot_rows if r["value"]}
        if not combo:
            continue

        text_key = _make_key(combo)
        compound_key = json.dumps(
            {"combo": json.loads(text_key), "image_slot": 0},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        try:
            _write_convergence_observation(
                db, user_id, ad_id, compound_key, json.dumps(combo),
                ctr, "ctr", source="ingest_native",
            )
            logger.info("native Google ad observation: ad=%s ctr=%.4f impr=%d", ad_id, ctr, impressions)
        except Exception as exc:
            logger.warning("native Google ad CTR write failed for %s: %s", ad_id, exc)
    db.commit()
    db.close()


async def _store_native_meta_display_metrics(
    user_id: int, campaign_id: str, access_token: str
) -> None:
    """Fetch lifetime insights for every non-clone Meta ad and write to native_ad_insights.
    Always writes (even when the server returns empty/zero) so the structure endpoint
    never has to generate synthetic data — the server is the only source of truth."""
    db = get_db()
    ad_rows = db.execute(
        """SELECT DISTINCT ad_id FROM ad_creative_structures
           WHERE user_id=? AND campaign_id=? AND is_pushed_clone=0""",
        (user_id, campaign_id),
    ).fetchall()
    db.close()

    if not ad_rows:
        return

    async with httpx.AsyncClient(timeout=15) as client:
        for row in ad_rows:
            ad_id = row["ad_id"]
            try:
                resp = await client.get(
                    f"{META_GRAPH}/{ad_id}/insights",
                    params={
                        "access_token": access_token,
                        "fields": "impressions,clicks,spend,ctr,cpm",
                        "date_preset": "lifetime",
                    },
                )
                if resp.status_code != 200:
                    continue
                data = resp.json().get("data", [])
                if data:
                    d = data[0]
                    impressions = int(d.get("impressions", 0) or 0)
                    clicks = int(d.get("clicks", 0) or 0) if "clicks" in d else None
                    spend = float(d.get("spend", 0) or 0) if "spend" in d else None
                    # Meta returns CTR as a percentage string (e.g. "3.5"); store as fraction
                    ctr = float(d.get("ctr", 0) or 0) / 100 if "ctr" in d else None
                    cpm = float(d.get("cpm", 0) or 0) if "cpm" in d else None
                else:
                    impressions, clicks, spend, ctr, cpm = 0, None, None, None, None

                db = get_db()
                db.execute(
                    """INSERT INTO native_ad_insights
                               (user_id, ad_id, platform, impressions, clicks, spend, ctr, cpm)
                           VALUES (?, ?, 'meta', ?, ?, ?, ?, ?)
                           ON CONFLICT (user_id, ad_id) DO UPDATE SET
                               impressions = excluded.impressions,
                               clicks      = excluded.clicks,
                               spend       = excluded.spend,
                               ctr         = excluded.ctr,
                               cpm         = excluded.cpm,
                               updated_at  = datetime('now')""",
                    (user_id, ad_id, impressions, clicks, spend, ctr, cpm),
                )
                db.commit()
                db.close()
            except Exception as exc:
                logger.warning("native Meta display metrics failed for ad=%s: %s", ad_id, exc)


async def _store_native_google_display_metrics(
    user_id: int, campaign_id: str,
    access_token: str, customer_id: str, login_customer_id: str | None,
) -> None:
    """Fetch metrics for every non-clone Google ad and write to native_ad_insights.
    Always writes (even when the server returns zero) — server is the only source of truth."""
    from google_ads_api import query_gaql as _query_gaql

    db = get_db()
    ad_rows = db.execute(
        """SELECT DISTINCT ad_id FROM ad_creative_structures
           WHERE user_id=? AND campaign_id=? AND is_pushed_clone=0 AND platform='google'""",
        (user_id, campaign_id),
    ).fetchall()
    db.close()

    if not ad_rows:
        return

    gaql = f"""
        SELECT ad_group_ad.resource_name, metrics.impressions, metrics.clicks,
               metrics.cost_micros, metrics.ctr, metrics.average_cpm
        FROM ad_group_ad
        WHERE campaign.id = '{campaign_id}'
          AND ad_group_ad.status != 'REMOVED'
    """
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            gaql_rows = await _query_gaql(
                client=client, gaql=gaql,
                api_version=GOOGLE_ADS_API_VERSION,
                customer_id=customer_id, access_token=access_token,
                developer_token=GOOGLE_DEVELOPER_TOKEN,
                login_customer_id=login_customer_id,
            )
        except Exception as exc:
            logger.warning("Google display metrics GAQL failed: %s", exc)
            return

    metrics_by_rn = {
        (gr.get("adGroupAd") or {}).get("resourceName", ""): gr.get("metrics") or {}
        for gr in gaql_rows
    }

    db = get_db()
    for row in ad_rows:
        ad_id = row["ad_id"]
        m = metrics_by_rn.get(ad_id, {})
        impressions = int(m.get("impressions", 0) or 0)
        clicks = int(m.get("clicks", 0) or 0) if "clicks" in m else None
        cost_micros = int(m.get("costMicros", m.get("cost_micros", 0)) or 0)
        spend = round(cost_micros / 1_000_000, 2) if cost_micros else None
        ctr = float(m.get("ctr", 0) or 0) if "ctr" in m else None
        cpm = float(m.get("averageCpm", m.get("average_cpm", 0)) or 0) if m else None
        try:
            db.execute(
                """INSERT INTO native_ad_insights
                           (user_id, ad_id, platform, impressions, clicks, spend, ctr, cpm)
                       VALUES (?, ?, 'google', ?, ?, ?, ?, ?)
                       ON CONFLICT (user_id, ad_id) DO UPDATE SET
                           impressions = excluded.impressions,
                           clicks      = excluded.clicks,
                           spend       = excluded.spend,
                           ctr         = excluded.ctr,
                           cpm         = excluded.cpm,
                           updated_at  = datetime('now')""",
                (user_id, ad_id, impressions, clicks, spend, ctr, cpm),
            )
        except Exception as exc:
            logger.warning("Google display metrics write failed for ad=%s: %s", ad_id, exc)
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
                     lifecycle_status, effective_status, data_source, mask_profile)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id, ad_id, slot, slot_index)
                DO UPDATE SET
                    creative_type    = excluded.creative_type,
                    value            = excluded.value,
                    ingested_at      = excluded.ingested_at,
                    lifecycle_status = excluded.lifecycle_status,
                    effective_status = excluded.effective_status,
                    data_source      = excluded.data_source,
                    mask_profile     = excluded.mask_profile
                """,
                (
                    user_id, ad_account_id, campaign_id, adset_id, ad_id,
                    creative_type, comp["slot"], comp["slot_index"],
                    comp["value"], ingested_at, lifecycle_status, raw_status,
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
            "SELECT id, platform_ad_id, combination FROM pushed_ad_combos "
            "WHERE user_id = ? AND platform = 'meta'",
            (user_id,),
        ).fetchall()
        pushed_platform_ids = {r["platform_ad_id"] for r in pushed_rows if r["platform_ad_id"]}
        clone_ids = current_ad_ids & pushed_platform_ids
        if clone_ids:
            placeholders = ",".join("?" * len(clone_ids))
            db.execute(
                f"UPDATE ad_creative_structures SET is_pushed_clone = 1, role = 'test_clone' "
                f"WHERE user_id = ? AND ad_id IN ({placeholders})",
                (user_id, *clone_ids),
            )
            # Invalidation detection: compare ingested values against stored combination
            pac_by_id = {r["platform_ad_id"]: r for r in pushed_rows if r["platform_ad_id"]}
            for clone_id in clone_ids:
                pac = pac_by_id.get(clone_id)
                if not pac:
                    continue
                combination = json.loads(pac["combination"] or "{}")
                mismatch = False
                for slot in ("headline", "description", "primary_text"):
                    expected = combination.get(slot)
                    if expected is None:
                        continue
                    cur = db.execute(
                        "SELECT value FROM ad_creative_structures "
                        "WHERE user_id = ? AND ad_id = ? AND slot = ? AND slot_index = 0",
                        (user_id, clone_id, slot),
                    ).fetchone()
                    if cur and cur["value"] != expected:
                        mismatch = True
                        break
                if mismatch:
                    db.execute(
                        "UPDATE pushed_ad_combos SET clone_status = 'clone_invalidated' WHERE id = ?",
                        (pac["id"],),
                    )

    # ── parent_should_pause: any clone_paused/clone_active rows for this campaign ──
    parent_should_pause = bool(
        db.execute(
            """
            SELECT 1 FROM pushed_ad_combos pac
            JOIN ad_creative_structures acs
                ON acs.ad_id = pac.seed_ad_id AND acs.user_id = pac.user_id
            WHERE pac.user_id = ? AND pac.platform = 'meta'
              AND acs.campaign_id = ?
              AND pac.clone_status IN ('clone_paused', 'clone_active')
            LIMIT 1
            """,
            (user_id, campaign_id),
        ).fetchone()
    )

    db.commit()
    db.close()

    # ── Convergence check + native ad observations (fire-and-forget) ─────────
    asyncio.create_task(_check_meta_convergence(user_id, campaign_id, access_token))
    asyncio.create_task(_write_native_ad_observations(user_id, campaign_id, access_token))
    asyncio.create_task(_store_native_meta_display_metrics(user_id, campaign_id, access_token))

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
        parent_should_pause=parent_should_pause,
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
        SELECT acs.ad_id, acs.adset_id, acs.creative_type, acs.slot, acs.slot_index,
               acs.value, acs.lifecycle_status, acs.is_pushed_clone, acs.effective_status,
               pac.current_impressions, pac.days_running, pac.converged,
               pac.convergence_metric, pac.push_status, pac.clone_status,
               pac.platform_ad_id, pac.id AS combo_id, pac.ad_name AS ad_name,
               nai.impressions AS native_impressions, nai.clicks AS native_clicks,
               nai.spend AS native_spend, nai.ctr AS native_ctr, nai.cpm AS native_cpm
        FROM ad_creative_structures acs
        LEFT JOIN pushed_ad_combos pac
               ON pac.platform_ad_numeric_id = acs.ad_id AND pac.user_id = acs.user_id
        LEFT JOIN native_ad_insights nai
               ON nai.ad_id = acs.ad_id AND nai.user_id = acs.user_id
        WHERE acs.user_id = ? AND acs.campaign_id = ?
        ORDER BY acs.ad_id, acs.slot, acs.slot_index
        """,
        (user_id, campaign_id),
    ).fetchall()
    db.close()

    ads_map: dict[str, dict] = {}
    for row in rows:
        ad_id = row["ad_id"]
        if ad_id not in ads_map:
            if row["is_pushed_clone"]:
                clone_stats = {
                    "impressions": row["current_impressions"] or 0,
                    "days_running": row["days_running"] or 0,
                    "converged": bool(row["converged"]),
                    "ctr": row["convergence_metric"],
                    "push_status": row["push_status"],
                    "clone_status": row["clone_status"],
                    "platform_ad_id": row["platform_ad_id"],
                    "combo_id": row["combo_id"],
                }
            elif row["native_impressions"] is not None:
                clone_stats = {
                    "impressions": row["native_impressions"],
                    "clicks": row["native_clicks"],
                    "spend": row["native_spend"],
                    "ctr": row["native_ctr"],
                    "cpm": row["native_cpm"],
                }
            else:
                clone_stats = None
            ads_map[ad_id] = {
                "ad_id": ad_id,
                "ad_name": row["ad_name"] or None,
                "adset_id": row["adset_id"],
                "campaign_id": campaign_id,
                "creative_type": row["creative_type"],
                "lifecycle_status": row["lifecycle_status"],
                "effective_status": row["effective_status"] or None,
                "is_pushed_clone": bool(row["is_pushed_clone"]),
                "clone_stats": clone_stats,
                "components": {},
            }
        else:
            # Prefer a real status over UNKNOWN (placement slot sorts first)
            cur = ads_map[ad_id]["effective_status"]
            new = row["effective_status"]
            if new and new != "UNKNOWN" and (not cur or cur == "UNKNOWN"):
                ads_map[ad_id]["effective_status"] = new
        slot = row["slot"]
        if not slot.startswith("_"):
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
    # 1. Fetch source ad to get page_id and the destination link URL.
    # REAL META: page_id is NOT stored in the DB — structural ingest ignores it.
    # It is always re-fetched from the platform here. If the source ad uses an
    # image-hash-only creative (no object_story_spec.page_id), this will 502.
    # Fake server fixture ads have page_id="fake_page_999" so this path always works locally.
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
    else:
        # BO combinations store the image as "image_url"; suggestion flow uses "image".
        # "picture" only works on real Meta if the URL is publicly accessible —
        # localhost URLs won't work; upload via _upload_image_to_meta first instead.
        _img_fallback = components.get("image") or components.get("image_url")
        if _img_fallback:
            link_data["picture"] = _img_fallback

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
    nearest_known: list[dict] = []   # explainability: cosine-nearest scored ads (pre-PCA space)
    placements: list[dict] = []
    # Lifecycle — populated by _enrich_pick after checking pushed_ad_combos
    ad_name: str | None = None
    already_pushed: bool = False
    push_status: str | None = None
    converged: bool = False
    platform_ad_id: str | None = None
    current_impressions: int = 0
    clone_status: str | None = None
    combo_id: int | None = None
    matches_existing_ad: dict | None = None  # {ad_id, effective_status} if combo matches a native static


class BORunRequest(BaseModel):
    seed_ad_id: str
    text_source_id: str  # usually same as seed_ad_id
    target_metric: str | None = None
    generator_id: str | None = None  # if set, overrides seed_ad_id/text_source_id


class GeneratorMember(BaseModel):
    ad_id: str
    contribution_mode: str = "dynamic"  # 'dynamic' (multi-asset) | 'static' (single fixed combo)


class GeneratorCreateRequest(BaseModel):
    name: str
    members: list[GeneratorMember]


class GeneratorResponse(BaseModel):
    id: str
    name: str
    members: list[GeneratorMember]
    created_at: str


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


def _find_native_static_match(user_id: int, combination_key: str, seed_ad_id: str) -> dict | None:
    """Return {ad_id, effective_status} if the combination_key matches a native static ad
    in the same campaign as seed_ad_id. Returns None if no match or seed not found."""
    db = get_db()
    seed_row = db.execute(
        "SELECT DISTINCT campaign_id, platform FROM ad_creative_structures "
        "WHERE user_id = ? AND ad_id = ? LIMIT 1",
        (user_id, seed_ad_id),
    ).fetchone()
    if not seed_row:
        db.close()
        return None
    campaign_id = seed_row["campaign_id"]
    platform = seed_row["platform"]
    rows = db.execute(
        """SELECT ad_id, slot, value, effective_status
           FROM ad_creative_structures
           WHERE user_id = ? AND campaign_id = ? AND platform = ?
             AND is_pushed_clone = 0 AND creative_type = 'static'
           ORDER BY ad_id, slot, slot_index""",
        (user_id, campaign_id, platform),
    ).fetchall()
    db.close()
    ads: dict[str, dict] = {}
    status_map: dict[str, str] = {}
    for row in rows:
        ad_id = row["ad_id"]
        slot = row["slot"]
        if not slot.startswith("_"):
            ads.setdefault(ad_id, {})[slot] = row["value"]
            if ad_id not in status_map:
                status_map[ad_id] = row["effective_status"]
    for ad_id, slots in ads.items():
        key = json.dumps(slots, sort_keys=True)
        if key == combination_key:
            return {"ad_id": ad_id, "effective_status": status_map.get(ad_id)}
    return None


def _enrich_pick(p: dict, seed_ad_id: str, user_id: int) -> BOPick:
    combo = _resolve_combination_image_url(p["combination"])
    placements = _get_placements_for_ad(seed_ad_id, user_id)
    db = get_db()
    pushed_row = db.execute(
        "SELECT id, ad_name, push_status, converged, platform_ad_id, "
        "current_impressions, clone_status "
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
            "clone_status": pushed_row["clone_status"],
            "combo_id": pushed_row["id"],
        }
    else:
        match = _find_native_static_match(user_id, p["combination_key"], seed_ad_id)
        if match:
            lifecycle["matches_existing_ad"] = match
    return BOPick(**{**p, "combination": combo, "placements": placements, **lifecycle})


def _resolve_generator(generator_id: str, user_id: int) -> tuple[list[str], list[str]]:
    """Return (all_member_ad_ids, dynamic_member_ad_ids) for a generator."""
    db = get_db()
    rows = db.execute(
        "SELECT ad_id, contribution_mode FROM ad_generator_members WHERE generator_id = ?",
        (generator_id,),
    ).fetchall()
    db.close()
    all_ids = [r["ad_id"] for r in rows]
    dynamic_ids = [r["ad_id"] for r in rows if r["contribution_mode"] == "dynamic"]
    return all_ids, dynamic_ids


@app.post("/api/generators", response_model=GeneratorResponse)
def create_generator(body: GeneratorCreateRequest, user_id: int = Depends(get_current_user_id)):
    """Create a named ad generator from one or more member ads."""
    import uuid
    gen_id = str(uuid.uuid4())
    db = get_db()
    db.execute(
        "INSERT INTO ad_generators (id, user_id, name) VALUES (?, ?, ?)",
        (gen_id, user_id, body.name),
    )
    for m in body.members:
        db.execute(
            "INSERT INTO ad_generator_members (generator_id, ad_id, contribution_mode) VALUES (?, ?, ?)",
            (gen_id, m.ad_id, m.contribution_mode),
        )
    created_at = db.execute(
        "SELECT created_at FROM ad_generators WHERE id = ?", (gen_id,)
    ).fetchone()["created_at"]
    db.commit()
    db.close()
    return GeneratorResponse(id=gen_id, name=body.name, members=body.members, created_at=created_at)


@app.get("/api/generators", response_model=list[GeneratorResponse])
def list_generators(user_id: int = Depends(get_current_user_id)):
    """List all ad generators for the current user."""
    db = get_db()
    gens = db.execute(
        "SELECT id, name, created_at FROM ad_generators WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    result = []
    for g in gens:
        members = db.execute(
            "SELECT ad_id, contribution_mode FROM ad_generator_members WHERE generator_id = ?",
            (g["id"],),
        ).fetchall()
        result.append(GeneratorResponse(
            id=g["id"], name=g["name"], created_at=g["created_at"],
            members=[GeneratorMember(ad_id=m["ad_id"], contribution_mode=m["contribution_mode"]) for m in members],
        ))
    db.close()
    return result


@app.delete("/api/generators/{generator_id}")
def delete_generator(generator_id: str, user_id: int = Depends(get_current_user_id)):
    """Delete an ad generator and its members."""
    db = get_db()
    result = db.execute(
        "DELETE FROM ad_generators WHERE id = ? AND user_id = ?", (generator_id, user_id)
    )
    db.execute("DELETE FROM ad_generator_members WHERE generator_id = ?", (generator_id,))
    db.commit()
    db.close()
    if result.rowcount == 0:
        raise HTTPException(404, "Generator not found")
    return {"deleted": generator_id}


def _get_pushed_exclude_keys(user_id: int, seed_ad_id: str) -> set[str]:
    """Return combination_keys already pushed for this seed, its generator(s), and sibling members.

    When an ad is a member of a generator, pushes from sibling members or the
    generator-level run are also excluded so per-member and generator-level runs
    stay consistent.
    """
    db = get_db()
    sibling_rows = db.execute(
        """SELECT m2.ad_id, m.generator_id
           FROM ad_generator_members m
           JOIN ad_generator_members m2 ON m2.generator_id = m.generator_id
           WHERE m.ad_id = ?""",
        (seed_ad_id,),
    ).fetchall()
    ids: set[str] = {seed_ad_id}
    for r in sibling_rows:
        ids.add(r["ad_id"])
        ids.add(r["generator_id"])
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT combination_key FROM pushed_ad_combos WHERE user_id = ? AND seed_ad_id IN ({placeholders})",
        [user_id, *sorted(ids)],
    ).fetchall()
    db.close()
    return {r["combination_key"] for r in rows}


@app.post("/api/bo/run", response_model=BORunResponse)
async def run_bo_endpoint(body: BORunRequest, user_id: int = Depends(get_current_user_id)):
    """
    Run Bayesian Optimisation for an ad (or ad generator) and return up to 2
    recommended combinations.

    If no scored observations exist yet, runs the warm-start mini BO first
    (invisible to the caller) to seed scored_observations with qwen_warm scores
    before the main BO runs.  Subsequent calls skip warm-start (idempotent).

    Pass generator_id to merge candidate pools and scored observations across
    multiple dynamic/static member ads. Pass seed_ad_id for a single-ad run.
    """
    from bo_pipeline.pipeline import run_bo
    from bo_pipeline.selector import get_scored_combinations, get_real_observation_count
    from bo_pipeline.storage import DB_PATH as BO_DB_PATH, save_bo_run
    from bo_pipeline.gpr import MIN_TRAINING_POINTS
    from warm_start import run_warm_start

    if body.generator_id:
        all_ids, dynamic_ids = _resolve_generator(body.generator_id, user_id)
        if not all_ids:
            raise HTTPException(400, f"Generator {body.generator_id} has no members")
        effective_seed = body.generator_id
        effective_source = body.generator_id
        seed_ad_ids = all_ids
        text_source_ids = all_ids
        image_ad_ids = all_ids
    else:
        effective_seed = body.seed_ad_id
        effective_source = body.text_source_id
        seed_ad_ids = text_source_ids = image_ad_ids = None

    # Warm-start: fire when real platform metrics (ctr/cvr/roas) are below the
    # GP minimum.  Qwen scores (qwen_warm) are the warm-start output — their
    # presence does not skip this check.  run_warm_start is idempotent.
    real_count = get_real_observation_count(
        effective_seed, user_id, BO_DB_PATH, seed_ad_ids=seed_ad_ids
    )
    if real_count < MIN_TRAINING_POINTS:
        logger.info("run_bo_endpoint: %d real observations for %s — running warm-start", real_count, effective_seed)
        await run_warm_start(effective_seed, effective_source, user_id, BO_DB_PATH)

    pushed_keys = _get_pushed_exclude_keys(user_id, effective_seed)
    picks, warning, scored_count, candidate_count = run_bo(
        effective_seed, effective_source, user_id, BO_DB_PATH,
        additional_exclude_keys=pushed_keys,
        target_metric=body.target_metric,
        seed_ad_ids=seed_ad_ids,
        text_source_ids=text_source_ids,
        image_ad_ids=image_ad_ids,
    )

    if picks:
        save_bo_run(effective_seed, effective_source, picks, BO_DB_PATH)

    return BORunResponse(
        seed_ad_id=effective_seed,
        text_source_id=effective_source,
        picks=[_enrich_pick(p, effective_seed, user_id) for p in picks],
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
    Run Bayesian Optimisation for a Google RSA (or ad generator) and return up to 2
    recommended combinations. Pass generator_id to merge pools across member ads.
    Falls back to random when fewer than MIN_TRAINING_POINTS scored variants exist.
    """
    from bo_pipeline.pipeline import run_bo
    from bo_pipeline.storage import DB_PATH as BO_DB_PATH, save_bo_run

    if body.generator_id:
        all_ids, dynamic_ids = _resolve_generator(body.generator_id, user_id)
        if not all_ids:
            raise HTTPException(400, f"Generator {body.generator_id} has no members")
        effective_seed = body.generator_id
        effective_source = body.generator_id
        seed_ad_ids = all_ids
        text_source_ids = all_ids
        image_ad_ids = all_ids
    else:
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
        effective_seed = body.seed_ad_id
        effective_source = body.text_source_id
        seed_ad_ids = text_source_ids = image_ad_ids = None

    pushed_keys = _get_pushed_exclude_keys(user_id, effective_seed)
    picks, warning, scored_count, candidate_count = run_bo(
        effective_seed, effective_source, user_id, BO_DB_PATH,
        platform="google", additional_exclude_keys=pushed_keys,
        target_metric=body.target_metric,
        seed_ad_ids=seed_ad_ids,
        text_source_ids=text_source_ids,
        image_ad_ids=image_ad_ids,
    )

    if picks:
        save_bo_run(effective_seed, effective_source, picks, BO_DB_PATH)

    return BORunResponse(
        seed_ad_id=effective_seed,
        text_source_id=effective_source,
        picks=[_enrich_pick(p, effective_seed, user_id) for p in picks],
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
    nearest_known: list[dict] = []   # explainability: cosine-nearest scored ads (pre-PCA space)
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
    target_metric: str | None = None


@app.post("/api/bo/cross-platform/unified", response_model=CrossPlatformBOResponse)
async def run_unified_cross_platform_bo_endpoint(
    body: UnifiedCrossPlatformBORequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    Unified cross-platform BO: per-group PCA each to the same K-dim output,
    pooled into a single GP/Modal call.  Returns top_n globally-ranked picks.

    Supports arbitrary mixes of platforms, campaigns, and ad types in one batch.
    top_n defaults to 4 and is configurable via the request body.

    If no real platform observations exist for a pair, runs warm-start (Qwen
    scoring) first — same logic as the single-platform /api/bo/run endpoint.
    """
    from bo_pipeline.cross_platform import run_unified_cross_platform_bo
    from bo_pipeline.gpr import MIN_TRAINING_POINTS
    from bo_pipeline.selector import get_real_observation_count
    from bo_pipeline.storage import DB_PATH as BO_DB_PATH, save_bo_run
    from warm_start import run_warm_start

    if not body.pairs:
        raise HTTPException(400, "At least one pair is required.")

    for pair in body.pairs:
        real_count = get_real_observation_count(pair.seed_ad_id, user_id, BO_DB_PATH)
        if real_count < MIN_TRAINING_POINTS:
            logger.info(
                "unified_cross_platform_bo: %d real observations for %s — running warm-start",
                real_count, pair.seed_ad_id,
            )
            await run_warm_start(pair.seed_ad_id, pair.text_source_id, user_id, BO_DB_PATH)

    pairs_dicts = [p.model_dump() for p in body.pairs]
    picks, raw_stats = run_unified_cross_platform_bo(
        pairs_dicts, user_id, BO_DB_PATH, top_n=body.top_n, target_metric=body.target_metric
    )

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
    """Look up ad group + final_url, create a PAUSED RSA.

    When ad_parent_fillers exist for the seed ad, all 5 slots are pinned so
    every impression shows exactly the BO-selected (headline, description) pair.
    Falls back to generated text variants (unpinned) when no fillers are stored.
    Returns the resource name of the created ad group ad.
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
    filler_row = db.execute(
        "SELECT filler_headline_1, filler_headline_2, filler_description_1 "
        "FROM ad_parent_fillers WHERE parent_ad_id = ?",
        (seed_ad_id,),
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

    # Prefer filler slots from ad_parent_fillers so every clone pins all 5 slots
    # and CTR measures exactly the BO-selected (headline, description) pair.
    if filler_row:
        headlines = [bo_headline, filler_row["filler_headline_1"], filler_row["filler_headline_2"]]
        descriptions = [bo_description, filler_row["filler_description_1"]]
        pin_all = True
    else:
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
        pin_all = False

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
        pin_all=pin_all,
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

    Golden path: dynamic template ad → BO recommends combination → push creates a
    PAUSED static clone → user activates the clone → real CTR feeds back into BO.
    The template (parent) ad should stay paused while clones are running.

    Meta:  creates a static ad via _clone_dynamic_to_static_ad.
    Google: creates a PAUSED RSA via _create_google_rsa_ad.

    Records the push in pushed_ad_combos so future BO runs exclude this
    combination and the UI can show lifecycle state.

    REAL META: requires Meta app to be in Live mode. Fake server (FAKE_META_BASE_URL)
    works without this restriction.

    TODO(spend): pushed clone inherits adset budget but has no per-ad spend cap.
    User must configure budget/bid in Meta Ads Manager before activating.
    Until a spend field is added to PushPickRequest and threaded through here,
    document this gap to users at the point of activation.
    """
    db = get_db()
    struct_row = db.execute(
        "SELECT ad_id, ad_account_id, adset_id FROM ad_creative_structures "
        "WHERE user_id = ? AND ad_id = ? LIMIT 1",
        (user_id, body.seed_ad_id),
    ).fetchone()

    # body.seed_ad_id may be a generator_id (UUID) rather than a real platform ad_id.
    # Generators don't appear in ad_creative_structures, so we resolve to the first
    # member ad to get a valid adset_id and source ad for the platform API call.
    # The generator_id is still stored in pushed_ad_combos so _get_pushed_exclude_keys
    # correctly excludes this combination from future generator BO runs.
    actual_seed_id = body.seed_ad_id
    if not struct_row:
        member_row = db.execute(
            """SELECT s.ad_id, s.ad_account_id, s.adset_id
               FROM ad_generator_members m
               JOIN ad_creative_structures s ON s.ad_id = m.ad_id AND s.user_id = ?
               WHERE m.generator_id = ?
               LIMIT 1""",
            (user_id, body.seed_ad_id),
        ).fetchone()
        if member_row:
            struct_row = member_row
            actual_seed_id = member_row["ad_id"]
    db.close()

    if not struct_row:
        raise HTTPException(400, f"No ingested structure for ad or generator {body.seed_ad_id}")

    if body.platform == "meta":
        access_token, ad_account_id = _meta_creds(user_id)
        adset_id = struct_row["adset_id"]
        if not adset_id:
            raise HTTPException(400, "No adset_id found for this ad — reingest the campaign")

        # BO combinations use key "image_url"; suggestion flow uses "image".
        # _upload_image_to_meta reads the file from disk (downloaded at ingest) and
        # uploads bytes to Meta → returns a hash usable in link_data.image_hash.
        # Without this, the clone creative has no image — silent on fake server, broken on real Meta.
        _img_value = body.combination.get("image") or body.combination.get("image_url")
        image_hash: str | None = None
        async with httpx.AsyncClient(timeout=30) as client:
            if _img_value:
                image_hash = await _upload_image_to_meta(
                    client, access_token, ad_account_id, _img_value
                )
            platform_ad_id = await _clone_dynamic_to_static_ad(
                client=client,
                access_token=access_token,
                ad_account_id=ad_account_id,
                adset_id=adset_id,
                source_ad_id=actual_seed_id,
                components=body.combination,
                suggestion_id=0,
                image_hash=image_hash,
                ad_name=body.name,
            )

    elif body.platform == "google":
        access_token, customer_id, login_customer_id = await _google_creds(user_id)
        platform_ad_id = await _create_google_rsa_ad(
            user_id=user_id,
            seed_ad_id=actual_seed_id,
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
             platform_ad_id, platform_ad_numeric_id, push_status, clone_status, pushed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'paused', 'clone_paused', datetime('now'))
        """,
        (user_id, body.platform, body.seed_ad_id, body.name,
         body.combination_key, json.dumps(body.combination), platform_ad_id, numeric_id),
    )
    db.commit()
    db.close()

    logger.info("push/pick: platform=%s seed=%s actual_seed=%s ad_id=%s name=%s",
                body.platform, body.seed_ad_id, actual_seed_id, platform_ad_id, body.name)
    return PushPickResponse(platform_ad_id=platform_ad_id, ad_name=body.name)


# ── Record a BO pick that matches an existing native ad (no new ad created) ───

class PushMatchRequest(BaseModel):
    platform: str
    seed_ad_id: str
    combination_key: str
    combination: dict
    existing_ad_id: str


class PushMatchResponse(BaseModel):
    platform_ad_id: str
    clone_status: str


@app.post("/api/push/match", response_model=PushMatchResponse)
def push_match(body: PushMatchRequest, user_id: int = Depends(get_current_user_id)):
    """Record that a BO pick matches an existing native static ad — no new ad is created.

    Writes a pushed_ad_combos row pointing at the existing ad so future BO runs
    exclude this combination. clone_status is derived from the ad's current
    effective_status (ACTIVE/ENABLED → clone_active, else clone_paused).
    """
    db = get_db()
    row = db.execute(
        "SELECT effective_status FROM ad_creative_structures "
        "WHERE user_id = ? AND ad_id = ? LIMIT 1",
        (user_id, body.existing_ad_id),
    ).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, f"Ad {body.existing_ad_id} not found in ingested structure")
    effective_status = row["effective_status"] or "UNKNOWN"
    clone_status = "clone_active" if effective_status in ("ACTIVE", "ENABLED") else "clone_paused"
    push_status = "active" if clone_status == "clone_active" else "paused"
    numeric_id = body.existing_ad_id.split("/")[-1]
    db.execute(
        """INSERT OR REPLACE INTO pushed_ad_combos
               (user_id, platform, seed_ad_id, ad_name, combination_key, combination,
                platform_ad_id, platform_ad_numeric_id, push_status, clone_status, pushed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (user_id, body.platform, body.seed_ad_id, body.existing_ad_id,
         body.combination_key, json.dumps(body.combination),
         body.existing_ad_id, numeric_id, push_status, clone_status),
    )
    db.commit()
    db.close()
    logger.info("push/match: platform=%s seed=%s existing_ad=%s clone_status=%s",
                body.platform, body.seed_ad_id, body.existing_ad_id, clone_status)
    return PushMatchResponse(platform_ad_id=body.existing_ad_id, clone_status=clone_status)


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
        "UPDATE pushed_ad_combos SET push_status = 'active', clone_status = 'clone_active' "
        "WHERE user_id = ? AND platform = ? AND platform_ad_id = ?",
        (user_id, body.platform, body.platform_ad_id),
    )
    db.commit()
    db.close()
    return {"status": "active", "platform_ad_id": body.platform_ad_id}


@app.post("/api/pause-ad")
async def pause_ad(body: ActivateRequest, user_id: int = Depends(get_current_user_id)):
    """Pause any ad (template or clone) on the platform."""
    if body.platform == "meta":
        access_token, _ = _meta_creds(user_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{META_GRAPH}/{body.platform_ad_id}",
                data={"status": "PAUSED", "access_token": access_token},
            )
        if resp.status_code != 200:
            raise HTTPException(502, f"Meta pause failed: {resp.text}")

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
                        "status": "PAUSED",
                    },
                    "updateMask": "status",
                }
            }]
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=mutate_body, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(502, f"Google pause failed: {resp.text}")
    else:
        raise HTTPException(400, f"Unsupported platform: {body.platform}")

    db = get_db()
    db.execute(
        "UPDATE pushed_ad_combos SET push_status = 'paused', clone_status = 'clone_paused' "
        "WHERE user_id = ? AND platform = ? AND platform_ad_id = ?",
        (user_id, body.platform, body.platform_ad_id),
    )
    db.commit()
    db.close()
    return {"status": "paused", "platform_ad_id": body.platform_ad_id}


@app.post("/api/push/retain/{combo_id}")
def retain_clone(combo_id: int, user_id: int = Depends(get_current_user_id)):
    """Mark a converged test clone as retained — it keeps running but BO stops
    writing new observations for it."""
    db = get_db()
    result = db.execute(
        "UPDATE pushed_ad_combos SET clone_status = 'clone_retained' "
        "WHERE id = ? AND user_id = ?",
        (combo_id, user_id),
    )
    db.commit()
    db.close()
    if result.rowcount == 0:
        raise HTTPException(404, "Combo not found")
    return {"status": "clone_retained", "combo_id": combo_id}


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

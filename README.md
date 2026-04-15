# AdStac.kr — Meta Ads Demo

A minimal SaaS control-plane for Meta ad accounts. Connects to the Meta Marketing
API, ingests campaign and creative structure, tracks lifecycle status, and provides
scaffolding for an AI-driven ad suggestion and static-clone workflow.

## Architecture

```
frontend/ (React + Vite, port 5173)
  └── calls ──▶ backend/ (FastAPI, port 8000)
                   └── calls ──▶ Meta Marketing API (graph.facebook.com)
```

All backend logic lives in `backend/main.py` (single-file FastAPI app).
The database is SQLite (`backend/app.db`, auto-created on first run).

For schema details see [`SCHEMAS.md`](SCHEMAS.md).
For running in production/EC2 with ngrok see [`NGROK_SETUP.md`](NGROK_SETUP.md).

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- A Meta Developer App (in Dev mode) with:
  - **Marketing API** product enabled
  - OAuth redirect URI registered (see Setup below)
  - Your App ID and App Secret
- A Meta test ad account with at least one campaign

---

## Setup

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Fill in:
#   META_APP_ID=<your app id>
#   META_APP_SECRET=<your app secret>
#   META_REDIRECT_URI=http://localhost:8000/auth/meta/callback
#   JWT_SECRET=<any random string>
#   # Optional — these have defaults:
#   META_API_VERSION=v19.0
#   FRONTEND_URL=http://localhost:5173

python main.py
# → http://localhost:8000
```

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env   # VITE_API_URL defaults to http://localhost:8000 (usually no change needed)
npm run dev
# → http://localhost:5173
```

### 3. Tests

```bash
cd backend
source .venv/bin/activate
python -m pytest test_structural_ingest.py test_suggestions.py -v
```

---

## Feature Walkthrough

### Connect Meta

1. Sign up at `http://localhost:5173` → land on Auth page
2. Navigate to **Settings** → click **Connect Meta Ads Account**
3. Approve access on Meta's OAuth screen → redirected back with `?meta_connected=true`

### Campaigns

The Campaigns page shows live data from Meta. Each campaign row supports:

- **Pause / Resume** — calls Meta API directly; status updates in place
- **History** — expands stored metric snapshots (requires running Ingest first)
- **Creatives** — triggers structural ingest for that campaign, then shows an inline panel:
  - Creative slots per ad (headline, primary text, description, image)
  - **Lifecycle badge** per ad: `active`, `inactive`, or `no longer in Meta` (missing)
  - **Suggestions panel** — lists any stored suggestions for this campaign with a Confirm Create button

### Preview & Ingest

Click **Preview & Ingest** in the campaigns header to:
1. Preview campaigns and ads live from Meta (nothing saved)
2. Confirm to persist metric snapshots to `ad_insights` for history tracking

### Suggestions (scaffolding)

The suggestion workflow is partially implemented:

1. An external suggestion API POSTs to `POST /api/suggestions` with a chosen set of component values derived from a dynamic ad
2. The Campaigns page shows pending suggestions in the Creatives panel
3. Clicking **Confirm Create** calls `POST /api/suggestions/{id}/confirm` with `action="create"`:
   - Fetches the source dynamic ad from Meta to get `page_id` and destination URL
   - Creates a new ad creative in Meta with the chosen components
   - Creates a new ad in the target adset (`status=PAUSED`)
   - Persists the returned Meta ad id as `static_ad_id`
   - Updates `deployment_status` → `created_static`

The new static ad is always created **PAUSED**; activate it manually in Meta Ads Manager.

---

## API Reference

All routes except auth require `Authorization: Bearer <jwt>`.

### Auth

| Method | Path | Description |
|---|---|---|
| POST | `/auth/signup` | Create account, returns JWT |
| POST | `/auth/login` | Login, returns JWT |
| GET | `/me/meta-status` | Check Meta OAuth connection |
| GET | `/auth/meta/login-url` | Start Meta OAuth flow |
| GET | `/auth/meta/callback` | Meta OAuth callback (browser redirect) |

### Campaigns

| Method | Path | Description |
|---|---|---|
| GET | `/api/campaigns` | Live campaigns + 7d metrics from Meta |
| POST | `/api/campaigns/{id}/pause` | Pause a campaign |
| POST | `/api/campaigns/{id}/resume` | Resume a campaign |
| GET | `/api/campaigns/{id}/history` | Stored metric snapshots (`?days=30`) |

### Ingest

| Method | Path | Description |
|---|---|---|
| GET | `/api/ingest/preview` | Preview campaigns + ads without saving |
| POST | `/api/ingest` | Save campaign metric snapshots to `ad_insights` |
| POST | `/api/ingest/structure/{campaign_id}` | Normalize + persist creative structure; compute lifecycle status |
| GET | `/api/structure/{campaign_id}` | Read persisted structure grouped by ad |

### Suggestions

| Method | Path | Description |
|---|---|---|
| GET | `/api/suggestions` | List suggestions (`?campaign_id=` optional filter) |
| POST | `/api/suggestions` | Store a suggested configuration |
| POST | `/api/suggestions/{id}/confirm` | Confirm create or replace |

### Other

| Method | Path | Description |
|---|---|---|
| GET | `/api/ads` | Live ad creatives from Meta |
| GET | `/api/explore` | Raw Meta data (campaigns → adsets → ads) for debugging |

---

## Notes

- **Auth:** Minimal JWT, 24h expiry. No email verification, rate limiting, or refresh. Not for production.
- **Meta token:** Short-lived user token stored as-is. No refresh or System User token logic.
- **SQLite:** `backend/app.db` is gitignored. Delete it to reset all data.
- **Insights:** 7-day metrics on `GET /api/campaigns` are best-effort; failures return `null` fields.
- **Static ad images:** The clone flow passes `image` values as hosted URLs (`picture` in Meta's `link_data`). Creatives whose images are stored only as hashes (not URLs) will fail at Meta creative creation — a known limitation for this phase.

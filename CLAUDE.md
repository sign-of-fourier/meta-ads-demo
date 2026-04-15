# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend (FastAPI)
```bash
cd backend
source .venv/bin/activate
python main.py          # runs uvicorn on http://localhost:8000 with --reload
```

### Frontend (React + Vite)
```bash
cd frontend
npm run dev             # http://localhost:5173
npm run build
npm run preview
```

### Environment setup (first time)
```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill in META_APP_ID, META_APP_SECRET, JWT_SECRET

# Frontend
cd frontend
npm install
cp .env.example .env    # VITE_API_URL defaults to http://localhost:8000
```

## Architecture

```
frontend/ (React + Vite, port 5173)
  └── calls ──▶ backend/ (FastAPI, port 8000)
                   └── calls ──▶ Meta Marketing API (graph.facebook.com)
```

### Backend (`backend/main.py`)
Single-file FastAPI application. All routes, models, and DB logic live here. Key sections:

- **SQLite** (`backend/app.db`, auto-created on first run) — 4 tables: `users`, `meta_connections`, `oauth_states`, `ad_insights`
- **JWT auth** — 24h tokens, Bearer header, decoded via `get_current_user_id()` dependency
- **Meta OAuth flow** — CSRF state stored in `oauth_states` table; callback exchanges code for token, fetches ad accounts, persists first account in `meta_connections`
- **Campaign ingestion** — `GET /api/campaigns` fetches campaigns + 7d insights in two parallel Meta API calls (avoids N+1), then writes snapshots to `ad_insights`
- **`_meta_creds(user_id)`** — shared helper that loads `(access_token, ad_account_id)` from DB for the authenticated user

### Frontend (`frontend/src/`)
- `api.js` — single fetch wrapper; stores JWT in `localStorage`; all API calls go through here
- `App.jsx` — root layout with nav; uses React Router `<Outlet>`
- `pages/AuthPage.jsx` — signup/login
- `pages/SettingsPage.jsx` — Meta OAuth connect flow, reads `?meta_connected=true` redirect param
- `pages/CampaignsPage.jsx` — campaign table with pause/resume actions
- `pages/AdsPage.jsx` — ad creatives listing

### Required env vars (backend)
| Variable | Description |
|---|---|
| `META_APP_ID` | Meta Developer App ID |
| `META_APP_SECRET` | Meta Developer App Secret |
| `META_REDIRECT_URI` | Must be `http://localhost:8000/auth/meta/callback` |
| `META_API_VERSION` | Defaults to `v19.0` |
| `JWT_SECRET` | Any random string |
| `FRONTEND_URL` | Defaults to `http://localhost:5173` |

## Key constraints
- The Meta access token stored is a **short-lived user token** — no refresh logic exists
- Insights on `GET /api/campaigns` are best-effort (failures are silently swallowed)
- `ad_insights` accumulates a new row per campaign per `GET /api/campaigns` call (no deduplication by date)
- No test suite exists; manual walkthrough is described in `README.md`

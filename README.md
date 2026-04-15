# Meta Ads Demo

Minimal SaaS control-plane that connects to a Meta test ad account, lists
campaigns, and lets you pause/resume them from a custom UI.

## Architecture

```
frontend/ (React + Vite, port 5173)
  └── calls ──▶ backend/ (FastAPI, port 8000)
                   └── calls ──▶ Meta Marketing API
```

## Prerequisites

- Python 3.11+
- Node.js 18+
- A Meta Developer App (in Dev mode) with:
  - **Marketing API** product enabled
  - OAuth redirect URI set to `http://localhost:8000/auth/meta/callback`
  - Your App ID and App Secret
- A Meta test ad account with at least one campaign

## Setup

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and fill in:
#   META_APP_ID=<your app id>
#   META_APP_SECRET=<your app secret>
#   META_REDIRECT_URI=http://localhost:8000/auth/meta/callback
#   META_API_VERSION=v19.0
#   FRONTEND_URL=http://localhost:5173
#   JWT_SECRET=<any random string>

python main.py
```

Backend runs on http://localhost:8000.

### 2. Frontend

```bash
cd frontend
npm install

cp .env.example .env
# VITE_API_URL=http://localhost:8000  (default, usually no change needed)

npm run dev
```

Frontend runs on http://localhost:5173.

## Acceptance Test Walkthrough

1. Open http://localhost:5173 → you land on the Auth page.
2. **Sign Up** with any email/password.
3. You're taken to **Settings**. Click **"Connect Meta Ads Account"**.
4. You're redirected to Meta's consent screen. Approve access.
5. You're sent back to Settings. You see **"Connected to ad account act_XXXXX"**.
6. Navigate to **Campaigns**. You see a table of campaigns from the ad account.
7. Click **Pause** on an ACTIVE campaign. The row updates to PAUSED.
8. Verify in Meta Ads Manager — the campaign is now Paused there too.

## API Routes

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/signup` | – | Create account, get JWT |
| POST | `/auth/login` | – | Log in, get JWT |
| GET | `/me/meta-status` | JWT | Meta connection status |
| GET | `/auth/meta/login-url` | JWT | Get Meta OAuth URL |
| GET | `/auth/meta/callback` | – | OAuth callback (browser redirect) |
| GET | `/api/campaigns` | JWT | List campaigns from Meta |
| POST | `/api/campaigns/{id}/pause` | JWT | Pause a campaign |
| POST | `/api/campaigns/{id}/resume` | JWT | Resume a campaign |

## Notes

- **Persistence**: SQLite (`backend/app.db`), auto-created on first run.
- **Auth**: Minimal JWT auth — no email verification, no refresh tokens, no rate
  limiting. Not for production use.
- **Token storage**: The Meta access token is a short-lived user token. For
  production you'd exchange it for a long-lived token or use a System User token.
- **Insights**: 7-day spend is fetched per-campaign. If the campaign has no data
  or the request fails, it returns `null` (displayed as "–" in the UI).

# ngrok + Meta OAuth Setup Guide

A complete reference for running this app on EC2 with ngrok and connecting Meta OAuth.

---

## Architecture

```
Browser
  └──▶ https://<your-ngrok-subdomain>.ngrok-free.dev   (ngrok HTTPS tunnel)
              └──▶ EC2 :5173  (Vite dev server)
                      └──▶ Vite proxy: /api/*, /auth/*, /me/*
                                    └──▶ localhost:8000  (FastAPI backend)
```

Meta OAuth requires HTTPS for redirect URIs. ngrok provides that tunnel so you don't need a certificate on the EC2 instance itself.

---

## 1. ngrok Setup

### Install & authenticate
Follow ngrok's quickstart at https://dashboard.ngrok.com/get-started/setup to install and run `ngrok config add-authtoken <token>`.

### Start the tunnel

```bash
ngrok http 5173         # prod
ngrok http 5174         # staging
```

Run `./start.sh` to print a reminder of all ports if you forget which is which.
Point ngrok at the **Vite port**, not 8000. Vite's proxy handles forwarding to the backend internally — the browser never needs to reach port 8000 directly.

### AWS VPC / Security Groups
If ngrok fails to establish a tunnel from EC2, your VPC security group may be blocking outbound traffic. ngrok connects outbound on port 443 (or 80). Fix:
- Go to **EC2 → Security Groups → your instance's group → Outbound rules**
- Ensure there is an outbound rule allowing **HTTPS (443)** to `0.0.0.0/0`
- See ngrok's firewall/VPC guide: https://ngrok.com/docs/guides/running-behind-firewalls/

### Free tier interstitial page
On the free ngrok tier, first-time visitors (and incognito sessions) see a warning page: "You are about to visit...". This is ngrok's browser interstitial, not a caching issue.

To bypass it for API requests, add this header to Vite's dev server config (`vite.config.js`):
```js
server: {
  headers: {
    "ngrok-skip-browser-warning": "true",
  },
}
```
For browser visits, just click through the interstitial once per session. It won't appear again until you open a new incognito window or clear cookies.

---

## 2. Vite Dev Server Config (`frontend/vite.config.js`)

```js
server: {
  host: "0.0.0.0",   // bind to all interfaces (needed if you also want direct EC2 IP access)
                      // change to "127.0.0.1" if only using ngrok (more secure)
  port: 5173,         // overridable via VITE_PORT env var
  allowedHosts: ["<your-ngrok-subdomain>.ngrok-free.dev"],  // must be updated when ngrok URL changes
  proxy: {
    "/api":          "http://localhost:8000",  // overridable via VITE_BACKEND_URL env var
    "/auth/signup":  "http://localhost:8000",
    "/auth/login":   "http://localhost:8000",
    "/auth/meta":    "http://localhost:8000",
    "/auth/google":  "http://localhost:8000",
    "/me":           "http://localhost:8000",
    "/images":       "http://localhost:8000",  // serves generated images
    "/ad-images":    "http://localhost:8000",  // serves downloaded Meta CDN images
  },
  headers: {
    "ngrok-skip-browser-warning": "true",  // bypasses ngrok free tier interstitial for API calls
  },
}
```

**What the proxy does:** Vite intercepts any request from the browser matching those paths and forwards it to the FastAPI backend on port 8000. This means `api.js` can use relative paths (e.g. `/api/campaigns`) and the browser never makes a direct HTTP call to port 8000 — avoiding mixed-content issues when the frontend is served over HTTPS via ngrok.

**`allowedHosts`** must include your ngrok subdomain, otherwise Vite will reject incoming requests from that host.

---

## 3. Backend `.env` Updates

Every time your ngrok URL changes (free tier subdomains are random and reset on restart), update `backend/.env`:

```env
META_REDIRECT_URI=https://<your-ngrok-subdomain>.ngrok-free.dev/auth/meta/callback
FRONTEND_URL=https://<your-ngrok-subdomain>.ngrok-free.dev
```

Then **restart the backend** — FastAPI reads `.env` at startup and won't pick up changes otherwise.

---

## 4. Meta Developer App Settings

Go to https://developers.facebook.com → your app, and update these fields:

### App Settings → Basic
| Field | Value |
|---|---|
| **App Domains** | `<your-ngrok-subdomain>.ngrok-free.dev` |
| **Site URL** | `https://<your-ngrok-subdomain>.ngrok-free.dev` |

### Facebook Login → Settings
| Field | Value |
|---|---|
| **Valid OAuth Redirect URIs** | `https://<your-ngrok-subdomain>.ngrok-free.dev/auth/meta/callback` |

**All three must be set.** App Domains alone is not enough — Meta also requires the full callback URL registered under Valid OAuth Redirect URIs. Missing the redirect URI is the most common cause of the "domain not included in app's domains" error even when App Domains looks correct.

---

## 5. Checklist When ngrok URL Changes

Free tier ngrok subdomains change every time you restart ngrok. Go through this list:

### Frontend + backend config
- [ ] Update `allowedHosts` in `frontend/vite.config.js`
- [ ] Update `FRONTEND_URL` in `backend/.env`
- [ ] Restart the Vite dev server (`npm run dev`)
- [ ] Restart the FastAPI backend (`python main.py`)

### Meta OAuth
- [ ] Update `META_REDIRECT_URI` in `backend/.env`
- [ ] Update **App Domains** in Meta Developer App
- [ ] Update **Site URL** in Meta Developer App
- [ ] Update **Valid OAuth Redirect URIs** in Meta → Facebook Login → Settings

### Google OAuth
- [ ] Update `GOOGLE_REDIRECT_URI` in `backend/.env`
- [ ] Go to [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials → your OAuth 2.0 client
- [ ] Under **Authorized redirect URIs**, replace the old ngrok URL with the new one: `https://<your-ngrok-subdomain>.ngrok-free.dev/auth/google/callback`
- [ ] Save

To avoid this churn, consider upgrading to a paid ngrok plan which gives you a fixed custom subdomain.

---

## 6. Troubleshooting

| Symptom | Likely cause |
|---|---|
| "The domain of this URL isn't included in the app's domains" | Redirect URI not added to **Valid OAuth Redirect URIs** (not just App Domains), or a typo in the domain |
| ngrok interstitial page in incognito | Expected on free tier — click through, or add `ngrok-skip-browser-warning` header |
| OAuth callback still redirecting to old URL | Backend not restarted after `.env` change |
| ngrok tunnel won't start on EC2 | Outbound port 443 blocked by VPC security group |
| Vite returns 403 for ngrok requests | ngrok subdomain missing from `allowedHosts` in `vite.config.js` |

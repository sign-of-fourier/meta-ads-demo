# Fake Ad Server — Status

Goal: a standalone HTTP server that speaks the Meta Graph API and Google Ads API dialects,
so the real backend provider code runs end-to-end but never touches live platforms.
**Delete this file when the browser smoke-test passes.**

---

## Status

| Step | Done? | Notes |
|---|---|---|
| 1. Scaffold `fake_ad_server/` + `server.py` + `requirements.txt` | ✅ | |
| 2. Seed fixture files from demo providers | ✅ | 8 JSON files in `fake_ad_server/fixtures/` |
| 3. Meta routes | ✅ | All routes, not just campaigns/insights |
| 4. Google routes | ✅ | All 4 GAQL variants + mutate + customer info |
| 5. Backend base-URL env vars (2-line change) | ✅ | `meta_live.py` + `google_ads_api.py` |
| 6. `.env` switch (commented stubs) | ✅ | In `backend/.env` |
| 7. `start.sh` entry | ✅ | Listed in cheat sheet output |
| 8. Curl smoke test | ✅ | Every route confirmed correct shape |
| 9. Browser smoke test | ❌ | **Next: flip the switch and hit Sync** |

---

## What remains

### Browser smoke test (next session)

1. Start fake server: `cd fake_ad_server && uvicorn server:app --port 9000 --reload`
2. In `backend/.env` uncomment:
   ```
   FAKE_META_BASE_URL=http://localhost:9000/meta/v19.0
   FAKE_GOOGLE_BASE_URL=http://localhost:9000/google
   ```
3. Restart backend
4. Log in, go to Campaigns page, hit **Sync**
5. Verify fake campaigns load with metrics
6. Hit **Ingest** on a campaign — verify structure populates (adsets + dynamic ad slots)
7. Check Google campaigns tab loads too

### `start.sh` entry

`./start.sh` now prints a cheat sheet (does not start anything). The fake ad server (:9000) is the only fake server.

---

## What it is NOT

- Not a replacement for the masking layer (masking replaces *fields*; this replaces the *network*).
- Not touched by `main.py`, `providers/`, or `frontend/` — lives in `fake_ad_server/`.
- Not involved in OAuth. Auth flows still go to real Google/Meta.

---

## How to activate / deactivate

**Activate:** uncomment both lines in `backend/.env`, restart backend.

```bash
FAKE_META_BASE_URL=http://localhost:9000/meta/v19.0
FAKE_GOOGLE_BASE_URL=http://localhost:9000/google
```

**Deactivate:** comment both lines back out, restart backend.

No code changes needed either way.

---

## Case 1 vs Case 2

The fake server has two operating modes controlled by env vars at startup.

**Case 1 — pre-warmed (default)**

```bash
cd fake_ad_server && uvicorn server:app --port 9000 --reload
```

Fixture campaigns return real (random) metrics. After structural ingest, native static
ads write their CTR to `scored_observations`. BO runs without warm-start on the first
click.

**Case 2 — cold start + fast demo loop**

```bash
cd fake_ad_server && COLD_START=true FAST_RAMP=true uvicorn server:app --port 9000 --reload
# backend/.env also needs:
CONVERGENCE_MARGIN_FRACTION=0.80
```

`COLD_START=true` makes all fixture campaign and ad insights return zero, so ingest sees
no metrics and `scored_observations` stays empty. Warm-start (Qwen) fires on the first
BO run.

`FAST_RAMP=true` means pushed clone ads are treated as having 24 hours of delivery from
the moment they are pushed (~360–1560 impressions immediately).

`CONVERGENCE_MARGIN_FRACTION=0.80` widens the acceptable CI to ±80% of the baseline CTR,
reducing the required impression count to ~100–200. Combined with FAST_RAMP, a pushed
clone converges on the very next ingest after push.

**Why you need CONVERGENCE_MARGIN_FRACTION:** the convergence threshold is now computed
from the campaign's baseline CTR — `n = z² × (1-p) / (f² × p)` where `f` is the margin
fraction. At the production default of f=0.20 (±20% CI), a 3.5% CTR campaign needs
~2,650 impressions. FAST_RAMP's 24-hour simulation only gives ~360–1560, so convergence
won't fire without widening the margin for demo purposes.

**Switching between cases:** change the env vars and restart the fake server, then click
Sync + Ingest Structure in the UI. No DB changes needed — ingest drives app state.

---

## Directory layout

```
fake_ad_server/
  server.py               FastAPI app; mounts /meta and /google routers
  routes/
    meta.py               All Graph API endpoints (catch-all dispatcher)
    google.py             All Google Ads REST endpoints (catch-all dispatcher)
  fixtures/
    meta_campaigns.json   3 campaigns (2 ACTIVE, 1 PAUSED)
    meta_insights.json    7-day metrics per campaign
    meta_adsets.json      adsets keyed by campaign_id
    meta_ads.json         ads keyed by campaign_id (camp 1=dynamic, 2&3=static)
    google_campaigns.json GAQL rows for campaigns query
    google_adgroups.json  GAQL rows for ad_group query, keyed by campaign_id
    google_ads.json       GAQL rows for ad_group_ad query, keyed by campaign_id
    google_pmax.json      GAQL rows for asset_group_asset query, keyed by campaign_id
  requirements.txt        fastapi, uvicorn only
  README.md
```

---

## Backend changes made (permanent, safe)

**`backend/providers/meta_live.py` line 13:**
```python
META_GRAPH = os.getenv(
    "FAKE_META_BASE_URL",
    f"https://graph.facebook.com/{_META_API_VERSION}",
)
```

**`backend/google_ads_api.py` line 10:**
```python
_GOOGLE_ADS_BASE = os.getenv("FAKE_GOOGLE_BASE_URL", "https://googleads.googleapis.com")
```

Both default to the real URLs when the env vars are unset — zero impact on production.

---

## GAQL dispatch (Google routes)

The `searchStream` handler inspects the `FROM` clause of the GAQL body:

| FROM clause | Fixture |
|---|---|
| `FROM campaign` | `google_campaigns.json` |
| `FROM ad_group` | `google_adgroups.json[campaign_id]` |
| `FROM ad_group_ad` | `google_ads.json[campaign_id]` |
| `FROM asset_group_asset` | `google_pmax.json[campaign_id]` |

`campaign_id` is extracted from `WHERE campaign.id = <id>` via regex.

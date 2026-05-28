# Fake Ad Server

A standalone FastAPI server that mimics the Meta Graph API and Google Ads REST API,
so the real backend provider code runs end-to-end without touching live platforms.

## Start

```bash
cd fake_ad_server
pip install -r requirements.txt          # one-time
uvicorn server:app --port 9000 --reload
```

Health check: `curl http://localhost:9000/`

## Activate the switch

In `backend/.env`, uncomment both lines:

```bash
FAKE_META_BASE_URL=http://localhost:9000/meta/v19.0
FAKE_GOOGLE_BASE_URL=http://localhost:9000/google
```

Restart the backend. OAuth still goes to real Google/Meta. All data API calls
(campaigns, insights, structure, push) now hit the fake server instead.

To deactivate: comment the lines back out and restart.

## What is and isn't faked

| Faked | Not faked |
|---|---|
| `GET /campaigns`, `/insights` | Meta OAuth (`facebook.com/dialog/oauth`) |
| `GET /adsets`, `/ads`, `/adimages` | Google OAuth (`accounts.google.com`) |
| `POST /adcreatives`, `/ads` (push) | Google token refresh (`oauth2.googleapis.com`) |
| Google `searchStream` GAQL | Any frontend ↔ backend calls |
| Google `:mutate` (push) | |
| Google customer info | |

## Fixtures

All data lives in `fixtures/`. Edit the JSON files directly to change what
the server returns. No restart needed when using `--reload`.

| File | Content |
|---|---|
| `meta_campaigns.json` | 3 campaigns (2 ACTIVE, 1 PAUSED) |
| `meta_insights.json` | 7-day metrics for each campaign |
| `meta_adsets.json` | Adsets keyed by campaign_id |
| `meta_ads.json` | Ads keyed by campaign_id (campaign 1 = dynamic, 2 & 3 = static) |
| `google_campaigns.json` | 3 campaigns (RSA, Display, pMax) as GAQL rows |
| `google_adgroups.json` | Ad groups keyed by campaign_id |
| `google_ads.json` | RSA + Display ads as GAQL rows, keyed by campaign_id |
| `google_pmax.json` | pMax asset group rows keyed by campaign_id |

## Routes handled

### Meta (`/meta/...`)

| Method | Path | Handler |
|---|---|---|
| GET | `/{version}/act_{id}/campaigns` | returns all campaigns |
| GET | `/{version}/act_{id}/insights` | returns all insights |
| GET | `/{version}/act_{id}/adsets` | filtered by `campaign.id` |
| GET | `/{version}/act_{id}/ads` | filtered by `campaign.id` |
| GET | `/{version}/act_{id}/adimages` | resolves hashes to URLs |
| POST | `/{version}/act_{id}/adimages` | fake image upload |
| POST | `/{version}/act_{id}/adcreatives` | fake creative create |
| POST | `/{version}/act_{id}/ads` | fake ad create (push) |
| GET/POST | `/{version}/{entity_id}` | creative detail / pause-resume |

### Google (`/google/...`)

| Method | Path | Handler |
|---|---|---|
| POST | `/{v}/customers/{id}/googleAds:searchStream` | GAQL dispatch |
| POST | `/{v}/customers/{id}:mutate` | RSA create (push) |
| GET | `/{v}/customers/{id}` | customer info |
| GET | `/{v}/customers:listAccessibleCustomers` | list customers |

## GAQL dispatch

The `searchStream` handler inspects the `FROM` clause:

| FROM clause | Fixture used |
|---|---|
| `FROM campaign` | `google_campaigns.json` |
| `FROM ad_group` | `google_adgroups.json[campaign_id]` |
| `FROM ad_group_ad` | `google_ads.json[campaign_id]` |
| `FROM asset_group_asset` | `google_pmax.json[campaign_id]` |

`campaign_id` is extracted from `WHERE campaign.id = <id>` in the query.

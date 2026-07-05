# AdStac.kr — AI-Powered Ad Experimentation for Performance Marketers

Welcome to **AdStac.kr** — an experimentation layer built for performance-focused media buyers.

We take the best practices in A/B testing and automate them, freeing you up to focus on the
important stuff: strategy, creative direction, and scale. Connect your Meta or Google Ads account,
and AdStac.kr handles the rest — ingesting your campaigns, generating image and copy variants,
scoring them with AI, and surfacing the next best combination to test via Bayesian Optimisation.
No more spreadsheet-driven split tests. Just signal.

---

## Table of Contents

### Getting Started
- [Quick Start](QUICK_START.md) — fastest path to a running demo
- [Dev Quickstart](DEV_QUICKSTART.md) — local dev setup from scratch
- [nginx / HTTPS Setup](DEV_QUICKSTART.md#8--nginx--https-setup) — expose the backend for Meta/Google OAuth redirects

### Reference
- [Schemas](SCHEMAS.md) — full SQLite table definitions
- [Tests](backend/TEST.md) — test catalog, individual test descriptions, manual curl tests
- [Staging Policy](STAGING_POLICY.md) — what is and isn't safe to run against live Meta accounts
- [Claude Code Instructions](CLAUDE.md) — instructions for AI-assisted development in this repo

### AI Module Docs
- [Embeddings pipeline](backend/embeddings/README.md) — embed ingested ads (text + image, truncate, concatenate)
- [Image generation pipeline](backend/ad_generation/README.md) — generate FLUX image variants
- [Text generation pipeline](backend/ad_text_generation/README.md) — generate copy variants via GPT-4o
- [Combination embeddings](backend/ad_combination_embeddings/README.md) — embed all text slot combinations for BO
- [Bayesian Optimisation](backend/bo_pipeline/README.md) — GPR kernel, length scale, EI acquisition, fantasy batch step

---

## System Architecture

```
frontend/ (React + Vite, port 5173)
  └── calls ──▶ backend/ (FastAPI, port 8000)
                   ├── providers/          Platform API abstraction layer
                   │     ├── Meta:   LiveMetaProvider / MaskingMetaProvider / DemoMetaProvider
                   │     └── Google: GooglePlatformProvider / GoogleMaskingProvider / GoogleDemoProvider
                   └── AI module suite (standalone, independently testable)
                         ├── embeddings/                Embed ingested ads (image + text)
                         ├── ad_generation/             Generate image variants via FLUX
                         ├── ad_text_generation/        Generate text variants via GPT-4o (Meta + Google RSA)
                         ├── ad_combination_embeddings/ Embed all text slot combinations
                         ├── ad_embedding_combiner/     Fuse text + image vectors for GPR
                         └── bo_pipeline/               GPR-based Bayesian Optimisation
```

All backend logic lives in `backend/main.py` (single-file FastAPI app). The database is
SQLite (`backend/app.db`, auto-created on first run). See [`SCHEMAS.md`](SCHEMAS.md) for
full table definitions.

---

## AI Module Suite

Each module is **standalone and independently testable** — no running server required,
no metadata coupling between modules.

### 1. Ad embeddings (`backend/embeddings/`)

Structural ingest fires three embedding tasks per ad (fire-and-forget):

| Task | Output table | What it stores |
|---|---|---|
| `embed_ad` | `ad_embeddings` | Seed embedding: slot[0] text + image[0] combined |
| `embed_images` | `ad_image_embeddings` | One vector per image slot (URL images only) |
| `embed_all_combinations` | `ad_text_combination_embeddings` | N×M×K text combo vectors |

Image hashes from Meta's `asset_feed_spec` are resolved to CDN URLs via the Meta `adimages` API before embedding. Already-embedded combinations are skipped (no repeated API calls).

Standalone re-embed:
```bash
python -m embeddings.pipeline          # embed all un-embedded ads
python -m embeddings.pipeline --ad-id <id>
```

See [`backend/embeddings/README.md`](backend/embeddings/README.md) for full documentation.

### 2. Image generation pipeline (`backend/ad_generation/`)

7-step async pipeline for a single seed image:

```
analyze → generate (FLUX img2img via deAPI) → poll → save → score → QA → correct
```

- GPT-4o analyzes the seed and proposes 10 edit suggestions
- deAPI generates one image variant per suggestion (async polling)
- A fine-tuned scorer rates each variant
- GPT-4o QA checks for visual artifacts; flagged variants become `defunct` and trigger a correction pass

```python
from ad_generation.pipeline import create_job, run_generation_job
job_id = create_job(user_id, campaign_id, adset_id, seed_image_url, headline, short_text)
await run_generation_job(job_id)
```

See [`backend/ad_generation/README.md`](backend/ad_generation/README.md) for full documentation.

### 3. Text generation pipeline (`backend/ad_text_generation/`)

Generates N new copy variants per text slot from a seed ad using GPT-4o, then assembles
them with optional image URLs into a dynamic ad component list and stores the result.
Platform-aware: Meta generates `headline`, `primary_text`, `description`, `cta`; Google RSA
generates `headline` and `description` only (30-char / 90-char limits).

```python
from ad_text_generation.pipeline import run_text_pipeline
generated_ad_id = await run_text_pipeline(seed_components, n_per_slot=5, platform="meta")
generated_ad_id = await run_text_pipeline(seed_components, n_per_slot=10, platform="google")
```

See [`backend/ad_text_generation/README.md`](backend/ad_text_generation/README.md) for full documentation.

### 4. Combination embeddings (`backend/ad_combination_embeddings/`)

For a dynamic ad with N headlines × M primary texts × K descriptions, embeds every
Cartesian combination as a single text vector (e.g. 4 × 4 × 4 = 64 embeddings). Each
combination is stored individually — the BO layer reads these as its candidate pool.

```python
from ad_combination_embeddings import embed_all_combinations, combination_count
n = combination_count(components)   # dry-run
await embed_all_combinations(source_id="gen_ad_001", components=components)
```

See [`backend/ad_combination_embeddings/README.md`](backend/ad_combination_embeddings/README.md) for full documentation.

### 5. Embedding combiner (`backend/ad_embedding_combiner/`)

Pure function module. Truncates text and image embeddings to fixed dimensions
(`TEXT_DIM=128`, `IMAGE_DIM=128`) and concatenates them into a single feature vector
for the GPR. Handles `image_vec=None` (e.g. Google RSA ads) by zero-padding the image
half — the BO pipeline works across both platforms without modification.

```python
from ad_embedding_combiner import combine
vec = combine(text_vector, image_vector)   # shape: (256,)
vec = combine(text_vector, None)           # shape: (256,), image half = zeros
```

### 6. Bayesian Optimisation pipeline (`backend/bo_pipeline/`)

Selects two text+image combinations to test next. Operates strictly per-ad: scored
variants and text candidates must share the same seed ad.

```python
from bo_pipeline import run_bo, save_bo_run
picks = run_bo(seed_ad_id, text_source_id, user_id)
# picks: [{combination_key, combination, selection_type, ei_score, gpr_mean, gpr_std}, ...]
save_bo_run(seed_ad_id, text_source_id, picks)
```

**Two methods:**
- **Modal GP q-EI** (default, `method="modal"`): PCA-reduces embeddings to 64 dims, calls a Modal serverless GP service for proper batch q-EI selection. Requires `MODAL_BO_API_URL` to be set; silently falls back to local when unset or on API failure.
- **Local GPR + fantasy** (`method="local"`): fits a local sklearn GPR, picks highest EI, then applies a fantasy step for the second pick.

Falls back to random selection when fewer than 2 scored observations exist.

**HTTP endpoints (Meta):** `POST /api/bo/run` runs BO and persists picks; `GET /api/bo/results/{ad_id}` returns latest picks.
**HTTP endpoints (Google):** `POST /api/google/bo/run`; `GET /api/google/bo/results/{ad_id}` — identical pipeline, different route prefix.

**Seeding test data:** `backend/seed_bo.py` inserts synthetic scored observations for local testing.
```bash
python seed_bo.py --ad-id <ad_id> --user-id <user_id> --campaign-id <id> --n 5
```

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- A Meta Developer App with **Marketing API** enabled and an OAuth redirect URI registered *(Meta integration)*
- A Google Cloud OAuth 2.0 client and a Google Ads developer token *(Google integration)*
- OpenAI API key (text embeddings)
- Azure AI Inference credentials (image embeddings — separate resource from Azure OpenAI)
- Azure OpenAI credentials (image analysis, text generation, scoring, QA)
- deAPI credentials (FLUX image generation)

Both Meta and Google integrations are optional — the app runs with either, both, or neither connected.

### API keys at a glance

| Service | Key var | Endpoint var | Used for |
|---|---|---|---|
| OpenAI | `OPENAI_KEY` | — (openai.com) | Text embeddings |
| Azure AI Inference | `AZURE_INFERENCE_KEY` | `AZURE_EMBEDDING_ENDPOINT` | Image embeddings (`embed-v-4-0`) |
| Azure OpenAI | `AZURE_OPENAI_KEY` | `AZURE_OPENAI_ENDPOINT` | Ad analysis, text gen, scoring |
| deAPI | `DEAPI_API_KEY` | — | FLUX img2img generation |
| Meta | `META_APP_ID` + `META_APP_SECRET` | — | Meta Marketing API |
| Google | `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` + `GOOGLE_DEVELOPER_TOKEN` | — | Google Ads API |

Azure AI Inference and Azure OpenAI are **different resources** with different endpoints and keys, even if they share an Azure subscription.

---

## Setup

See [`DEV_QUICKSTART.md`](DEV_QUICKSTART.md) for the full environment setup, server startup, curl workflow, and BO seeding guide. See [`backend/TEST.md`](backend/TEST.md) for the test catalog.

---

## Feature Walkthrough

### Connect Meta

1. Sign up at `http://localhost:5173` → Auth page
2. Navigate to **Settings** → **Connect Meta Ads Account**
3. Approve on Meta's OAuth screen → redirected with `?meta_connected=true`

### Connect Google Ads

1. Navigate to **Settings** → **Connect Google Ads Account**
2. Complete Google's OAuth consent screen
3. If your Google account has access to multiple ad accounts, you'll see a picker — select the account to connect (or enter a customer ID manually for test accounts)
4. Optionally enter a **Login Customer ID** if connecting through an MCC manager account

### Meta Campaigns

The Campaigns page shows live data from Meta. Each campaign row supports:

- **Pause / Resume** — calls Meta API; status updates in place
- **History** — stored metric snapshots (requires prior ingest)
- **Creatives** — triggers structural ingest, then shows:
  - Creative slots per ad (headline, primary text, description, image)
  - Lifecycle badge: `active`, `inactive`, or `no longer in Meta`
  - **Suggestions panel** — pending/confirmed suggestions with Confirm Create button
- **Static Text Ads** — generates 10 copy variants per slot (headline, primary_text, description, cta) via GPT-4o
- **Dynamic Ad (AI Images)** — starts an async job that generates 4 AI image variants + 4 text variants per slot, fires embeddings, and stores the result as a new dynamic ad locally
- **Get Recommendations** — runs Bayesian Optimisation and surfaces 2 text+image combinations to test next

### Google Campaigns

The **Google Ads** page (separate from Meta) shows your Google Ads campaigns with 7-day metrics. Each campaign row supports:

- **Ingest / Reingest** — ingests creative structure from Google Ads API into `ad_creative_structures` with `platform='google'`; normalizes RSA, Responsive Display, Video, Performance Max, and Shopping creatives
- **Generate RSA Text** — generates 10 headline + description variants (with Google character limits) for RSA ads
- **Get Recommendations** — runs BO over text combinations and surfaces 2 RSA variants to push
- **Sync** (header button) — pushes all unpushed BO picks to Google Ads as new PAUSED RSA ads

### Preview & Ingest (Meta)

Click **Preview & Ingest** to preview campaigns live from Meta, then confirm to persist
metric snapshots to `ad_insights`.

### Suggestions

1. An external source POSTs to `POST /api/suggestions` with chosen component values
2. The Campaigns page surfaces suggestions in the Creatives panel
3. **Confirm Create** calls `POST /api/suggestions/{id}/confirm` with `action="create"`:
   - Fetches source dynamic ad from Meta to get `page_id` and destination URL
   - Creates a new static ad creative in Meta
   - Creates a new ad in the target adset (`status=PAUSED`)
   - Updates `deployment_status → created_static`

The new static ad is always created **PAUSED** — activate manually in Meta Ads Manager.

---

## API Reference

All routes except auth require `Authorization: Bearer <jwt>`.

### Auth — common

| Method | Path | Description |
|---|---|---|
| POST | `/auth/signup` | Create account, returns JWT |
| POST | `/auth/login` | Login, returns JWT |
| GET | `/me` | Current user's email and tier |

### Admin (internal — `X-Admin-Key` header, not a user JWT)

| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/users` | List all users with tier, tier_source, tier_expires_at, login stats |
| POST | `/api/admin/users/{id}/tier` | Set tier and/or tier_expires_at (partial updates supported); bypasses Stripe entirely — see `BACKEND.md` §`permissions.py` |

Mini UI at `/admin` (`frontend/src/pages/AdminPage.jsx`) — not linked from the app nav.

### Auth — Meta

| Method | Path | Description |
|---|---|---|
| GET | `/me/meta-status` | Check Meta OAuth connection |
| GET | `/auth/meta/login-url` | Start Meta OAuth flow |
| GET | `/auth/meta/callback` | Meta OAuth callback (browser redirect) |

### Auth — Google

| Method | Path | Description |
|---|---|---|
| GET | `/me/google-status` | Check Google Ads connection (`{connected, customer_id, customer_name}`) |
| GET | `/auth/google/login-url` | Start Google OAuth flow |
| GET | `/auth/google/callback` | Google OAuth callback — redirects to account picker |
| GET | `/auth/google/pending/{key}` | Fetch accessible accounts for the picker |
| POST | `/auth/google/select-account` | Save chosen account and login customer ID |

### Meta — Campaigns

| Method | Path | Description |
|---|---|---|
| GET | `/api/campaigns` | Live campaigns + 7d metrics from Meta |
| POST | `/api/campaigns/{id}/pause` | Pause a campaign |
| POST | `/api/campaigns/{id}/resume` | Resume a campaign |
| GET | `/api/campaigns/{id}/history` | Stored metric snapshots (`?days=30`) |

### Meta — Ingest

| Method | Path | Description |
|---|---|---|
| GET | `/api/ingest/preview` | Preview campaigns + ads without saving |
| POST | `/api/ingest` | Save campaign metric snapshots to `ad_insights` |
| POST | `/api/ingest/structure/{campaign_id}` | Normalize + persist creative structure; fires embeddings |
| GET | `/api/structure/{campaign_id}` | Read persisted structure grouped by ad |

### Meta — Suggestions

| Method | Path | Description |
|---|---|---|
| GET | `/api/suggestions` | List suggestions (`?campaign_id=` optional) |
| POST | `/api/suggestions` | Store a suggested configuration |
| POST | `/api/suggestions/{id}/confirm` | Confirm create or replace |

### Meta — Ad Generation

| Method | Path | Description |
|---|---|---|
| POST | `/api/generate/text/{campaign_id}` | Generate 10 text variants per slot (synchronous) |
| POST | `/api/generate/dynamic/{campaign_id}` | Start async job: 4×4 text + 4 AI images + embeddings |
| GET | `/api/generate/dynamic/status/{job_id}` | Poll job status; returns slots + image_urls when complete |
| POST | `/api/push` | Push unpushed generated ads to Meta as PAUSED static ads |

### Meta — Bayesian Optimisation

| Method | Path | Description |
|---|---|---|
| POST | `/api/bo/run` | Run BO on Meta ad, return and persist up to 2 picks |
| GET | `/api/bo/results/{ad_id}` | Latest BO picks for a Meta ad |

### Google — Campaigns

| Method | Path | Description |
|---|---|---|
| GET | `/api/google/campaigns` | Live campaigns + 7d metrics from Google Ads |

### Google — Ingest

| Method | Path | Description |
|---|---|---|
| POST | `/api/google/ingest/structure/{campaign_id}` | Normalize + persist Google creative structure; fires embeddings |
| GET | `/api/google/structure/{campaign_id}` | Read persisted Google structure grouped by ad |

### Google — Text Generation

| Method | Path | Description |
|---|---|---|
| POST | `/api/google/generate/text/{campaign_id}` | Generate 10 RSA headline + description variants (`?seed_ad_id=` optional) |

### Google — Bayesian Optimisation

| Method | Path | Description |
|---|---|---|
| POST | `/api/google/bo/run` | Run BO on Google RSA ad, return and persist up to 2 picks |
| GET | `/api/google/bo/results/{ad_id}` | Latest BO picks for a Google ad |

### Google — Push

| Method | Path | Description |
|---|---|---|
| POST | `/api/google/push` | Push unpushed BO picks to Google Ads as PAUSED RSA ads |

### Ad Generators (cross-platform candidate pools)

| Method | Path | Description |
|---|---|---|
| POST | `/api/generators` | Create a named ad generator from one or more member ads (multi-member BO candidate pool) |
| GET | `/api/generators` | List all ad generators for the current user |
| DELETE | `/api/generators/{generator_id}` | Delete an ad generator and its members |

### Cross-Platform Bayesian Optimisation

| Method | Path | Description |
|---|---|---|
| POST | `/api/bo/cross-platform` | Run BO jointly across Meta and Google (per-platform GPR, shared ECDF); returns up to 2 globally-ranked picks |
| POST | `/api/bo/cross-platform/unified` | Unified BO: per-group PCA to shared K-dim space, single pooled GP/Modal call; returns top_n picks (`top_n` in body, default 4). Wired to the Dashboard UI |
| POST | `/api/bo/seed-scored-variants` | Seed synthetic scored observations for BO testing without API keys |

### Push Lifecycle (cross-platform)

| Method | Path | Description |
|---|---|---|
| POST | `/api/push/pick` | Push a specific BO-recommended combination as a new PAUSED ad (Meta static clone or Google RSA) |
| POST | `/api/push/match` | Record that a BO pick matches an existing native static ad — no new ad created |
| POST | `/api/activate` | Enable a PAUSED pushed clone on the platform |
| POST | `/api/pause-ad` | Pause any ad (template or clone) on the platform |
| POST | `/api/push/retain/{combo_id}` | Mark a converged test clone as retained — keeps running, BO stops writing new observations for it |

### Manual Platform (Studio)

Ads with no source platform (created directly in-app) — see `AD.md` / `FRONTEND.md` for the Studio UI.

| Method | Path | Description |
|---|---|---|
| POST | `/api/manual/campaigns` | Create a manual campaign |
| GET | `/api/manual/campaigns` | List manual campaigns for the current user |
| PATCH | `/api/manual/campaigns/{campaign_id}` | Rename a manual campaign |
| DELETE | `/api/manual/campaigns/{campaign_id}` | Delete a manual campaign |
| POST | `/api/manual/upload-image` | Upload an image for use in a manual ad |
| POST | `/api/manual/campaigns/{campaign_id}/ads` | Create a manual ad (static or template) in a campaign |
| GET | `/api/manual/campaigns/{campaign_id}/ads` | List manual ads in a campaign |
| DELETE | `/api/manual/ads/{ad_id}` | Delete a manual ad |
| GET | `/api/manual/ads/{ad_id}/combinations` | List BO-candidate combinations for a manual template ad |
| POST | `/api/manual/ads/{ad_id}/score` | Write a scored observation for a manual combination |

### Other

| Method | Path | Description |
|---|---|---|
| GET | `/api/ads` | Live ad creatives from Meta |
| GET | `/api/ads/local` | All locally stored ads (ingested + generated), grouped by ad_id with full slot data |
| DELETE | `/api/ads/local/{ad_id}` | Delete a local ad and its embeddings; Meta-sourced ads reappear on next sync |
| GET | `/api/explore` | Raw Meta data (campaigns → adsets → ads) for debugging |
| GET | `/images/{filename}` | Serve AI-generated images (static mount) |
| GET | `/ad-images/{filename}` | Serve downloaded Meta CDN images (static mount) |

---

## Masking / Demo Mode

For staging demos without spending real budget:

### Meta masking

| Env var | Effect |
|---|---|
| `APP_MODE=demo` | Fully synthetic Meta data — no Meta API calls |
| `MASK_MODE=selective\|full` | Selectively override fields (status, budgets, metrics) |
| `MASK_STATUS=true` | Force all campaign statuses → ACTIVE |
| `MASK_METRICS=true` | Synthesize metrics for low-delivery campaigns |
| `MASK_PAUSE_RESUME=true` | pause/resume → no-op (returns success) |
| `METRIC_PROFILE=healthy\|stable\|weak` | Controls synthetic metric magnitude |

### Google masking

| Env var | Effect |
|---|---|
| `GOOGLE_APP_MODE=demo` | Fully synthetic Google data — no Google API calls (3 fixture campaigns: RSA, Display, pMax) |
| `GOOGLE_MASK_MODE=selective\|full` | Selectively override Google campaign fields |
| `GOOGLE_MASK_STATUS=true` | Force Google campaign statuses → ACTIVE |
| `GOOGLE_MASK_BUDGETS=true` | Replace daily budgets with deterministic synthetic values |
| `GOOGLE_MASK_METRICS=true` | Synthesize metrics for low-delivery Google campaigns |
| `GOOGLE_MASK_PAUSE_RESUME=true` | pause/resume → no-op |
| `GOOGLE_METRIC_PROFILE=healthy\|stable\|weak` | Controls synthetic Google metric magnitude |

`APP_MODE=demo` enables demo mode for **both** platforms simultaneously. Use platform-specific vars to demo one platform with the other live.

See [`STAGING_POLICY.md`](STAGING_POLICY.md) for what is and isn't safe to run against live accounts, and `CLAUDE.md` for the full variable reference.

---

## Notes

- **Auth:** Minimal JWT, 24h expiry. No email verification or rate limiting.
- **Meta token:** Short-lived user token — no refresh logic. Re-connect via Settings when it expires.
- **Google token:** OAuth2 refresh token is stored and refreshed automatically on every API call.
- **SQLite:** `backend/app.db` is gitignored. Delete it to reset all data.
- **AI modules:** All generation modules are wired into HTTP routes. See `CLAUDE.md` for the full route list. Modules are also independently runnable — no running server required.
- **Static ad images:** The Meta clone flow passes image values as hosted URLs. Creatives stored only as image hashes (not URLs) will fail at Meta creative creation.
- **New ads are always PAUSED:** Both Meta (`POST /api/push`) and Google (`POST /api/google/push`) create ads in PAUSED state. Activate manually in the respective Ads Manager.
- **Google RSA BO:** Operates on text combinations only — no image dimension. The combiner zero-pads the image half, so the BO pipeline code is identical for both platforms.

For schema details see [`SCHEMAS.md`](SCHEMAS.md).
For running on EC2 with nginx/HTTPS see [Section 8 of DEV_QUICKSTART.md](DEV_QUICKSTART.md#8--nginx--https-setup).

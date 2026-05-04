# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Start everything (preferred)
```bash
./start.sh              # prod: backend :8000, frontend :5173, then ngrok on :5173
./start.sh staging      # staging: backend :8001, frontend :5174, APP_ENV=staging, then ngrok on :5174
```
`start.sh` starts backend + frontend in the background, then runs ngrok in the foreground on the frontend port.

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

### Tests

All test files are in `backend/tests/`. See `backend/TEST.md` for the full catalog.

```bash
cd backend
source .venv/bin/activate
python -m pytest tests/test_structural_ingest.py tests/test_suggestions.py -v   # unit tests (no API keys needed)
python -m pytest tests/test_bo_pipeline.py -v                                    # BO pipeline tests (no API keys needed)
python -m pytest tests/test_combination_embeddings.py -v -k "TestBuildCombinations or TestCombinationKey"  # pure (no API)
python -m pytest tests/test_generation_pipeline.py -v                            # image pipeline integration tests (API keys required)
python -m pytest tests/test_text_pipeline.py -v                                  # text pipeline integration tests (API keys required)
python -m pytest tests/test_combination_embeddings.py -v                         # combination embedding integration tests (API keys required)
python -m pytest tests/test_google_db.py tests/test_google_auth.py tests/test_google_campaigns.py tests/test_google_structural_ingest.py tests/test_google_text_pipeline.py tests/test_google_bo.py -v  # Google integration (no API keys needed)
python -m pytest tests/test_google_campaigns.py::test_google_campaigns_live_smoke -v -s  # live smoke test (requires Google credentials in .env)
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
                   └── providers/ (PlatformProvider interface)
                         ├── LiveMetaProvider      → Meta Marketing API (graph.facebook.com)
                         ├── MaskingMetaProvider   → wraps Live; selectively overrides fields
                         ├── DemoMetaProvider      → fully synthetic fixtures (APP_MODE=demo)
                         └── GooglePlatformProvider → Google Ads REST API (googleads.googleapis.com)
```

### Provider selection (`providers/factory.py`)

| Condition | Provider returned |
|---|---|
| `APP_MODE=demo` | `DemoMetaProvider` |
| `MASK_MODE=selective\|full` **or** any `MASK_*=true` flag | `MaskingMetaProvider(LiveMetaProvider(), policy)` |
| default | `LiveMetaProvider` |

## Backend (`backend/main.py`)

Single-file FastAPI application. All routes, Pydantic models, DB schema, and
business logic live here. Key sections in reading order:

| Section | What it does |
|---|---|
| `init_db()` | Creates all SQLite tables (including `ad_generation_*`, `ad_text_combination_embeddings`, `dynamic_generation_jobs`, `google_connections`, and `google_pending_connections` tables via helper calls); runs ALTER TABLE migrations for columns added after initial schema — including `tier TEXT NOT NULL DEFAULT 'free'` on `users`, `seed_ad_id`/`adset_id`/`meta_ad_id` on `dynamic_generation_jobs`, and `platform` on `ad_insights`/`ad_creative_structures` |
| `get_current_user_id()` | FastAPI dependency; decodes Bearer JWT |
| `_meta_creds(user_id)` | Loads `(access_token, ad_account_id)` from `meta_connections` for authenticated user |
| `_fetch_campaigns_and_insights()` | Shared async helper for campaigns + 7d insights from Meta; returns `(campaigns_raw, metrics_by_campaign, insights_error_count)` — callers use `insights_error_count` to distinguish API failure from genuine zero delivery |
| `_fetch_campaign_structure()` | Fetches adsets + ads with expanded creative fields for one campaign; includes `effective_status` |
| `_normalize_creative(ad)` | Pure function; detects dynamic (presence of `asset_feed_spec`) vs static; extracts slots |
| `_clone_dynamic_to_static_ad()` | Creates a new static Meta ad from chosen components; used by confirm-create flow |
| `_suggestion_from_row()` | Converts a DB row → `SuggestionResponse` Pydantic model |
| `_download_ad_images(components)` | Downloads Meta CDN image URLs to `backend/ad_images/` synchronously during ingest (before expiry); returns components list with local URLs substituted. Required because Meta CDN URLs are signed and expire quickly — background tasks can't use them. |
| `GET /me` | Returns current user's email and tier (`free`/`premium`) |
| `_upload_image_to_meta()` | Resolves a locally-served image URL (`/images/...` or `/ad-images/...`) to a file on disk, uploads it to `/{ad_account_id}/adimages`, returns the image hash. Returns `None` on failure — push continues without image rather than aborting. |
| `_clone_dynamic_to_static_ad()` | Creates a new static Meta ad from chosen component values. Accepts optional `image_hash`; uses `image_hash` in `link_data` when provided, falls back to `picture` URL otherwise. Fetches `object_story_spec` and `asset_feed_spec` from source ad to inherit `page_id` and destination link URL. |
| `POST /api/push` | Finds all completed `dynamic_generation_jobs` for the user where `meta_ad_id IS NULL` and `seed_ad_id`/`adset_id` are set; uploads each ad's image to Meta, then calls `_clone_dynamic_to_static_ad` to create a PAUSED static ad; records `meta_ad_id` on success. Requires Meta app in Live mode — returns per-job errors gracefully if blocked. |
| `_google_creds(user_id)` | Reads `google_connections`, always refreshes via `google_ads_api.refresh_access_token`, raises 400 if not connected; returns `(access_token, customer_id, login_customer_id)` |
| `GET /me/google-status` | Returns `{connected, customer_id, customer_name}` |
| `GET /auth/google/login-url` | Generates Google OAuth consent URL; stores state in `oauth_states` with `provider='google'` |
| `GET /auth/google/callback` | Validates state, exchanges code, fetches names for all accessible customers in parallel, stores list in `google_pending_connections`, redirects to `FRONTEND_URL/app/settings?google_pick=<key>` |
| `GET /auth/google/pending/{key}` | Returns `{accounts: [{customer_id, name}]}` for the pending connection; requires auth; validates `user_id` matches |
| `POST /auth/google/select-account` | Body `{key, customer_id, login_customer_id?}`; strips dashes from IDs; accepts manual customer IDs not in the accounts list (for test accounts); saves to `google_connections`, deletes pending row |
| `GET /api/google/campaigns` | Calls `_google_creds`, creates `httpx.AsyncClient`, delegates to `google_provider.fetch_campaigns_and_insights` (passing `login_customer_id`), returns `list[Campaign]` |
| `POST /api/google/ingest/structure/{campaign_id}` | Calls `_google_creds`, runs two parallel GAQL queries (ad groups + ads) via `google_provider.fetch_campaign_structure`, writes to `ad_creative_structures` with `platform='google'`; same idempotent upsert and missing-ad detection as Meta route; fires `embed_ad` + `embed_all_combinations(slots=('headline','description'))` per ad fire-and-forget after commit |
| `GET /api/google/structure/{campaign_id}` | Returns persisted Google creative structures filtered by `platform='google'`; same `list[AdStructure]` shape as Meta route |
| `POST /api/google/generate/text/{campaign_id}` | Generates 10 RSA headline + description variants from the first ingested Google ad (or `?seed_ad_id=`); calls `run_text_pipeline(platform='google')`; returns `GenerateTextResponse` |
| `POST /api/google/bo/run` | Body `{seed_ad_id, text_source_id}`; runs BO via the same `run_bo`/`save_bo_run` functions as the Meta route; returns `BORunResponse`; falls back to random when fewer than MIN_TRAINING_POINTS scored variants exist |
| `GET /api/google/bo/results/{ad_id}` | Returns most recent BO picks for a Google ad via `get_latest_bo_run` |
| `POST /api/google/push` | Finds all latest unpushed `bo_selections` (pick_rank=1, `google_ad_resource_name IS NULL`) for user's Google ads; for each, resolves `adset_id` + `final_url` from `ad_creative_structures` and generated text from `generated_ad_slots`; creates PAUSED RSA via `google_ads_api.create_rsa`; writes resource name to `bo_selections.google_ad_resource_name`; returns `GooglePushResponse` with per-ad results and optional `note` |

Image serving: `main.py` mounts `StaticFiles` at `/images` → `backend/generated_images/` and at `/ad-images` → `backend/ad_images/`. The `/ad-images` mount serves downloaded copies of Meta CDN ad images used by the embedding pipeline.

See `SCHEMAS.md` for full table definitions and `README.md` for the API route table.

## Embeddings (`backend/embeddings/`)

Standalone async module. Called fire-and-forget from structural ingest and from the dynamic ad generation background task. See **`AD.md`** for a full conceptual explanation of what embeddings are, why we use three separate tasks, and how they connect to BO. See **`backend/embeddings/README.md`** for env vars and standalone runner docs.

Structural ingest and dynamic ad generation both fire **three** embedding tasks per ad (all fire-and-forget):
1. `embed_ad(...)` — seed embedding: slot[0] text + image[0] → `ad_embeddings` (one row per ad)
2. `embed_images(...)` — per-image embeddings for each URL image slot → `ad_image_embeddings` (one row per image)
3. `embed_all_combinations(...)` — N×M×K text combinations → `ad_text_combination_embeddings`; slots always `('headline', 'primary_text', 'description')` — 4×4×4 = 64 rows for a generated dynamic ad

`embed_all_combinations` skips already-embedded combinations (idempotent, no repeated API calls).
Image slots stored as Meta hashes (not URLs) are resolved to CDN URLs via the Meta `adimages` API during `fetch_campaign_structure` before normalization.

**Image download before embedding:** `_download_ad_images(components)` is called synchronously during ingest (before firing background tasks) to download Meta CDN image URLs to `backend/ad_images/`. The local URLs (`http://localhost:8000/ad-images/...`) are passed to `embed_ad` and `embed_images`. This prevents embedding failures due to expired signed CDN URLs. AI-generated images (at `/images/...`) are already local and don't need this step.

**Smart skip for `embed_ad`:** Skips only if `text_snapshot` matches AND both `text_vector` AND `image_vector` are non-NULL. Any partial failure leaves a NULL column; next ingest retries both.

Public entry points in `embeddings/pipeline.py`: `embed_ad(...)`, `embed_images(...)`.

## Ad generation pipeline (`backend/ad_generation/`)

Fully async standalone module. 7-step pipeline: analyze → generate → poll → save → score → QA → correct. Called directly from the dynamic ad generation background task (`_run_dynamic_generation`). See **`backend/ad_generation/README.md`** for full documentation, env vars, and test instructions.

Public entry points from `ad_generation/pipeline.py`: `create_job()`, `run_generation_job()`, `get_job_status()`, `get_job_variants()`, `get_active_variants()`.

**Image passing to Azure:** Scoring and QA use `_image_data_url(filename)` which reads the file from disk and sends it as a `data:image/png;base64,...` URL. This is required because Azure OpenAI cannot reach `localhost` URLs. Falls back to the serve URL if the file cannot be read. Image deduplication in variant selection uses `local_filename` to prevent the same cached deAPI result appearing multiple times.

The dynamic gen task calls `create_job()` once, then `run_generation_job()` which generates ~10 image variants internally. After completion, the top-4 scored active variants are selected as the image pool for the new dynamic ad.

## Ad text generation pipeline (`backend/ad_text_generation/`)

Standalone async module. Generates N new text variants per slot from a seed ad's components. **Meta text slots: `headline`, `primary_text`, `description`, `cta`** (`TEXT_SLOTS` in `generator.py`). **Google RSA slots: `headline`, `description`** (`GOOGLE_RSA_SLOTS`). See **`backend/ad_text_generation/README.md`** for full documentation.

**Platform-aware generation:** `slots_for_platform(platform)` returns `GOOGLE_RSA_SLOTS` for `'google'`, `TEXT_SLOTS` otherwise. `generate_all_slots` and `generate_slot_variants` accept a `platform` parameter and pick the right prompt hints — `GOOGLE_RSA_SLOT_HINTS` (30-char headline, 90-char description limits) vs `SLOT_HINTS` (Meta defaults).

Public entry point: `run_text_pipeline(seed_components, n_per_slot, image_urls, platform='meta', ...) → generated_ad_id` in `ad_text_generation/pipeline.py`.

**HTTP routes (wired):**
- `POST /api/generate/text/{campaign_id}` — Meta static mode: generates 10 text variants per slot from the first ingested Meta ad; stores in `generated_ads`/`generated_ad_slots`; synchronous; no images; no embeddings
- `POST /api/google/generate/text/{campaign_id}` — Google RSA mode: generates 10 headline + description variants; calls `run_text_pipeline(platform='google')`; synchronous; no images
- `POST /api/generate/dynamic/{campaign_id}` — Meta dynamic mode: starts background job; returns `{job_id, status:'running'}`; generates 4 variants per slot + 4 AI images + fires all embeddings; stores in `ad_creative_structures`
- `GET /api/generate/dynamic/status/{job_id}` — poll status; returns full slots + image_urls when `status='complete'`

## Ad text combination embeddings (`backend/ad_combination_embeddings/`)

Standalone async module. Embeds every Cartesian combination of text slot values as a single text vector. Reuses `embeddings.embedder.embed_text`. See **`backend/ad_combination_embeddings/README.md`** for full documentation.

Default slots: `('headline', 'primary_text', 'description')` — 4×4×4 = 64 rows for a Meta dynamic ad. For Google RSA, callers pass `slots=('headline', 'description')` — no `primary_text`. The combination key and JSON format are identical regardless of which slots are active.

Public entry points: `embed_all_combinations(source_id, components, slots=TEXT_SLOTS)`, `get_embeddings_for_source(source_id)`, `combination_count(components)` (dry-run, no I/O).

## Embedding combiner (`backend/ad_embedding_combiner/`)

Standalone pure module. Truncates text and image embeddings to fixed dimensions (`TEXT_DIM=128`, `IMAGE_DIM=128`) and concatenates them into a single feature vector for GPR/BO. Separated because the combination strategy may change independently of the BO logic.

Public entry points: `combine(text_vec, image_vec)`, `truncate_pad(vec, dim)`, `output_dim()`, `TEXT_DIM`, `IMAGE_DIM` from `ad_embedding_combiner/combiner.py`. `image_vec` may be `None` — the image half of the combined vector is zero-padded. This allows RSA (text-only) ads to participate in BO without image embeddings.

## Bayesian Optimisation pipeline (`backend/bo_pipeline/`)

Standalone sync module. Fits a GPR on scored image variants, then selects two candidate text+image combinations via Expected Improvement (pick 1) and a fantasy step (pick 2, batch BO). Operates strictly per-ad — scored variants and text candidates must share the same seed ad. See **`backend/bo_pipeline/`** for implementation details.

Public entry points: `run_bo(seed_ad_id, text_source_id, user_id, db_path)` → list of up to 2 picks; `save_bo_run(...)`, `get_latest_bo_run(...)` for persistence.

Key design: `selector.py` is the only file that knows the DB schema; `gpr.py` is pure numpy/sklearn; `pipeline.py` orchestrates. Falls back to random selection when fewer than 2 scored observations exist.

Scored combinations: `ad_generation_variants` (score IS NOT NULL, status != defunct) joined via `ad_embeddings` using convention `ad_id = gen_{job_id}_{variant_id}`.
Candidate combinations: **N_text × N_images cross-product** — all rows in `ad_text_combination_embeddings` for `text_source_id`, each paired with every row in `ad_image_embeddings` for the seed ad. Falls back to the seed ad's single `image_vector` from `ad_embeddings` if no per-image embeddings exist. Each pick's `combination` dict includes `image_url` (the local `/ad-images/...` URL) for display. With 64 text combos and 4 image embeddings → 256 candidates.

**HTTP endpoints (wired):** `POST /api/bo/run` / `GET /api/bo/results/{ad_id}` — Meta BO. `POST /api/google/bo/run` / `GET /api/google/bo/results/{ad_id}` — Google BO (identical implementation; same models and DB tables; `seed_ad_id` is the Google ad_id from ingest).

**Seeding scored observations for testing:** `backend/seed_bo.py` inserts synthetic scored variants into `ad_generation_jobs`, `ad_generation_variants`, and `ad_embeddings`. Run with:
```bash
python seed_bo.py --ad-id <ad_id> --user-id <user_id> --campaign-id <campaign_id> --n 5
```

## Frontend (`frontend/src/`)

| File | Role |
|---|---|
| `api.js` | Single fetch wrapper; JWT stored in `localStorage`; all API calls go through here — includes `getMe()`, `runBO()`, `generateTextAds()`, `startDynamicGeneration()`, `getDynamicGenStatus()`, `getLocalAds()`, `deleteLocalAd(adId)`, `getGoogleStatus()`, `getGoogleLoginUrl()`, `getGoogleCampaigns()`, `ingestGoogleStructure(campaignId)`, `getGoogleStructure(campaignId)`, `getGooglePendingAccounts(key)`, `selectGoogleAccount(key, customerId, loginCustomerId)`, `generateGoogleTextAds(campaignId, seedAdId)`, `runGoogleBO(seedAdId, textSourceId)`, `getGoogleBOResults(adId)`, `pushGoogleAds()` |
| `App.jsx` | Root layout with nav; React Router `<Outlet>`; fetches `GET /me` on load and exposes user via `UserContext`; shows tier badge in nav; nav links: Settings / Meta Ads / Google Ads / Ad Library / Explorer |
| `UserContext.js` | React context (`UserContext`) + `useUser()` hook; default tier `"free"` |
| `pages/AuthPage.jsx` | Signup / login |
| `pages/SettingsPage.jsx` | Meta and Google OAuth connect sections; reads `?meta_connected`, `?google_error` redirect params; on `?google_pick=<key>` fetches pending accounts and shows an account picker (radio list + manual customer ID field + optional login customer ID field); on confirm calls `selectGoogleAccount` then refreshes status |
| `pages/CampaignsPage.jsx` | Main working page — Meta campaigns table, pause/resume, metric history, structure panel, suggestions panel |
| `pages/GoogleCampaignsPage.jsx` | Google campaigns table at `/app/google-campaigns`; handles not-connected (400), empty results, loading state; **Sync button** (header) calls `POST /api/google/push` then re-fetches campaigns; shows success/amber/error note after sync; Ingest/Reingest button per row (tracks ingested IDs in `localStorage`); inline `StructurePanel` shows creative type badge, lifecycle status, and slot values (including `final_url` slot); labels RSA/display/video/unknown creative types; "Generate RSA Text" button appears after ingest (fires `POST /api/google/generate/text`; shows `TextGenResults` with headline + description variants); "Get Recommendations" button appears after text gen (fires `POST /api/google/bo/run` using `source_ad_id` from gen result; shows `BOPicksPanel` with up to 2 picks) |
| `pages/AdsPage.jsx` | Local ad library — reads `GET /api/ads/local`; card grid with source/status badges; click a card to open a detail modal showing image grid + all text slot variants; delete button with confirm dialog calls `DELETE /api/ads/local/{ad_id}` |
| `pages/ExplorerPage.jsx` | Raw Meta API explorer (debug) |

The Campaigns page drives panels per campaign row:
- **Header controls** — "Sync" button: pushes unpushed generated ads (`POST /api/push`) then pulls latest campaigns (`POST /api/ingest`); shows last-synced timestamp (stored in `localStorage`); amber note if push is blocked by Meta dev-mode
- **Ingest/Reingest button** — per-campaign; label is "Ingest" on first use, "Reingest" thereafter (tracked in `localStorage` as `ingestedIds`); both trigger `POST /api/ingest/structure/<id>`
- **History panel** — stored metric snapshots; when campaign is ingested, shows three action buttons:
  - **Get Recommendations** — triggers BO; shows picks with image preview
  - **Static Text Ads** — triggers `POST /api/generate/text/{campaign_id}`; synchronous; shows generated text variants per slot (headline, primary_text, description, cta)
  - **Dynamic Ad (AI Images)** — triggers `POST /api/generate/dynamic/{campaign_id}`; async; polls every 5s; shows 4-image grid + 4 text variants per slot when complete
- **Structure panel** — ingested creative structure (slots + lifecycle badges)
- **Suggestions panel** (inside structure panel) — pending/confirmed suggestions with Confirm Create button

State keys in `CampaignsPage.jsx`: `boStateById`, `genStateById` (static), `dynJobById` (dynamic). Dynamic polling runs via `useEffect` watching `dynJobById` — clears interval when no jobs have `status='running'`.

## Product model

A **dynamic ad** in Meta is treated as a template / exploration container. AdStac.kr:
1. Ingests the template and normalises its component variants into slots (`headline`, `description`, `primary_text`, `image`)
2. Receives suggested exact configurations from an external suggestion API (not yet built)
3. Lets the user confirm a suggestion → clones it as a new static ad in Meta

The **lifecycle** of a static clone:
```
suggested → pending_confirmation (replace path only, not yet deployed)
suggested → [Meta API call] → created_static  (create path, implemented)
created_static → active_static  (future: user activates in Meta)
* → rejected
```

## Required env vars (backend)

| Variable | Description |
|---|---|
| `META_APP_ID` | Meta Developer App ID |
| `META_APP_SECRET` | Meta Developer App Secret |
| `META_REDIRECT_URI` | Must match Meta app settings (e.g. `http://localhost:8000/auth/meta/callback`) |
| `META_API_VERSION` | Defaults to `v19.0` |
| `JWT_SECRET` | Any random string |
| `FRONTEND_URL` | Defaults to `http://localhost:5173` |

### Google Ads env vars

| Variable | Description |
|---|---|
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | Must match Google console (e.g. `http://localhost:8000/auth/google/callback`) |
| `GOOGLE_DEVELOPER_TOKEN` | Google Ads developer token |
| `GOOGLE_ADS_API_VERSION` | e.g. `v18` — **no default, must be set explicitly** to avoid baking in a stale version |

All Google vars are optional until Google integration is activated. `GOOGLE_ADS_API_VERSION` has no hardcoded fallback.

### Google masking env vars

| Variable | Values | Description |
|---|---|---|
| `GOOGLE_APP_MODE` | `demo` \| _(unset)_ | Force `GoogleDemoProvider` regardless of `APP_MODE` |
| `GOOGLE_MASK_MODE` | `off` \| `selective` \| `full` | Coarse switch. `full` sets all mask flags to `true` by default |
| `GOOGLE_MASK_STATUS` | `true\|false` | Force campaign status → `ACTIVE` |
| `GOOGLE_MASK_BUDGETS` | `true\|false` | Replace `daily_budget` with a deterministic synthetic value |
| `GOOGLE_MASK_METRICS` | `true\|false` | Synthesize insights for campaigns with impressions < 100 |
| `GOOGLE_MASK_PAUSE_RESUME` | `true\|false` | pause/resume → no-op |
| `GOOGLE_METRIC_PROFILE` | `healthy` \| `stable` \| `weak` | Synthetic metric magnitude profile (default `healthy`) |

### API keys and endpoints — full reference

There are **four separate external services**, each with its own key and endpoint. Do not mix them up.

#### 1. OpenAI — text embeddings
| Variable | Description |
|---|---|
| `OPENAI_KEY` | OpenAI API key (sk-...) |
| `OPENAI_TEXT_MODEL` | Embedding model (default `text-embedding-3-small`) |

Used by: `embeddings/embedder.py` → `embed_text()`, `ad_combination_embeddings/pipeline.py`

#### 2. Azure AI Inference — image embeddings
| Variable | Description |
|---|---|
| `AZURE_INFERENCE_KEY` | Azure AI Inference key for the multimodal embedding resource |
| `AZURE_EMBEDDING_ENDPOINT` | Resource endpoint (default `https://markpshipman-2243-resource.services.ai.azure.com/models`) |
| `AZURE_IMAGE_MODEL` | Image embedding model (default `embed-v-4-0`) |

Used by: `embeddings/embedder.py` → `embed_image_url()`
**This is a different resource and key from the Azure OpenAI used for ad generation.**

#### 3. Azure OpenAI — ad generation, analysis, scoring, text generation
| Variable | Description |
|---|---|
| `AZURE_OPENAI_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource endpoint (e.g. `https://your-resource.openai.azure.com/`) |
| `AZURE_OPENAI_API_VERSION` | Defaults to `2024-12-01-preview` |
| `AZURE_ANALYSIS_DEPLOYMENT` | GPT-4o deployment for image analysis (default `gpt-4o`) |
| `AZURE_SCORING_DEPLOYMENT` | Fine-tuned scorer deployment (default `gpt-4-04-14`) |
| `AZURE_TEXT_GEN_DEPLOYMENT` | GPT-4o deployment for text generation (default `gpt-4o`) |

Used by: `ad_generation/` pipeline (analyze, score, QA, text gen steps)

#### 4. deAPI — FLUX image generation
| Variable | Description |
|---|---|
| `DEAPI_API_KEY` | deAPI key for FLUX img2img image generation |
| `IMAGES_SERVE_BASE_URL` | Base URL for serving generated images (default `http://localhost:8000/images`). Must be a full URL with scheme — used by the ad generation pipeline for scoring/QA and by `_upload_image_to_meta` to resolve local file paths. |

Used by: `ad_generation/` pipeline (generate + poll steps)

### Masking layer env vars

Rule of thumb: **fake what costs money, keep everything else real.**

Masking activates when `MASK_MODE=selective|full` **or** when any individual `MASK_*` flag is set to `true`. Setting `MASK_STATUS=true` alone is sufficient — `MASK_MODE` does not need to be set explicitly.

| Variable | Values | Description |
|---|---|---|
| `MASK_MODE` | `off` \| `selective` \| `full` | Coarse switch. `full` sets all mask flags to `true` by default; `selective` leaves them `false` unless individually enabled. |
| `MASK_STATUS` | `true\|false` | Force campaign status → `ACTIVE` |
| `MASK_BUDGETS` | `true\|false` | Replace `daily_budget` with a deterministic demo value |
| `MASK_METRICS` | `true\|false` | Synthesize insights for campaigns with no/low real delivery (impressions < 100); real metrics are preserved when healthy |
| `MASK_PAUSE_RESUME` | `true\|false` | pause/resume calls → no-op (return success without hitting Meta) |
| `MASK_AD_STATUSES` | `true\|false` | Force ad status → `ACTIVE` (also implied by `MASK_STATUS`) |
| `REAL_ASSET_CREATION` | `true\|false` | Default `true`; **reserved for future use — not consulted by any route yet** |
| `METRIC_PROFILE` | `healthy` \| `stable` \| `weak` | Synthetic metric profile; affects magnitude of impressions/CTR/CPC |

Synthetic metrics are **deterministic per campaign ID** — the same campaign always gets the same numbers across restarts. Values are internally consistent (`ctr = clicks/impressions`, `cpm = spend/impressions*1000`, `cpc = spend/clicks`). Campaigns without a valid `id` field are skipped entirely by the masking layer.

Routes that always hit Meta directly (not masked): `/auth/meta/callback`, `/api/explore`, structural ingest (`_fetch_campaign_structure`), and static ad creation (`_clone_dynamic_to_static_ad`).

## Manual API testing

See `DEV_QUICKSTART.md` for full curl examples covering auth, ingest, embeddings verification, BO seeding, and BO runs.

## Provider layer (`backend/providers/`)

| File | Role |
|---|---|
| `meta_provider.py` | `PlatformProvider` ABC (`ABC` + `@abstractmethod`); defines interface for all providers; `MetaProvider = PlatformProvider` alias kept for backward compat |
| `meta_live.py` | Calls real Meta Graph API |
| `meta_masking.py` | Wraps `LiveMetaProvider`; overrides selected fields per `MaskPolicy` |
| `mask_policy.py` | Reads `MASK_*` env vars into a `MaskPolicy` config object |
| `meta_demo.py` | Fully synthetic fixtures; used when `APP_MODE=demo` |
| `google_provider.py` | `GooglePlatformProvider` — `fetch_campaigns_and_insights` runs campaigns GAQL and normalizes (ENABLED→ACTIVE, micros conversions, multi-row aggregation); `normalize_creative(ad)` maps `ad_type` to `creative_type` (`rsa`/`display`/`video`/`unknown`) and extracts slot components from camelCase API response; `fetch_campaign_structure` runs two parallel GAQL queries (ad groups + ads) and returns normalized `(adsets, ads)`; `youtube_thumbnail_url(video_id)` static method returns `https://img.youtube.com/vi/{id}/hqdefault.jpg` (deferred from ingest until video asset resource names can be resolved to YouTube IDs); remaining methods (`fetch_ads`, `pause_campaign`, `resume_campaign`) raise `NotImplementedError` until their chunks |
| `google_demo.py` | `GoogleDemoProvider` — fully synthetic Google fixtures; 3 campaigns (RSA, Display, pMax); used when `APP_MODE=demo` or `GOOGLE_APP_MODE=demo` |
| `google_mask_policy.py` | `GoogleMaskPolicy` — reads `GOOGLE_MASK_MODE`, individual `GOOGLE_MASK_*` flags, and `GOOGLE_METRIC_PROFILE` env vars |
| `google_masking.py` | `GoogleMaskingProvider` — wraps live Google provider; applies status/budget/metrics/pause masks per `GoogleMaskPolicy`; deterministic synthetic metrics seeded by MD5 of campaign_id |
| `factory.py` | `get_meta_provider()` / `get_platform_provider(platform)` — selects Meta provider based on `APP_MODE` + `MASK_MODE`; `get_google_provider()` — returns `GoogleDemoProvider` when `APP_MODE=demo` or `GOOGLE_APP_MODE=demo`; returns `GoogleMaskingProvider` when `GoogleMaskPolicy.enabled`; returns `GooglePlatformProvider` otherwise |

`main.py` calls platform providers through the `PlatformProvider` interface — it has no knowledge of which concrete provider is active.

## Key constraints and known gaps

- The Meta access token stored is a **short-lived user token** — no refresh logic exists
- `GET /api/campaigns` swallows insights errors silently (best-effort metrics); `POST /api/ingest` logs and surfaces them via `insights_errors` in the response
- `ad_insights` accumulates one row per campaign per `POST /api/ingest` call — no date deduplication
- Structural ingest is idempotent via `ON CONFLICT DO UPDATE`; running it twice does not grow row count
- `_clone_dynamic_to_static_ad` requires the source ad's creative to have a `page_id` and a `link` URL in `object_story_spec.link_data`; image-hash-only creatives will fail at Meta creative creation
- New static ads are always created **PAUSED** — no auto-activation
- Frontend has no test infrastructure (no Jest/Vitest); backend tests use pytest + FastAPI TestClient with monkeypatched Meta calls

## What's not implemented yet

- `action="replace"` on confirm — validated but no Meta call; stays at `pending_confirmation`
- `POST /api/suggestions/{id}/reject` — no reject endpoint yet
- Token refresh for Meta access tokens
- Login-time reconciliation of ingested structure against live Meta state
- Sync classification (new / updated / unchanged) on structural ingest
- Push to Meta is implemented (`POST /api/push`) but requires the Meta app to be in **Live mode** (not Development); until then, generated ads show a "ready to push" amber note after Sync
- Auto-activation of new static ads (always created PAUSED, user activates manually in Meta)
- User tier enforcement beyond UI display — no backend guard on premium-only routes yet
- Google Ads: cross-platform unification (Chunk 11) not yet implemented
- Google `normalize_creative` stores marketing image asset resource names (e.g. `customers/123/assets/456`) and video asset resource names rather than resolved URLs — URL resolution requires a separate asset query; `youtube_thumbnail_url` utility exists but is not yet wired into the ingest embedding hook for this reason
- Google structural ingest fires `embed_ad` (text-only; `image_vec=None` zero-pads combiner) and `embed_all_combinations(slots=('headline','description'))` but not `embed_images` — no URL images available from ingest yet
- pMax and Shopping campaigns are ingested as stubs: pMax → `creative_type='pmax'` with headline/description/image/video slots from asset groups; Shopping → `creative_type='shopping'` with `final_url` slot only. Both types are blocked from text generation and BO with a 400 (shopping/unknown are unsupported; pMax proceeds since it has headline/description slots)
- `creative_type` values for Google: `rsa`, `display`, `video`, `pmax`, `shopping`, `unknown`

## Known technical notes

- `combined_vector` stored in `ad_embeddings` is the **raw concatenation** of text (1536-dim) + image (1536-dim) = 3072-dim float32 blob. The BO pipeline's `_build_X()` truncates via `ad_embedding_combiner` to 256-dim at inference time. The stored blob is not used directly by the BO; it is informational only.
- The `embed_ad` fallback path uses image_vector slot from `ad_embeddings` (the seed ad's combined_vector image slot), not from `ad_image_embeddings`. Per-image BO uses `ad_image_embeddings` directly in `selector.get_candidate_combinations`.
- Google RSA ads produce `image_vector = NULL` in `ad_embeddings` (no image URL available from GAQL). The BO combiner zero-pads the image half of the combined vector when `image_vec=None`. RSA BO therefore optimises over text combinations only, with a constant zero image component — this is intentional for RSA ads.

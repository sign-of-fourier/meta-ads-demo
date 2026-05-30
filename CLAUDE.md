# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Start everything (preferred)
```bash
./start.sh              # prod: backend :8000, frontend :5173, then ngrok on :5173
./start.sh staging      # staging: backend :8001, frontend :5174, APP_ENV=staging, then ngrok on :5174
```
`start.sh` starts backend + frontend in the background, then runs ngrok in the foreground on the frontend port.

### Fake Ad Server (optional — replaces live Meta/Google data API calls)
```bash
cd fake_ad_server
uvicorn server:app --port 9000 --reload
```
Then uncomment in `backend/.env`:
```
FAKE_META_BASE_URL=http://localhost:9000/meta/v19.0
FAKE_GOOGLE_BASE_URL=http://localhost:9000/google
```
OAuth still goes to real Google/Meta. Only data API calls (campaigns, insights, structure, push) are intercepted. When `FAKE_META_BASE_URL` is set, `META_GRAPH` in `main.py` uses the fake server base URL — this covers all Meta API calls including `_clone_dynamic_to_static_ad` and convergence insight fetches. See `fake_ad_server/README.md` and `FAKE_AD_SERVER.md` for full details.

**Fake server convergence support:** Meta `GET /{ad_id}/insights?date_preset=lifetime` returns deterministic ramp metrics for pushed clones (from `state.pushed_meta_ad_metrics`). Google GAQL `FROM ad_group_ad` with `metrics.*` in the SELECT returns per-ad ramp metrics for pushed clones (from `state.pushed_google_ad_metrics`); fixture ads return zero metrics.

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
python -m pytest tests/test_bo_pipeline.py -v                                    # Meta BO pipeline tests (no API keys needed)
python -m pytest tests/test_modal_bo.py -v -k "TestModalBOUnit"                 # Modal BO unit tests (no network)
python -m pytest tests/test_cross_platform_bo.py -v                             # cross-platform BO tests (no API keys needed)
python -m pytest tests/test_combination_embeddings.py -v -k "TestBuildCombinations or TestCombinationKey"  # pure (no API)
python -m pytest tests/test_generation_pipeline.py -v                            # image pipeline integration tests (API keys required)
python -m pytest tests/test_text_pipeline.py -v                                  # text pipeline integration tests (API keys required)
python -m pytest tests/test_combination_embeddings.py -v                         # combination embedding integration tests (API keys required)
python -m pytest tests/test_google_db.py tests/test_google_auth.py tests/test_google_campaigns.py tests/test_google_structural_ingest.py tests/test_google_text_pipeline.py tests/test_google_bo.py tests/test_google_push.py tests/test_google_pmax_shopping.py tests/test_google_demo.py tests/test_google_login_customer_id.py tests/test_cross_platform_bo.py -v  # Google + cross-platform (no API keys needed)
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

# Optional: fake_ad_server/ (port 9000) intercepts data API calls at the network layer.
# Activated by FAKE_META_BASE_URL + FAKE_GOOGLE_BASE_URL env vars in backend/.env.
# OAuth is NOT intercepted — auth still goes to real Google/Meta.
#
# frontend/ → backend/ → fake_ad_server/  (when env vars set)
#                      ↘ graph.facebook.com / googleads.googleapis.com  (default)
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
| `init_db()` | Creates all SQLite tables (including `ad_generation_*`, `ad_text_combination_embeddings`, `dynamic_generation_jobs`, `google_connections`, `google_pending_connections`, and `scored_observations` tables via helper calls); runs ALTER TABLE migrations for columns added after initial schema — including `tier TEXT NOT NULL DEFAULT 'free'` on `users`, `seed_ad_id`/`adset_id`/`meta_ad_id` on `dynamic_generation_jobs`, `platform` on `ad_insights`/`ad_creative_structures`, `is_pushed_clone` on `ad_creative_structures`, and `platform_ad_numeric_id`/`current_impressions`/`days_running` on `pushed_ad_combos` |
| `get_current_user_id()` | FastAPI dependency; decodes Bearer JWT |
| `_meta_creds(user_id)` | Loads `(access_token, ad_account_id)` from `meta_connections` for authenticated user |
| `_fetch_campaigns_and_insights()` | Shared async helper for campaigns + 7d insights from Meta; returns `(campaigns_raw, metrics_by_campaign, insights_error_count)` — callers use `insights_error_count` to distinguish API failure from genuine zero delivery |
| `_fetch_campaign_structure()` | Fetches adsets + ads with expanded creative fields for one campaign; includes `effective_status` |
| `_normalize_creative(ad)` | Pure function; detects dynamic (presence of `asset_feed_spec`) vs static; extracts slots |
| `_clone_dynamic_to_static_ad()` | Creates a new static Meta ad from chosen components; used by confirm-create flow |
| `_check_meta_convergence(user_id, campaign_id, access_token)` | Async helper fired fire-and-forget at end of Meta ingest; fetches lifetime impressions via `GET /{platform_ad_id}/insights` for each active unconverged pushed clone in the campaign; updates `current_impressions`, `days_running`; sets `converged=1` and locks in CTR when both thresholds are met; also calls `_write_convergence_observation()` to write `metric='ctr'` to `scored_observations` |
| `_write_convergence_observation(db, user_id, seed_ad_id, combination_key, combination_json, score, metric)` | Helper called by both convergence checkers; looks up `text_vector` from `ad_text_combination_embeddings` and `image_vector` from `ad_image_embeddings`/`ad_embeddings`, then writes to `scored_observations` with `source='convergence'` |
| `_check_google_convergence(user_id, campaign_id, access_token, customer_id, login_customer_id)` | Same as above for Google; GAQL query `SELECT ad_group_ad.resource_name, metrics.impressions, metrics.ctr FROM ad_group_ad WHERE campaign.id = X`; matches rows to pushed clones by resource name |
| `_suggestion_from_row()` | Converts a DB row → `SuggestionResponse` Pydantic model |
| `_download_ad_images(components)` | Downloads Meta CDN image URLs to `backend/ad_images/` synchronously during ingest (before expiry); returns components list with local URLs substituted. Required because Meta CDN URLs are signed and expire quickly — background tasks can't use them. |
| `GET /me` | Returns current user's email and tier (`free`/`premium`) |
| `_upload_image_to_meta()` | Resolves a locally-served image URL (`/images/...` or `/ad-images/...`) to a file on disk, uploads it to `/{ad_account_id}/adimages`, returns the image hash. Returns `None` on failure — push continues without image rather than aborting. |
| `_clone_dynamic_to_static_ad()` | Creates a new static Meta ad from chosen component values. Accepts optional `image_hash`; uses `image_hash` in `link_data` when provided, falls back to `picture` URL otherwise. Fetches `object_story_spec` and `asset_feed_spec` from source ad to inherit `page_id` and destination link URL. |
| `POST /api/push` | Finds all completed `dynamic_generation_jobs` for the user where `meta_ad_id IS NULL` and `seed_ad_id`/`adset_id` are set; uploads each ad's image to Meta, then calls `_clone_dynamic_to_static_ad` to create a PAUSED static ad; records `meta_ad_id` on success. Requires Meta app in Live mode — returns per-job errors gracefully if blocked. |
| `POST /api/ingest/structure/{campaign_id}` (Meta) | After DB commit: runs Meta clone detection (matching `ad_id` against `pushed_ad_combos.platform_ad_id`), then fires `_check_meta_convergence` fire-and-forget, then fires embedding tasks |
| `_google_creds(user_id)` | Reads `google_connections`, always refreshes via `google_ads_api.refresh_access_token`, raises 400 if not connected; returns `(access_token, customer_id, login_customer_id)` |
| `GET /me/google-status` | Returns `{connected, customer_id, customer_name}` |
| `GET /auth/google/login-url` | Generates Google OAuth consent URL; stores state in `oauth_states` with `provider='google'` |
| `GET /auth/google/callback` | Validates state, exchanges code, fetches names for all accessible customers in parallel, stores list in `google_pending_connections`, redirects to `FRONTEND_URL/app/settings?google_pick=<key>` |
| `GET /auth/google/pending/{key}` | Returns `{accounts: [{customer_id, name}]}` for the pending connection; requires auth; validates `user_id` matches |
| `POST /auth/google/select-account` | Body `{key, customer_id, login_customer_id?}`; strips dashes from IDs; accepts manual customer IDs not in the accounts list (for test accounts); saves to `google_connections`, deletes pending row |
| `GET /api/google/campaigns` | Calls `_google_creds`, creates `httpx.AsyncClient`, delegates to `google_provider.fetch_campaigns_and_insights` (passing `login_customer_id`), returns `list[Campaign]` |
| `POST /api/google/ingest/structure/{campaign_id}` | Calls `_google_creds`, runs two parallel GAQL queries (ad groups + ads) via `google_provider.fetch_campaign_structure`, writes to `ad_creative_structures` with `platform='google'`; detects pushed clones by matching `ad_id` against `pushed_ad_combos.platform_ad_numeric_id`; fires `_check_google_convergence` and `embed_ad` + `embed_all_combinations(slots=('headline','description'))` per ad fire-and-forget after commit |
| `GET /api/google/structure/{campaign_id}` | Returns persisted Google creative structures filtered by `platform='google'`; same `list[AdStructure]` shape as Meta route |
| `POST /api/google/generate/text/{campaign_id}` | Generates 10 RSA headline + description variants from the first ingested Google ad (or `?seed_ad_id=`); calls `run_text_pipeline(platform='google')`; returns `GenerateTextResponse`; fires `embed_all_combinations(source_id=seed_ad_id, slots=('headline','description'), components=seed+generated)` fire-and-forget so variants become BO candidates immediately |
| `POST /api/google/bo/run` | Body `{seed_ad_id, text_source_id}`; runs BO via the same `run_bo`/`save_bo_run` functions as the Meta route; returns `BORunResponse`; falls back to random when fewer than MIN_TRAINING_POINTS scored variants exist |
| `GET /api/google/bo/results/{ad_id}` | Returns most recent BO picks for a Google ad via `get_latest_bo_run` |
| `POST /api/google/push` | Finds all latest unpushed `bo_selections` (pick_rank=1, `google_ad_resource_name IS NULL`) for user's Google ads; for each, resolves `adset_id` + `final_url` from `ad_creative_structures` and generated text from `generated_ad_slots`; creates PAUSED RSA via `google_ads_api.create_rsa`; writes resource name to `bo_selections.google_ad_resource_name`; returns `GooglePushResponse` with per-ad results and optional `note` |
| `POST /api/push/pick` | Unified per-pick push for both platforms; creates PAUSED ad, records in `pushed_ad_combos` with `platform_ad_id` (full resource name for Google) and `platform_ad_numeric_id` (last path segment for Google, same as `platform_ad_id` for Meta — used for clone detection during ingest) |
| `_enrich_pick(p, seed_ad_id, user_id)` | Queries `pushed_ad_combos` for the combination key; annotates pick with `ad_name`, `already_pushed`, `push_status`, `converged`, `platform_ad_id`, `current_impressions` |

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
- `POST /api/generate/text/{campaign_id}` — Meta static mode: generates 10 text variants per slot from the first ingested Meta ad; stores in `generated_ads`/`generated_ad_slots`; synchronous; no images; fires `embed_all_combinations(source_id=seed_ad_id, components=seed+generated)` fire-and-forget so generated variants become BO candidates under the same pool as the ingested ad
- `POST /api/google/generate/text/{campaign_id}` — Google RSA mode: generates 10 headline + description variants; calls `run_text_pipeline(platform='google')`; synchronous; no images; fires `embed_all_combinations(source_id=seed_ad_id, slots=('headline','description'), components=seed+generated)` fire-and-forget
- `POST /api/generate/dynamic/{campaign_id}` — Meta dynamic mode: starts background job; returns `{job_id, status:'running'}`; generates 4 variants per slot + 4 AI images + fires all embeddings; stores in `ad_creative_structures`
- `GET /api/generate/dynamic/status/{job_id}` — poll status; returns full slots + image_urls when `status='complete'`

## Ad text combination embeddings (`backend/ad_combination_embeddings/`)

Standalone async module. Embeds every Cartesian combination of text slot values as a single text vector. Reuses `embeddings.embedder.embed_text`. See **`backend/ad_combination_embeddings/README.md`** for full documentation.

Default slots: `('headline', 'primary_text', 'description')` — 4×4×4 = 64 rows for a Meta dynamic ad. For Google RSA, callers pass `slots=('headline', 'description')` — no `primary_text`. The combination key and JSON format are identical regardless of which slots are active.

Public entry points: `embed_all_combinations(source_id, components, slots=TEXT_SLOTS)`, `get_embeddings_for_source(source_id)`, `combination_count(components)` (dry-run, no I/O).

## Embedding combiner (`backend/ad_embedding_combiner/`)

Standalone pure module. Concatenates the full text and image embeddings into a single feature vector for GPR/BO — no truncation. `TEXT_DIM=1536`, `IMAGE_DIM=1536`, output is 3072-dim. PCA in the BO pipeline then reduces this to the working dimension (default 64). Separated because the combination strategy may change independently of the BO logic.

Public entry points: `combine(text_vec, image_vec)`, `truncate_pad(vec, dim)`, `output_dim()`, `TEXT_DIM`, `IMAGE_DIM` from `ad_embedding_combiner/combiner.py`. `image_vec` may be `None` — the image half of the combined vector is zero-padded. This allows RSA (text-only) ads to participate in BO without image embeddings.

## Bayesian Optimisation pipeline (`backend/bo_pipeline/`)

Standalone sync module. Selects two candidate text+image combinations to test next. Operates strictly per-ad — scored variants and text candidates must share the same seed ad. See **`backend/bo_pipeline/`** for implementation details.

**Two methods — controlled by `method` kwarg to `run_bo()` and by env vars:**

| Method | How it works | Activated when |
|---|---|---|
| `"modal"` (default) | PCA-reduces 3072-dim embeddings to `MODAL_BO_PCA_DIMS` dims (default 64), sends the actual discrete candidate PCA vectors to the Modal GP service (stateless q-EI endpoint), receives back candidate indices — no snap-to-pool | `MODAL_BO_API_URL` is set |
| `"local"` | Fits a local sklearn GPR, picks highest EI (pick 1), re-fits with a fantasy observation to pick a diverse second candidate | Always available; automatic fallback when Modal is unconfigured or fails |

Public entry points: `run_bo(seed_ad_id, text_source_id, user_id, db_path, method="modal", platform="meta", target_metric=None)` → `(picks, warning, scored_count, candidate_count)` 4-tuple; `save_bo_run(...)`, `get_latest_bo_run(...)` for persistence. Google BO endpoint passes `platform="google"` so `_build_X` uses 1536-dim text-only instead of 3072-dim zero-padded combined.

Key design: `selector.py` is the only file that knows the DB schema; `gpr.py` is pure numpy/sklearn (local path only); `modal_bo.py` contains PCA helpers and the Modal HTTP call; `pipeline.py` orchestrates both paths. Falls back to random selection when fewer than 2 scored observations exist.

**Scored observations source: `scored_observations` table** — decoupled from the generation pipeline. Three writers:

| Writer | `metric` value | `source` value |
|---|---|---|
| Qwen2-VL (`ad_generation/pipeline.py` after scoring) | `qwen` | `generation_pipeline` |
| Convergence checker (`_check_meta/google_convergence`) | `ctr` | `convergence` |
| Seed script / seed endpoint | `synthetic` | `seed_script` |

`selector.py`'s `get_scored_combinations()` walks `METRIC_PREFERENCE = ("ctr", "cvr", "roas", "qwen", "synthetic")` and returns the first metric with ≥ 1 row. Optional `target_metric` overrides this. All three BO functions (`run_bo`, `run_cross_platform_bo`, `run_unified_cross_platform_bo`) accept `target_metric: str | None = None`.

Candidate combinations: **N_text × N_images cross-product** — all rows in `ad_text_combination_embeddings` for `text_source_id`, each paired with every row in `ad_image_embeddings` for the seed ad. Falls back to the seed ad's single `image_vector` from `ad_embeddings` if no per-image embeddings exist. Each pick's `combination` dict includes `image_url` (the local `/ad-images/...` URL) for display. With 64 text combos and 4 image embeddings → 256 candidates.

**HTTP endpoints (wired):** `POST /api/bo/run` / `GET /api/bo/results/{ad_id}` — Meta BO. `POST /api/google/bo/run` / `GET /api/google/bo/results/{ad_id}` — Google BO. `POST /api/bo/cross-platform` — two-GPR cross-platform BO (original, kept). `POST /api/bo/cross-platform/unified` — unified single-GP cross-platform BO; request body `{pairs: [{platform, seed_ad_id, text_source_id}], top_n: int = 4}`; wired to the Dashboard "Run Cross-Platform Analysis" button. `POST /api/bo/seed-scored-variants` — seeds synthetic `scored_observations` rows directly from existing `ad_text_combination_embeddings`; body `{seed_ad_id, platform, n, text_source_id?}`; requires combination embeddings to already exist. `run_cross_platform_bo` returns `(picks, group_stats)` 2-tuple; `run_unified_cross_platform_bo` does the same. Both cross-platform functions build `group_stats` internally — endpoints no longer pre-fetch scorer/candidate counts.

**Unified cross-platform PCA:** `run_unified_cross_platform_bo` uses `BOGroup.build_X_unified()` for all groups — Google RSAs use `combine(text_vec, None)` → 3072-dim (image half zero-padded), same as Meta. One shared PCA is fit on the pooled 3072-dim union of all groups (scored + candidates) before the single GP call. This ensures EI scores are directly comparable across platforms. The per-platform `run_cross_platform_bo` still uses per-group PCA (separate GPRs per platform, not pooled).

**Seeding scored observations for testing:** `backend/seed_bo_synthetic.py` writes synthetic scores directly to `scored_observations` (no API keys needed). Requires `ad_text_combination_embeddings` to already exist for the ad (run Ingest + wait ~10s, or run Generate Text). Supports `--platform meta|google|cross`. Run with:
```bash
python seed_bo_synthetic.py --platform meta --ad-id <ad_id> --user-id <user_id> --n 5
```

## Frontend (`frontend/src/`)

| File | Role |
|---|---|
| `api.js` | Single fetch wrapper; JWT stored in `localStorage`; all API calls go through here — includes `getMe()`, `runBO()`, `generateTextAds()`, `startDynamicGeneration()`, `getDynamicGenStatus()`, `getLocalAds()`, `deleteLocalAd(adId)`, `getGoogleStatus()`, `getGoogleLoginUrl()`, `getGoogleCampaigns()`, `ingestGoogleStructure(campaignId)`, `getGoogleStructure(campaignId)`, `getGooglePendingAccounts(key)`, `selectGoogleAccount(key, customerId, loginCustomerId)`, `generateGoogleTextAds(campaignId, seedAdId)`, `runGoogleBO(seedAdId, textSourceId)`, `getGoogleBOResults(adId)`, `pushGoogleAds()`, `runCrossPlatformBO(pairs)` (original two-GPR, kept), `runUnifiedCrossPlatformBO(pairs, topN)` (unified single-GP, wired to Dashboard button), `seedScoredVariants(seedAdId, platform, n, textSourceId?)` |
| `App.jsx` | Root layout with nav; React Router `<Outlet>`; fetches `GET /me` on load and exposes user via `UserContext`; shows tier badge in nav; nav links: Settings / Dashboard / Ad Library / Explorer |
| `UserContext.js` | React context (`UserContext`) + `useUser()` hook; default tier `"free"` |
| `pages/AuthPage.jsx` | Signup / login |
| `pages/SettingsPage.jsx` | Connect banner ("authenticate here, then go to Dashboard"); Meta and Google OAuth connect sections; reads `?meta_connected`, `?google_error` redirect params; on `?google_pick=<key>` fetches pending accounts and shows an account picker (radio list + manual customer ID field + optional login customer ID field); on confirm calls `selectGoogleAccount` then refreshes status |
| `pages/DashboardPage.jsx` | Unified ingestion dashboard at `/app/dashboard`; two collapsible platform sections (Meta Ads, Google Ads); cross-platform section has `selectedPairs` batch state `[{platform, seed_ad_id, text_source_id, label}]` (persisted to localStorage), removable chips, `top_n` input (1–8, default 4), "Run Cross-Platform Analysis" button calling `runUnifiedCrossPlatformBO`; passes `onIngest={handleAddToBatch}` and `batchedAdIds` to each platform section |
| `pages/CampaignsPage.jsx` | Meta campaigns — table, pause/resume, metric history, structure panel, suggestions panel; also rendered inside DashboardPage Meta section. Props: `onIngest(adInfo)` where `adInfo={platform,seed_ad_id,text_source_id,label}` (explicit toggle, not auto-called); `batchedAdIds: string[]`. "Add to Analysis"/"In Batch ✓" toggle button in recommendations header per ingested campaign. |
| `pages/GoogleCampaignsPage.jsx` | Google campaigns table; also rendered inside DashboardPage Google section; **Sync button** calls `POST /api/google/push` then re-fetches; Ingest/Reingest per row; after ingest shows both **Get Recommendations** (fires `POST /api/google/bo/run` immediately, seed ad ID resolved from stored structure) and **Generate RSA Text** (fires `POST /api/google/generate/text`) side-by-side; `BOPicksPanel` shows up to 2 picks; `TextGenResults` shows headline + description variants. Same `onIngest`/`batchedAdIds` props as CampaignsPage; "Add to Analysis" toggle in action buttons. |
| `pages/AdsPage.jsx` | Local ad library — reads `GET /api/ads/local`; card grid with source/status badges; click a card to open a detail modal showing image grid + all text slot variants; delete button with confirm dialog calls `DELETE /api/ads/local/{ad_id}` |
| `pages/ExplorerPage.jsx` | Raw Meta API explorer (debug) |
| `components/BOPickCard.jsx` | Shared pick card for Meta and Google; 2-per-row CSS grid in `.bo-results`; click anywhere on the card (except the action footer) to open Preview modal; both Preview and Push modals use `createPortal` → `document.body` to avoid table/grid stacking-context issues; shows image filename if `image_url` present; lifecycle action button states: **Push and Run** → **Activate** → **Running ✓** (active, zero impressions) → **Testing… N impr.** (active, below convergence, amber) → **Running ✓ — Tested** (converged, green). `current_impressions` and `converged` come from the server via `_enrich_pick`; updated on every ingest. |

The Campaigns page drives panels per campaign row:
- **Header controls** — "Sync" button: pushes unpushed generated ads (`POST /api/push`) then pulls latest campaigns (`POST /api/ingest`); shows last-synced timestamp (stored in `localStorage`); amber note if push is blocked by Meta dev-mode
- **Ingest/Reingest button** — per-campaign; label is "Ingest" on first use, "Reingest" thereafter (tracked in `localStorage` as `ingestedIds`); both trigger `POST /api/ingest/structure/<id>`
- **History panel** — stored metric snapshots; when campaign is ingested, shows four action buttons:
  - **Get Recommendations** — triggers BO; shows picks with image preview; shows amber note when `scored_count=0` ("no scored data yet, picks are random")
  - **Static Text Ads** — triggers `POST /api/generate/text/{campaign_id}`; synchronous; shows generated text variants per slot (headline, primary_text, description, cta)
  - **Dynamic Ad (AI Images)** — triggers `POST /api/generate/dynamic/{campaign_id}`; async; polls every 5s; shows 4-image grid + 4 text variants per slot when complete
  - **Seed test data** — compact row with `n` input (1–50) and Seed button; calls `POST /api/bo/seed-scored-variants`; requires combination embeddings to exist first
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
| `AZURE_ANALYSIS_DEPLOYMENT` | Deployment for image analysis (default `gpt-4.1-nano`) |
| `MODAL_SCORING_ENDPOINT` | Modal endpoint for fine-tuned Qwen2-VL ad scorer (default is the `bad-ads-qwen2vl` deployment) |
| `AZURE_TEXT_GEN_DEPLOYMENT` | Deployment for text generation (default `gpt-4.1-nano`) |

Used by: `ad_generation/` pipeline (analyze, score, QA, text gen steps)

#### 4. deAPI — FLUX image generation
| Variable | Description |
|---|---|
| `DEAPI_API_KEY` | deAPI key for FLUX img2img image generation |
| `IMAGES_SERVE_BASE_URL` | Base URL for serving generated images (default `http://localhost:8000/images`). Must be a full URL with scheme — used by the ad generation pipeline for scoring/QA and by `_upload_image_to_meta` to resolve local file paths. |

Used by: `ad_generation/` pipeline (generate + poll steps)

#### 5. Modal GP service — Bayesian Optimisation
| Variable | Description |
|---|---|
| `MODAL_BO_API_URL` | Full URL of the Modal GP q-EI endpoint. Leave blank to fall back to local sklearn GPR. |
| `MODAL_BO_PCA_DIMS` | PCA components to reduce 3072-dim embeddings to before the API call (default `64`). Lower = faster; higher = more fidelity. |
| `MIN_CONVERGENCE_IMPRESSIONS` | Minimum lifetime impressions before a pushed clone is considered converged (default `500`). |
| `MIN_CONVERGENCE_DAYS` | Minimum days running before a pushed clone is considered converged (default `3`). |

Used by: `bo_pipeline/modal_bo.py` → `call_modal_api()`. When unset, `run_bo()` silently uses the local `"local"` (GPR + fantasy) path.

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

## Open design decisions

These are flagged for discussion before implementation — do not implement until resolved.

| Decision | Options | Notes |
|---|---|---|
| **Cross-platform BO** | Two implementations coexist. Original (`POST /api/bo/cross-platform`): per-platform PCA+GPR with shared ECDF, separate GP calls, global EI rank. Unified (`POST /api/bo/cross-platform/unified`, wired to UI): **single shared PCA** fit on pooled 3072-dim vectors from all groups (Google zero-pads image half via `build_X_unified`), then single GP/Modal call, `top_n` parameter (default 4). UI supports arbitrary batch of ads across platforms and campaigns via `selectedPairs` chip list + explicit "Add to Analysis" toggles. |
| **Ad Library — Google ads** | Show Google-ingested/generated ads in the same library with a "Google" source badge, or keep a separate Google Library page. | Currently `GET /api/ads/local` returns Meta-sourced ads only. `SOURCE_LABELS` in `AdsPage.jsx` has no `google` entry. Backend and frontend both need additions when decided. |

---

## What's not implemented yet

- `action="replace"` on confirm — validated but no Meta call; stays at `pending_confirmation`
- `POST /api/suggestions/{id}/reject` — no reject endpoint yet
- Token refresh for Meta access tokens
- Login-time reconciliation of ingested structure against live Meta state
- Sync classification (new / updated / unchanged) on structural ingest
- Push to Meta is implemented (`POST /api/push`) but requires the Meta app to be in **Live mode** (not Development); until then, generated ads show a "ready to push" amber note after Sync
- Auto-activation of new static ads (always created PAUSED, user activates manually in Meta)
- User tier enforcement beyond UI display — no backend guard on premium-only routes yet
- Google Ads: cross-platform UI unification (unified ad library, combined push flow) not yet implemented
- Google `normalize_creative` stores marketing image asset resource names (e.g. `customers/123/assets/456`) and video asset resource names rather than resolved URLs — URL resolution requires a separate asset query; `youtube_thumbnail_url` utility exists but is not yet wired into the ingest embedding hook for this reason
- Google structural ingest fires `embed_ad` (text-only; `image_vec=None` zero-pads combiner) and `embed_all_combinations(slots=('headline','description'))` but not `embed_images` — no URL images available from ingest yet
- **Score existing images at ingest time** — currently only AI-generated image variants (from the generation pipeline) receive Qwen2-VL scores. Native ads with existing images get zero scored observations until the user runs the generation pipeline. Fix: fire a Qwen2-VL scoring task fire-and-forget during ingest for each ad's existing images.
- **UI selector for target_metric (CTR / CVR / ROAS)** — `BORunRequest.target_metric` and `run_bo(..., target_metric=)` are wired end-to-end; UI dropdown to let users choose the optimisation target is not yet built. Currently the selector auto-picks the best available metric via `METRIC_PREFERENCE`.
- **Mid-test edit detection (orphan ad handling)** — convergence checking runs at ingest; edit detection (comparing ingested creative fields against `pushed_ad_combos.combination`) is not yet wired. See Gap A in `WORKFLOW.md`.
- pMax and Shopping campaigns are ingested as stubs: pMax → `creative_type='pmax'` with headline/description/image/video slots from asset groups; Shopping → `creative_type='shopping'` with `final_url` slot only. Both types are blocked from text generation and BO with a 400 (shopping/unknown are unsupported; pMax proceeds since it has headline/description slots)
- `creative_type` values for Google: `rsa`, `display`, `video`, `pmax`, `shopping`, `unknown`

## Known technical notes

- `combined_vector` stored in `ad_embeddings` is the **raw concatenation** of text (1536-dim) + image (1536-dim) = 3072-dim float32 blob. `_build_X(combinations, platform)` is platform-aware: `platform="google"` returns 1536-dim text-only (no zero-padding); `platform="meta"` (default) returns 3072-dim via `combine(text_vec, image_vec)`. PCA in `_run_modal_bo` / `_run_local_bo` reduces to the working dimension at inference time. `BOGroup.build_X_unified()` always returns 3072-dim for all platforms (Google gets `combine(text_vec, None)` → zero-padded image half) — used only by `run_unified_cross_platform_bo` for the shared PCA step.
- The `embed_ad` fallback path uses image_vector slot from `ad_embeddings` (the seed ad's combined_vector image slot), not from `ad_image_embeddings`. Per-image BO uses `ad_image_embeddings` directly in `selector.get_candidate_combinations`.
- Google RSA ads produce `image_vector = NULL` in `ad_embeddings` (no image URL available from GAQL). The BO combiner zero-pads the image half of the combined vector when `image_vec=None`. RSA BO therefore optimises over text combinations only, with a constant zero image component — this is intentional for RSA ads.

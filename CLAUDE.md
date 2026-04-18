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

### Tests
```bash
cd backend
source .venv/bin/activate
python -m pytest test_structural_ingest.py test_suggestions.py -v   # unit tests (no API keys needed)
python -m pytest test_generation_pipeline.py -v                      # image pipeline integration tests (API keys required)
python -m pytest test_text_pipeline.py -v                            # text pipeline integration tests (API keys required)
python -m pytest test_combination_embeddings.py -v -k "TestBuildCombinations or TestCombinationKey"  # pure (no API)
python -m pytest test_combination_embeddings.py -v                   # combination embedding integration tests
python -m pytest test_bo_pipeline.py -v                              # BO pipeline tests (no API keys needed)
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
                   └── providers/ (MetaProvider interface)
                         ├── LiveMetaProvider   → Meta Marketing API (graph.facebook.com)
                         ├── MaskingMetaProvider → wraps Live; selectively overrides fields
                         └── DemoMetaProvider   → fully synthetic fixtures (APP_MODE=demo)
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
| `init_db()` | Creates all 6 SQLite tables; runs ALTER TABLE migrations for columns added after initial schema |
| `get_current_user_id()` | FastAPI dependency; decodes Bearer JWT |
| `_meta_creds(user_id)` | Loads `(access_token, ad_account_id)` from `meta_connections` for authenticated user |
| `_fetch_campaigns_and_insights()` | Shared async helper for campaigns + 7d insights from Meta; returns `(campaigns_raw, metrics_by_campaign, insights_error_count)` — callers use `insights_error_count` to distinguish API failure from genuine zero delivery |
| `_fetch_campaign_structure()` | Fetches adsets + ads with expanded creative fields for one campaign; includes `effective_status` |
| `_normalize_creative(ad)` | Pure function; detects dynamic (presence of `asset_feed_spec`) vs static; extracts slots |
| `_clone_dynamic_to_static_ad()` | Creates a new static Meta ad from chosen components; used by confirm-create flow |
| `_suggestion_from_row()` | Converts a DB row → `SuggestionResponse` Pydantic model |

Image serving: `main.py` mounts `StaticFiles` at `/images` → `backend/generated_images/`. This is how locally-saved generated images are made accessible to the scoring model and eventually to the frontend.

See `SCHEMAS.md` for full table definitions and `README.md` for the API route table.

## Embeddings (`backend/embeddings/`)

Standalone async module. Called fire-and-forget from structural ingest; also runnable standalone. Not wired into any HTTP route. See **`backend/embeddings/README.md`** for full documentation, env vars, and test instructions.

Public entry point: `embed_ad(user_id, ad_id, campaign_id, components, db_path)` in `embeddings/pipeline.py`.

## Ad generation pipeline (`backend/ad_generation/`)

Fully async standalone module. 7-step pipeline: analyze → generate → poll → save → score → QA → correct. Not wired into any HTTP route yet. See **`backend/ad_generation/README.md`** for full documentation, env vars, and test instructions.

Public entry points from `ad_generation/pipeline.py`: `create_job()`, `run_generation_job()`, `get_job_status()`, `get_job_variants()`, `get_active_variants()`.

## Ad text generation pipeline (`backend/ad_text_generation/`)

Standalone async module. Generates N new text variants per slot (headline, primary_text, description) from a seed ad's components, assembles them with optional image URLs into a dynamic ad component list, and stores the result. See **`backend/ad_text_generation/README.md`** for full documentation.

Public entry point: `run_text_pipeline(seed_components, n_per_slot, image_urls, ...) → generated_ad_id` in `ad_text_generation/pipeline.py`.

## Ad text combination embeddings (`backend/ad_combination_embeddings/`)

Standalone async module. For a dynamic ad with N headlines × M primary texts × K descriptions, embeds every Cartesian combination as a single text vector (JSON of `{"headline": "...", "primary_text": "..."}` — no image). Reuses `embeddings.embedder.embed_text`. See **`backend/ad_combination_embeddings/README.md`** for full documentation.

Public entry points: `embed_all_combinations(source_id, components)`, `get_embeddings_for_source(source_id)`, `combination_count(components)` (dry-run, no I/O).

## Embedding combiner (`backend/ad_embedding_combiner/`)

Standalone pure module. Truncates text and image embeddings to fixed dimensions (`TEXT_DIM=128`, `IMAGE_DIM=128`) and concatenates them into a single feature vector for GPR/BO. Separated because the combination strategy may change independently of the BO logic.

Public entry points: `combine(text_vec, image_vec)`, `truncate_pad(vec, dim)`, `output_dim()`, `TEXT_DIM`, `IMAGE_DIM` from `ad_embedding_combiner/combiner.py`.

## Bayesian Optimisation pipeline (`backend/bo_pipeline/`)

Standalone sync module. Fits a GPR on scored image variants, then selects two candidate text+image combinations via Expected Improvement (pick 1) and a fantasy step (pick 2, batch BO). Operates strictly per-ad — scored variants and text candidates must share the same seed ad. See **`backend/bo_pipeline/`** for implementation details.

Public entry points: `run_bo(seed_ad_id, text_source_id, user_id, db_path)` → list of up to 2 picks; `save_bo_run(...)`, `get_latest_bo_run(...)` for persistence.

Key design: `selector.py` is the only file that knows the DB schema; `gpr.py` is pure numpy/sklearn; `pipeline.py` orchestrates. Falls back to random selection when fewer than 2 scored observations exist.

Scored combinations: `ad_generation_variants` (score IS NOT NULL, status != defunct) joined via `ad_embeddings` using convention `ad_id = gen_{job_id}_{variant_id}`.
Candidate combinations: all rows in `ad_text_combination_embeddings` for `text_source_id`, each paired with the seed ad's image embedding.

## Frontend (`frontend/src/`)

| File | Role |
|---|---|
| `api.js` | Single fetch wrapper; JWT stored in `localStorage`; all API calls go through here |
| `App.jsx` | Root layout with nav; React Router `<Outlet>` |
| `pages/AuthPage.jsx` | Signup / login |
| `pages/SettingsPage.jsx` | Meta OAuth connect flow; reads `?meta_connected=true` redirect param |
| `pages/CampaignsPage.jsx` | Main working page — campaigns table, pause/resume, metric history, Creatives panel, Suggestions panel |
| `pages/AdsPage.jsx` | Ad creatives listing |
| `pages/ExplorerPage.jsx` | Raw Meta API explorer (debug) |

The Campaigns page drives three separate panels per campaign row, all toggled inline:
- **History panel** — stored metric snapshots
- **Structure panel** — ingested creative structure (slots + lifecycle badges)
- **Suggestions panel** (inside structure panel) — pending/confirmed suggestions with Confirm Create button

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

### Embeddings env vars

| Variable | Description |
|---|---|
| `AZURE_INFERENCE_KEY` | Azure AI Inference API key (required for embeddings) |
| `AZURE_ENDPOINT` | Azure AI Inference endpoint (defaults to project resource URL) |
| `AZURE_IMAGE_MODEL` | Embedding model for images (default `embed-v-4-0`) |
| `AZURE_TEXT_MODEL` | Embedding model for text (default `embed-v-4-0`) |

### Ad generation env vars

| Variable | Description |
|---|---|
| `AZURE_OPENAI_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource endpoint (e.g. `https://your-resource.openai.azure.com/`) |
| `AZURE_OPENAI_API_VERSION` | Defaults to `2024-12-01-preview` |
| `AZURE_ANALYSIS_DEPLOYMENT` | GPT-4o deployment name for image analysis (default `gpt-4o`) |
| `AZURE_SCORING_DEPLOYMENT` | Fine-tuned model deployment for ad scoring (default `gpt-4-04-14`) |
| `AZURE_TEXT_GEN_DEPLOYMENT` | GPT-4o deployment name for text variant generation (default `gpt-4o`) |
| `DEAPI_API_KEY` | deAPI key for FLUX img2img generation |
| `IMAGES_SERVE_BASE_URL` | Base URL where `backend/generated_images/` is served (default `http://localhost:8000/images`) |

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

## Provider layer (`backend/providers/`)

| File | Role |
|---|---|
| `meta_provider.py` | Abstract base class (`ABC` + `@abstractmethod`); defines interface for all providers |
| `meta_live.py` | Calls real Meta Graph API |
| `meta_masking.py` | Wraps `LiveMetaProvider`; overrides selected fields per `MaskPolicy` |
| `mask_policy.py` | Reads `MASK_*` env vars into a `MaskPolicy` config object |
| `meta_demo.py` | Fully synthetic fixtures; used when `APP_MODE=demo` |
| `factory.py` | `get_meta_provider()` — selects provider based on `APP_MODE` + `MASK_MODE` |

`main.py` calls `meta_provider.fetch_campaigns_and_insights(...)`, `fetch_ads(...)`, `pause_campaign(...)`, and `resume_campaign(...)` through the interface — it has no knowledge of which provider is active.

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
- HTTP routes to trigger / inspect `ad_generation` jobs — module exists but is not yet wired into `main.py`
- HTTP routes to trigger `ad_text_generation`, `ad_combination_embeddings`, or `bo_pipeline` — all standalone, none wired into `main.py`
- HTTP routes to query `ad_embeddings` or `bo_selections` — results not exposed via API
- Frontend visibility into generated images, text variants, or BO recommendations
- Wiring `bo_pipeline` output into the existing `suggested_configurations` / Suggestions panel flow
- Auto-activation of new static ads (always created PAUSED, user activates manually in Meta)

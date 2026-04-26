

===== FILE: ./AD.md =====

# Dynamic Ads, Embeddings, and the Generation Pipeline

This document covers:
- What Meta dynamic ads are and how they work
- How this codebase mirrors that structure locally
- How we generate new dynamic ads with AI (text + images)
- How embeddings are created and used
- How all of it connects to the Bayesian Optimisation loop

For DB table column-by-column definitions see `SCHEMAS.md`. For BO math see `backend/bo_pipeline/README.md`.

---

## What is a Meta dynamic ad?

A **static ad** in Meta has exactly one value per slot — one headline, one image, one body text. Meta serves that one combination to everyone.

A **dynamic ad** (officially: Dynamic Creative) is a template. You provide a pool of values for each slot:

```
headline:      ["Save 20% today", "Limited time offer", "New arrivals in"]
primary_text:  ["Shop our summer collection", "Free shipping on orders over $50"]
description:   ["Up to 50% off", "While stocks last"]
image:         [<image A>, <image B>, <image C>]
```

Meta's delivery system automatically assembles combinations from these pools and learns which combinations perform best for different audience segments. A campaign with 3 headlines × 2 primary texts × 2 descriptions × 3 images has 36 possible combinations; Meta explores them automatically and shifts budget toward winners.

### How Meta stores dynamic creative internally

Meta's API exposes dynamic creatives via the `asset_feed_spec` field on the creative object. Each slot is an array of items, and each item has an index. Our normalisation mirrors this exactly — see the storage model below.

---

## How we store ads: `ad_creative_structures`

Every ad — whether ingested from Meta or generated locally — is stored as rows in `ad_creative_structures`. One row per `(user_id, ad_id, slot, slot_index)`.

### Static ad rows
```
ad_id="23851234"  creative_type="static"  slot="headline"      slot_index=0  value="Buy Now"
ad_id="23851234"  creative_type="static"  slot="primary_text"  slot_index=0  value="Great deals"
ad_id="23851234"  creative_type="static"  slot="image"         slot_index=0  value="https://..."
```

Static ads always have `slot_index=0` — there is only one value per slot.

### Dynamic ad rows
```
ad_id="23859999"  creative_type="dynamic"  slot="headline"      slot_index=0  value="Save 20%"
ad_id="23859999"  creative_type="dynamic"  slot="headline"      slot_index=1  value="New arrivals"
ad_id="23859999"  creative_type="dynamic"  slot="headline"      slot_index=2  value="Limited offer"
ad_id="23859999"  creative_type="dynamic"  slot="image"         slot_index=0  value="https://cdn.../a.jpg"
ad_id="23859999"  creative_type="dynamic"  slot="image"         slot_index=1  value="https://cdn.../b.jpg"
```

Dynamic ads have multiple rows per slot — one per variant. The `slot_index` is the variant number within that slot.

### Key columns for distinguishing origin

| `data_source` | `lifecycle_status` | Meaning |
|---|---|---|
| `'real'` | `'active'` / `'inactive'` / `'missing'` | Ingested from Meta live API |
| `'masked'` | same | Meta data with synthetic metrics injected |
| `'demo'` | same | Fully synthetic fixtures (APP_MODE=demo) |
| `'generated'` | `'generated'` | Created locally by our AI pipeline; not yet in Meta |

The `lifecycle_status='generated'` rows are the target for the future "push to Meta" sync direction. The pull direction (Meta → local) uses `'active'`, `'inactive'`, `'missing'`.

---

## Structural ingest: pulling from Meta

`POST /api/ingest/structure/{campaign_id}` calls `_fetch_campaign_structure()` then `_normalize_creative()`.

**`_normalize_creative(ad)`** detects dynamic vs static by checking for `asset_feed_spec` in the creative:
- If present: reads `titles[]`, `bodies[]`, `descriptions[]`, `images[]` → one row per array entry
- If absent: reads top-level fields (`title`, `body`, `image_url`, `thumbnail_url`) → always `slot_index=0`

Image hashes (Meta stores images as content hashes, not URLs, in `asset_feed_spec.images`) are resolved to CDN URLs via the Meta `adimages` API during this step, before rows are written.

After all rows are written, three embedding tasks fire **fire-and-forget** for each ad:
```python
asyncio.create_task(embed_ad(user_id, ad_id, campaign_id, local_comps))
asyncio.create_task(embed_images(user_id, ad_id, campaign_id, local_comps))
asyncio.create_task(embed_all_combinations(source_id=ad_id, components=components))
```

`local_comps` has Meta CDN URLs replaced with local `/ad-images/...` URLs (downloaded synchronously before task launch to prevent expiry).

---

## Generating new ads locally

### Mode 1: Static text generation (`POST /api/generate/text/{campaign_id}`)

Generates **10 new text variants per slot** (headline, primary_text, description, cta) from the seed ad's existing copy, using GPT-4o. Results are stored in `generated_ads` / `generated_ad_slots` — a separate staging table, not `ad_creative_structures`. This is a one-shot synchronous call.

**Does not generate images. Does not fire embeddings.** It's a drafting tool.

### Mode 2: Dynamic ad generation (`POST /api/generate/dynamic/{campaign_id}`)

Generates a full **4×4×4×4 dynamic ad** with AI-generated images and fires the complete embedding pipeline. Returns immediately with a `job_id`; the job runs as a background task. Poll `GET /api/generate/dynamic/status/{job_id}` every 5 seconds.

**What the background task does:**

1. **Text generation** — calls `generate_all_slots(n_per_slot=4, slots=['headline','primary_text','description','cta'])`. Each slot gets 4 variants via the LLM. `cta` has no seed values in `ad_creative_structures` (Meta doesn't expose CTA text as a slot), so the model generates action-oriented button text from scratch using the slot hint.

2. **Image generation** — creates one `ad_generation_jobs` record, runs the full 7-step pipeline (`analyze → submit 10 to deAPI → poll → download → score → QA → correct`). After completion, picks the **top-4 scored active variants** (sorted by score descending, then unscored as fallback). Falls back to the seed image if the pipeline fails. Pads to 4 images using the seed image if fewer than 4 are generated.

3. **Assembly** — builds a components list with 4 values per text slot + up to 4 image URLs.

4. **Persistence** — writes all components to `ad_creative_structures` with:
   - `ad_id = f"gen_dyn_{uuid[:12]}"` (e.g. `gen_dyn_3a9f2c1b0e4d`)
   - `creative_type = 'dynamic'`
   - `data_source = 'generated'`
   - `lifecycle_status = 'generated'`
   - `ad_account_id = 'generated'`, `adset_id = 'generated'` (placeholders until pushed)

5. **Embeddings** — fires all three embedding tasks for the new `gen_dyn_*` ad_id (see below).

6. **Status update** — marks `dynamic_generation_jobs.status = 'complete'` with the new `ad_id`.

The generated dynamic ad is now a first-class entry in `ad_creative_structures` and behaves identically to a Meta-ingested dynamic ad for all downstream purposes (BO, structure API, future push-to-Meta).

---

## The embedding system

### Why embed ads?

An embedding is a dense float32 vector — a point in high-dimensional space — where semantic similarity maps to geometric closeness. Two ads with similar copy and visuals will have nearby vectors. This gives us a continuous, differentiable representation of "ad content" that the Bayesian Optimisation pipeline can reason about.

### Three embedding tasks per ad

Every ad (ingested or generated) triggers three separate embedding jobs:

#### 1. Seed embedding — `embed_ad`

**Table:** `ad_embeddings` — one row per `(user_id, ad_id)`, upserted.

Uses the first text slots (headline + primary_text + description at slot_index=0) and the first image URL. Embeds them separately and concatenates:

```
text_vector  (1536-dim)  ← OpenAI text-embedding-3-small
image_vector (1536-dim)  ← Azure AI Inference embed-v-4-0 (raw model output)
combined_vector (3072-dim) ← raw concat, stored as-is
```

The combined_vector blob is informational — the BO pipeline does NOT use it directly. The BO applies its own truncation at inference time.

**Smart skip:** Re-runs only if `text_snapshot` changed OR either vector is NULL. A past partial failure (e.g., image key missing) leaves a NULL column; the next ingest retries both.

#### 2. Per-image embeddings — `embed_images`

**Table:** `ad_image_embeddings` — one row per `(user_id, ad_id, slot_index)`.

Embeds each image URL slot separately. A dynamic ad with 4 images gets 4 rows. The BO pipeline reads these to form the image dimension of its candidate pool (each text combination is paired with each image embedding → N_text × N_images candidates).

`image_ref` stores the local `/ad-images/...` or `/images/...` URL. The embedder downloads the image via httpx and passes it to Azure as a base64 data URI — Azure never touches localhost URLs directly.

#### 3. Text combination embeddings — `embed_all_combinations`

**Table:** `ad_text_combination_embeddings` — one row per Cartesian combination of text slots.

For a 4-headline × 4-primary_text × 4-description dynamic ad: **4 × 4 × 4 = 64 rows**.

Each combination is embedded as a single JSON string:
```json
{"description": "Free shipping.", "headline": "Wool Socks", "primary_text": "Hand made in Switzerland."}
```

The `combination_key` is `json.dumps(combo, sort_keys=True)` — deterministic, forms the UNIQUE key with `source_id`. Already-embedded combinations are skipped on re-runs (idempotent).

The `slots` parameter is always `('headline', 'primary_text', 'description')` — CTA is stored in `ad_creative_structures` but is not included in the combination embeddings because the BO scoring model was trained on headline+primary_text+description only.

### Embedding models

| Modality | Service | Model | Dimension |
|---|---|---|---|
| Text | OpenAI | `text-embedding-3-small` | 1536 |
| Image | Azure AI Inference | `embed-v-4-0` | 1024 (raw) |

These are **two different services** with different API keys (`OPENAI_KEY` vs `AZURE_INFERENCE_KEY`). Do not confuse with the Azure OpenAI service used for text generation.

### CDN URL expiry problem

Meta CDN URLs are signed and expire within minutes. By the time a background embedding task runs, the URL is stale. Solution:

During `_fetch_campaign_structure()`, `_download_ad_images(components)` is called **synchronously** (before any background tasks launch). It downloads each image to `backend/ad_images/` and returns the components list with CDN URLs replaced by `http://localhost:8000/ad-images/<filename>`. The local URL is passed to `embed_ad` and `embed_images`. Since the embedder downloads the image via httpx, it hits the local FastAPI server — which is always reachable from within the same process.

For locally generated images (from the AI image pipeline), images are already saved to `backend/generated_images/` and served at `/images/<filename>`, so no download step is needed.

### Truncation for BO

The `ad_embedding_combiner` module (called only at BO inference time) truncates both vectors before passing them to the GPR:

```
text_vec  (1536-dim) → truncate to TEXT_DIM=128
image_vec (1024-dim) → truncate to IMAGE_DIM=128
combined  = [text_128 | image_128]  →  256-dim float32
```

The leading dimensions of both OpenAI and Azure embeddings capture the bulk of semantic variance by design. 256 dimensions is a deliberate computational trade-off for fast GPR inference — see `backend/bo_pipeline/README.md` for the full rationale.

---

## How embeddings connect to BO

The BO pipeline (`POST /api/bo/run`) takes a `seed_ad_id` and `text_source_id` (usually the same ad_id) and returns 2 picks.

**Scored observations (training data):**
Rows in `ad_generation_variants` where `score IS NOT NULL` and `status != 'defunct'`, joined to `ad_embeddings` via the convention `ad_id = f"gen_{job_id}_{variant_id}"`. These are the ad combinations we've already shown to audiences and gotten feedback on.

**Candidate pool:**
Cross-product of:
- All rows in `ad_text_combination_embeddings` for `text_source_id` (the 64 text combos)
- All rows in `ad_image_embeddings` for the seed ad (up to 4 image embeddings)
- Total: 64 × 4 = **256 candidates**

Each candidate gets a 256-dim feature vector: `[text_combination_vector_128 | image_embedding_128]`.

The GPR fits on scored observations, then Expected Improvement selects the two best unscored candidates. Pick 1 = highest EI. Pick 2 = highest EI after a "fantasy" refitting step that treats Pick 1 as already scored (encouraging diversity).

---

## Data lineage summary

```
Meta API
  └── _fetch_campaign_structure()
        └── _normalize_creative()
              └── ad_creative_structures  (data_source='real')
                    │
                    ├── embed_ad()         → ad_embeddings
                    ├── embed_images()     → ad_image_embeddings
                    └── embed_all_combinations() → ad_text_combination_embeddings

AI Generation pipeline
  └── _run_dynamic_generation() [background task]
        ├── generate_all_slots()       (text: 4 per slot)
        ├── run_generation_job()       (images: FLUX via deAPI → 4 best)
        └── ad_creative_structures    (data_source='generated', lifecycle='generated')
              │
              ├── embed_ad()          → ad_embeddings
              ├── embed_images()      → ad_image_embeddings
              └── embed_all_combinations() → ad_text_combination_embeddings

Both paths feed the same BO pipeline:
  ad_text_combination_embeddings (64 text combos)
  × ad_image_embeddings          (4 images)
  = 256 candidates
  + ad_generation_variants       (scored observations, training data)
  → bo_pipeline → 2 picks → bo_selections
```

---

## Future: push to Meta

Locally-generated ads (`data_source='generated'`) need to be pushed to Meta to become real ads. The architecture is already set up for this:

- `ad_creative_structures` rows with `data_source='generated'` represent the payload
- `ad_account_id` and `adset_id` are currently `'generated'` placeholders — these need to be resolved to real Meta IDs at push time
- The `lifecycle_status` transitions: `'generated'` → `'created_static'` or `'active'` after push
- "Sync" should become bidirectional: pull (existing) and push (rows where `data_source='generated'` and `lifecycle_status='generated'`)
- The `_clone_dynamic_to_static_ad()` function already handles creating one static ad from chosen components; a push-all flow would iterate over the generated dynamic ad's slot pool and create the full creative via `asset_feed_spec`



===== FILE: ./CLAUDE.md =====

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
| `init_db()` | Creates all SQLite tables (including `ad_generation_*`, `ad_text_combination_embeddings`, and `dynamic_generation_jobs` tables via helper calls); runs ALTER TABLE migrations for columns added after initial schema — including `tier TEXT NOT NULL DEFAULT 'free'` on `users` |
| `get_current_user_id()` | FastAPI dependency; decodes Bearer JWT |
| `_meta_creds(user_id)` | Loads `(access_token, ad_account_id)` from `meta_connections` for authenticated user |
| `_fetch_campaigns_and_insights()` | Shared async helper for campaigns + 7d insights from Meta; returns `(campaigns_raw, metrics_by_campaign, insights_error_count)` — callers use `insights_error_count` to distinguish API failure from genuine zero delivery |
| `_fetch_campaign_structure()` | Fetches adsets + ads with expanded creative fields for one campaign; includes `effective_status` |
| `_normalize_creative(ad)` | Pure function; detects dynamic (presence of `asset_feed_spec`) vs static; extracts slots |
| `_clone_dynamic_to_static_ad()` | Creates a new static Meta ad from chosen components; used by confirm-create flow |
| `_suggestion_from_row()` | Converts a DB row → `SuggestionResponse` Pydantic model |
| `_download_ad_images(components)` | Downloads Meta CDN image URLs to `backend/ad_images/` synchronously during ingest (before expiry); returns components list with local URLs substituted. Required because Meta CDN URLs are signed and expire quickly — background tasks can't use them. |
| `GET /me` | Returns current user's email and tier (`free`/`premium`) |

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

Standalone async module. Generates N new text variants per slot from a seed ad's components. **Text slots: `headline`, `primary_text`, `description`, `cta`** (CTA added to `TEXT_SLOTS` in `generator.py`). See **`backend/ad_text_generation/README.md`** for full documentation.

Public entry point: `run_text_pipeline(seed_components, n_per_slot, image_urls, ...) → generated_ad_id` in `ad_text_generation/pipeline.py`.

**HTTP routes (wired):**
- `POST /api/generate/text/{campaign_id}` — static mode: generates 10 text variants per slot from the first ingested ad; stores in `generated_ads`/`generated_ad_slots`; synchronous; no images; no embeddings
- `POST /api/generate/dynamic/{campaign_id}` — dynamic mode: starts background job; returns `{job_id, status:'running'}`; generates 4 variants per slot + 4 AI images + fires all embeddings; stores in `ad_creative_structures`
- `GET /api/generate/dynamic/status/{job_id}` — poll status; returns full slots + image_urls when `status='complete'`

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
Candidate combinations: **N_text × N_images cross-product** — all rows in `ad_text_combination_embeddings` for `text_source_id`, each paired with every row in `ad_image_embeddings` for the seed ad. Falls back to the seed ad's single `image_vector` from `ad_embeddings` if no per-image embeddings exist. Each pick's `combination` dict includes `image_url` (the local `/ad-images/...` URL) for display. With 64 text combos and 4 image embeddings → 256 candidates.

**HTTP endpoints (wired):** `POST /api/bo/run` runs BO and saves picks; `GET /api/bo/results/{ad_id}` returns the latest picks.

**Seeding scored observations for testing:** `backend/seed_bo.py` inserts synthetic scored variants into `ad_generation_jobs`, `ad_generation_variants`, and `ad_embeddings`. Run with:
```bash
python seed_bo.py --ad-id <ad_id> --user-id <user_id> --campaign-id <campaign_id> --n 5
```

## Frontend (`frontend/src/`)

| File | Role |
|---|---|
| `api.js` | Single fetch wrapper; JWT stored in `localStorage`; all API calls go through here — includes `getMe()`, `runBO()`, `generateTextAds()`, `startDynamicGeneration()`, `getDynamicGenStatus()`, `getLocalAds()`, `deleteLocalAd(adId)` |
| `App.jsx` | Root layout with nav; React Router `<Outlet>`; fetches `GET /me` on load and exposes user via `UserContext`; shows tier badge in nav |
| `UserContext.js` | React context (`UserContext`) + `useUser()` hook; default tier `"free"` |
| `pages/AuthPage.jsx` | Signup / login |
| `pages/SettingsPage.jsx` | Meta OAuth connect flow; reads `?meta_connected=true` redirect param |
| `pages/CampaignsPage.jsx` | Main working page — campaigns table, pause/resume, metric history, structure panel, suggestions panel |
| `pages/AdsPage.jsx` | Local ad library — reads `GET /api/ads/local`; card grid with source/status badges; click a card to open a detail modal showing image grid + all text slot variants; delete button with confirm dialog calls `DELETE /api/ads/local/{ad_id}` |
| `pages/ExplorerPage.jsx` | Raw Meta API explorer (debug) |

The Campaigns page drives panels per campaign row:
- **Header controls** — "Sync Campaigns" button (direct `POST /api/ingest` + reload) and last-synced timestamp (stored in `localStorage`)
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
| `IMAGES_SERVE_BASE_URL` | Base URL for serving generated images (default `http://localhost:8000/images`) |

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
- Push to Meta — locally-generated ads (`data_source='generated'`) exist in `ad_creative_structures` but no route pushes them to Meta; "Sync" is currently pull-only
- Auto-activation of new static ads (always created PAUSED, user activates manually in Meta)
- User tier enforcement beyond UI display — no backend guard on premium-only routes yet

## Known technical notes

- `combined_vector` stored in `ad_embeddings` is the **raw concatenation** of text (1536-dim) + image (1536-dim) = 3072-dim float32 blob. The BO pipeline's `_build_X()` truncates via `ad_embedding_combiner` to 256-dim at inference time. The stored blob is not used directly by the BO; it is informational only.
- The `embed_ad` fallback path uses image_vector slot from `ad_embeddings` (the seed ad's combined_vector image slot), not from `ad_image_embeddings`. Per-image BO uses `ad_image_embeddings` directly in `selector.get_candidate_combinations`.



===== FILE: ./DEV_QUICKSTART.md =====

# Developer / Operator Quick Start

This is for whoever is running the server. For the end-user guide see `QUICK_START.md`.

---

## 1 — Environment variables

Create `backend/.env` (gitignored). Never commit secrets.

### Required — Meta & Auth
```env
META_APP_ID=your_meta_app_id
META_APP_SECRET=your_meta_app_secret
META_REDIRECT_URI=https://<your-ngrok-subdomain>.ngrok-free.dev/auth/meta/callback
FRONTEND_URL=https://<your-ngrok-subdomain>.ngrok-free.dev
JWT_SECRET=any_random_string
META_API_VERSION=v19.0
```

### AI services — four separate accounts, do not mix up keys

| Service | What it does | Key var | Endpoint var |
|---|---|---|---|
| OpenAI | Text embeddings | `OPENAI_KEY` | — (openai.com) |
| Azure AI Inference | Image embeddings | `AZURE_INFERENCE_KEY` | `AZURE_EMBEDDING_ENDPOINT` |
| Azure OpenAI | Ad analysis, text gen, scoring | `AZURE_OPENAI_KEY` | `AZURE_OPENAI_ENDPOINT` |
| deAPI | FLUX image generation | `DEAPI_API_KEY` | — |

```env
# Text embeddings (OpenAI)
OPENAI_KEY=sk-...
OPENAI_TEXT_MODEL=text-embedding-3-small

# Image embeddings (Azure AI Inference — different from Azure OpenAI)
AZURE_INFERENCE_KEY=...
AZURE_EMBEDDING_ENDPOINT=https://markpshipman-2243-resource.services.ai.azure.com/models
AZURE_IMAGE_MODEL=embed-v-4-0

# Ad generation / analysis / scoring (Azure OpenAI)
AZURE_OPENAI_KEY=...
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-12-01-preview
AZURE_ANALYSIS_DEPLOYMENT=gpt-4o
AZURE_SCORING_DEPLOYMENT=gpt-4-04-14
AZURE_TEXT_GEN_DEPLOYMENT=gpt-4o

# Image generation (deAPI / FLUX)
DEAPI_API_KEY=...
IMAGES_SERVE_BASE_URL=http://localhost:8000/images
```

---

## 2 — Start the servers

```bash
# Terminal 1 — Backend (auto-reloads on file save)
cd backend
source .venv/bin/activate
python main.py        # → http://localhost:8000

# Terminal 2 — Frontend
cd frontend
npm run dev           # → http://localhost:5173

# Terminal 3 — ngrok tunnel (required for Meta OAuth)
ngrok http 5173
# Copy the https://*.ngrok-free.dev URL
# Update META_REDIRECT_URI and FRONTEND_URL in backend/.env
# Update allowedHosts in frontend/vite.config.js
# Restart both servers
```

See `NGROK_SETUP.md` for the full checklist when the ngrok URL changes.

---

## 3 — Get a JWT for curl testing

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "yourpassword"}'
# → {"token": "eyJ..."}

TOKEN=eyJ...   # paste token here
```

---

## 4 — Ingest campaign metrics

```bash
curl -s -X POST http://localhost:8000/api/ingest \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Expected:
```json
{
  "campaigns_saved": 1,
  "ad_account_id": "act_...",
  "message": "Ingested 1 campaign snapshot(s) for account act_..."
}
```

`campaigns_saved=0` is normal for campaigns with no delivery in the last 7 days — a NULL-metric snapshot is still saved.

Verify:
```bash
sqlite3 backend/app.db "SELECT ad_account_id, object_id, date, impressions, clicks, spend FROM ad_insights ORDER BY rowid DESC LIMIT 5;"
```

---

## 5 — Ingest creative structure (triggers embeddings)

```bash
curl -s -X POST http://localhost:8000/api/ingest/structure/<campaign_id> \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Expected:
```json
{
  "campaign_id": "120244448900420577",
  "ads_processed": 1,
  "components_saved": 16
}
```

Embedding jobs fire in the background. Wait a few seconds, then verify:

```bash
# Creative components stored
sqlite3 backend/app.db "SELECT campaign_id, ad_id, creative_type, slot, slot_index, value FROM ad_creative_structures WHERE campaign_id='<campaign_id>' ORDER BY slot, slot_index;"

# Seed ad embedding (1 per ad)
sqlite3 backend/app.db "SELECT ad_id, CASE WHEN image_vector IS NULL THEN 'no' ELSE 'yes' END AS has_image FROM ad_embeddings;"

# Per-image embeddings (one per image — expect 4 for a 4-image dynamic ad)
sqlite3 backend/app.db "SELECT ad_id, slot_index, CASE WHEN vector IS NULL THEN 'no' ELSE 'yes' END AS embedded FROM ad_image_embeddings;"

# Text combination embeddings (N×M×K — expect 64 for a 4×4×4 dynamic ad)
sqlite3 backend/app.db "SELECT source_id, COUNT(*) AS combinations FROM ad_text_combination_embeddings GROUP BY source_id;"
```

---

## 6 — Seed synthetic BO training data (first time / testing only)

The BO pipeline needs scored observations to fit the GPR. Use the seed script to insert synthetic ones:

```bash
# Find ad_id and user_id
sqlite3 backend/app.db "SELECT DISTINCT ad_id FROM ad_creative_structures;"
sqlite3 backend/app.db "SELECT id, email FROM users;"

cd backend && source .venv/bin/activate
python seed_bo.py --ad-id <ad_id> --user-id <user_id> --campaign-id <campaign_id> --n 5
```

Expected:
```
Seeding 5 scored observations for ad_id=..., user_id=2
  [1] job=1 variant=1 score=5.87 headline='Meet Your Dog's New Best Friend'
  [2] job=2 variant=2 score=5.74 headline='Squeak, Chew, Repeat'
  ...
Done. Run BO via POST /api/bo/run
```

---

## 7 — Run Bayesian Optimisation

```bash
curl -s -X POST http://localhost:8000/api/bo/run \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"seed_ad_id": "<ad_id>", "text_source_id": "<ad_id>"}' | python3 -m json.tool
```

Expected (with ≥2 scored observations):
```json
{
  "seed_ad_id": "120244804530430577",
  "text_source_id": "120244804530430577",
  "scored_count": 5,
  "candidate_count": 64,
  "picks": [
    {
      "selection_type": "ei",
      "combination": {
        "headline": "Meet Your Dog's New Best Friend",
        "primary_text": "Soft on the outside...",
        "description": "Playtime just got an upgrade..."
      },
      "ei_score": 0.109,
      "gpr_mean": 5.614,
      "gpr_std": 1.09
    },
    {
      "selection_type": "fantasy",
      "combination": { "..." : "..." },
      "ei_score": 0.085,
      "gpr_mean": 5.614,
      "gpr_std": 0.997
    }
  ]
}
```

- `scored_count` — training observations used by GPR
- `candidate_count` — unscored combinations evaluated (should be 64 minus scored)
- `selection_type: "ei"` — highest Expected Improvement pick
- `selection_type: "fantasy"` — diversity pick via fantasy GPR step
- With fewer than 2 scored observations → `selection_type: "random"`

Get latest picks later:
```bash
curl -s http://localhost:8000/api/bo/results/<ad_id> \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

---

## 8 — Explorer (debug)

```bash
curl -s http://localhost:8000/api/explore \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Returns the full raw Meta API response: campaigns → adsets → ads with creative fields. Use this to inspect what Meta is sending back, including `asset_feed_spec` image hashes and `thumbnail_url`.



===== FILE: ./NGROK_SETUP.md =====

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
ngrok http 5173
```
Point ngrok at **port 5173** (Vite), not 8000. Vite's proxy handles forwarding to the backend internally — the browser never needs to reach port 8000 directly.

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
  port: 5173,
  allowedHosts: ["<your-ngrok-subdomain>.ngrok-free.dev"],  // must be updated when ngrok URL changes
  proxy: {
    "/api":          "http://localhost:8000",
    "/auth/signup":  "http://localhost:8000",
    "/auth/login":   "http://localhost:8000",
    "/auth/meta":    "http://localhost:8000",
    "/me":           "http://localhost:8000",
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

- [ ] Update `allowedHosts` in `frontend/vite.config.js`
- [ ] Update `META_REDIRECT_URI` in `backend/.env`
- [ ] Update `FRONTEND_URL` in `backend/.env`
- [ ] Update **App Domains** in Meta Developer App
- [ ] Update **Site URL** in Meta Developer App
- [ ] Update **Valid OAuth Redirect URIs** in Meta → Facebook Login → Settings
- [ ] Restart the Vite dev server (`npm run dev`)
- [ ] Restart the FastAPI backend (`python main.py`)

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



===== FILE: ./QUICK_START.md =====

# Quick Start — AdStac.kr

## 1 — Sign up

Go to the app URL and click **Sign Up**. Enter your email and a password. You'll be logged in automatically.

---

## 2 — Connect your Meta Ads account

1. Click **Settings** in the top nav
2. Click **Connect Meta Account**
3. Complete the Meta login and permissions flow
4. You'll be redirected back — you should see your account listed as connected

---

## 3 — Import your campaigns

1. Click **Campaigns** in the nav
2. Click **Preview and Ingest** — this shows your campaigns and their last 7 days of metrics
3. Click **Confirm Ingest** to save the snapshot

Campaigns that haven't run ads recently will show blank metrics — that's normal.

---

## 4 — Import creative components

For each campaign you want to optimise:

1. Find the campaign row and click **Creatives**
2. Click **Ingest Structure**

This imports all the headlines, body texts, descriptions, and images from your dynamic ad into AdStac.kr. It also generates text embeddings for every possible combination of your copy in the background (this takes a few seconds).

---

## 5 — Verify your creatives were imported

After ingesting, expand the **Creatives** panel for your campaign. You should see your ad listed with its component slots — headlines, primary texts, descriptions, and images.

---

## 6 — Get AI-recommended combinations (BO)

Once your creatives are imported, AdStac.kr can recommend which headline + body text + description combinations are most likely to perform well, using Bayesian Optimisation over your ad's embedding space.

This improves as more real performance data is added. On first run it returns two combinations to test — one selected by Expected Improvement, one by a diversity step.

*(This feature is currently accessed via the API — UI coming soon.)*



===== FILE: ./README.md =====

# AdStac.kr — AI-Powered Ad Experimentation for Performance Marketers

Welcome to **AdStac.kr** — an experimentation layer built for performance-focused media buyers.

We take the best practices in A/B testing and automate them, freeing you up to focus on the
important stuff: strategy, creative direction, and scale. Connect your Meta Ads account,
and AdStac.kr handles the rest — ingesting your campaigns, generating image and copy variants,
scoring them with AI, and surfacing the next best combination to test via Bayesian Optimisation.
No more spreadsheet-driven split tests. Just signal.

---

## Table of Contents

### Getting Started
- [Quick Start](QUICK_START.md) — fastest path to a running demo
- [Dev Quickstart](DEV_QUICKSTART.md) — local dev setup from scratch
- [Ngrok / EC2 Setup](NGROK_SETUP.md) — expose local backend to Meta's OAuth redirect

### Reference
- [Schemas](SCHEMAS.md) — full SQLite table definitions
- [Tests](TEST.md) — test catalog, individual test descriptions, manual curl tests
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
                   ├── providers/          Meta API abstraction (live / masking / demo)
                   └── AI module suite (standalone, independently testable)
                         ├── embeddings/                Embed ingested ads (image + text)
                         ├── ad_generation/             Generate image variants via FLUX
                         ├── ad_text_generation/        Generate text variants via GPT-4o
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

Generates N new copy variants per text slot (headline, primary_text, description, cta) from
a seed ad using GPT-4o, then assembles them with optional image URLs into a dynamic ad
component list and stores the result.

```python
from ad_text_generation.pipeline import run_text_pipeline
generated_ad_id = await run_text_pipeline(seed_components, n_per_slot=5)
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
for the GPR. Separated because the fusion strategy is expected to evolve.

```python
from ad_embedding_combiner import combine
vec = combine(text_vector, image_vector)   # shape: (256,)
```

### 6. Bayesian Optimisation pipeline (`backend/bo_pipeline/`)

Fits a GPR on scored image variants, then selects two text+image combinations to test
next — one via Expected Improvement, one via a fantasy (batch BO) second pick. Operates
strictly per-ad: scored variants and text candidates must share the same seed ad.

```python
from bo_pipeline import run_bo, save_bo_run
picks = run_bo(seed_ad_id, text_source_id, user_id)
# picks: [{combination_key, combination, selection_type='ei'|'fantasy', ei_score, gpr_mean, gpr_std}, ...]
save_bo_run(seed_ad_id, text_source_id, picks)
```

Falls back to random selection when fewer than 2 scored observations exist.

**HTTP endpoints:** `POST /api/bo/run` runs BO and persists picks; `GET /api/bo/results/{ad_id}` returns latest picks.

**Seeding test data:** `backend/seed_bo.py` inserts synthetic scored observations for local testing.
```bash
python seed_bo.py --ad-id <ad_id> --user-id <user_id> --campaign-id <id> --n 5
```

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- A Meta Developer App with **Marketing API** enabled and an OAuth redirect URI registered
- OpenAI API key (text embeddings)
- Azure AI Inference credentials (image embeddings — separate resource from Azure OpenAI)
- Azure OpenAI credentials (image analysis, text generation, scoring, QA)
- deAPI credentials (FLUX image generation)

### API keys at a glance

| Service | Key var | Endpoint var | Used for |
|---|---|---|---|
| OpenAI | `OPENAI_KEY` | — (openai.com) | Text embeddings |
| Azure AI Inference | `AZURE_INFERENCE_KEY` | `AZURE_EMBEDDING_ENDPOINT` | Image embeddings (`embed-v-4-0`) |
| Azure OpenAI | `AZURE_OPENAI_KEY` | `AZURE_OPENAI_ENDPOINT` | Ad analysis, text gen, scoring |
| deAPI | `DEAPI_API_KEY` | — | FLUX img2img generation |
| Meta | `META_APP_ID` + `META_APP_SECRET` | — | Marketing API |

These are **four separate accounts/resources**. Azure AI Inference and Azure OpenAI use different endpoints and keys even if they share an Azure subscription.

---

## Setup

See [`DEV_QUICKSTART.md`](DEV_QUICKSTART.md) for the full environment setup, server startup, curl workflow, and BO seeding guide. See [`TEST.md`](TEST.md) for the test catalog.

---

## Feature Walkthrough

### Connect Meta

1. Sign up at `http://localhost:5173` → Auth page
2. Navigate to **Settings** → **Connect Meta Ads Account**
3. Approve on Meta's OAuth screen → redirected with `?meta_connected=true`

### Campaigns

The Campaigns page shows live data from Meta. Each campaign row supports:

- **Pause / Resume** — calls Meta API; status updates in place
- **History** — stored metric snapshots (requires prior ingest)
- **Creatives** — triggers structural ingest, then shows:
  - Creative slots per ad (headline, primary text, description, image)
  - Lifecycle badge: `active`, `inactive`, or `no longer in Meta`
  - **Suggestions panel** — pending/confirmed suggestions with Confirm Create button

### Preview & Ingest

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
| POST | `/api/ingest/structure/{campaign_id}` | Normalize + persist creative structure |
| GET | `/api/structure/{campaign_id}` | Read persisted structure grouped by ad |

### Suggestions

| Method | Path | Description |
|---|---|---|
| GET | `/api/suggestions` | List suggestions (`?campaign_id=` optional) |
| POST | `/api/suggestions` | Store a suggested configuration |
| POST | `/api/suggestions/{id}/confirm` | Confirm create or replace |

### Bayesian Optimisation

| Method | Path | Description |
|---|---|---|
| POST | `/api/bo/run` | Run BO, return and persist up to 2 picks |
| GET | `/api/bo/results/{ad_id}` | Latest BO picks for an ad |

### Ad Generation

| Method | Path | Description |
|---|---|---|
| POST | `/api/generate/text/{campaign_id}` | Generate 10 static text variants per slot; synchronous |
| POST | `/api/generate/dynamic/{campaign_id}` | Start async dynamic ad job (4×4 text + 4 AI images + embeddings) |
| GET | `/api/generate/dynamic/status/{job_id}` | Poll job status; returns slots + image_urls when complete |

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

| Env var | Effect |
|---|---|
| `APP_MODE=demo` | Fully synthetic data — no Meta API calls |
| `MASK_MODE=selective\|full` | Selectively override fields (status, budgets, metrics) |
| `MASK_STATUS=true` | Force all campaign statuses → ACTIVE |
| `MASK_METRICS=true` | Synthesize metrics for low-delivery campaigns |
| `MASK_PAUSE_RESUME=true` | pause/resume → no-op (returns success) |
| `METRIC_PROFILE=healthy\|stable\|weak` | Controls synthetic metric magnitude |

See [`STAGING_POLICY.md`](STAGING_POLICY.md) for what is and isn't safe to run against live accounts, and `CLAUDE.md` for the full masking variable reference.

---

## Notes

- **Auth:** Minimal JWT, 24h expiry. No email verification or rate limiting.
- **Meta token:** Short-lived user token, no refresh logic.
- **SQLite:** `backend/app.db` is gitignored. Delete it to reset all data.
- **AI modules:** All generation modules are now wired into HTTP routes. See `CLAUDE.md` for the full route list. Modules are also independently runnable — no running server required.
- **Static ad images:** The clone flow passes image values as hosted URLs. Creatives stored only as image hashes (not URLs) will fail at Meta creative creation.

For schema details see [`SCHEMAS.md`](SCHEMAS.md).
For running on EC2/ngrok see [`NGROK_SETUP.md`](NGROK_SETUP.md).



===== FILE: ./SCHEMAS.md =====

# Database & API Schemas

SQLite database: `backend/app.db` (auto-created on first run via `init_db()`).

---

## SQLite Tables

### `users`

Local auth accounts. Independent of Meta identity.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `email` | TEXT UNIQUE | |
| `pw_hash` | TEXT | bcrypt |
| `created_at` | TEXT | `datetime('now')` |

---

### `meta_connections`

One row per user — the connected Meta ad account.

| Column | Type | Notes |
|---|---|---|
| `user_id` | INTEGER PK | FK → `users.id` |
| `meta_user_id` | TEXT | Meta's user id |
| `access_token` | TEXT | Short-lived user token; no refresh logic |
| `ad_account_id` | TEXT | e.g. `act_123456789` |
| `connected_at` | TEXT | `datetime('now')` |

---

### `oauth_states`

CSRF state tokens for the Meta OAuth flow. Deleted after successful callback.

| Column | Type | Notes |
|---|---|---|
| `state` | TEXT PK | random `urlsafe` token |
| `user_id` | INTEGER | FK → `users.id` |
| `created_at` | TEXT | |

---

### `ad_insights`

Metric snapshots. One row per campaign per `POST /api/ingest` call. No date deduplication — rows accumulate over time.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER | FK → `users.id` |
| `ad_account_id` | TEXT | |
| `level` | TEXT | `'campaign'` (only value currently written) |
| `object_id` | TEXT | campaign_id / adset_id / ad_id depending on level |
| `date` | TEXT | `YYYY-MM-DD` — date of ingest, not date of impressions |
| `impressions` | INTEGER | 7-day aggregate at time of ingest |
| `clicks` | INTEGER | |
| `spend` | REAL | |
| `ctr` | REAL | computed: `clicks/impressions * 100` |
| `cpm` | REAL | computed: `spend/impressions * 1000` |
| `cpc` | REAL | computed: `spend/clicks` |
| `data_source` | TEXT | `'real'` \| `'masked'` \| `'demo'` — added via ALTER TABLE migration |
| `mask_profile` | TEXT nullable | `'healthy'` \| `'stable'` \| `'weak'` — only set when `data_source='masked'` |
| `created_at` | TEXT | |

---

### `ad_creative_structures`

Normalised creative components. One row per `(user, ad, slot, slot_index)`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER | FK → `users.id` |
| `ad_account_id` | TEXT | |
| `campaign_id` | TEXT | |
| `adset_id` | TEXT | |
| `ad_id` | TEXT | Meta ad id |
| `creative_type` | TEXT | `'static'` or `'dynamic'` |
| `slot` | TEXT | `'headline'`, `'description'`, `'primary_text'`, `'image'` |
| `slot_index` | INTEGER | `0` for static; `0, 1, 2…` for dynamic variants |
| `value` | TEXT | The component text or image URL / hash |
| `ingested_at` | TEXT | ISO timestamp of last ingest |
| `lifecycle_status` | TEXT | `'active'`, `'inactive'`, or `'missing'` (see below) |
| `data_source` | TEXT | `'real'` \| `'masked'` \| `'demo'` \| `'generated'` — added via ALTER TABLE migration |
| `mask_profile` | TEXT nullable | `'healthy'` \| `'stable'` \| `'weak'` — only set when `data_source='masked'` |
| UNIQUE | | `(user_id, ad_id, slot, slot_index)` — drives idempotent upsert |

**Lifecycle status rules** (set during `POST /api/ingest/structure/{campaign_id}` for Meta-ingested ads):

| Status | Condition |
|---|---|
| `active` | Ad present in latest Meta fetch AND `effective_status == 'ACTIVE'` |
| `inactive` | Ad present in latest Meta fetch BUT `effective_status != 'ACTIVE'` |
| `missing` | Ad has rows in DB but was absent from the latest Meta fetch for this campaign |

**Ingest behaviour:** `ON CONFLICT DO UPDATE` replaces `creative_type`, `value`, `ingested_at`, and `lifecycle_status` in place. Previously ingested ads absent from the current fetch are updated to `lifecycle_status = 'missing'` in a separate `UPDATE` pass.

**Generated ad rows** (written by `_run_dynamic_generation`): use `data_source='generated'`, `lifecycle_status='generated'`, `ad_account_id='generated'`, `adset_id='generated'`, `ad_id='gen_dyn_{uuid12}'`. These represent locally-created ads not yet pushed to Meta.

---

### `suggested_configurations`

One row per suggested exact configuration derived from a dynamic ad template.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER | FK → `users.id` |
| `ad_account_id` | TEXT | From user's Meta connection at time of storage |
| `campaign_id` | TEXT | |
| `adset_id` | TEXT | Target adset for the static clone |
| `source_ad_id` | TEXT | The dynamic template ad this was derived from |
| `components` | TEXT | **JSON blob**: `{"slot": "chosen_value", …}` |
| `deployment_status` | TEXT | See status lifecycle below |
| `static_ad_id` | TEXT nullable | Set after a successful `action="create"` Meta deployment |
| `created_at` | TEXT | |
| `updated_at` | TEXT | Touched on every status transition |

**`components` JSON shape:**

```json
{
  "headline":     "Buy Now — Summer Sale",
  "primary_text": "Up to 50% off selected items",
  "description":  "Limited time offer",
  "image":        "https://example.com/img.jpg"
}
```

All four slots are optional; only slots with chosen values are present.

**Deployment status lifecycle:**

```
suggested
  │
  ├─ action="create" ──▶ [Meta API] ──▶ created_static   (static_ad_id set)
  │                           └── on failure: stays "suggested"
  │
  ├─ action="replace" ──▶ pending_confirmation            (Meta call not yet implemented)
  │
  └─ reject ──▶ rejected                                  (endpoint not yet implemented)

created_static ──▶ active_static    (future: user activates in Meta Ads Manager)
created_static ──▶ replaced_static  (future: replace flow)
```

---

## Pydantic / API Response Models

### `SuggestionResponse`

Returned by `POST /api/suggestions`, `POST /api/suggestions/{id}/confirm`, and `GET /api/suggestions`.

```python
{
  "id":                int,
  "source_ad_id":      str,
  "campaign_id":       str,
  "adset_id":          str,
  "components":        { slot: value },   # dict[str, str]
  "deployment_status": str,
  "static_ad_id":      str | None,
  "created_at":        str                # ISO datetime string
}
```

### `AdStructure`

Returned by `GET /api/structure/{campaign_id}` — one item per ad.

```python
{
  "ad_id":            str,
  "adset_id":         str,
  "campaign_id":      str,
  "creative_type":    "static" | "dynamic",
  "lifecycle_status": "active" | "inactive" | "missing" | None,
  "components":       { slot: [value, …] }  # dict[str, list[str | None]]
}
```

`components` values are ordered lists: single-element for static ads, multi-element for dynamic variants.

### `StructureIngestResult`

Returned by `POST /api/ingest/structure/{campaign_id}`.

```python
{
  "campaign_id":      str,
  "ads_processed":    int,
  "components_saved": int
}
```

### `Campaign`

Returned by `GET /api/campaigns`.

```python
{
  "id":              str,
  "name":            str,
  "status":          str,          # e.g. "ACTIVE", "PAUSED"
  "daily_budget":    int | None,   # in cents
  "spend_7d":        float | None,
  "impressions_7d":  int | None,
  "clicks_7d":       int | None,
  "ctr_7d":          float | None,
  "cpm_7d":          float | None
}
```

---

### `ad_embeddings`

One row per `(user, ad)`. Written by `embeddings/pipeline.py` — called fire-and-forget from structural ingest. Upserted on `(user_id, ad_id)`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER | FK → `users.id` |
| `ad_id` | TEXT | Meta ad id — joins to `ad_creative_structures.ad_id` |
| `campaign_id` | TEXT | Denormalised for fast campaign-level queries |
| `text_model` | TEXT nullable | Azure model used for text embedding |
| `image_model` | TEXT nullable | Azure model used for image embedding |
| `text_vector` | BLOB nullable | Raw float32 bytes (numpy `.tobytes()`) |
| `image_vector` | BLOB nullable | Raw float32 bytes |
| `combined_vector` | BLOB | Concatenation of text + image vectors; always present |
| `text_snapshot` | TEXT | JSON of the text fields embedded (for debugging drift) |
| `image_url` | TEXT nullable | URL that was embedded |
| `embedded_at` | TEXT | `datetime('now')` |
| UNIQUE | | `(user_id, ad_id)` |

---

### `ad_generation_jobs`

One row per generation run, tied to a (user, campaign, adset, seed ad). Created by `ad_generation.create_job()`. Status advances through the pipeline stages.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER | FK → `users.id` |
| `campaign_id` | TEXT | Joins to `ad_creative_structures.campaign_id` |
| `adset_id` | TEXT | Joins to `ad_creative_structures.adset_id` |
| `seed_ad_id` | TEXT nullable | Joins to `ad_creative_structures.ad_id` — the source creative |
| `seed_image_url` | TEXT | Public URL of the image used as the generation seed |
| `headline` | TEXT | Ad headline passed to the analyzer |
| `short_text` | TEXT | Ad body text passed to the analyzer |
| `status` | TEXT | `pending` → `analyzing` → `generating` → `polling` → `scoring` → `qa` → `correcting` → `done` \| `failed` |
| `suggestions` | TEXT nullable | JSON array of 10 edit strings returned by GPT-4o |
| `error` | TEXT nullable | Set on terminal failure |
| `created_at` | TEXT | |
| `updated_at` | TEXT | Touched on every status transition |

---

### `ad_generation_variants`

One row per generated image — one per suggestion within a job.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `job_id` | INTEGER | FK → `ad_generation_jobs.id` |
| `suggestion` | TEXT | The edit string used as the generation prompt |
| `deapi_request_id` | TEXT nullable | deAPI async job id; used for polling |
| `status` | TEXT | `submitted` → `done` → `scored` \| `failed` |
| `result_url` | TEXT nullable | Temporary signed S3 URL returned by deAPI (expires) |
| `local_filename` | TEXT nullable | Basename saved under `backend/generated_images/` (e.g. `a3f9c12b.png`) |
| `score` | REAL nullable | Badness score from fine-tuned model (0–1, lower = better) |
| `severity` | TEXT nullable | `'low'` \| `'medium'` \| `'high'` |
| `score_labels` | TEXT nullable | JSON array of short issue descriptors |
| `qa_status` | TEXT nullable | `null` (not yet checked) \| `'passed'` \| `'flagged'` \| `'skipped'` |
| `qa_corrections` | TEXT nullable | JSON array of ≤2 short correction strings (set when `qa_status='flagged'`) |
| `parent_variant_id` | INTEGER nullable | FK → `ad_generation_variants.id` — set on correction variants; points to the defunct original |
| `created_at` | TEXT | |
| `updated_at` | TEXT | |

**Variant status lifecycle:**
```
submitted → done → scored → qa_status=passed            ← stays in active pool
                          → qa_status=flagged, status=defunct
                                    └─▶ child variant (parent_variant_id set):
                                        submitted → done → scored → qa_status=passed
```

**Active pool:** variants where `status != 'defunct'`. Use `get_active_variants(job_id)` from the pipeline module, or `WHERE status != 'defunct'` in raw SQL.

**Serving:** `local_filename` is reachable at `GET /images/{local_filename}` (StaticFiles mount in `main.py`). The `IMAGES_SERVE_BASE_URL` env var configures the base used when passing the URL to the scorer and QA checker.

---

## Data lineage — campaign → adset → seed ad → embeddings + generated images

All ML-adjacent data traces back to a specific ad in `ad_creative_structures`. The joins below are all inner joins on `(user_id, campaign_id, adset_id, ad_id)`.

```
ad_creative_structures
  (user_id, campaign_id, adset_id, ad_id, slot, value)
          │
          ├──▶ ad_embeddings
          │      JOIN ON (user_id, ad_id)
          │      → combined_vector, image_url, text_snapshot
          │
          └──▶ ad_generation_jobs          (seed_ad_id = ad_id)
                 JOIN ON (user_id, campaign_id, adset_id, seed_ad_id)
                 → status, suggestions
                         │
                         └──▶ ad_generation_variants
                                JOIN ON (job_id)
                                → local_filename, score, severity, score_labels
                                → qa_status, qa_corrections
                                → parent_variant_id (null = original; set = correction)
                                → served at GET /images/{local_filename}
                                → active pool: WHERE status != 'defunct'
```

**Example query — all scored variants for a campaign:**

```sql
SELECT
    j.campaign_id,
    j.adset_id,
    j.seed_ad_id,
    v.suggestion,
    v.local_filename,
    v.score,
    v.severity,
    v.score_labels
FROM ad_generation_jobs j
JOIN ad_generation_variants v ON v.job_id = j.id
WHERE j.user_id = ?
  AND j.campaign_id = ?
  AND v.status = 'scored'
ORDER BY v.score ASC;
```

**Example query — embedding for a seed ad:**

```sql
SELECT combined_vector, image_url, text_snapshot
FROM ad_embeddings
WHERE user_id = ? AND ad_id = ?;
```

---

### `generated_ads`

Header record for an assembled generated ad (written by `ad_text_generation`).
Contains no application metadata — the outer layer links this to campaigns/users.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Returned as `generated_ad_id` by `run_text_pipeline()` |
| `source_ad_id` | TEXT nullable | Identifier of the seed ad used as input; for traceability only |
| `created_at` | TEXT | `datetime('now')` |

---

### `generated_ad_slots`

One row per slot value in an assembled generated ad.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `generated_ad_id` | INTEGER | FK → `generated_ads.id` |
| `slot` | TEXT | `'headline'`, `'primary_text'`, `'description'`, `'image'` |
| `slot_index` | INTEGER | Seed values keep original indices; generated values continue from `max(seed) + 1` |
| `value` | TEXT | Text string or image URL |
| `source` | TEXT | `'seed'` \| `'generated_text'` \| `'generated_image'` |
| `created_at` | TEXT | |

**Source values:**

| `source` | Meaning |
|---|---|
| `seed` | Carried from the seed ad unchanged |
| `generated_text` | New copy variant produced by GPT-4o |
| `generated_image` | Image URL from the image generation pipeline |

**Retrieval:**

```python
from ad_text_generation import get_generated_ad
slots = get_generated_ad(generated_ad_id)
# [{"slot": "headline", "slot_index": 0, "value": "...", "source": "seed"}, ...]
```

---

### `ad_text_combination_embeddings`

One row per Cartesian text combination for a given source. Written by `ad_combination_embeddings.embed_all_combinations()`. No metadata at this layer — `source_id` is a caller-provided string.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `source_id` | TEXT | Caller-provided identifier (e.g. an ad_id or generated_ad_id); retrieval key |
| `combination_key` | TEXT | `json.dumps(combo, sort_keys=True)` — deterministic; forms UNIQUE with `source_id` |
| `vector` | BLOB | `float32` bytes (`numpy.ndarray.tobytes()`) |
| `model` | TEXT nullable | Azure embedding model name |
| `embedded_at` | TEXT | `datetime('now')` |
| UNIQUE | | `(source_id, combination_key)` — upsert-safe |

**What gets embedded:** the `combination_key` string itself — e.g.
`'{"description":"Free shipping.","headline":"Wool Socks","primary_text":"Hand made in Switzerland."}'`

**Retrieval:**

```python
from ad_combination_embeddings import get_embeddings_for_source
rows = get_embeddings_for_source("my_ad_001")
# Each row: {"combination_key": str, "combination": dict, "vector": np.ndarray, "model": str, "embedded_at": str}
```

---

### `dynamic_generation_jobs`

Tracks the status of async `POST /api/generate/dynamic/{campaign_id}` jobs. One row per button click.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Returned as `job_id` to the frontend |
| `user_id` | INTEGER | FK → `users.id` |
| `campaign_id` | TEXT | The campaign being generated for |
| `status` | TEXT | `'running'` \| `'complete'` \| `'failed'` |
| `ad_id` | TEXT nullable | Set on completion — the `gen_dyn_*` ad_id in `ad_creative_structures` |
| `error` | TEXT nullable | Set on failure |
| `created_at` | TEXT | |
| `completed_at` | TEXT nullable | Set on completion or failure |

---

### `bo_selections`

One row per BO-recommended combination per run. Written by `bo_pipeline.save_bo_run()`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `seed_ad_id` | TEXT | The seed ad whose image variants were used as scored training data |
| `text_source_id` | TEXT | `source_id` in `ad_text_combination_embeddings`; the candidate text pool |
| `pick_rank` | INTEGER | 1 = EI pick, 2 = fantasy (batch BO) pick |
| `combination_key` | TEXT | JSON key of the selected text combination |
| `combination` | TEXT | JSON dict of the text slots (headline, primary_text, etc.) |
| `selection_type` | TEXT | `'ei'` \| `'fantasy'` \| `'random'` (fallback when < 2 scored observations) |
| `ei_score` | REAL nullable | Expected Improvement value at selection time |
| `gpr_mean` | REAL nullable | GPR posterior mean at the selected point |
| `gpr_std` | REAL nullable | GPR posterior std at the selected point |
| `created_at` | TEXT | `datetime('now')` — groups a run's two picks by timestamp |

**Variant embedding convention:** when embedding a generated image variant for use in `bo_pipeline`, store it in `ad_embeddings` with `ad_id = f"gen_{job_id}_{variant_id}"`. The selector joins on this convention.

---

## Meta API Fields Used

### Campaigns (`/{ad_account_id}/campaigns`)
`id, name, status, daily_budget`

### Insights (`/{ad_account_id}/insights`)
`campaign_id, impressions, clicks, spend, ctr, cpm, cpc` — `level=campaign`, `date_preset=last_7d`

### Ads for structural ingest (`/{ad_account_id}/ads`)
`id, name, status, effective_status, campaign_id, adset_id, creative{id, name, body, title, image_url, thumbnail_url, asset_feed_spec, object_story_spec}`

**`effective_status`** is preferred over `status` for lifecycle classification because it reflects aggregate delivery state (e.g. an ad with `status=ACTIVE` whose parent campaign is paused will have `effective_status=CAMPAIGN_PAUSED`).

### Source ad fetch for clone (`/{source_ad_id}`)
`creative{page_id, object_story_spec}` — used to inherit `page_id` and destination `link` URL when creating a static clone.



===== FILE: ./STAGING_POLICY.md =====

# AdStac.kr Reality Matrix

This document defines which actions are real, simulated, local-only, archived, or resettable across backend modes. It is intended to remove ambiguity during development, demos, QA, and internal testing. The current backend includes real Meta reads, local persistence flows, and some write-capable paths, so this matrix distinguishes those behaviors explicitly.

## Core terms

Use these terms consistently in code, UI copy, and internal discussion.

- **External read** — Sends a real request to Meta and reads data without intentionally changing Meta state.
- **External write** — Sends a real request to Meta that can change object state or create assets.
- **Local write** — Writes only to AdStac.kr storage such as SQLite tables like `ad_insights`, `ad_creative_structures`, `suggested_configurations`, or connection records.
- **Simulated** — Returns fabricated or overridden values via demo provider behavior or masking logic rather than representing the underlying live truth exactly.
- **Soft delete / archive** — Keeps a record but marks it non-current, inactive, missing, rejected, replaced, or otherwise no longer primary.
- **Hard reset / wipe** — Intentionally removes local development or test state so the environment can be re-created cleanly.

## Modes

The backend currently supports at least two broad behavior families: `APP_MODE=live|demo` and a masking layer controlled by `MASK_MODE` plus individual masking flags. These should be treated as user-visible operating modes even if the implementation combines them from multiple environment variables.

### Live mode

`APP_MODE=live` means the backend uses real Meta credentials from `meta_connections` and calls the real Meta APIs for reads and any enabled write actions. If masking is disabled, the responses are pass-through and pause/resume writes can reach Meta directly.

### Demo mode

`APP_MODE=demo` means the backend can return demo credentials (`demo-token`, `demo-account`) and use a demo provider path rather than real account credentials. Demo mode should be treated as simulated-by-default unless a specific action is explicitly documented as still real.

### Masked live mode

Masked live mode is the most important hybrid: reads may still hit live Meta, but selected fields are overridden on the way out, such as campaign status, ad status, daily budgets, or metrics. In this mode, the system may be using real upstream data transport while presenting simulated business truth to the app and user. Masked live mode can override campaign status, ad status, budgets, and metrics independently. In particular, `MASK_AD_STATUSES=true` forces ad statuses returned by `fetch_ads()` to `ACTIVE`, even when campaign-level masking is configured separately.

## Configuring masked live mode

Masking is controlled by `MASK_MODE` and a set of individual `MASK_*` flags in `backend/.env`. You do not need to set `MASK_MODE` — setting any individual flag is sufficient to activate masking.

### `MASK_MODE` values

| Value | Effect |
|---|---|
| `off` (default) | No masking. All data is passed through from Meta as-is. |
| `selective` | Masking is off unless individual `MASK_*` flags are explicitly enabled. |
| `full` | All mask flags are enabled automatically. |

### Individual flags

| Flag | Effect |
|---|---|
| `MASK_STATUS=true` | Forces all campaign statuses → `ACTIVE` |
| `MASK_BUDGETS=true` | Replaces `daily_budget` with a deterministic demo value |
| `MASK_METRICS=true` | Synthesizes insights for campaigns with no/low real delivery (impressions < 100); preserves real metrics otherwise |
| `MASK_PAUSE_RESUME=true` | Makes pause/resume calls a no-op — returns success without hitting Meta |
| `MASK_AD_STATUSES=true` | Forces all ad statuses → `ACTIVE` (also implied by `MASK_STATUS`) |

### `METRIC_PROFILE`

When `MASK_METRICS=true`, synthetic metrics are generated deterministically per campaign ID (same campaign always gets the same numbers across restarts). `METRIC_PROFILE` controls their magnitude:

| Value | Effect |
|---|---|
| `healthy` (default) | High impressions, good CTR and CPC |
| `stable` | Moderate delivery |
| `weak` | Low impressions, poor CTR — useful for testing low-delivery stories |

### Typical demo setup

```env
MASK_MODE=selective
MASK_STATUS=true
MASK_BUDGETS=true
MASK_METRICS=true
MASK_PAUSE_RESUME=true
MASK_AD_STATUSES=true
METRIC_PROFILE=healthy
```

## Reality matrix

| Action | Live mode | Demo mode | Masked live mode | Mutation type | Risk level | Notes |
|---|---|---|---|---|---|---|
| `GET /me/meta-status` | Local read from `meta_connections`. | Local/demo semantics only. | Same as live. | Local read | Low | No Meta request is required. |
| `GET /auth/meta/login-url` | Real OAuth entrypoint generation. | Usually not needed in pure demo mode. | Same as live. | Local write/read flow | Medium | Starts connection flow but does not itself spend money. |
| `GET /auth/meta/callback` | Real OAuth token exchange and local connection persistence. | Usually bypassed in demo setups. | Same as live. | External read + local write | Medium | Stores access token and selected ad account locally. |
| `GET /api/campaigns` | Real Meta read for campaigns and metrics. | Simulated/demo output if demo provider is used. | Real read with possible status, budget, and metric overrides. | External read or simulated | Low | Safe from spend by itself, but not necessarily truthful in masked mode. |
| `GET /api/ads` | Real Meta read for ads and creatives. | Simulated/demo output if demo provider is used. | Real read with possible forced ad statuses. | External read or simulated | Low | Does not create spend by itself. |
| `GET /api/ingest/preview` | Real read of campaigns and ads; no persistence. | Simulated or demo-backed preview. | Real read with masked values possible; still no persistence. | External read or simulated | Low | Best inspection endpoint for safe verification. |
| `POST /api/ingest` | Real Meta read, then local insert into `ad_insights`. | Simulated/demo read, then local insert into `ad_insights`. | Real Meta read with masked metrics possible, then local insert. | External read + local write | Low | No external Meta state change; may persist synthetic metrics locally in masked/demo mode. |
| `POST /api/ingest/structure/{campaign_id}` | Real Meta read, then local upsert into `ad_creative_structures`. | Simulated/demo-backed structure ingest if supported by provider. | Real read with statuses potentially overridden before persistence. | External read + local write | Low | Missing previously seen ads are marked `missing`, not deleted. |
| `GET /api/structure/{campaign_id}` | Local read of persisted structure. | Local read of persisted structure. | Same as live. | Local read | Low | Returns the current local truth, which may itself have been built from simulated inputs. |
| `GET /api/explore` | Real raw Meta read of campaigns, adsets, and ads. | Simulated/demo-backed raw explorer if provider supports it. | Real read with masking depending on provider path. | External read or simulated | Low | No DB persistence. |
| `GET /api/campaigns/{campaign_id}/history` | Local read from `ad_insights`. | Local read from demo-ingested history if present. | Same as live. | Local read | Low | History reflects whatever was previously saved, including masked/demo values. |
| `POST /api/campaigns/{campaign_id}/pause` | Real Meta write unless intercepted. | Should be treated as simulated or disabled in demo contexts. | No-op only if `MASK_PAUSE_RESUME=true`; otherwise still real. | External write or simulated | High | State-changing operation with direct platform impact. |
| `POST /api/campaigns/{campaign_id}/resume` | Real Meta write unless intercepted. | Should be treated as simulated or disabled in demo contexts. | No-op only if `MASK_PAUSE_RESUME=true`; otherwise still real. | External write or simulated | High | Resuming delivery can create real cost exposure depending on the campaign. |
| `GET /api/suggestions` | Local read from `suggested_configurations`. | Same. | Same. | Local read | Low | No Meta side effects. |
| `POST /api/suggestions` | Local insert into `suggested_configurations`. | Same. | Same. | Local write | Low | Stores suggested configurations only. |
| Suggestion confirmation that only updates deployment status | Local-only mutation if implemented as DB status transition. | Same. | Same. | Local write | Low | Safe unless it invokes asset creation logic. |
| `_clone_dynamic_to_static_ad(...)` and any route that calls it | Real Meta creative creation and ad creation. | Should be considered simulated or blocked unless explicitly documented otherwise. | Potentially still real unless separately blocked; masking shown does not guarantee asset-creation no-op. | External write | High | Creates ad creative and ad objects in Meta, even though created ads are initialized as `PAUSED`. |

## Delete and archive semantics

Delete behavior should be documented separately from resets because the product intent is archival where possible rather than destructive erasure. The current backend already follows this pattern for some ingested structure data by marking records as `missing` when an ad no longer appears in a new fetch.

### Implemented archival behavior

The clearest implemented archive-like behavior today is in `ad_creative_structures`: during structure ingest, previously ingested `ad_id` values that are absent from the current fetch are updated to `lifecycle_status = 'missing'` instead of being removed. The same table also carries `active` and `inactive` lifecycle states, which means the local model already supports “kept but no longer current” semantics.

`suggested_configurations` also includes archival-like deployment states such as `rejected`, `replaced_static`, `created_static`, and `active_static`, which are status transitions rather than destructive deletes. Those should be treated as soft lifecycle changes, not as data removal.

### Recommended delete vocabulary

Use the following terms consistently in documentation and eventually in the product UI.

| Term | Meaning | Storage expectation |
|---|---|---|
| Archive | Hide from active workflows but retain full record. | Row remains; status changes only. |
| Missing | Previously ingested object is no longer returned by current upstream fetch. | Row remains; `lifecycle_status='missing'`. |
| Inactive | Object still exists but is not currently live/serving. | Row remains; status/lifecycle marks inactive. |
| Rejected | Human or system declined to use a suggestion. | Row remains in `suggested_configurations`. |
| Replaced | Older suggestion or derived object has been superseded. | Row remains with replacement status. |
| Hard delete | Physically remove data. | Only use for explicit reset/wipe operations. |

### Recommended delete rules

- Ingested Meta-derived records should default to archive semantics rather than hard delete.
- “No longer returned by Meta” should map to `missing`, not deletion.
- “User no longer wants this suggestion” should map to `rejected` or archived, not deletion.
- Hard delete should be reserved for dev/test reset actions, privacy-required removal, or deliberate administrative cleanup.

## Reset semantics

Reset behavior should be documented by scope. The user preference is for throwaway but thoughtfully structured dev data with easy resets, while still avoiding accidental loss of useful business history in normal flows.

### Reset levels

| Reset type | What it does | Meta impact | Data impact | Recommended use |
|---|---|---|---|---|
| Soft reset | Clears derived local artifacts only, such as snapshots, normalized structures, and suggestions. | None | Removes or archives local working data only. | Routine QA reruns, ingest retesting, demo cleanup. |
| Connection reset | Removes local Meta connection state such as `meta_connections` and OAuth state. | None directly | Requires reconnect before further live reads. | Account switching, broken token recovery, dev cleanup. |
| User workspace reset | Clears one user’s local AdStac.kr workspace, including insights, structures, suggestions, and optional connection state. | None directly | Recreates “fresh install” experience for that user. | Integration testing and onboarding rehearsals. |
| Full local wipe | Clears all local dev/test tables for the environment. | None directly | Destroys local environment state for all users in that environment. | Rebuild from scratch in dev only. |
| External reset | Changes Meta objects directly, such as pausing campaigns or replacing ads. | Real Meta effect | External platform state changes. | Should never be part of a generic “reset” unless explicitly named and confirmed. |

### Planned reset targets

The following local entities are good candidates for reset operations or admin scripts because they are local artifacts or connection state already represented in the current schema.

- `ad_insights` — stored metric snapshots from ingest.
- `ad_creative_structures` — normalized creative structure derived from Meta reads.
- `suggested_configurations` — stored suggestions and deployment lifecycle statuses.
- `meta_connections` — local storage for access token, ad account id, and connection metadata.
- `oauth_states` — transient connection-flow state.

### Reset rules

- Resets should be local-only by default.
- Anything that can mutate Meta should never be labeled simply “reset”; it should be called out as an external write.
- Reset endpoints or scripts should be documented by scope: one campaign, one user, one account, or full environment.
- A full wipe should only exist in development or explicitly non-production environments.

## Safe defaults by mode

These defaults reduce ambiguity and cost risk while keeping the product useful for demos and integration testing.

### Live mode defaults

- Allow real reads.
- Allow local ingest and structure persistence.
- Require explicit confirmation for any Meta write.
- Label pause/resume and asset creation paths as **real external writes** in UI and docs.

### Demo mode defaults

- Treat all campaign/ad/metric truth as simulated unless explicitly noted otherwise.
- Allow local persistence for testing downstream flows.
- Disable or stub all external writes by default.

### Masked live mode defaults

- Treat campaign status, ad status, budgets, and metrics as potentially simulated even when upstream reads are real.
- Mark any persisted outputs derived from masked responses as “locally stored from masked/live source” in future provenance documentation or metadata.
- Keep pause/resume blocked with `MASK_PAUSE_RESUME=true` unless an operator intentionally wants real control-plane behavior.

## Recommended implementation notes

This document can remain documentation-only at first, but it will be more durable if eventually mirrored in code-level metadata. A future version should expose mode and action semantics through a small internal policy object or admin endpoint so the frontend, backend, and docs all share the same definitions.

A minimal future shape would be:

```json
{
 "mode": "masked_live",
 "actions": {
 "read_campaigns": {"reality": "real_read_masked", "writes_local": false, "writes_external": false},
 "ingest_metrics": {"reality": "masked_read", "writes_local": true, "writes_external": false},
 "resume_campaign": {"reality": "simulated_noop", "writes_local": false, "writes_external": false},
 "create_static_ad": {"reality": "disabled"}
 }
}
```

That would turn the current documentation into enforceable runtime truth over time.

## Current practical summary

At present, the backend already supports a meaningful distinction between safe read flows, local-only persistence, simulated masking, and real external writes. What is still missing is not the concept, but the single explicit source of truth that tells operators and developers which category each action falls into in each mode.

Until that source is implemented in code, this document should be treated as the canonical internal reference.




===== FILE: ./TEST.md =====

# AdStac.kr — Test Catalog

All pytest tests live in `backend/`. Run them from there with the venv active:

```bash
cd backend && source .venv/bin/activate
```

---

## Pytest test files

### No API keys required

| File | Classes | Tests | What it covers |
|---|---|---|---|
| `test_structural_ingest.py` | — | ~10 | Structural ingest normalization, lifecycle status logic |
| `test_suggestions.py` | — | ~10 | Suggestion storage and retrieval |
| `test_combination_embeddings.py` | `TestBuildCombinations`, `TestCombinationKey` | 16 | Cartesian combination logic, key determinism |
| `test_bo_pipeline.py` | `TestCombineEmbeddings`, `TestGPR`, `TestSelector`, `TestBOPipeline` | 34 | Embedding combiner, GPR functions, DB selector, full BO run |

Run all pure tests in one shot:

```bash
python -m pytest test_structural_ingest.py test_suggestions.py \
    test_combination_embeddings.py -k "TestBuildCombinations or TestCombinationKey" \
    test_bo_pipeline.py -v
```

### API keys required

| File | Classes | Tests | Keys needed | What it covers |
|---|---|---|---|---|
| `test_combination_embeddings.py` | `TestEmbedAllCombinations`, `TestRetrieval` | 15 | `AZURE_INFERENCE_KEY` | Embed real text combos, store/retrieve vectors |
| `test_text_pipeline.py` | `TestAssembleDynamicAd`, `TestGenerateSlots`, `TestRunTextPipeline`, `TestRetrieval` | ~20 | `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT` | GPT-4o text generation, assembler, DB storage |
| `test_generation_pipeline.py` | `TestSeedEmbedding`, `TestGenerationPipeline`, `TestGeneratedVariantEmbedding`, `TestMetadataChain` | ~20 | `AZURE_INFERENCE_KEY`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT`, `DEAPI_API_KEY` | Full 7-step image pipeline end-to-end |

---

## Notable individual tests

### `test_bo_pipeline.py::TestBOPipeline::test_full_bo_report`

The most informative single test to run. Executes the full BO pipeline and prints a
human-readable report: scored observations, GPR fit, EI ranking over all candidates,
pick 1 (EI), pick 2 (fantasy), and a summary row.

```bash
python -m pytest test_bo_pipeline.py::TestBOPipeline::test_full_bo_report -v -s
```

Example output:
```
============================================================
  AdStac.kr BO Run — Full Report
============================================================

Training set  : 5 scored observation(s)
Candidate pool: 7 unscored combination(s)

Scored observations:
  [1] score=3.50  combo={'headline': 'Wool Socks', 'primary_text': 'Hand made in Switzerland.'}
  ...

GPR fit on 5 point(s)  |  best observed score: 5.10
Optimized kernel: 0.949**2 * RBF(length_scale=1) + WhiteKernel(noise_level=0.099)

PICK 1  [EI]
  Combination : {'headline': 'Wool Socks', 'primary_text': 'Premium wool since 1952.'}
  GPR mean    : 4.0600  |  GPR std: 0.8261  |  EI: 0.039983

PICK 2  [FANTASY]
  Combination : {'headline': 'Warm Feet Forever', 'primary_text': 'Hand made in Switzerland.'}
  GPR mean    : 4.0600  |  GPR std: 0.7541  |  EI: 0.028118

Summary
  n_scored=5  n_fit=5  n_candidates=7  best_obs=5.10
```

**Note on uniform EI:** the test seeds all embeddings as random vectors, so all
candidates sit equidistant from the training set in embedding space and share
identical EI. With real Azure embeddings, semantically similar candidates will
differentiate. The fantasy step is still exercised correctly — σ drops from
0.826 → 0.754 on pick 2.

---

## Module-level test docs

Each AI module has its own README with test-specific instructions and env var
requirements:

| Module | README |
|---|---|
| Embeddings | `backend/embeddings/README.md` |
| Image generation | `backend/ad_generation/README.md` |
| Text generation | `backend/ad_text_generation/README.md` |
| Combination embeddings | `backend/ad_combination_embeddings/README.md` |

---

## Manual / curl tests — Masking layer

For verifying the masking layer behaviour against a live Meta account.

```bash
export API="http://localhost:8000"
export TOKEN="<paste JWT here>"   # from POST /auth/login
```

### Campaigns

```bash
curl -s "$API/api/campaigns" -H "Authorization: Bearer $TOKEN" | jq
```

Run twice and diff to verify determinism when masking is on:

```bash
curl -s "$API/api/campaigns" -H "Authorization: Bearer $TOKEN" | jq > /tmp/c1.json
curl -s "$API/api/campaigns" -H "Authorization: Bearer $TOKEN" | jq > /tmp/c2.json
diff /tmp/c1.json /tmp/c2.json   # should be empty
```

### Sync campaigns (ingest metric snapshots)

```bash
curl -s -X POST "$API/api/ingest" -H "Authorization: Bearer $TOKEN" | jq
```

### Ingest creative structure for a campaign

```bash
export CAMPAIGN_ID="<id from /api/campaigns>"
curl -s -X POST "$API/api/ingest/structure/$CAMPAIGN_ID" -H "Authorization: Bearer $TOKEN" | jq
```

### Verify embeddings after structural ingest

After clicking "Ingest" on a campaign (or calling the endpoint above), the embedding
tasks run in the background. Wait a few seconds, then confirm:

```bash
# Seed embedding per ad — has_text and has_image should both be 'yes' when keys are set
sqlite3 backend/app.db "SELECT ad_id, text_snapshot, CASE WHEN text_vector IS NULL THEN 'no' ELSE 'yes' END as has_text, CASE WHEN image_vector IS NULL THEN 'no' ELSE 'yes' END as has_image FROM ad_embeddings;"

# Check combined vector dimension (expect 256: TEXT_DIM=128 + IMAGE_DIM=128)
sqlite3 backend/app.db "SELECT ad_id, LENGTH(combined_vector) / 8 as combined_dim FROM ad_embeddings;"

# Per-image-slot embeddings
sqlite3 backend/app.db "SELECT ad_id, slot_index, image_ref, CASE WHEN vector IS NULL THEN 'no' ELSE 'yes' END as embedded FROM ad_image_embeddings;"

# Text combination embeddings — a 4×4×4 dynamic ad produces 64 rows
sqlite3 backend/app.db "SELECT source_id, COUNT(*) as combinations FROM ad_text_combination_embeddings GROUP BY source_id;"
```

If `has_image` is `no` or `ad_image_embeddings` is empty, `AZURE_INFERENCE_KEY` is
likely missing or wrong. If `has_text` is `no`, check `OPENAI_KEY`. Once keys are
fixed, hit "Reingest" — the backend skips already-complete embeddings and only
re-runs what failed.

### Pause / Resume

```bash
export CAMPAIGN_ID="<id from /api/campaigns>"
curl -s -X POST "$API/api/campaigns/$CAMPAIGN_ID/pause"  -H "Authorization: Bearer $TOKEN" | jq
curl -s -X POST "$API/api/campaigns/$CAMPAIGN_ID/resume" -H "Authorization: Bearer $TOKEN" | jq
```

With `MASK_PAUSE_RESUME=true` both return success without hitting Meta.

### Reference env configs

| Scenario | Env vars |
|---|---|
| Real baseline | `APP_MODE=live MASK_MODE=off` |
| Full selective mask | `MASK_MODE=selective MASK_STATUS=true MASK_BUDGETS=true MASK_METRICS=true MASK_PAUSE_RESUME=true MASK_AD_STATUSES=true METRIC_PROFILE=healthy` |
| Weak delivery story | `MASK_MODE=selective MASK_STATUS=true MASK_METRICS=true METRIC_PROFILE=weak` |
| Full demo (no Meta) | `APP_MODE=demo` |



===== FILE: ./all_markdown_combined.md =====


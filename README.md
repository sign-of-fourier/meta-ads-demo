# AdStac.kr — Meta Ads AI Optimisation Platform

AdStac.kr connects to the Meta Marketing API to ingest campaign and creative
structure, then runs a suite of AI modules to generate, score, and select
optimised ad combinations for testing.

---

## System Architecture

```
frontend/ (React + Vite, port 5173)
  └── calls ──▶ backend/ (FastAPI, port 8000)
                   ├── providers/          Meta API abstraction (live / masking / demo)
                   └── AI module suite (standalone, not yet wired into HTTP routes)
                         ├── embeddings/               Embed ingested ads (image + text)
                         ├── ad_generation/            Generate image variants via FLUX
                         ├── ad_text_generation/       Generate text variants via GPT-4o
                         ├── ad_combination_embeddings/ Embed all text slot combinations
                         ├── ad_embedding_combiner/    Fuse text + image vectors for GPR
                         └── bo_pipeline/              GPR-based Bayesian Optimisation
```

All backend logic lives in `backend/main.py` (single-file FastAPI app). The database is
SQLite (`backend/app.db`, auto-created on first run). See [`SCHEMAS.md`](SCHEMAS.md) for
full table definitions.

---

## AI Module Suite

Each module is **standalone and independently testable** — no running server required,
no metadata coupling between modules.

### 1. Ad embeddings (`backend/embeddings/`)

Embeds ingested ads (one image + all text slots concatenated) into `ad_embeddings`.
Called fire-and-forget from structural ingest. Also runnable standalone:

```bash
python -m embeddings.pipeline          # embed all un-embedded ads
python -m embeddings.pipeline --ad-id <id>
```

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

### 3. Text generation pipeline (`backend/ad_text_generation/`)

Generates N new copy variants per text slot (headline, primary_text, description) from
a seed ad using GPT-4o, then assembles them with optional image URLs into a dynamic ad
component list and stores the result.

```python
from ad_text_generation.pipeline import run_text_pipeline
generated_ad_id = await run_text_pipeline(seed_components, n_per_slot=5)
```

### 4. Combination embeddings (`backend/ad_combination_embeddings/`)

For a dynamic ad with N headlines × M primary texts × K descriptions, embeds every
Cartesian combination as a single text vector (e.g. 4 × 4 × 4 = 64 embeddings). Each
combination is stored individually — the BO layer reads these as its candidate pool.

```python
from ad_combination_embeddings import embed_all_combinations, combination_count
n = combination_count(components)   # dry-run
await embed_all_combinations(source_id="gen_ad_001", components=components)
```

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

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- A Meta Developer App with **Marketing API** enabled and an OAuth redirect URI registered
- Azure AI Inference credentials (for embeddings)
- Azure OpenAI credentials (for image analysis, text generation, and QA)
- deAPI credentials (for FLUX image generation)

---

## Setup

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Required: META_APP_ID, META_APP_SECRET, META_REDIRECT_URI, JWT_SECRET
# AI modules: AZURE_INFERENCE_KEY, AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, DEAPI_API_KEY

python main.py   # → http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env   # VITE_API_URL defaults to http://localhost:8000
npm run dev            # → http://localhost:5173
```

### Tests

See [`TEST.md`](TEST.md) for the full test catalog, individual test descriptions, and manual curl tests for the masking layer.

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

### Other

| Method | Path | Description |
|---|---|---|
| GET | `/api/ads` | Live ad creatives from Meta |
| GET | `/api/explore` | Raw Meta data (campaigns → adsets → ads) for debugging |
| GET | `/images/{filename}` | Serve generated images (static mount) |

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

See `CLAUDE.md` for the full masking variable reference.

---

## Notes

- **Auth:** Minimal JWT, 24h expiry. No email verification or rate limiting.
- **Meta token:** Short-lived user token, no refresh logic.
- **SQLite:** `backend/app.db` is gitignored. Delete it to reset all data.
- **AI modules:** All six AI modules are implemented and tested but not yet wired into HTTP routes. They are runnable standalone or callable from Python directly.
- **Static ad images:** The clone flow passes image values as hosted URLs. Creatives stored only as image hashes (not URLs) will fail at Meta creative creation.

For schema details see [`SCHEMAS.md`](SCHEMAS.md).
For running on EC2/ngrok see [`NGROK_SETUP.md`](NGROK_SETUP.md) if present.

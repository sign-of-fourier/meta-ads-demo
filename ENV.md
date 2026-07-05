# Environment Variables

All vars go in `backend/.env`. Copy from `backend/.env.example` and fill in values.

## Core (always required)

| Variable | Description |
|---|---|
| `META_APP_ID` | Meta Developer App ID |
| `META_APP_SECRET` | Meta Developer App Secret |
| `META_REDIRECT_URI` | Must match Meta app settings (e.g. `http://localhost:8000/auth/meta/callback`) |
| `META_API_VERSION` | Defaults to `v19.0` |
| `JWT_SECRET` | Any random string |
| `FRONTEND_URL` | Defaults to `http://localhost:5173` |
| `ADMIN_API_KEY` | Shared secret for `POST /api/admin/users/{id}/tier` (checked via `X-Admin-Key` header, constant-time compare). Unset → the admin route always 503s. Internal use only — never expose to the frontend. See `BACKEND.md` §`permissions.py` |

## Google Ads

All optional until Google integration is activated. `GOOGLE_ADS_API_VERSION` has **no hardcoded fallback** — must be set explicitly.

| Variable | Description |
|---|---|
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | Must match Google console (e.g. `http://localhost:8000/auth/google/callback`) |
| `GOOGLE_DEVELOPER_TOKEN` | Google Ads developer token |
| `GOOGLE_ADS_API_VERSION` | e.g. `v18` — no default, must be set explicitly |

## External API Keys

There are **four separate external services** — do not mix keys or endpoints.

### 1. OpenAI — text embeddings

| Variable | Description |
|---|---|
| `OPENAI_KEY` | OpenAI API key (`sk-...`) |
| `OPENAI_TEXT_MODEL` | Embedding model (default `text-embedding-3-small`) |

Used by: `embeddings/embedder.py` → `embed_text()`, `ad_combination_embeddings/pipeline.py`

### 2. Azure AI Inference — image embeddings

| Variable | Description |
|---|---|
| `AZURE_INFERENCE_KEY` | Azure AI Inference key for the multimodal embedding resource |
| `AZURE_EMBEDDING_ENDPOINT` | Resource endpoint (default `https://markpshipman-2243-resource.services.ai.azure.com/models`) |
| `AZURE_IMAGE_MODEL` | Image embedding model (default `embed-v-4-0`) |

Used by: `embeddings/embedder.py` → `embed_image_url()`. **Different resource from Azure OpenAI below.**

### 3. Azure OpenAI — ad generation, analysis, scoring, text generation

| Variable | Description |
|---|---|
| `AZURE_OPENAI_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource endpoint |
| `AZURE_OPENAI_API_VERSION` | Defaults to `2024-12-01-preview` |
| `AZURE_ANALYSIS_DEPLOYMENT` | Deployment for image analysis (default `gpt-4.1-nano`) |
| `MODAL_SCORING_ENDPOINT` | Modal endpoint for Qwen2-VL ad scorer |
| `AZURE_TEXT_GEN_DEPLOYMENT` | Deployment for text generation (default `gpt-4.1-nano`) |

Used by: `ad_generation/` pipeline (analyze, score, QA, text gen steps)

### 4. deAPI — FLUX image generation

| Variable | Description |
|---|---|
| `DEAPI_API_KEY` | deAPI key for FLUX img2img image generation |
| `IMAGES_SERVE_BASE_URL` | Base URL for serving generated images (default `http://localhost:8000/images`). Must be a full URL with scheme. |

Used by: `ad_generation/` pipeline (generate + poll steps)

### 5. Modal GP service — Bayesian Optimisation

| Variable | Description |
|---|---|
| `MODAL_BO_API_URL` | Full URL of the Modal GP q-EI endpoint. Leave blank to fall back to local sklearn GPR. |
| `MODAL_BO_PCA_DIMS` | PCA components for Meta embeddings before API call (default `64`). |
| `GOOGLE_BO_PCA_DIMS` | PCA components for Google embeddings before API call (default `32`). |
| `BO_MIN_REAL_OBS` | Real-platform observations (CTR/CVR/ROAS) required before Qwen warm-start scores are dropped. Below this, the GP runs on Qwen fallback. (default `5`) |
| `MIN_CONVERGENCE_IMPRESSIONS` | Minimum lifetime impressions before convergence check fires (default `500`). |
| `MIN_CONVERGENCE_DAYS` | Minimum days running before convergence check fires (default `3`). |
| `CONVERGENCE_MARGIN_FRACTION` | CTR margin-of-error width used to compute required sample size (default `0.20`). Set `0.80` to converge quickly in demo/FAST_RAMP mode. |

Used by: `bo_pipeline/selector.py`, `bo_pipeline/modal_bo.py`, `main.py` convergence checker. When `MODAL_BO_API_URL` is unset, `run_bo()` uses local GPR automatically.

## Fake Ad Server (optional)

Uncomment in `backend/.env` to intercept data API calls at the network layer. OAuth still goes to real providers.

```
FAKE_META_BASE_URL=http://localhost:9000/meta/v19.0
FAKE_GOOGLE_BASE_URL=http://localhost:9000/google
```

See `FAKE_ADS_TESTING.md` for details.

## Masking / demo env vars

See `STAGING_POLICY.md` — "Legacy Masking Env Vars" section.

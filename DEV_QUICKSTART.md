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

### Required — Google Ads (optional until Google integration activated)
```env
GOOGLE_CLIENT_ID=your_google_oauth_client_id
GOOGLE_CLIENT_SECRET=your_google_oauth_client_secret
GOOGLE_REDIRECT_URI=https://<your-ngrok-subdomain>.ngrok-free.dev/auth/google/callback
GOOGLE_DEVELOPER_TOKEN=your_google_developer_token
GOOGLE_ADS_API_VERSION=v18
```

The Google OAuth callback follows the same flow as Meta but includes an account picker: after callback the user is redirected to `/app/settings?google_pick=<key>` to choose which Google Ads account to connect.

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
AZURE_ANALYSIS_DEPLOYMENT=gpt-4.1-nano
AZURE_SCORING_DEPLOYMENT=gpt-4-04-14
AZURE_TEXT_GEN_DEPLOYMENT=gpt-4.1-nano

# Image generation (deAPI / FLUX)
DEAPI_API_KEY=...
IMAGES_SERVE_BASE_URL=/images   # use relative path — absolute localhost URLs break when accessed via ngrok

# Modal GP service — Bayesian Optimisation (optional; falls back to local sklearn GPR if unset)
# The Modal app is deployed separately (not in this repo). Set this to the deployed endpoint URL.
MODAL_BO_API_URL=https://markshipman4273--bo-gp-service-gp-suggest.modal.run
MODAL_BO_PCA_DIMS=64
```

---

## 2 — First-time setup (clean clone)

Skip this section if you've already done it on this machine.

```bash
# Backend — create virtualenv and install Python deps
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your values per Section 1

# Frontend — install Node deps
cd ../frontend
npm install
cp .env.example .env   # VITE_API_URL defaults to http://localhost:8000
```

---

## 3 — Services overview

The app has up to four processes. Two are required; the others are optional or pre-deployed.

| Process | Required | How to start |
|---|---|---|
| Backend (FastAPI) | Yes | `cd backend && source .venv/bin/activate && python main.py` → :8000 |
| Frontend (React/Vite) | Yes | `cd frontend && npm run dev` → :5173 |
| ngrok | Yes for OAuth | `ngrok http 5173` — required so Meta/Google OAuth callbacks reach localhost |
| Modal GP service | No | Already deployed in the cloud; set `MODAL_BO_API_URL` in `.env`. BO falls back to local sklearn GPR if unset. The Modal app source is not in this repo — deploy once via Modal's CLI if you need to redeploy. |
| Fake ad server | No | `cd fake_ad_server && uvicorn server:app --port 9000 --reload` — replaces live Meta/Google calls with fixture data. Add `FAST_RAMP=true` for Case 2 (cold-start demo with Qwen-derived CTR). |

### Starting services (tmux sessions — start each separately)

Run `./start.sh` (or `./start.sh staging`) to print all commands and ports as a reminder.

| Service | Prod | Staging | Command |
|---|---|---|---|
| backend | :8000 | :8001 | `cd backend && source .venv/bin/activate && python main.py` |
| frontend | :5173 | :5174 | `cd frontend && npm run dev` |
| ngrok | — | — | `ngrok http 5173` (or 5174 for staging) |
| fake ads | :9000 | :9000 | `cd fake_ad_server && uvicorn server:app --port 9000 --reload` |

See `NGROK_SETUP.md` for the checklist when the ngrok URL changes.

### Fake ad server (optional — replaces live Meta/Google data calls)

```bash
cd fake_ad_server && uvicorn server:app --port 9000 --reload
```

Then uncomment in `backend/.env`:
```
FAKE_META_BASE_URL=http://localhost:9000/meta/v19.0
FAKE_GOOGLE_BASE_URL=http://localhost:9000/google
```

OAuth still hits real Google/Meta. See `FAKE_ADS_TESTING.md` for the full walkthrough including BO seeding.

`./start.sh` prints a reminder line for any active fake-server vars it detects in `backend/.env`.

**Switching from fake to real mode:** comment out the `FAKE_*` lines and restart the backend. Real campaigns appear immediately (live API). Fake rows remain in the DB keyed on fake IDs (`120210001`, `120212001`, etc.) — they are inert since real campaigns use different IDs.

---

## 4 — Get a JWT for curl testing

```bash
curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "yourpassword"}'
# → {"token": "eyJ..."}

TOKEN=eyJ...   # paste token here
```

---

## 5 — Ingest campaign metrics

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

## 6 — Ingest creative structure (triggers embeddings)

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

## 7 — Generate a Dynamic Ad (AI Images)

In the UI: click a campaign name to expand it → click **Dynamic Ad (AI Images)**. This starts a background job that:
1. Analyzes the seed ad image with GPT-4o and produces 10 edit suggestions
2. Submits each suggestion to deAPI (FLUX img2img) to generate image variants
3. Scores and QA-checks each image, keeps the top 4
4. Generates 4 text variants per slot (headline, primary_text, description, cta)
5. Stores everything in `ad_creative_structures` and fires embeddings in the background

Poll job status in the UI (auto-refreshes every 5s) or via curl:

```bash
curl -s http://localhost:8000/api/generate/dynamic/status/<job_id> \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Verify the edit suggestions and generated images in the DB:

```bash
# Edit suggestions sent to deAPI — one row per image variant
sqlite3 backend/app.db "SELECT v.id, v.suggestion, v.status, v.score, v.qa_status, v.local_filename FROM ad_generation_variants v JOIN ad_generation_jobs j ON v.job_id = j.id ORDER BY j.id DESC, v.id;"

# Completed job — check stored slots and image URLs
sqlite3 backend/app.db "SELECT slot, slot_index, value FROM ad_creative_structures WHERE ad_id LIKE 'gen_dyn_%' ORDER BY ad_id DESC, slot, slot_index LIMIT 40;"
```

---

## 9 — Seed synthetic BO training data (first time / testing only)

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

## 10 — Run Bayesian Optimisation

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
      "selection_type": "modal_q_ei",
      "combination": { "..." : "..." },
      "ei_score": null,
      "gpr_mean": null,
      "gpr_std": null
    }
  ]
}
```

- `scored_count` — training observations used by the GP
- `candidate_count` — unscored combinations evaluated (should be 64 minus scored)
- `selection_type: "modal_q_ei"` — both picks jointly selected by the Modal GP service via batch q-EI (default when `MODAL_BO_API_URL` is set)
- `selection_type: "ei"` — pick 1 via local sklearn GPR Expected Improvement (local fallback path)
- `selection_type: "fantasy"` — pick 2 via local GPR fantasy step (local fallback path)
- With fewer than 2 scored observations → `selection_type: "random"`

`ei_score`, `gpr_mean`, `gpr_std` are `null` for `modal_q_ei` picks (the Modal API does not return per-candidate GP stats in production mode).

Get latest picks later:
```bash
curl -s http://localhost:8000/api/bo/results/<ad_id> \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

---

## 11 — Explorer (debug)

```bash
curl -s http://localhost:8000/api/explore \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Returns the full raw Meta API response: campaigns → adsets → ads with creative fields. Use this to inspect what Meta is sending back, including `asset_feed_spec` image hashes and `thumbnail_url`.

---

## Troubleshooting

### Broken image URLs in the Ads page (ERR_CONNECTION_REFUSED)

Happens when `IMAGES_SERVE_BASE_URL` was set to an absolute `http://localhost:8000/images` URL. The browser tries to reach the server's localhost, which isn't reachable remotely. Fix existing rows:

```bash
sqlite3 backend/app.db "UPDATE ad_creative_structures SET value = '/images/' || substr(value, instr(value, '/images/') + 8) WHERE slot = 'image' AND value LIKE 'http://localhost:8000/images/%';"
```

Also set `IMAGES_SERVE_BASE_URL=/images` in `backend/.env` so future generations use relative paths.

### Inspect image generation variants and edit suggestions

```bash
sqlite3 backend/app.db "SELECT v.id, v.suggestion, v.status, v.score, v.qa_status, v.local_filename FROM ad_generation_variants v JOIN ad_generation_jobs j ON v.job_id = j.id ORDER BY j.id DESC, v.id;"
```

### Check image URLs for a specific generated ad

```bash
sqlite3 backend/app.db "SELECT slot_index, value FROM ad_creative_structures WHERE ad_id = '<ad_id>' AND slot = 'image' ORDER BY slot_index;"
```

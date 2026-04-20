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

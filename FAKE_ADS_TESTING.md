# Fake Ad Server — Manual BO Testing Guide

End-to-end walkthrough of every campaign type using the fake ad server.
No live Meta or Google account needed; OAuth still uses real credentials.

---

## Prerequisites — API keys

Embeddings always call real external APIs even with fake campaign data.
Both keys must be set in `backend/.env` or embedding tasks will fail
silently and BO will have no candidates.

| Key | Used for |
|---|---|
| `OPENAI_KEY` | Text embeddings (all platforms) |
| `AZURE_INFERENCE_KEY` | Image embeddings (Meta only) |

---

## Start everything

### 1 — Fake ad server (new terminal)

```bash
cd fake_ad_server
uvicorn server:app --port 9000 --reload
```

Health check: `curl http://localhost:9000/`

### 2 — Activate the fake data switch

In `backend/.env`, uncomment both lines:

```
FAKE_META_BASE_URL=http://localhost:9000/meta/v19.0
FAKE_GOOGLE_BASE_URL=http://localhost:9000/google
```

### 3 — Backend (new terminal)

```bash
cd backend
source .venv/bin/activate
python main.py
# runs on http://localhost:8000
```

### 4 — Frontend (new terminal)

```bash
cd frontend
npm run dev
# runs on http://localhost:5173
```

Log in at http://localhost:5173 — use your existing account or sign up.

### Look up your user ID (needed for seeding)

```bash
cd backend
sqlite3 app.db "SELECT id, email FROM users;"
```

Save this; it's needed in every `seed_bo_synthetic.py` call below.
Replace `USER_ID` throughout with your actual value.

---

## Verification queries

Run these any time to check embedding progress.

```bash
cd backend

# How many text combination embeddings exist per ad
sqlite3 app.db "SELECT source_id, COUNT(*) as n FROM ad_text_combination_embeddings GROUP BY source_id;"

# Ad IDs that have been ingested
sqlite3 app.db "SELECT DISTINCT ad_id, platform FROM ad_creative_structures;"

# Scored observations seeded so far
sqlite3 app.db "SELECT j.seed_ad_id, COUNT(*) as n FROM ad_generation_variants v JOIN ad_generation_jobs j ON v.job_id=j.id WHERE v.score IS NOT NULL GROUP BY j.seed_ad_id;"

# What BO returned last (check selection_type: should be 'ei'/'fantasy' not 'random')
sqlite3 app.db "SELECT ad_id, pick_rank, selection_type, combination FROM bo_selections ORDER BY created_at DESC LIMIT 6;"
```

---

## Campaign 1 — Meta Dynamic Ad

**Campaign:** `Fake Summer Sale — Prospecting` (ID `120210001`)
**Ad:** `Dynamic Prospecting Ad` (ID `120212001`) — has `asset_feed_spec`
**Combinations from ingest:** 4 headlines × 4 bodies × 4 descriptions = **64**

This is the cleanest path — ingest alone produces enough combinations.
No text generation step needed.

### Steps

**1. Sync campaigns**

Dashboard → Meta section → **Sync**

You should see three "Fake …" campaigns appear.

**2. Ingest campaign 1**

Click **Ingest** on _Fake Summer Sale — Prospecting_.

The backend fires three embedding tasks fire-and-forget:
- `embed_ad` → 1 text + 1 image embedding for the seed ad
- `embed_images` → 4 per-image embeddings (the picsum photos)
- `embed_all_combinations` → 64 text combination vectors

**3. Wait for embeddings**

```bash
cd backend

# Watch until count reaches 64
sqlite3 app.db "SELECT COUNT(*) FROM ad_text_combination_embeddings WHERE source_id='120212001';"
```

Allow ~15 s. Repeat if still 0 after 30 s (check backend logs for errors).

**4. Seed scored observations**

```bash
cd backend
source .venv/bin/activate

python seed_bo_synthetic.py \
  --platform meta \
  --ad-id 120212001 \
  --user-id USER_ID \
  --campaign-id 120210001 \
  --n 5
```

Expected output: five lines each showing `score=X.XX  headline='...'`

**5. Run BO**

In the UI, click **Get Recommendations** on _Fake Summer Sale — Prospecting_.

**6. Verify**

```bash
sqlite3 app.db "SELECT pick_rank, selection_type, combination FROM bo_selections WHERE ad_id='120212001' ORDER BY created_at DESC LIMIT 2;"
```

`selection_type` should be `ei` (pick 1) and `fantasy` (pick 2) — or `modal_q_ei`
if `MODAL_BO_API_URL` is configured. If it says `random`, the scored observations
were not found — recheck the user ID or re-run step 4.

---

## Campaign 2 — Meta Static Ad (Retargeting)

**Campaign:** `Fake Retargeting — Abandoned Cart` (ID `120210002`)
**Ad:** `Come Back Static Ad` (ID `120212002`) — single headline, body, description
**Combinations from ingest:** **1** (not enough for meaningful BO)

Static ads have only one copy combination until text generation adds variants.
Text generation must run before seeding.

### Steps

**1. Sync and ingest**

Dashboard → Meta section → **Sync**, then **Ingest** on
_Fake Retargeting — Abandoned Cart_.

Wait for the single combination to appear:

```bash
sqlite3 app.db "SELECT COUNT(*) FROM ad_text_combination_embeddings WHERE source_id='120212002';"
# Expect: 1
```

**2. Generate text variants**

Click **Static Text Ads** on the campaign row.

This calls `POST /api/generate/text/120210002`, which:
- Generates ~10 new variants per slot (headline, primary_text, description, cta)
- Fires `embed_all_combinations` on seed + generated variants combined

**3. Wait for combination embeddings**

```bash
# Watch until count grows well above 1 (expect 40–200+ depending on unique combos)
sqlite3 app.db "SELECT COUNT(*) FROM ad_text_combination_embeddings WHERE source_id='120212002';"
```

Allow ~30 s after the text generation response comes back.

**4. Seed scored observations**

```bash
cd backend
source .venv/bin/activate

python seed_bo_synthetic.py \
  --platform meta \
  --ad-id 120212002 \
  --user-id USER_ID \
  --campaign-id 120210002 \
  --n 5
```

**5. Run BO**

Click **Get Recommendations** on _Fake Retargeting — Abandoned Cart_.

**6. Verify**

```bash
sqlite3 app.db "SELECT pick_rank, selection_type, combination FROM bo_selections WHERE ad_id='120212002' ORDER BY created_at DESC LIMIT 2;"
```

---

## Campaign 3 — Google RSA

**Campaign:** `Fake Search — Brand Keywords` (ID `9876543210`)
**Ad:** `Brand RSA — Primary` (ID `7654321000`) — 6 headlines, 2 descriptions
**Combinations from ingest:** 6 × 2 = **12** (enough; no text gen required)

Google RSA BO is text-only — image half of the embedding vector is zero-padded.

### Steps

**1. Sync Google campaigns**

Dashboard → Google section.
If Google OAuth is not connected, go to Settings and connect first
(OAuth still hits real Google even with the fake server active).

Click **Sync** (or the equivalent trigger to load campaigns).

**2. Ingest campaign**

Click **Ingest** on _Fake Search — Brand Keywords_.

The backend fires:
- `embed_ad` → text embedding only (no image for RSA)
- `embed_all_combinations` with `slots=('headline','description')` → 12 vectors

```bash
sqlite3 app.db "SELECT COUNT(*) FROM ad_text_combination_embeddings WHERE source_id='7654321000';"
# Expect: 12
```

Allow ~10 s.

**3. (Optional) Generate RSA text**

If you want a larger candidate pool, click **Generate RSA Text**.
This adds ~10 variants per slot and fires `embed_all_combinations` again,
growing the pool to ~(6+10) × (2+10) = 192 combinations.
Otherwise skip — 12 combinations is sufficient for seeding.

**4. Seed scored observations**

```bash
cd backend
source .venv/bin/activate

python seed_bo_synthetic.py \
  --platform google \
  --ad-id 7654321000 \
  --user-id USER_ID \
  --campaign-id 9876543210 \
  --n 5
```

**5. Run BO**

Click **Get Recommendations** on _Fake Search — Brand Keywords_.

**6. Verify**

```bash
sqlite3 app.db "SELECT pick_rank, selection_type, combination FROM bo_selections WHERE ad_id='7654321000' ORDER BY created_at DESC LIMIT 2;"
```

`image_url` will be absent from the combination JSON — that is expected for RSA.

---

## Campaign 4 — Cross-Platform BO

Runs a single GPR over both a Meta ad and a Google RSA ad.
Scores are normalised across platforms via ECDF before fitting.

**Prerequisites:** complete Campaigns 1 and 3 above first
(both must have combination embeddings and seeded scored observations).

### Steps

**1. Confirm both ads are seeded**

```bash
sqlite3 app.db "
SELECT j.seed_ad_id, COUNT(*) as scored
FROM ad_generation_variants v
JOIN ad_generation_jobs j ON v.job_id = j.id
WHERE v.score IS NOT NULL
GROUP BY j.seed_ad_id;"
# Expect rows for both 120212001 and 7654321000
```

**2. Seed cross-platform in one command** (if you skipped the individual seeds above)

```bash
cd backend
source .venv/bin/activate

python seed_bo_synthetic.py \
  --platform cross \
  --meta-ad-id 120212001 \
  --google-ad-id 7654321000 \
  --user-id USER_ID \
  --campaign-id synthetic_campaign \
  --n 5
```

**3. Call the cross-platform BO endpoint**

```bash
curl -s -X POST http://localhost:8000/api/bo/cross-platform \
  -H "Authorization: Bearer YOUR_JWT" \
  -H "Content-Type: application/json" \
  -d '{
    "pairs": [
      {"platform": "meta",   "seed_ad_id": "120212001", "text_source_id": "120212001"},
      {"platform": "google", "seed_ad_id": "7654321000", "text_source_id": "7654321000"}
    ]
  }' | python3 -m json.tool
```

JWT: log in via the UI, open DevTools → Application → Local Storage →
`auth_token` (or whatever key `api.js` uses).

**4. Verify**

Response should contain picks tagged with `"platform": "meta"` and
`"platform": "google"`, each with a `selection_type` of `ei`/`fantasy`
(not `random`), and a `global_rank` ordering them by predicted quality
across both platforms.

---

## Teardown / reset

```bash
# Turn off fake server:
# Ctrl-C the uvicorn process

# Deactivate env vars:
# In backend/.env comment out both FAKE_* lines, restart backend

# Wipe seeded observations (keeps structure and embeddings):
cd backend
sqlite3 app.db "DELETE FROM ad_generation_variants WHERE job_id IN (SELECT id FROM ad_generation_jobs WHERE adset_id='synthetic_adset');"
sqlite3 app.db "DELETE FROM ad_generation_jobs WHERE adset_id='synthetic_adset';"
sqlite3 app.db "DELETE FROM bo_selections;"
```

---

## Fake campaign reference

| ID | Name | Type | Ad ID | Combos after ingest |
|---|---|---|---|---|
| `120210001` | Fake Summer Sale — Prospecting | Meta dynamic | `120212001` | 64 |
| `120210002` | Fake Retargeting — Abandoned Cart | Meta static | `120212002` | 1 → needs text gen |
| `120210003` | Fake Brand Awareness Q2 (PAUSED) | Meta static | `120212003` | 1 → needs text gen |
| `9876543210` | Fake Search — Brand Keywords | Google RSA | `7654321000` | 12 |
| `9876543211` | Fake Display — Prospecting | Google display | `7654321002` | blocked (display) |
| `9876543212` | Fake Performance Max (PAUSED) | Google pMax | — | no ads in fixture |

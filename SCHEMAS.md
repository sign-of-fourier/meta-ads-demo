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

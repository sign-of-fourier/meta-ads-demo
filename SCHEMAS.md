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
| `tier` | TEXT | `'free'` \| `'trial'` \| `'beta'` \| `'basic'` \| `'premium'` \| `'enterprise'` — added via ALTER TABLE migration; column default `'free'`, but `signup()` explicitly inserts `'beta'` for every new account (no Stripe yet — wide open until an admin closes/upgrades it). `free`/`trial`/`beta`/`basic` are read-only (no ad push/launch/pause); only `premium`/`enterprise` can write. See `backend/permissions.py` |
| `tier_source` | TEXT | `'default'` \| `'admin'` \| `'stripe'` — who last set `tier`. Added via ALTER TABLE migration; default `'default'`. No Stripe webhook handler exists yet (see `TECHNICAL_DEBT.md`) — today this is either `'default'` or `'admin'` (set via `POST /api/admin/users/{id}/tier`) |
| `tier_expires_at` | TEXT, nullable | Admin-set reminder date; **not auto-enforced** — nothing reads this to revoke access. An admin is expected to check it and manually downgrade the tier when it's time. Editable from the admin screen (`/admin`) |
| `stripe_customer_id` | TEXT, nullable | Reserved for future Stripe billing integration; unused until the webhook handler is built |
| `stripe_subscription_id` | TEXT, nullable | Same |
| `last_login_at` | TEXT, nullable | Updated on every successful `/auth/login` (not signup) |
| `login_count` | INTEGER | Default `0`; incremented on every successful `/auth/login` |
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

CSRF state tokens for Meta and Google OAuth flows. Deleted after successful callback.

| Column | Type | Notes |
|---|---|---|
| `state` | TEXT PK | random `urlsafe` token |
| `user_id` | INTEGER | FK → `users.id` |
| `provider` | TEXT | `'meta'` \| `'google'` — added via ALTER TABLE migration; default `'meta'` |
| `created_at` | TEXT | |

---

### `google_connections`

One row per user — the connected Google Ads account.

| Column | Type | Notes |
|---|---|---|
| `user_id` | INTEGER PK | FK → `users.id` |
| `customer_id` | TEXT | Google Ads customer ID (no hyphens, e.g. `1234567890`) |
| `login_customer_id` | TEXT nullable | MCC manager account ID when using agency setup |
| `refresh_token` | TEXT | OAuth2 refresh token; exchanged for access token on each request |
| `customer_name` | TEXT nullable | Human-readable account name |
| `connected_at` | TEXT | `datetime('now')` |

---

### `google_pending_connections`

Temporary holding table for Google OAuth when multiple accounts are accessible. Row is written after callback and deleted after account selection.

| Column | Type | Notes |
|---|---|---|
| `key` | TEXT PK | `secrets.token_urlsafe(32)` — passed as `?google_pick=<key>` in redirect |
| `user_id` | INTEGER | FK → `users.id` |
| `refresh_token` | TEXT | OAuth2 refresh token |
| `accounts_json` | TEXT | JSON array of `{customer_id, name}` dicts from `listAccessibleCustomers` |
| `created_at` | TEXT | `datetime('now')` |

---

### `ad_insights`

Metric snapshots. One row per `(user, ad_account, level, object_id, date)` per day — a repeat `POST /api/ingest` call the same day updates that day's row in place (`ON CONFLICT DO UPDATE`) rather than inserting a duplicate.

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
| `platform` | TEXT | `'meta'` \| `'google'` — added via ALTER TABLE migration; default `'meta'` |
| `data_source` | TEXT | `'real'` \| `'masked'` \| `'demo'` — added via ALTER TABLE migration |
| `mask_profile` | TEXT nullable | `'healthy'` \| `'stable'` \| `'weak'` — only set when `data_source='masked'` |
| `created_at` | TEXT | Overwritten to the update time on each re-ingest of the same day's row |
| UNIQUE | | `(user_id, ad_account_id, level, object_id, date)` — `idx_ad_insights_unique`; drives the upsert. A one-time startup migration deduplicates any pre-existing rows (keeping the highest `id` per key) before the index is created. |

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
| `ad_id` | TEXT | Platform ad id |
| `platform` | TEXT | `'meta'` \| `'google'` — added via ALTER TABLE migration; default `'meta'` |
| `creative_type` | TEXT | Meta: `'static'` \| `'dynamic'`; Google: `'rsa'` \| `'display'` \| `'video'` \| `'pmax'` \| `'shopping'` \| `'unknown'` |
| `slot` | TEXT | `'headline'`, `'description'`, `'primary_text'`, `'image'`, `'video'`, `'final_url'` |
| `slot_index` | INTEGER | `0` for static; `0, 1, 2…` for dynamic variants |
| `value` | TEXT | The component text or image URL / hash |
| `ingested_at` | TEXT | ISO timestamp of last ingest |
| `lifecycle_status` | TEXT | `'active'`, `'inactive'`, `'missing'`, or `'generated'` (see below) |
| `effective_status` | TEXT | Raw platform status: `'ACTIVE'`, `'PAUSED'`, `'ENABLED'`, `'CAMPAIGN_PAUSED'`, etc. — added via ALTER TABLE migration; default `'UNKNOWN'` |
| `is_pushed_clone` | INTEGER | `1` if this ad was created by the push flow and its `ad_id` matches a `pushed_ad_combos.platform_ad_numeric_id`; else `0` — added via ALTER TABLE migration |
| `role` | TEXT | `'parent'` (default) \| `'test_clone'` — set at ingest; `test_clone` is set when `is_pushed_clone` is detected. Does not change after first write. — added via ALTER TABLE migration |
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

Returned by `GET /api/structure/{campaign_id}` and `GET /api/google/structure/{campaign_id}` — one item per ad.

```python
{
  "ad_id":            str,
  "adset_id":         str,
  "campaign_id":      str,
  "creative_type":    "static" | "dynamic" | "rsa" | "display" | "video" | "pmax" | "shopping" | "unknown",
  "lifecycle_status": "active" | "inactive" | "missing" | None,
  "effective_status": str | None,   # e.g. "ACTIVE", "PAUSED", "ENABLED"; None if UNKNOWN
  "is_pushed_clone":  bool,         # True if ad was created by the push flow
  "clone_stats":      dict | None,  # see below
  "components":       { slot: [value, …] }  # dict[str, list[str | None]]
}
```

`components` values are ordered lists: single-element for static ads, multi-element for dynamic variants. Internal slots (prefixed `_`, e.g. `_placements`) are excluded.

**`clone_stats` shape** — present for all ads:

```python
# Pushed clones (is_pushed_clone=True):
{
  "impressions":   int,
  "days_running":  int,
  "converged":     bool,
  "ctr":           float | None,   # convergence_metric; None until clone converges
  "push_status":   str,            # "paused" | "active" — legacy; prefer clone_status
  "clone_status":  str,            # lifecycle state: see pushed_ad_combos table
  "platform_ad_id": str | None,    # platform ad ID for activate calls
  "combo_id":      int | None,     # pushed_ad_combos.id for retain calls
}

# Native ads with ingest metrics (is_pushed_clone=False, impressions in native_ad_insights):
{
  "impressions": int,
  "clicks":      int,
  "ctr":         float,   # decimal fraction (0.045 = 4.5%)
  "spend":       float,
  "cpm":         float,
}
```

### `StructureIngestResult`

Returned by `POST /api/ingest/structure/{campaign_id}` and `POST /api/google/ingest/structure/{campaign_id}`.

```python
{
  "campaign_id":        str,
  "ads_processed":      int,
  "components_saved":   int,
  "parent_should_pause": bool   # True when any clone_paused/clone_active rows exist for this campaign
}
```

### `BOPick`

Returned inside `BORunResponse` by `/api/bo/run`, `/api/google/bo/run`, and results endpoints.

```python
{
  "combination_key":    str,
  "combination":        dict,          # { slot: value } — text slots + optional image_url
  "selection_type":     str,           # "ei" | "fantasy" | "random" | "modal_q_ei"
  "ei_score":           float | None,
  "gpr_mean":           float | None,
  "gpr_std":            float | None,
  "nearest_known":      list[dict],    # explainability: cosine-nearest scored ads (pre-PCA space), default []
  "confidence":         str | None,    # "low" | None — T12, drafted not signed off, see TECHNICAL_DEBT.md
  "placements":         list[dict],    # Meta placement data; empty for Google
  # lifecycle fields — populated when already pushed:
  "ad_name":            str | None,
  "already_pushed":     bool,
  "push_status":        str | None,    # "paused" | "active" — legacy
  "converged":          bool,
  "platform_ad_id":     str | None,
  "current_impressions": int,
  "clone_status":       str | None,    # clone lifecycle state from pushed_ad_combos
  "combo_id":           int | None,    # pushed_ad_combos.id — used by POST /api/push/retain
  "matches_existing_ad": dict | None,  # {ad_id, effective_status} if combo matches a native static ad
}
```

`CrossPlatformBOPick` (returned by `/api/bo/cross-platform*`) has the same fields plus `platform`, `seed_ad_id`, `text_source_id`, and a `pca_warning: str | None` field on each `group_stats` entry (not on the pick itself) — see `BACKEND.md`.

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

### `ad_image_embeddings`

One row per image slot per ad. Written by `embeddings/pipeline.py` — called fire-and-forget from structural ingest and from the dynamic ad generation background task. Upserted on `(user_id, ad_id, slot_index)`.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER | FK → `users.id` |
| `ad_id` | TEXT | Platform ad id — joins to `ad_creative_structures.ad_id` |
| `campaign_id` | TEXT | Denormalised for fast campaign-level queries |
| `slot_index` | INTEGER | Image slot index (0, 1, 2… for dynamic ads) |
| `image_ref` | TEXT | Local URL of the image that was embedded (`/ad-images/…` or `/images/…`) |
| `vector` | BLOB | Raw `float32` bytes — the image embedding from Azure AI Inference |
| `model` | TEXT nullable | Azure model used for image embedding |
| `embedded_at` | TEXT | `datetime('now')` |
| UNIQUE | | `(user_id, ad_id, slot_index)` |

The BO pipeline reads `ad_image_embeddings` to form the image dimension of its candidate pool: each text combination from `ad_text_combination_embeddings` is paired with each row here → N_text × N_images candidates.

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

### `scored_observations`

Lives in `backend/bo_pipeline/storage.py`, not `main.py`. The central table BO fits on — every GPR/Modal call reads its training set from here, never from `ad_generation_variants` directly. Three writers populate it: the Qwen2-VL image-scoring pipeline, CTR convergence checking (`_write_convergence_observation` in `main.py`), and the synthetic warm-start/seed path (`POST /api/bo/seed-scored-variants`, `warm_start.py`).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER | |
| `seed_ad_id` | TEXT | The ad this observation belongs to (or a `generator_id` for multi-member pools — see `ad_generators`) |
| `combination_key` | TEXT | `json.dumps(combo, sort_keys=True)` — matches `ad_text_combination_embeddings.combination_key` |
| `combination` | TEXT | JSON — the slot dict this observation scores |
| `score` | REAL | Meaning depends on `metric` — never mixed across metric types in one GP fit |
| `metric` | TEXT | `'ctr'` \| `'cvr'` \| `'roas'` \| `'qwen'` \| `'qwen_warm'` \| `'synthetic'` — resolution order in `bo_pipeline/config.py`'s `METRIC_PREFERENCE` |
| `source` | TEXT | `'seed_script'` \| provenance of the writer, e.g. which pipeline wrote this row |
| `text_vector` | BLOB nullable | `float32` bytes |
| `image_vector` | BLOB nullable | `float32` bytes; `NULL` for text-only ads (Google RSA) — presence, not platform name, decides 1536- vs 3072-dim in `_build_X`/`BOGroup.build_X` |
| `created_at` | TEXT | |
| UNIQUE | | `(user_id, seed_ad_id, combination_key, metric)` — a new observation for the same combination+metric supersedes rather than duplicates |

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
| `seed_ad_id` | TEXT nullable | The source ad used as seed — added via ALTER TABLE migration |
| `adset_id` | TEXT nullable | The adset the generated ad will be pushed to — added via ALTER TABLE migration |
| `meta_ad_id` | TEXT nullable | Set after successful push to Meta — added via ALTER TABLE migration |
| `images_generated` | INTEGER nullable | Count of images generated — added via ALTER TABLE migration |
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
| `google_ad_resource_name` | TEXT nullable | Set after successful push to Google Ads — added via ALTER TABLE migration |
| `run_id` | TEXT nullable | UUID4 hex shared by every pick from one `save_bo_run()` call — added via ALTER TABLE migration; NULL on rows written before this column existed |
| `created_at` | TEXT | `datetime('now')` |
| UNIQUE (partial) | | `idx_bo_selections_unpushed` on `(seed_ad_id, text_source_id, combination_key) WHERE google_ad_resource_name IS NULL` |

**Run replacement semantics:** `save_bo_run()` deletes any not-yet-pushed rows (`google_ad_resource_name IS NULL`) for the same `(seed_ad_id, text_source_id)` before inserting the new run's picks, in one transaction (`BEGIN IMMEDIATE`) — a fresh run supersedes the prior recommendation rather than accumulating alongside it. Rows already pushed to Google (`google_ad_resource_name` set) are exempt and persist forever as push history. `get_latest_bo_run()` returns the picks sharing the `run_id` of the highest-`id` row for that `(seed_ad_id, text_source_id)`. The partial unique index is a backstop, not the primary mechanism — it turns a would-be race between two concurrent `save_bo_run()` calls for the same ad into a loud `IntegrityError` rather than silent duplicate rows.

**Variant embedding convention:** when embedding a generated image variant for use in `bo_pipeline`, store it in `ad_embeddings` with `ad_id = f"gen_{job_id}_{variant_id}"`. The selector joins on this convention.

---

### `pushed_ad_combos`

One row per pushed test clone. Written by `POST /api/push/pick`; updated by convergence checkers and activate/retain endpoints.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER | FK → `users.id` |
| `platform` | TEXT | `'meta'` \| `'google'` |
| `seed_ad_id` | TEXT | The parent ad that was optimised |
| `ad_name` | TEXT | Name given to the pushed clone |
| `combination_key` | TEXT | JSON key identifying the BO-selected (headline, description, …) combination |
| `combination` | TEXT | JSON dict of the selected slots |
| `platform_ad_id` | TEXT nullable | Meta ad ID or Google resource name |
| `platform_ad_numeric_id` | TEXT nullable | Numeric ad ID (last segment of Google resource name); used for clone detection |
| `push_status` | TEXT | `'paused'` \| `'active'` — legacy; use `clone_status` going forward |
| `clone_status` | TEXT | Lifecycle state: `'clone_paused'` → `'clone_active'` → `'clone_converged'` / `'clone_retained'` / `'clone_invalidated'` |
| `converged` | INTEGER | `1` when CTR thresholds met — kept for backward compat; derivable from `clone_status = 'clone_converged'` |
| `converged_at` | TEXT nullable | Timestamp when convergence was recorded |
| `convergence_metric` | REAL nullable | CTR at convergence time |
| `current_impressions` | INTEGER | Latest impression count from convergence checker |
| `days_running` | INTEGER | Days since push (updated by convergence checker) |
| `pushed_at` | TEXT | `datetime('now')` |
| UNIQUE | | `(user_id, platform, seed_ad_id, combination_key)` |

**`clone_status` lifecycle:**

| Status | Set when |
|---|---|
| `clone_paused` | On push; or initial default |
| `clone_active` | User activates via `/api/activate`; or convergence checker detects `ENABLED` (Google) / `impressions > 0` (Meta) |
| `clone_converged` | Convergence checker: impressions ≥ threshold AND days ≥ threshold |
| `clone_retained` | User clicks "Keep Running" → `POST /api/push/retain/{combo_id}` |
| `clone_invalidated` | Ingest detects creative mismatch between stored combination and current ad values |

---

### `ad_parent_fillers`

Stores the constant filler slots extracted from a Google parent RSA at first ingest. Used when pushing test clones so all 5 RSA slots are pinned and CTR measures exactly one (headline, description) pair.

| Column | Type | Notes |
|---|---|---|
| `parent_ad_id` | TEXT PK | `ad_creative_structures.ad_id` of the parent RSA |
| `filler_headline_1` | TEXT | 2nd headline from the parent RSA (slot_index=1); fallback `"Learn More"` |
| `filler_headline_2` | TEXT | 3rd headline from the parent RSA (slot_index=2); fallback `"Get Started"` |
| `filler_description_1` | TEXT | 2nd description from the parent RSA (slot_index=1); fallback `"Find out more today."` |
| `created_at` | TIMESTAMP | `CURRENT_TIMESTAMP` |

---

### `ad_generators`

Named groups of ads whose candidate pools are merged for BO. Created via `POST /api/generators`.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID |
| `user_id` | INTEGER | FK → `users.id` |
| `name` | TEXT | User-visible label; auto-generated as `"Auto <platform> YYYY-MM-DD"` when created via BatchPanel |
| `created_at` | TEXT | `datetime('now')` |

---

### `ad_generator_members`

Members of an ad generator. Each row links one ad to one generator.

| Column | Type | Notes |
|---|---|---|
| `generator_id` | TEXT | FK → `ad_generators.id` |
| `ad_id` | TEXT | `ad_creative_structures.ad_id` of the member ad |
| `contribution_mode` | TEXT | `'dynamic'` — multi-asset ad (Meta dynamic / Google RSA); its slots form a Cartesian candidate pool. `'static'` — single fixed combination; contributes one candidate as-is. |
| PRIMARY KEY | | `(generator_id, ad_id)` |

When BO runs with a `generator_id`, all member ads' `ad_text_combination_embeddings` are queried together and `scored_observations` are merged across all member `seed_ad_id` values. The `generator_id` itself becomes the `seed_ad_id` key in `pushed_ad_combos` and `bo_selections` for any picks from that run.

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

---

### `manual_campaigns`

Top-level container for Manual platform campaigns. Each row represents one user-created campaign with no external API connection.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | `man_cmp_{12-char hex}` |
| `user_id` | INTEGER | FK → `users.id` |
| `name` | TEXT | User-supplied display name |
| `created_at` | TEXT | ISO datetime |

Manual ads (dynamic templates and static ads) reuse `ad_creative_structures` with `platform='manual'`, `ad_account_id='manual'`, `adset_id='manual'`, `campaign_id=man_cmp_...`. Dynamic template IDs have prefix `man_dyn_`; static ad IDs have prefix `man_sta_`.

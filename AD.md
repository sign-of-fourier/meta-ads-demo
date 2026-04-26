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

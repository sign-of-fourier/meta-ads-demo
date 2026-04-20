# Embeddings Pipeline

Standalone async module that extracts fields from ingested ad components,
embeds text and image via separate model providers, and stores individual and
combined vectors for downstream use by the BO pipeline.

No dependency on the FastAPI app.

---

## What is an embedding?

An embedding is a dense real-valued vector — a point in a high-dimensional space — that
encodes semantic content. Two inputs that are semantically similar end up close together
(small cosine or Euclidean distance); dissimilar inputs end up far apart. The key property
for our use case is that **similarity in the vector space reflects similarity in ad
quality signal**: combinations whose embeddings are close are expected to produce similar
audience responses.

We embed two distinct modalities:

### Text embeddings

**Model:** OpenAI `text-embedding-3-small`
**Dimension:** 1536 float32

The input is a JSON string of all the ad's text slots:

```json
{"headline": "...", "primary_text": "...", "description": "..."}
```

This single call encodes the full copy in one vector. The JSON format means the model
sees the field names alongside the values, which tends to reduce positional ambiguity
(the model sees "headline: X" rather than just "X").

### Image embeddings

**Model:** Azure AI Inference `embed-v-4-0` (multimodal)
**Dimension:** 1024 float32 (model output; truncated to 128 for BO)

Image embeddings are computed from a URL. The model is multimodal — the same model family
that embeds text can embed images into the same space, which allows cross-modal similarity.
We use it in image-only mode here (no paired text prompt).

Images stored in Meta's `asset_feed_spec` as hashes rather than URLs are resolved to CDN
URLs via the Meta `adimages` API during structural ingest before this step runs.

---

## Truncation and concatenation

The GPR feature vector is built by the `ad_embedding_combiner` module:

```
text_vec  (1536-dim)  → truncate to TEXT_DIM=128
image_vec (1024-dim)  → truncate to IMAGE_DIM=128
combined  = [text_128 | image_128]  →  256-dim float32
```

Truncation keeps the leading dimensions, which carry the bulk of semantic variance in
both OpenAI and Azure embeddings (their training objectives concentrate the most
discriminative signal early). Zero-padding is used on the right if a vector is shorter
than the target dimension (this applies when no image embedding is available — the image
slot is zeroed out).

See `backend/bo_pipeline/BO.md` for the rationale behind this dimensionality choice.

---

## Three embedding tasks per ad

Structural ingest fires three tasks fire-and-forget for each ad:

### 1. Seed embedding (`embed_ad`)

One row in `ad_embeddings` per `(user_id, ad_id)`. Uses slot[0] text + image[0].

This is the **primary embedding** used by the BO pipeline as the image-side vector for
all candidate combinations. It represents the seed ad as a single point.

### 2. Per-image embeddings (`embed_images`)

One row in `ad_image_embeddings` per image URL slot. Captures each image variant
individually — useful for future per-image BO (pairing each image embedding with each
text combination).

### 3. Combination embeddings (`embed_all_combinations`)

One row in `ad_text_combination_embeddings` per Cartesian combination of text slots.
For a dynamic ad with N headlines × M primary texts × K descriptions: N × M × K rows.

Each combination is embedded as a JSON string:
```json
{"headline": "...", "primary_text": "..."}
```
(Description is stored in the combination key but not currently included in the embedded
text — this is consistent with the scorer input format.)

These vectors are the **text-side inputs** for all candidate combinations in the BO pipeline.
Already-embedded combinations are skipped on repeat runs (idempotent, no wasted API calls).

---

## File reference

| File | Role |
|---|---|
| `extractor.py` | Pure function — `extract_fields(components)`, `text_as_json(fields)` |
| `embedder.py` | Async clients — `embed_text(str)`, `embed_image_url(url)`, `concat_embeddings(...)` |
| `pipeline.py` | `embed_ad(user_id, ad_id, campaign_id, components)` — orchestrates extract → embed → upsert; standalone runner |

---

## Public API

```python
from embeddings.pipeline import embed_ad

ok = await embed_ad(
    user_id=1,
    ad_id="111222333",
    campaign_id="123456789",
    components=[
        {"slot": "headline",      "slot_index": 0, "value": "My Product"},
        {"slot": "primary_text",  "slot_index": 0, "value": "Best product ever."},
        {"slot": "image",         "slot_index": 0, "value": "https://example.com/ad.png"},
    ],
)
# Returns True on success, False on failure (never raises)
```

`embed_ad` accepts an optional `db_path` parameter to target a non-default database.

---

## DB tables

See `SCHEMAS.md` at the repo root for full column definitions.

| Table | Populated by | One row per |
|---|---|---|
| `ad_embeddings` | `embed_ad` | `(user_id, ad_id)` — upserted |
| `ad_image_embeddings` | `embed_images` | `(ad_id, slot_index)` |
| `ad_text_combination_embeddings` | `embed_all_combinations` | `(source_id, combination_key)` |

Vectors are stored as raw `float32` bytes (`numpy.ndarray.tobytes()`). To read back:

```python
import numpy as np
import sqlite3

conn = sqlite3.connect("app.db")
row = conn.execute(
    "SELECT combined_vector FROM ad_embeddings WHERE user_id=? AND ad_id=?",
    (user_id, ad_id)
).fetchone()
vec = np.frombuffer(row[0], dtype=np.float32)
# shape: (256,)  — 128 text + 128 image
```

---

## Linking to the generation pipeline

After running the generation pipeline, embed generated variant images as if they were
new ads. Use the variant's `local_filename` (served via `IMAGES_SERVE_BASE_URL`) as the
`image` component value, and assign the variant a synthetic `ad_id` (`gen_{job_id}_{variant_id}`).
See `test_generation_pipeline.py` for a worked example.

---

## Standalone runner

```bash
cd backend
source .venv/bin/activate
python -m embeddings.pipeline             # embed all un-embedded ads
python -m embeddings.pipeline --ad-id <ad_id>
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_KEY` | — | Required — OpenAI API key for text embeddings |
| `OPENAI_TEXT_MODEL` | `text-embedding-3-small` | OpenAI text embedding model |
| `AZURE_INFERENCE_KEY` | — | Required — Azure AI Inference key for image embeddings |
| `AZURE_EMBEDDING_ENDPOINT` | project resource URL | Azure AI Inference endpoint |
| `AZURE_IMAGE_MODEL` | `embed-v-4-0` | Azure multimodal image embedding model |

Note: image embeddings use **Azure AI Inference**, not Azure OpenAI. These are different
resources with different keys and endpoints.

---

## Running tests

```bash
cd backend
source .venv/bin/activate
python -m pytest test_structural_ingest.py -v          # pure unit tests, no API keys
python -m pytest test_generation_pipeline.py -v -k "embed"  # real API calls
```

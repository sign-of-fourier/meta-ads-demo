# Embeddings Pipeline

Standalone async module that extracts fields from ingested ad components,
embeds text and image via Azure AI Inference, and stores a combined vector
in `ad_embeddings`. No dependency on the FastAPI app.

## What it does

Given a set of ad component rows (from `ad_creative_structures`) for one ad:

1. **Extract** — Pull `headline`, `primary_text`, `description`, and `image_url` from slot rows
2. **Embed** — Call Azure AI Inference for text embedding and image embedding in parallel
3. **Store** — Upsert a row into `ad_embeddings` with both individual and concatenated vectors

## File reference

| File | Role |
|---|---|
| `extractor.py` | Pure function — `extract_fields(components)`, `text_as_json(fields)` |
| `embedder.py` | Async Azure AI clients — `embed_text(str)`, `embed_image_url(url)`, `concat_embeddings(...)` |
| `pipeline.py` | `embed_ad(user_id, ad_id, campaign_id, components)` — orchestrates extract → embed → upsert; also standalone runner |

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

## DB table

See `SCHEMAS.md` at the repo root for full column definitions.

`ad_embeddings` — one row per `(user_id, ad_id)`. Upserted on that unique key.

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
```

## Linking to the generation pipeline

After running the generation pipeline, you can embed generated variant images
as if they were new ads. Use the variant's `local_filename` (served via
`IMAGES_SERVE_BASE_URL`) as the `image` component value, and assign the
variant a synthetic `ad_id` (e.g. `gen_{job_id}_{variant_id}`).
See `test_generation_pipeline.py` for a worked example.

## Standalone runner

Re-embed all ads not yet in `ad_embeddings`:

```bash
cd backend
source .venv/bin/activate
python -m embeddings.pipeline
```

Re-embed a specific ad:

```bash
python -m embeddings.pipeline --ad-id <ad_id>
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `AZURE_INFERENCE_KEY` | — | Required |
| `AZURE_ENDPOINT` | project resource URL | Azure AI Inference endpoint |
| `AZURE_IMAGE_MODEL` | `embed-v-4-0` | Model for image embeddings |
| `AZURE_TEXT_MODEL` | `embed-v-4-0` | Model for text embeddings |

## Running tests

```bash
cd backend
source .venv/bin/activate
python -m pytest test_generation_pipeline.py -v -k "embed"
```

Tests make real API calls — env vars above must be set.

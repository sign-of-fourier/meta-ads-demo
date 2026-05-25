# Embeddings Pipeline

Standalone async module. Embeds ingested and generated ads into text and image vectors
for downstream use by the BO pipeline.

For a full explanation of what embeddings are, why there are three separate tasks, how
the CDN expiry problem is solved, and how this connects to BO — see **`AD.md`** at the
repo root.

---

## Public API

```python
from embeddings.pipeline import embed_ad, embed_images

# Seed embedding (one combined row per ad)
ok = await embed_ad(
    user_id=1,
    ad_id="111222333",
    campaign_id="123456789",
    components=[
        {"slot": "headline",      "slot_index": 0, "value": "My Product"},
        {"slot": "primary_text",  "slot_index": 0, "value": "Best product ever."},
        {"slot": "image",         "slot_index": 0, "value": "http://localhost:8000/ad-images/img.jpg"},
    ],
)

# Per-image embeddings (one row per image slot)
count = await embed_images(user_id, ad_id, campaign_id, components)
```

Both functions accept an optional `db_path` to target a non-default database.
Both return on failure without raising (failures are logged).

```python
from ad_combination_embeddings.pipeline import embed_all_combinations

# 4×4×4 = 64 text combination vectors
await embed_all_combinations(
    source_id=ad_id,
    components=components,
    slots=("headline", "primary_text", "description"),
)
```

---

## DB tables

| Table | Populated by | One row per |
|---|---|---|
| `ad_embeddings` | `embed_ad` | `(user_id, ad_id)` — upserted |
| `ad_image_embeddings` | `embed_images` | `(user_id, ad_id, slot_index)` |
| `ad_text_combination_embeddings` | `embed_all_combinations` | `(source_id, combination_key)` |

Vectors are stored as raw `float32` bytes (`numpy.ndarray.tobytes()`). To read back:

```python
import numpy as np, sqlite3
conn = sqlite3.connect("app.db")
row = conn.execute(
    "SELECT combined_vector FROM ad_embeddings WHERE user_id=? AND ad_id=?",
    (user_id, ad_id)
).fetchone()
vec = np.frombuffer(row[0], dtype=np.float32)
# shape: (3072,) — raw concat of text (1536) + image (1536), NOT truncated
# BO truncates to 256-dim at inference via ad_embedding_combiner
```

See `SCHEMAS.md` for full column definitions.

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

**Image embeddings use Azure AI Inference, not Azure OpenAI** — different resource, different key.

---

## Running tests

```bash
cd backend && source .venv/bin/activate
python -m pytest tests/test_structural_ingest.py -v                        # pure unit tests, no API keys
python -m pytest tests/test_generation_pipeline.py -v -k "embed"           # real API calls
python -m pytest tests/test_combination_embeddings.py -v                   # combination embedding tests
```

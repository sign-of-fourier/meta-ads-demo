# Ad Text Combination Embeddings

Part of the AdStac.kr standalone module suite. For a dynamic ad with multiple
values per slot, embeds every Cartesian combination of text values as a single
vector. No images, no metadata — pure text combinations.

## Why combinations?

A dynamic ad with 4 headlines × 4 primary texts × 4 descriptions has 64
distinct "flavours" of copy that a user might see together. Embedding each
combination captures the joint semantics of that pairing — which is more
useful for similarity search and quality ranking than embedding each slot in
isolation.

For Google RSA ads, only `headline` and `description` slots are combined (no `primary_text`). A RSA ad with 10 headlines × 4 descriptions produces 40 combination rows. The `combination_key` format and storage schema are identical regardless of which slots are active.

## What it does

1. **Enumerate** — Build the Cartesian product of all text slot values.
   A `{"headline": "Wool Socks", "primary_text": "Hand made in Switzerland.",
   "description": "Free shipping."}` dict is produced for every combination.
2. **Embed** — Serialize each combination as a compact JSON string and call
   the Azure AI Inference text embedding endpoint. All combinations run
   concurrently, gated by a semaphore (default 10 parallel calls).
3. **Store** — Upsert each vector into `ad_text_combination_embeddings`,
   keyed on `(source_id, combination_key)`. Running twice is safe.

## File reference

| File | Role |
|---|---|
| `combinations.py` | Pure functions — `build_combinations()`, `combination_key()`, `combination_count()` |
| `storage.py` | `ad_text_combination_embeddings` table; `save_embeddings_batch()`, `get_embeddings_for_source()` |
| `pipeline.py` | `embed_all_combinations()` — async top-level orchestrator |

## Public API

```python
from ad_combination_embeddings import (
    embed_all_combinations,       # async — embeds & stores all combinations
    get_embeddings_for_source,    # retrieve all stored embeddings for a source_id
    count_embeddings_for_source,  # count without fetching vectors
    combination_count,            # dry-run: how many combos will be produced?
)

# How many combinations?
n = combination_count(components)   # e.g. 64 for a 4×4×4 dynamic ad

# Embed and store — Meta (default slots: headline × primary_text × description)
stored = await embed_all_combinations(
    source_id="my_ad_001",   # any string the caller chooses
    components=components,   # list of {slot, slot_index, value} dicts
)

# Google RSA — headline × description only
stored = await embed_all_combinations(
    source_id="google_ad_001",
    components=components,
    slots=("headline", "description"),
)

# Retrieve
rows = get_embeddings_for_source("my_ad_001")
# Each row:
# {
#   "combination_key": '{"description":"Free shipping.","headline":"Wool Socks",...}',
#   "combination":     {"headline": "Wool Socks", "primary_text": "...", "description": "..."},
#   "vector":          np.ndarray (float32),
#   "model":           "embed-v-4-0",
#   "embedded_at":     "2026-04-18T12:00:00"
# }
```

## Combination format

Each combination is a plain `dict[str, str]` — one value per text slot.
The `combination_key` is `json.dumps(combo, sort_keys=True)` — deterministic
and used as the unique key in the DB.

Text in JSON is what gets embedded: `'{"description":"Free shipping.","headline":"Wool Socks","primary_text":"Hand made in Switzerland."}'`

## DB table

`ad_text_combination_embeddings` — no metadata at this layer.

| Column | Notes |
|---|---|
| `source_id` | Caller-provided identifier; retrieval key |
| `combination_key` | Deterministic JSON of the combination; with `source_id` forms UNIQUE constraint |
| `vector` | `float32` bytes (`numpy.ndarray.tobytes()`) |
| `model` | Embedding model name |

## Reusing the embedder

This module reuses `embeddings.embedder.embed_text` directly — same
Azure AI Inference endpoint, same env vars.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `AZURE_INFERENCE_KEY` | — | Required |
| `AZURE_ENDPOINT` | project resource URL | Azure AI Inference endpoint |
| `AZURE_TEXT_MODEL` | `embed-v-4-0` | Model used for text embedding |

## Running tests

```bash
cd backend
source .venv/bin/activate

# Pure combinatorics tests — no API keys needed
python -m pytest tests/test_combination_embeddings.py -v -k "TestBuildCombinations or TestCombinationKey"

# Integration tests — AZURE_INFERENCE_KEY must be set
python -m pytest tests/test_combination_embeddings.py -v
```

# Ad Text Generation Pipeline

Part of the AdStac.kr standalone module suite. Generates new ad copy variants
for each text slot in a dynamic ad, given a seed ad's existing components.
Fully independent — no dependency on the FastAPI app or any metadata layer.

## What it does

Given a seed ad's component list (one or more values per slot):

1. **Generate** — For each text slot appropriate to the platform, call GPT-4o with the
   existing seed values as context and produce N new variants in the same tone and style.
   All slots run in parallel.
   - **Meta slots:** `headline`, `primary_text`, `description`, `cta`
   - **Google RSA slots:** `headline`, `description` only (with 30-char / 90-char limits)
2. **Assemble** — Merge seed values + generated values + optional image URLs
   into a single dynamic ad component list. Seed values keep their original
   slot indices; generated values extend from there.
3. **Store** — Persist the assembled component list to `generated_ads` /
   `generated_ad_slots` tables. Returns the `generated_ad_id`.

## File reference

| File | Role |
|---|---|
| `prompts.py` | `GENERATE_SLOT_VARIANTS` prompt + `SLOT_HINTS` per slot type; `GOOGLE_RSA_SLOT_HINTS` with Google character limits |
| `generator.py` | `TEXT_SLOTS` (Meta); `GOOGLE_RSA_SLOTS = ('headline', 'description')`; `slots_for_platform(platform)`; `generate_slot_variants(slot, existing_values, n, platform)` and `generate_all_slots(seed_components, n_per_slot, platform)` |
| `assembler.py` | `assemble_dynamic_ad(seed_components, generated_text, image_urls)` → component list |
| `storage.py` | `generated_ads` + `generated_ad_slots` tables; `save_generated_ad()`, `get_generated_ad()`, `get_generated_slots_for_source(source_ad_id)` |
| `pipeline.py` | `run_text_pipeline(...)` — top-level orchestrator |

## Public API

```python
from ad_text_generation import run_text_pipeline, get_generated_ad, get_generated_ad_meta

# Meta (default) — generates headline, primary_text, description, cta
generated_ad_id = await run_text_pipeline(
    seed_components=[...],   # {slot, slot_index, value} dicts
    n_per_slot=5,            # new variants per text slot
    image_urls=[...],        # optional: from image generation pipeline
    source_ad_id="ad_123",  # optional: for traceability
    platform="meta",         # default; also accepts 'google'
)

# Google RSA — generates headline and description only, with 30/90 char limits
generated_ad_id = await run_text_pipeline(
    seed_components=[...],
    n_per_slot=10,
    source_ad_id="google_ad_123",
    platform="google",
)

slots = get_generated_ad(generated_ad_id)
# [{"slot": "headline", "slot_index": 0, "value": "...", "source": "seed"}, ...]
```

### Platform-aware slot selection

```python
from ad_text_generation.generator import slots_for_platform

slots_for_platform("meta")    # ('headline', 'primary_text', 'description', 'cta')
slots_for_platform("google")  # ('headline', 'description')
slots_for_platform("other")   # ('headline', 'primary_text', 'description', 'cta')  — Meta fallback
```

## Component format

Same as `ad_creative_structures` rows, with an added `source` field:

| `source` | Meaning |
|---|---|
| `seed` | Carried from the seed ad unchanged |
| `generated_text` | New variant produced by the text generator |
| `generated_image` | Image URL from the image generation pipeline |

## Slot index assignment

Seed values keep their original `slot_index` values. Generated values start
from `max(seed slot_index) + 1`. Example with 2 seed headlines + 5 generated:

```
headline slot_index 0  → seed ("Wool Socks")
headline slot_index 1  → seed ("Warm Feet")
headline slot_index 2  → generated_text
headline slot_index 3  → generated_text
...
headline slot_index 6  → generated_text
```

## DB tables

`generated_ads` and `generated_ad_slots` — no metadata attached at this layer.
The outer application is responsible for linking a `generated_ad_id` to a
campaign, adset, or user. See `SCHEMAS.md` at the repo root for full column
definitions.

## Combining with the image pipeline

Pass `image_urls` from `ad_generation.get_active_variants()` to replace
the seed image slots with AI-generated images:

```python
from ad_generation import get_active_variants
from ad_text_generation import run_text_pipeline

variants = get_active_variants(job_id, db_path)
image_urls = [
    f"{base_url}/{v['local_filename']}"
    for v in variants
    if v.get("local_filename")
]

generated_ad_id = await run_text_pipeline(
    seed_components=seed_components,
    n_per_slot=5,
    image_urls=image_urls,
)
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `AZURE_OPENAI_KEY` | — | Required |
| `AZURE_OPENAI_ENDPOINT` | — | Required |
| `AZURE_OPENAI_API_VERSION` | `2024-12-01-preview` | |
| `AZURE_TEXT_GEN_DEPLOYMENT` | `gpt-4.1-nano` | Deployment used for text generation |

## Running tests

```bash
cd backend
source .venv/bin/activate
python -m pytest tests/test_text_pipeline.py -v          # integration (API keys required)
python -m pytest tests/test_google_text_pipeline.py -v  # pure (no API keys needed)
```

`test_text_pipeline.py` makes real Azure OpenAI API calls — env vars above must be set.
`test_google_text_pipeline.py` is pure — mocks the LLM and tests slot filtering logic.

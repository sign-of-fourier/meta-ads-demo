# Test Catalog

All test files live in `backend/tests/`. Run from `backend/` with the virtual environment active.

```bash
cd backend
source .venv/bin/activate
```

---

## Pure tests — no API keys needed

### `tests/test_structural_ingest.py`
Unit and integration tests for structural creative ingestion.

- `_normalize_creative` — static ads (headline, primary_text, image slots)
- `_normalize_creative` — dynamic ads (asset_feed_spec decomposition)
- `POST /api/ingest/structure/{campaign_id}` — single static ad, single dynamic ad, multiple ads
- Idempotent upsert — reingest does not duplicate rows
- Lifecycle status — `active` / `inactive` derived from `effective_status`
- Missing-ad detection — ads absent from latest fetch get `lifecycle_status = 'missing'`
- `POST /api/ingest` metrics snapshot — campaigns saved, zero-metrics message, insights error surfacing

```bash
python -m pytest tests/test_structural_ingest.py -v
```

---

### `tests/test_suggestions.py`
Unit and integration tests for the suggestion scaffolding and confirm-create flow.

- `POST /api/suggestions` — store a suggestion linked to a source dynamic template
- `POST /api/suggestions/{id}/confirm` with `action=create` — transitions to `created_static`, records `static_ad_id`
- `POST /api/suggestions/{id}/confirm` with `action=replace` — transitions to `pending_confirmation`
- Cross-user isolation — confirm returns 404 for another user's suggestion
- Invalid action and terminal-status guard — 400 returned
- Meta API failure on create — status preserved, error surfaced
- `GET /api/suggestions` — filtered by campaign, unfiltered list

```bash
python -m pytest tests/test_suggestions.py -v
```

---

### `tests/test_bo_pipeline.py`
Tests for `ad_embedding_combiner` and `bo_pipeline`. No Azure API calls — all embeddings are pre-seeded synthetic numpy vectors.

- `TestCombineEmbeddings` — truncate/pad, combine, output dimension checks
- `TestGPR` — fit, predict_with_std, expected_improvement, fantasize (pure numpy/sklearn)
- `TestSelector` — scored combinations, candidate combinations, per-image embeddings, defunct exclusion
- `TestBOPipeline` — full end-to-end BO run with pre-seeded DB; EI pick, fantasy pick, persistence, retrieval

```bash
python -m pytest tests/test_bo_pipeline.py -v
```

Pure combinatorics subset (no DB):

```bash
python -m pytest tests/test_bo_pipeline.py -v -k "TestCombineEmbeddings or TestGPR"
```

---

### `tests/test_combination_embeddings.py`
Tests for `ad_combination_embeddings`. Pure classes run without API keys; integration classes require Azure.

- `TestBuildCombinations` — Cartesian product construction, slot-order invariance, empty-slot handling *(no API)*
- `TestCombinationKey` — determinism, key stability, order-independence *(no API)*
- `TestEmbedAllCombinations` — real Azure embeddings stored in temp DB *(requires `OPENAI_KEY`)*
- `TestRetrieval` — `get_embeddings_for_source` shape and content *(requires `OPENAI_KEY`)*

```bash
# Pure only
python -m pytest tests/test_combination_embeddings.py -v -k "TestBuildCombinations or TestCombinationKey"

# Full (API keys required)
python -m pytest tests/test_combination_embeddings.py -v
```

---

## Integration tests — API keys required

### `tests/test_generation_pipeline.py`
End-to-end tests for the image ad generation + embedding pipeline. Requires Azure OpenAI, deAPI, and Azure AI Inference keys. Spins up a local HTTP server to serve generated images for scoring/QA.

- Seed ad embedding → `ad_embeddings` row with correct metadata
- Full generation pipeline → job reaches `done`, variants scored and QA'd
- Active pool filtering → `get_active_variants` excludes defunct variants
- Generated variant embedding → `ad_embeddings` row links back to campaign
- Metadata chain integrity — SQL walk from campaign → job → variants → embeddings

```bash
python -m pytest tests/test_generation_pipeline.py -v
```

---

### `tests/test_text_pipeline.py`
Integration tests for the ad text generation pipeline. Requires Azure OpenAI.

- `generate_slot_variants` — correct count of strings per slot
- `generate_all_slots` — all slots produced in parallel
- `assemble_dynamic_ad` — slot indexing, source labels, image URL handling
- `run_text_pipeline` — full round-trip including DB storage
- Seed-only images — `image_urls=None` preserves seed image slots
- Combined pipeline — text pipeline receiving image URLs

```bash
python -m pytest tests/test_text_pipeline.py -v
```

---

## Standalone scripts (not pytest)

These are run directly with Python, not via pytest. They require API keys and a loaded `.env`.

### `tests/test_embed_text.py`
Quick smoke test for `embed_ad` — embeds a hardcoded ad with a real CDN image URL. Prints the result.

```bash
python tests/test_embed_text.py
```

### `tests/test_finetune_embed.py`
Exploratory script confirming that GPT-based fine-tunes do **not** support the `/embeddings` endpoint. Documents why `embed-v-4-0` on Azure AI Inference is used instead. Safe to run as documentation; will print errors by design.

```bash
python tests/test_finetune_embed.py
```

---

## Known pre-existing failures

Three tests in `test_structural_ingest.py` fail due to a logic mismatch in the `/api/ingest` route (campaigns are saved regardless of whether metrics exist). These are not related to the provider layer and predate Chunk 1 of the Google integration work:

- `test_ingest_zero_metrics_clear_message`
- `test_ingest_insights_error_surfaces_in_response`
- `test_ingest_partial_metrics_only_saves_campaigns_with_data`

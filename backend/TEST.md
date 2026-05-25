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

### `tests/test_modal_bo.py`
Tests for `bo_pipeline.modal_bo` — PCA helpers, snap logic, and the live Modal GP endpoint.

- `TestModalBOUnit` — `fit_pca` (shape, cap at n_samples/n_features), `project`, `dim_bounds`, `snap_to_pool` (correct indices, dedup, pool exhaustion), env-var reading for `MODAL_BO_API_URL` / `MODAL_BO_PCA_DIMS`. No network calls.
- `TestModalBOLive` — calls the real Modal GP endpoint with synthetic 256-dim embeddings; verifies response shape, bounds compliance, and end-to-end snap to distinct candidates. Skipped if `MODAL_BO_API_URL` is unset; defaults to the production endpoint if unset but the class is selected directly.

```bash
# Unit tests only (no network)
python -m pytest tests/test_modal_bo.py -v -k "TestModalBOUnit"

# Live smoke test (requires running Modal deployment, ~30s)
python -m pytest tests/test_modal_bo.py::TestModalBOLive -v -s
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

## Google integration tests — no API keys needed

All Google test files are pure (no live API calls) and use the same `tmp_db` / `TestClient` / monkeypatched-env pattern as the Meta test suite.

### `tests/test_google_db.py`
Schema migration tests.

- Fresh DB has `google_connections`, `google_pending_connections` tables and all new columns (`platform` on `ad_insights`/`ad_creative_structures`, `provider` on `oauth_states`, `tier` on `users`)
- `google_connections` schema matches spec
- Migrations are idempotent on both fresh and old-schema DBs

```bash
python -m pytest tests/test_google_db.py -v
```

---

### `tests/test_google_auth.py`
Google OAuth flow.

- `GET /me/google-status` — connected / not connected
- `GET /auth/google/login-url` — URL shape, state stored with `provider='google'`
- `GET /auth/google/callback` — writes `google_pending_connections` row, redirects with `?google_pick=`
- `GET /auth/google/callback` — error redirect on bad state
- `GET /auth/google/pending/{key}` — returns accounts list; validates user_id
- `POST /auth/google/select-account` — writes `google_connections`, deletes pending row
- `_google_creds` — raises 400 when not connected; refreshes token correctly; returns 3-tuple

```bash
python -m pytest tests/test_google_auth.py -v
```

---

### `tests/test_google_campaigns.py`
Campaign list normalization and route.

- Status normalization: `ENABLED` → `ACTIVE`
- Budget micros → cents conversion
- Metrics normalization (ctr decimal → %, spend/cpm/cpc micros → dollars)
- Zero-delivery campaigns produce `None` ratios
- Multi-row aggregation (multiple GAQL rows per campaign)
- Empty results
- Not-connected 400
- Live smoke test (skipped unless real credentials configured)

```bash
python -m pytest tests/test_google_campaigns.py -v
```

---

### `tests/test_google_structural_ingest.py`
Creative normalization and ingest route.

- `normalize_creative` — RSA, display, video, unknown, pMax (text/image/video assets), Shopping
- Ingest route: row counts, `platform='google'` column, active/inactive lifecycle, idempotency, missing detection
- `GET /api/google/structure/{campaign_id}` — returns data, empty before ingest, platform isolation from Meta rows, auth guards, not-connected 400

```bash
python -m pytest tests/test_google_structural_ingest.py -v
```

---

### `tests/test_google_text_pipeline.py`
Platform-aware RSA text generation.

- `slots_for_platform('google')` returns `('headline', 'description')` only
- Route uses `platform='google'` slot filter
- 404/400/200 route behaviour
- `seed_ad_id` query param handling
- Platform isolation (Google route only reads `platform='google'` rows)

```bash
python -m pytest tests/test_google_text_pipeline.py -v
```

---

### `tests/test_google_bo.py`
Embeddings + BO for Google RSA.

- Combiner `image_vec=None` pads the image half with zeros (3 tests)
- `youtube_thumbnail_url` utility (2 tests)
- Ingest embedding hook fires `embed_ad` + `embed_all_combinations` after commit
- BO route auth guards (2 tests)
- BO run shape, picks, empty-result fallback (3 tests)
- BO results retrieval (2 tests)

```bash
python -m pytest tests/test_google_bo.py -v
```

---

### `tests/test_google_push.py`
Push BO-selected RSA ad to Google Ads.

- Auth guard
- No unpushed picks → empty results
- Already-pushed ad skipped (no-op)
- Correct RSA request shape (headlines/descriptions lists)
- BO pick pinned first in headlines/descriptions
- `google_ad_resource_name` written to `bo_selections` on success
- Missing `final_url` → graceful per-ad error
- Not enough variants (< 3 headlines or < 2 descriptions) → graceful error
- Mutate API error → graceful per-ad error
- User isolation (other users' picks not pushed)
- Not-connected → `note` field in response

```bash
python -m pytest tests/test_google_push.py -v
```

---

### `tests/test_google_pmax_shopping.py`
Creative type guards on text generation and BO.

- 400 for Shopping campaign at text generation endpoint
- 400 for unknown creative type at text generation endpoint
- pMax proceeds through text generation (it has headline/description slots)
- 400 for Shopping campaign at BO endpoint
- 400 for unknown creative type at BO endpoint
- pMax proceeds through BO

```bash
python -m pytest tests/test_google_pmax_shopping.py -v
```

---

### `tests/test_google_demo.py`
Demo and masking layer.

- `GoogleDemoProvider`: platform name, campaigns shape, metrics, all-active status, RSA structure, unknown-campaign empty, fetch_ads count, pause/resume no-ops, normalize_creative delegates
- `GoogleMaskPolicy`: default off, full mode, individual flags, metric profiles, invalid profile fallback
- `GoogleMaskingProvider`: mask status forces ACTIVE, mask budgets replaces value, mask metrics for low-delivery, mask metrics preserves healthy, pause no-op when masked, pause delegates when unmasked
- `get_google_provider()` factory: demo via `APP_MODE`, demo via `GOOGLE_APP_MODE`, masking when policy enabled, live provider by default

```bash
python -m pytest tests/test_google_demo.py -v
```

---

### `tests/test_google_login_customer_id.py`
Login customer ID threading through the stack.

- Ingest structure route passes `login_customer_id` from DB
- Ingest structure route passes `None` when no MCC
- Google push route passes `login_customer_id` to Mutate API
- Google push route passes `None` for direct accounts
- `query_gaql` sets `login-customer-id` header for MCC
- `query_gaql` omits header for direct accounts
- `create_rsa` sets `login-customer-id` header for MCC
- `create_rsa` omits header when `login_customer_id` is None
- Text generation and BO routes do not call `_google_creds` (they operate on already-ingested data)

```bash
python -m pytest tests/test_google_login_customer_id.py -v
```

---

### Run all Google tests at once

```bash
python -m pytest tests/test_google_db.py tests/test_google_auth.py tests/test_google_campaigns.py tests/test_google_structural_ingest.py tests/test_google_text_pipeline.py tests/test_google_bo.py tests/test_google_push.py tests/test_google_pmax_shopping.py tests/test_google_demo.py tests/test_google_login_customer_id.py -v
```

---

## Integration tests — API keys required

### `tests/test_generation_pipeline.py`
End-to-end tests for the image ad generation + embedding pipeline. Requires Azure OpenAI, deAPI, Azure AI Inference keys, and the Modal scoring endpoint (no key needed — public endpoint). Spins up a local HTTP server to serve generated images for scoring/QA.

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
**Obsolete.** Historical exploratory script that confirmed GPT-based fine-tunes do not support the `/embeddings` endpoint. The fine-tuned scorer has since moved to a Modal-hosted Qwen2-VL model (`scorer.py`); this file still references `AZURE_SCORING_DEPLOYMENT` and is kept for reference only.

```bash
python tests/test_finetune_embed.py
```

---

## Known pre-existing failures

Three tests in `test_structural_ingest.py` fail due to a logic mismatch in the `/api/ingest` route (campaigns are saved regardless of whether metrics exist). These predate the Google integration work and remain unresolved:

- `test_ingest_zero_metrics_clear_message`
- `test_ingest_insights_error_surfaces_in_response`
- `test_ingest_partial_metrics_only_saves_campaigns_with_data`

All Google test files pass cleanly (1 skipped: the live smoke test in `test_google_campaigns.py` which requires real credentials).

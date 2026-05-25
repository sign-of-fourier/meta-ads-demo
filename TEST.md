# AdStac.kr — Test Catalog

All pytest tests live in `backend/`. Run them from there with the venv active:

```bash
cd backend && source .venv/bin/activate
```

---

## Pytest test files

All test files live in `backend/tests/`. Run from `backend/` with the venv active. See `backend/TEST.md` for a full catalog with per-test descriptions.

### No API keys required — Meta

| File | Classes | Tests | What it covers |
|---|---|---|---|
| `tests/test_structural_ingest.py` | — | ~10 | Structural ingest normalization, lifecycle status logic |
| `tests/test_suggestions.py` | — | ~10 | Suggestion storage and retrieval |
| `tests/test_combination_embeddings.py` | `TestBuildCombinations`, `TestCombinationKey` | 16 | Cartesian combination logic, key determinism |
| `tests/test_bo_pipeline.py` | `TestCombineEmbeddings`, `TestGPR`, `TestSelector`, `TestBOPipeline` | 34 | Embedding combiner, GPR functions, DB selector, full BO run |

```bash
python -m pytest tests/test_structural_ingest.py tests/test_suggestions.py \
    tests/test_combination_embeddings.py -k "TestBuildCombinations or TestCombinationKey" \
    tests/test_bo_pipeline.py -v
```

### No API keys required — Google

| File | Tests | What it covers |
|---|---|---|
| `tests/test_google_db.py` | 6 | Schema migrations, new tables |
| `tests/test_google_auth.py` | 7+ | OAuth flow, account picker, `_google_creds` |
| `tests/test_google_campaigns.py` | 9 + 1 skipped | Campaign normalization, metrics, route |
| `tests/test_google_structural_ingest.py` | 18 | Creative normalization (RSA/display/video/pMax/Shopping), ingest route |
| `tests/test_google_text_pipeline.py` | 10 | Platform-aware slot filter, RSA text generation route |
| `tests/test_google_bo.py` | 12 | Combiner NULL-image handling, BO routes |
| `tests/test_google_push.py` | 11 | RSA push route, Mutate API shape, resource name persistence |
| `tests/test_google_pmax_shopping.py` | 6 | 400 guards for unsupported creative types |
| `tests/test_google_demo.py` | 26 | Demo provider, masking layer, factory selection |
| `tests/test_google_login_customer_id.py` | 10 | MCC `login-customer-id` threading through all routes |

```bash
python -m pytest tests/test_google_db.py tests/test_google_auth.py tests/test_google_campaigns.py tests/test_google_structural_ingest.py tests/test_google_text_pipeline.py tests/test_google_bo.py tests/test_google_push.py tests/test_google_pmax_shopping.py tests/test_google_demo.py tests/test_google_login_customer_id.py -v
```

### API keys required

| File | Classes | Tests | Keys needed | What it covers |
|---|---|---|---|---|
| `tests/test_combination_embeddings.py` | `TestEmbedAllCombinations`, `TestRetrieval` | 15 | `AZURE_INFERENCE_KEY` | Embed real text combos, store/retrieve vectors |
| `tests/test_text_pipeline.py` | `TestAssembleDynamicAd`, `TestGenerateSlots`, `TestRunTextPipeline`, `TestRetrieval` | ~20 | `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT` | GPT-4o text generation, assembler, DB storage |
| `tests/test_generation_pipeline.py` | `TestSeedEmbedding`, `TestGenerationPipeline`, `TestGeneratedVariantEmbedding`, `TestMetadataChain` | ~20 | `AZURE_INFERENCE_KEY`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT`, `DEAPI_API_KEY` | Full 7-step image pipeline end-to-end |

---

## Notable individual tests

### `test_bo_pipeline.py::TestBOPipeline::test_full_bo_report`

The most informative single test to run. Executes the full BO pipeline and prints a
human-readable report: scored observations, GPR fit, EI ranking over all candidates,
pick 1 (EI), pick 2 (fantasy), and a summary row.

```bash
python -m pytest tests/test_bo_pipeline.py::TestBOPipeline::test_full_bo_report -v -s
```

Example output:
```
============================================================
  AdStac.kr BO Run — Full Report
============================================================

Training set  : 5 scored observation(s)
Candidate pool: 7 unscored combination(s)

Scored observations:
  [1] score=3.50  combo={'headline': 'Wool Socks', 'primary_text': 'Hand made in Switzerland.'}
  ...

GPR fit on 5 point(s)  |  best observed score: 5.10
Optimized kernel: 0.949**2 * RBF(length_scale=1) + WhiteKernel(noise_level=0.099)

PICK 1  [EI]
  Combination : {'headline': 'Wool Socks', 'primary_text': 'Premium wool since 1952.'}
  GPR mean    : 4.0600  |  GPR std: 0.8261  |  EI: 0.039983

PICK 2  [FANTASY]
  Combination : {'headline': 'Warm Feet Forever', 'primary_text': 'Hand made in Switzerland.'}
  GPR mean    : 4.0600  |  GPR std: 0.7541  |  EI: 0.028118

Summary
  n_scored=5  n_fit=5  n_candidates=7  best_obs=5.10
```

**Note on uniform EI:** the test seeds all embeddings as random vectors, so all
candidates sit equidistant from the training set in embedding space and share
identical EI. With real Azure embeddings, semantically similar candidates will
differentiate. The fantasy step is still exercised correctly — σ drops from
0.826 → 0.754 on pick 2.

---

## Module-level test docs

Each AI module has its own README with test-specific instructions and env var
requirements:

| Module | README |
|---|---|
| Embeddings | `backend/embeddings/README.md` |
| Image generation | `backend/ad_generation/README.md` |
| Text generation | `backend/ad_text_generation/README.md` |
| Combination embeddings | `backend/ad_combination_embeddings/README.md` |

For the full per-test breakdown including all Google test files, see `backend/TEST.md`.

---

## Manual / curl tests — Masking layer

For verifying the masking layer behaviour against a live Meta account.

```bash
export API="http://localhost:8000"
export TOKEN="<paste JWT here>"   # from POST /auth/login
```

### Campaigns

```bash
curl -s "$API/api/campaigns" -H "Authorization: Bearer $TOKEN" | jq
```

Run twice and diff to verify determinism when masking is on:

```bash
curl -s "$API/api/campaigns" -H "Authorization: Bearer $TOKEN" | jq > /tmp/c1.json
curl -s "$API/api/campaigns" -H "Authorization: Bearer $TOKEN" | jq > /tmp/c2.json
diff /tmp/c1.json /tmp/c2.json   # should be empty
```

### Sync campaigns (ingest metric snapshots)

```bash
curl -s -X POST "$API/api/ingest" -H "Authorization: Bearer $TOKEN" | jq
```

### Ingest creative structure for a campaign

```bash
export CAMPAIGN_ID="<id from /api/campaigns>"
curl -s -X POST "$API/api/ingest/structure/$CAMPAIGN_ID" -H "Authorization: Bearer $TOKEN" | jq
```

### Verify embeddings after structural ingest

After clicking "Ingest" on a campaign (or calling the endpoint above), the embedding
tasks run in the background. Wait a few seconds, then confirm:

```bash
# Seed embedding per ad — has_text and has_image should both be 'yes' when keys are set
sqlite3 backend/app.db "SELECT ad_id, text_snapshot, CASE WHEN text_vector IS NULL THEN 'no' ELSE 'yes' END as has_text, CASE WHEN image_vector IS NULL THEN 'no' ELSE 'yes' END as has_image FROM ad_embeddings;"

# Check combined vector dimension (expect 256: TEXT_DIM=128 + IMAGE_DIM=128)
sqlite3 backend/app.db "SELECT ad_id, LENGTH(combined_vector) / 8 as combined_dim FROM ad_embeddings;"

# Per-image-slot embeddings
sqlite3 backend/app.db "SELECT ad_id, slot_index, image_ref, CASE WHEN vector IS NULL THEN 'no' ELSE 'yes' END as embedded FROM ad_image_embeddings;"

# Text combination embeddings — a 4×4×4 dynamic ad produces 64 rows
sqlite3 backend/app.db "SELECT source_id, COUNT(*) as combinations FROM ad_text_combination_embeddings GROUP BY source_id;"
```

If `has_image` is `no` or `ad_image_embeddings` is empty, `AZURE_INFERENCE_KEY` is
likely missing or wrong. If `has_text` is `no`, check `OPENAI_KEY`. Once keys are
fixed, hit "Reingest" — the backend skips already-complete embeddings and only
re-runs what failed.

### Pause / Resume

```bash
export CAMPAIGN_ID="<id from /api/campaigns>"
curl -s -X POST "$API/api/campaigns/$CAMPAIGN_ID/pause"  -H "Authorization: Bearer $TOKEN" | jq
curl -s -X POST "$API/api/campaigns/$CAMPAIGN_ID/resume" -H "Authorization: Bearer $TOKEN" | jq
```

With `MASK_PAUSE_RESUME=true` both return success without hitting Meta.

### Reference env configs

| Scenario | Env vars |
|---|---|
| Real baseline | `APP_MODE=live MASK_MODE=off` |
| Full selective mask | `MASK_MODE=selective MASK_STATUS=true MASK_BUDGETS=true MASK_METRICS=true MASK_PAUSE_RESUME=true MASK_AD_STATUSES=true METRIC_PROFILE=healthy` |
| Weak delivery story | `MASK_MODE=selective MASK_STATUS=true MASK_METRICS=true METRIC_PROFILE=weak` |
| Full demo (no Meta) | `APP_MODE=demo` |

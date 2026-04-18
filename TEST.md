# AdStac.kr — Test Catalog

All pytest tests live in `backend/`. Run them from there with the venv active:

```bash
cd backend && source .venv/bin/activate
```

---

## Pytest test files

### No API keys required

| File | Classes | Tests | What it covers |
|---|---|---|---|
| `test_structural_ingest.py` | — | ~10 | Structural ingest normalization, lifecycle status logic |
| `test_suggestions.py` | — | ~10 | Suggestion storage and retrieval |
| `test_combination_embeddings.py` | `TestBuildCombinations`, `TestCombinationKey` | 16 | Cartesian combination logic, key determinism |
| `test_bo_pipeline.py` | `TestCombineEmbeddings`, `TestGPR`, `TestSelector`, `TestBOPipeline` | 34 | Embedding combiner, GPR functions, DB selector, full BO run |

Run all pure tests in one shot:

```bash
python -m pytest test_structural_ingest.py test_suggestions.py \
    test_combination_embeddings.py -k "TestBuildCombinations or TestCombinationKey" \
    test_bo_pipeline.py -v
```

### API keys required

| File | Classes | Tests | Keys needed | What it covers |
|---|---|---|---|---|
| `test_combination_embeddings.py` | `TestEmbedAllCombinations`, `TestRetrieval` | 15 | `AZURE_INFERENCE_KEY` | Embed real text combos, store/retrieve vectors |
| `test_text_pipeline.py` | `TestAssembleDynamicAd`, `TestGenerateSlots`, `TestRunTextPipeline`, `TestRetrieval` | ~20 | `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT` | GPT-4o text generation, assembler, DB storage |
| `test_generation_pipeline.py` | `TestSeedEmbedding`, `TestGenerationPipeline`, `TestGeneratedVariantEmbedding`, `TestMetadataChain` | ~20 | `AZURE_INFERENCE_KEY`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_ENDPOINT`, `DEAPI_API_KEY` | Full 7-step image pipeline end-to-end |

---

## Notable individual tests

### `test_bo_pipeline.py::TestBOPipeline::test_full_bo_report`

The most informative single test to run. Executes the full BO pipeline and prints a
human-readable report: scored observations, GPR fit, EI ranking over all candidates,
pick 1 (EI), pick 2 (fantasy), and a summary row.

```bash
python -m pytest test_bo_pipeline.py::TestBOPipeline::test_full_bo_report -v -s
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

### Ingest

```bash
curl -s "$API/api/ingest/preview" -H "Authorization: Bearer $TOKEN" | jq
curl -s -X POST "$API/api/ingest" -H "Authorization: Bearer $TOKEN" | jq
```

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

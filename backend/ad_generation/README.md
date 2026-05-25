# Ad Generation Pipeline

Standalone async module for AI-driven ad image generation, QA, and scoring.
No dependency on the FastAPI app — can be imported, run, and tested independently.

## What it does

Given a seed ad (image URL + headline + short text) tied to a campaign/adset:

1. **Analyze** — GPT-4o inspects the seed image and returns 10 short edit suggestions
2. **Generate** — Each suggestion is submitted to deAPI FLUX img2img; returns async `request_id`s
3. **Poll** — Each job is polled until done
4. **Save** — Result images are downloaded and saved locally under `backend/generated_images/`
5. **Score** — Each saved image is scored by the fine-tuned Qwen2-VL model on Modal (0–1, lower = better ad)
6. **QA check** — GPT-4o inspects each scored image for severe defects only (extra limbs, melted product, large gibberish text)
7. **Correct** — Flagged images are patched with another img2img pass (low strength, targeted prompt); the original is marked `defunct` and replaced by the corrected child

## File reference

| File | Role |
|---|---|
| `prompts.py` | All LLM prompts — `SUGGEST_EDITS`, `CHECK_ARTIFACTS`, `SCORE_CREATIVE` |
| `analyzer.py` | Step 1 — `analyze_image(image_url, headline, short_text) → list[str]` |
| `generator.py` | Steps 2 & 7 — `submit_generation(seed_image_url, prompt, ...) → request_id` |
| `poller.py` | Steps 3 & 4 — `poll_until_done(request_id)`, `save_image_locally(url)` |
| `scorer.py` | Step 5 — `score_variant(image_url, headline, short_text) → ScoreResult` |
| `qa_checker.py` | Step 6 — `check_for_artifacts(image_url) → QAResult` |
| `storage.py` | DB schema + CRUD for `ad_generation_jobs` and `ad_generation_variants` |
| `pipeline.py` | Orchestrator — public API below |

## Public API

```python
from ad_generation import (
    create_job,           # create DB record, return job_id
    run_generation_job,   # async; all 7 steps; fire-and-forget safe
    get_job_status,       # dict with status, suggestions, error
    get_job_variants,     # all variant rows including defunct
    get_active_variants,  # variant rows where status != 'defunct'
)
```

### Typical usage

```python
import asyncio
from ad_generation import create_job, run_generation_job

job_id = create_job(
    user_id=1,
    campaign_id="123456789",
    adset_id="987654321",
    seed_ad_id="111222333",                         # optional
    seed_image_url="https://example.com/ad.png",
    headline="My Product",
    short_text="Best product ever.",
)

# Fire and forget from an async context
asyncio.create_task(run_generation_job(job_id))

# Or run synchronously
asyncio.run(run_generation_job(job_id))
```

## Job status lifecycle

```
pending → analyzing → generating → polling → scoring → qa → correcting → done
                                                                        → failed
```

## Variant status lifecycle

```
submitted → done → scored → qa_status=passed              ← in active pool
                          → qa_status=flagged, status=defunct
                                     └─▶ child variant (parent_variant_id set):
                                         submitted → done → scored → qa_status=passed
```

## DB tables

See `SCHEMAS.md` at the repo root for full column definitions.

- `ad_generation_jobs` — one row per pipeline run
- `ad_generation_variants` — one row per generated image; `parent_variant_id` links corrections to their defunct originals

### Key query — active scored variants for a campaign

```sql
SELECT v.suggestion, v.local_filename, v.score, v.severity, v.score_labels
FROM ad_generation_jobs j
JOIN ad_generation_variants v ON v.job_id = j.id
WHERE j.user_id = ?
  AND j.campaign_id = ?
  AND v.status != 'defunct'
  AND v.status = 'scored'
ORDER BY v.score ASC;
```

## Image serving

Saved images live in `backend/generated_images/`. The FastAPI app mounts this directory at `GET /images/{filename}` via `StaticFiles`. The `IMAGES_SERVE_BASE_URL` env var tells the pipeline what base URL to use when passing images to the scoring and QA models.

For tests, override both `GENERATED_IMAGES_DIR` and `IMAGES_SERVE_BASE_URL` — see `test_generation_pipeline.py`.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `AZURE_OPENAI_KEY` | — | Required |
| `AZURE_OPENAI_ENDPOINT` | — | Required (e.g. `https://your-resource.openai.azure.com/`) |
| `AZURE_OPENAI_API_VERSION` | `2024-12-01-preview` | |
| `AZURE_ANALYSIS_DEPLOYMENT` | `gpt-4.1-nano` | Used for analysis (step 1) and QA (step 6) |
| `MODAL_SCORING_ENDPOINT` | `https://markshipman4273--bad-ads-qwen2vl-badadsmodel-web.modal.run/predict` | Fine-tuned Qwen2-VL scorer on Modal (step 5) |
| `DEAPI_API_KEY` | — | Required for generation (steps 2 & 7) |
| `IMAGES_SERVE_BASE_URL` | `http://localhost:8000/images` | Base URL for scoring/QA image access |
| `GENERATED_IMAGES_DIR` | `backend/generated_images/` | Override image save directory (useful for tests) |

## Running tests

```bash
cd backend
source .venv/bin/activate
python -m pytest test_generation_pipeline.py -v
```

Tests make real API calls — all env vars above must be set. A test user and test DB are created in a temp directory automatically.

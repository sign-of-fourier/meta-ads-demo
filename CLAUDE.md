# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Cheat sheet (tmux sessions — start each separately)
```bash
./start.sh              # prints all commands + ports for prod
./start.sh staging      # prints all commands + ports for staging
```

Services and ports:

| Service | Prod | Staging | Command |
|---|---|---|---|
| backend | :8000 | :8001 | `cd backend && source .venv/bin/activate && python main.py` |
| frontend (prod) | :80/:443 | — | nginx serves `frontend/dist/` — run `npm run build` to publish changes |
| frontend (dev) | :5173 | :5174 | `cd frontend && npm run dev` (Vite dev server, hot reload) |
| fake ads | :9000 | :9000 | `cd fake_ad_server && uvicorn server:app --port 9000 --reload` |

> **nginx** is enabled on boot (`systemctl status nginx`). It proxies `/api/`, `/auth/`, `/me` to FastAPI on port 8000 and serves everything else from `frontend/dist/`. SSL via certbot for `adstackers.com`. ngrok is no longer used.

### Tests
```bash
cd backend && source .venv/bin/activate
python -m pytest tests/ -v    # see backend/TEST.md for targeted test groups
```

### Fake servers (optional)
```bash
# Fake ad server — replaces live Meta/Google data API calls with fixtures
# Case 1 (pre-warmed, default): fixture ads return random metrics → ingest writes
#   real CTR to scored_observations → BO skips warm-start
cd fake_ad_server && uvicorn server:app --port 9000 --reload
#
# Case 2 (cold start + fast demo loop): zero metrics → warm-start fires; pushed clones
#   converge on the next ingest after push. Requires CONVERGENCE_MARGIN_FRACTION=0.80
#   in backend/.env — convergence threshold is now CTR-derived (n = z²(1-p)/(f²p)),
#   and FAST_RAMP's 24h simulation (~360–1560 impr) only clears the bar at f=0.80.
cd fake_ad_server && COLD_START=true FAST_RAMP=true uvicorn server:app --port 9000 --reload
#
# Switch cases: change env vars, restart fake server, click Sync + Ingest in UI
# Then uncomment FAKE_META_BASE_URL / FAKE_GOOGLE_BASE_URL in backend/.env
```
See `fake_ad_server/README.md`.

### First-time setup
```bash
cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cd frontend && npm install
# Copy .env.example → .env in both backend/ and frontend/
```

## External dependencies (cross-repo)

The BO pipeline (`backend/bo_pipeline/`) depends on three external repos/services.
A change in any of them can have downstream impact here.

| Dependency | What it provides | Where it lives | Current status |
|---|---|---|---|
| **quantecarlo** | `call_modal_api`, `call_modal_api_multioutput`, `modal_suggest`, `fantasize_suggest`, `DimSpec` | `~/projects/quantecarlo` — published to PyPI as `quantecarlo>=0.2.0` | Installed from PyPI (`requirements.txt`). Local editable install (`pip install -e ~/projects/quantecarlo`) takes precedence if present. |
| **Modal GP endpoint** | Remote GP server that does the actual q-EI computation | `~/projects/boaz/modal/modal_gp_api.py` — deployed via Modal | Two URLs: `https://markshipman4273--bo-gp-service-gp-suggest.modal.run` (primary) and `https://info-29741--bo-gp-service-gp-suggest.modal.run`. Set via `MODAL_BO_API_URL` env var. |
| **optunahub BatchSampler** | `BatchSampler` — wraps any `suggest_fn` with lock, cache, startup fallback | PR #376 merged to `optuna/optunahub-registry` main 2026-06-22 (`5277f63`) | Load via `optunahub.load_module("samplers/batch_sampler")` (official registry, no local fork needed). meta-ads-demo does **not** use BatchSampler and — per a deliberate decision, see `TECHNICAL_DEBT.md` T10 — will not: `backend/` has no Optuna Study/ask-tell loop, `bo_pipeline/pipeline.py` calls `call_modal_api` directly in one batch call per request, so BatchSampler's ask-bridging cache has nothing to bridge. This note is for `quantecarlo` demos (`demos/demo.py`, `demo7.py`) and the optunahub package itself only. |

**Impact map — if you change:**
- `quantecarlo/_modal_api.py` payload → Modal server must accept the new fields; update `boaz/modal/modal_gp_api.py` to match
- Modal endpoint URL or response schema → update `quantecarlo/_modal_api.py` (single source of truth); meta-ads-demo picks up the change via the package
- `quantecarlo` version → bump `backend/requirements.txt` pin

## Architecture

```
frontend/ (React + Vite, port 5173)
  └── calls ──▶ backend/ (FastAPI, port 8000)
                   └── providers/ (PlatformProvider interface)
                         ├── LiveMetaProvider      → Meta Marketing API (graph.facebook.com)
                         ├── DemoMetaProvider      → fully synthetic fixtures (APP_MODE=demo)
                         └── GooglePlatformProvider → Google Ads REST API (googleads.googleapis.com)

# Optional: fake_ad_server/ (port 9000) intercepts data API calls at the network layer.
# frontend/ → backend/ → fake_ad_server/  (when FAKE_*_BASE_URL env vars set)
```

Provider selection (`providers/factory.py`): `APP_MODE=demo` → Demo; any `MASK_*=true` → MaskingProvider wrapping Live; default → Live.

## Doc update map — check this before updating any .md files

When something changes, update every doc in the matching row. Check this table first, then open the listed files.

| What changed | Docs to update |
|---|---|
| Any session with code edits | `CHANGES.md` (always) |
| New or changed env var | `ENV.md`, `backend/.env.example` |
| BO pipeline logic, params, or thresholds | `backend/bo_pipeline/README.md`, `BACKEND.md`, `ENV.md` + `.env.example` if new param |
| Frontend page or component | `FRONTEND.md` |
| New API route or changed route behaviour | `README.md` (route table), `BACKEND.md` |
| DB schema change (new table, column, index) | `SCHEMAS.md` |
| New known gap, debt item, or backlog feature | `TECHNICAL_DEBT.md` (single home for all three) |
| Gap resolved / feature completed | `TECHNICAL_DEBT.md` (remove entry), `CHANGES.md` |
| Feature removed from "not implemented yet" | `CLAUDE.md` (this file — remove the entry) |
| New subsystem or major refactor | `CLAUDE.md` subsystems table below, relevant detail doc |
| Embedding or BO concept change | `AD.md` |
| Test added, removed, or baseline changed | `backend/TEST.md` |

## Subsystems — read-more index

| Module | Purpose | Details |
|---|---|---|
| `backend/main.py` | All routes, DB init, business logic | `BACKEND.md` |
| `backend/providers/` | Platform provider abstraction | `BACKEND.md` |
| `backend/embeddings/` | Text + image embedding pipeline | `backend/embeddings/README.md`, `AD.md` |
| `backend/ad_generation/` | 7-step AI image generation pipeline | `backend/ad_generation/README.md` |
| `backend/ad_text_generation/` | Slot text generation (Meta + Google RSA) | `backend/ad_text_generation/README.md` |
| `backend/ad_combination_embeddings/` | Cartesian text combination embeddings | `backend/ad_combination_embeddings/README.md` |
| `backend/ad_embedding_combiner/` | Text+image vector concat (3072-dim) | (self-documenting — `combiner.py`, no README) |
| `backend/bo_pipeline/` | Bayesian Optimisation (local GPR + Modal GP, single-output and multioutput) | `backend/bo_pipeline/README.md` |
| `backend/bo_pipeline/config.py` | **Single home for all BO tuning parameters** — metric preference, real-obs threshold, PCA dims, convergence floors. Every param is env-var backed with documented defaults. Read here before touching any BO constant. | (self-documenting) |
| `backend/permissions.py` | Tier taxonomy (`READ_ONLY_TIERS`/`WRITE_TIERS`), `tier_can_write()`, `meta_oauth_scopes()`, `require_write_access()` gate | `BACKEND.md` |
| `frontend/src/` | React pages + components | `FRONTEND.md` |
| DB schema | All table definitions | `SCHEMAS.md` |
| API routes | Full route table | `README.md` |
| Env vars + API keys | All required env vars | `ENV.md` |
| Tests | Full test catalog | `backend/TEST.md` |
| BO / embedding concepts | Mental model for embeddings + BO, universe model, roles | `AD.md` |
| Technical debt + backlog | Architecture gaps, feature backlog, UI gaps, lower-priority todo | `TECHNICAL_DEBT.md` |
| Masking layer (legacy) | Demo/staging masking env vars | `STAGING_POLICY.md` |
| Dev API testing + nginx/HTTPS setup | curl examples, EC2/nginx/OAuth checklist | `DEV_QUICKSTART.md` |

## Key constraints

- Meta access token is **short-lived** — no refresh logic exists
- Structural ingest is idempotent (`ON CONFLICT DO UPDATE`); running it twice doesn't grow row count
- `GET /api/campaigns` swallows insights errors (best-effort); `POST /api/ingest` surfaces them via `insights_errors`
- `_clone_dynamic_to_static_ad` requires `page_id` from `object_story_spec` (re-fetched live; not stored in DB). Image-hash-only creatives lack `page_id` → will 502. Images are uploaded from `backend/ad_images/` by `_upload_image_to_meta` before creative creation — BO combinations use key `"image_url"`, suggestion flow uses `"image"`, both are handled.
- New static ads always created **PAUSED** — no auto-activation
- Google RSA (and any text-only ad): `_build_X` in `pipeline.py` and `BOGroup.build_X` in `cross_platform.py` check `image_vector` presence, not platform name — no image → 1536-dim text-only path, no zero-padding. The unified single-PCA path (`BOGroup.build_X_unified`) still zero-pads to 3072-dim for the shared-PCA endpoint.
- `GOOGLE_ADS_API_VERSION` has **no hardcoded fallback** — must be set explicitly in `.env`
- `run_bo` returns a **4-tuple** `(picks, warning, scored_count, candidate_count)`; cross-platform functions return **2-tuple** `(picks, group_stats)` — mocks must match

## Open design decisions — do not implement until resolved

| Decision | Status |
|---|---|
| **Cross-platform BO** | Three implementations coexist: (1) original per-platform GPR (`POST /api/bo/cross-platform`), (2) unified single shared-PCA GP (`POST /api/bo/cross-platform/unified`, wired to Dashboard UI, `method="modal"`), (3) unified multioutput GP (`method="modal_multioutput"` kwarg to `run_unified_cross_platform_bo`) — separate PCA per platform, coregionalization kernel, not yet wired to a UI endpoint. | **Direction decided (not yet implemented):** move to modality-based outputs — `d=0` = text (all platforms, shared 1536-dim PCA), `d=1` = image (all platforms with images, shared 1536-dim PCA). Removes concatenation for Meta. Requires splitting scored observations and rethinking q-EI candidate pairing. Blocked on Modal server contract changes. See `MULTIOUTPUT_GP.md`. |
| **Ad Library — Google ads** | Undecided: unified library with "Google" badge vs separate page. **Not just missing — actively mislabeled today:** `ad_creative_structures.platform` is correctly set per row (`'meta'`/`'google'`/`'manual'`), but `GET /api/ads/local` (`main.py`) doesn't SELECT it and `LocalAd` has no `platform` field, so it never reaches the frontend; `SOURCE_LABELS` in `AdsPage.jsx` falls back to `data_source` (`'real'` for both Meta and Google) → `"Meta"` unconditionally. Google ads currently display with an incorrect "Meta" badge in the ad library. Small fix (no schema change needed) — see `TECHNICAL_DEBT.md` T14. |

## What's not implemented yet

- `action="replace"` on confirm — validated but no Meta call; stays `pending_confirmation`
- `POST /api/suggestions/{id}/reject` endpoint
- Token refresh for Meta access tokens
- Sync classification (new / updated / unchanged) on structural ingest
- Push to Meta requires app in **Live mode** — generated ads show amber note until then
- Stripe billing integration — `tier`/`tier_source`/`stripe_customer_id`/`stripe_subscription_id` columns and the write-access gate exist (`backend/permissions.py`), but there's no webhook handler yet; every signup defaults to `tier='beta'` (read-only, wide open) and the mini admin screen at `/admin` is the only way to change it. See `TECHNICAL_DEBT.md` T11
- Google cross-platform UI unification (unified ad library, combined push flow)
- Google `normalize_creative` stores asset resource names, not resolved URLs — `youtube_thumbnail_url` exists but not wired to ingest embedding hook
- Google structural ingest does not fire `embed_images` — no URL images available from GAQL yet
- **Score existing images at ingest time** — native static ads write text CTR observations to `scored_observations` after the embedding hook runs (second structural ingest); image scoring for native ads still requires the generation pipeline
- **Mid-test edit detection** — convergence checking runs at ingest; edit detection not wired (see Gap A in `WORKFLOW.md`)
- pMax/Shopping: pMax ingested as stub (headline/description/image/video slots) and *does* proceed through text gen + BO (has real slots to optimize). Shopping = `final_url` only — no optimizable slots, blocked from text gen + BO with 400 (`_UNSUPPORTED_FOR_OPTIMIZATION = {"shopping", "unknown"}` in `main.py`; pMax is deliberately not in this set)

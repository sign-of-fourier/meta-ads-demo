# Technical Debt & Backlog

Open gaps, debt items, and feature backlog. Resolved items go to CHANGES.md, not here.
Items are ordered by priority within each section.

---

## P0 — Active / Next up

### BO-1 — Multi-output GP with proper qEI for cross-platform batch selection

**What and why**
The current cross-platform BO (`run_cross_platform_bo`, `run_unified_cross_platform_bo`) runs independent per-platform GPs then globally ranks EI scores. It doesn't model cross-platform covariance — a Meta score cannot update a Google prediction and vice versa.

A multi-output GP with a coregionalization kernel `K((x₁,d₁),(x₂,d₂)) = B[d₁,d₂] × k_rbf(x₁,x₂)` fixes this. `B[0,1] = ρ` is the Meta–Google performance correlation. Separate PCAs per platform (no zero-padding) give each platform a semantically correct embedding.

**Experiment already done** (`experiments/gp_comparison/`)
- `data_loader.py` — loads Meta (3072-dim) and Google (1536-dim) raw embeddings from app.db
- `models.py` — `PooledGP`, `PooledGPWithFlag`, `MultiOutputGPModel` with uniform interface
- `run_experiment.py` — 3 holdouts × 3 models; confirmed machinery is numerically stable
- Key finding: platform flag adds nothing (PCA already separates via zero-padding); multi-output GP has better rank correlation and calibration under data scarcity

**Implementation (`experiments/gp_comparison/multioutput_qei.py`) — not built yet**
Entry point: `run_multioutput_bo(pairs, user_id, db_path, q=2, xi=0.01)` — same signature and return format as `run_cross_platform_bo()`.

Embedding layout:
- Meta:   `concat(text_1536, image_1536)` → 3072-dim → StandardScaler → PCA(3072→K)
- Google: `text_1536` → 1536-dim → StandardScaler → PCA(1536→K)
- Both project to R^K; no zero-padding
- `d` vector = integer array, same length as pooled candidate set: `0=Meta, 1=Google`
  - NOT a feature column — it indexes into B for the coregionalization kernel

Acquisition function — greedy qEI from the joint posterior (Option B):
1. `mean, cov = gp.predict(X_all_cands, d_all_cands, return_cov=True)` → full N×N covariance
2. Pick 1: closed-form single-point EI from diagonal
3. Pick 2: for each remaining candidate j, evaluate `E[max(f(pick₁), f(j)) − f_best]` using the 2×2 submatrix `cov[[pick₁,j], :][:,[pick₁,j]]` — bivariate MC or closed form
4. Genuinely different from Modal API (Modal uses standard RBF GP; this uses ρ-weighted cross-platform covariance for pick 2)

**Blocked by:** `scored_observations` is empty — no real cross-platform CTR data yet.
**Before promoting to production:** seed observations via `POST /api/bo/seed`, compare `run_cross_platform_bo` vs `run_multioutput_bo` recommendations side-by-side.
**Future extension:** context features (segment, budget band, placement) concatenated onto the K-dim PCA vector after projection. GP structure unchanged.
**Reference:** `MULTIOUTPUT_GP.md`, `chi_bad_ads/multioutput_gp.py` (`MultiOutputGP` class).

---

## Architecture gaps

### T1 — Native static ads get `role='parent'` by default

`ad_creative_structures.role` defaults to `'parent'` for every non-clone ad. A native static Meta ad (one fixed combination, not pushed by us) gets `role='parent'` even though it is not a universe generator — it has a candidate pool of size 1.

**Impact today:** Low. BO seed selection is user-driven (checkboxes). The pause-banner logic only fires when a campaign has active clones. Nothing auto-selects seeds by role.
**Design question still open:** Should a native static ad ever be a BO seed?
**When ready:** Add `role='native_static'` set at ingest for `creative_type='static'` Meta ads and single-slot-index Google RSAs. Exclude from BO seed picker. Include as pre-scored observation source (see T4).

---

### T2 — Deleted clone handling

When a user deletes a pushed clone in Meta/Google Ads Manager it shows as `lifecycle_status='missing'` at next ingest. The `pushed_ad_combos` record persists and the convergence checker keeps querying metrics for a dead ad indefinitely.

**Fix:** At ingest, if a clone transitions to `lifecycle_status='missing'` with `clone_status` of `clone_paused` or `clone_active`, set `clone_status='clone_deleted'`. Add that status to the convergence checker skip list.

---

### T4 — Native static CTR → `scored_observations` (partially addressed)

Native static ads now write their lifetime CTR to `scored_observations` with `source='ingest_native'` at every structural ingest, using `is_pushed_clone=0` filter (T1 dependency removed).

**Remaining gap:** Dynamic/RSA ads with multiple asset slots cannot produce a single-combination observation. Their overall CTR is an average across all served combinations and cannot be attributed to one row in `scored_observations`. Observations for dynamic seeds still come only from pushed-clone convergence.

---

### T5 — Cross-user observation sharing

`scored_observations` is scoped to `user_id`. In a multi-user or multi-account scenario, a user with a large history of scored observations cannot share that training data with a new user or a different account.

**What is needed:**
- `source_user_id` / `donor_account_id` to track where an observation originated
- A `visibility` concept: private / shared / global
- `get_real_observation_count` and `get_scored_combinations` to optionally draw from a shared pool, with appropriate trust weighting
- Schema migration to add provenance columns without breaking single-user queries

**Why deferred:** Design not yet settled. Implement alongside any multi-tenant or white-label expansion.

---

### T6 — Spend/budget configuration at push time

**Golden path gap.** When a BO pick is pushed via `POST /api/push/pick`, the new ad inherits the parent adset's budget. There is no mechanism to set a per-ad spend cap before activating the clone.

**Impact:** Material. Without budget control at push time, users either over-spend (clone runs on the full adset budget) or under-activate (they forget and the clone never gets impressions). Both paths corrupt the CTR signal.

**What is needed:**
- `daily_budget_cents: int | None` field on `PushPickRequest`
- Thread through `push_pick` → `POST /act_{account_id}/ads` (Meta field: `daily_budget`) or a subsequent update call
- Frontend `PushFooter`: optional budget input (e.g. "$5/day") before pushing
- Validation: warn if no budget set and the adset has a high existing budget

**Tracked in code:** `TODO(spend)` in `backend/main.py` → `push_pick` and `frontend/src/components/BatchPanel.jsx` → `PushFooter`.

---

### T10 — BatchSampler (optunahub) adoption — decided against for now

**Status: decided against, revisit only if the "why not" below stops applying.**

Context: an `optunahub-registry` PR (`samplers/batch_sampler`, formerly `q_ei_sampler`) written against this project's BO needs was merged to `optuna/optunahub-registry` main on 2026-06-22 (merge commit `5277f63`). The original intent was for meta-ads-demo's BO pipeline to eventually consume it. On investigation, adopting it here doesn't fit — decided to leave the backend as-is rather than force it.

**Why it doesn't fit:**
1. **No Optuna anywhere in `backend/`.** Confirmed by grep — zero `import optuna` hits in the whole backend. `bo_pipeline/pipeline.py:255-260` calls `call_modal_api(candidates=X_cands_pca, q=...)` directly, synchronously, once per `run_bo()` request. There is no `Study`, no `ask()`/`tell()` loop for BatchSampler to plug into.
2. **BatchSampler solves a problem this codebase doesn't have.** Its lock+cache exists to bridge Optuna's ask-one-point-at-a-time interface to a backend that only returns a batch — first `ask()` blocks and fills a cache, calls 2..q pop from it. meta-ads-demo already gets the whole batch back from one direct `call_modal_api` call, so there's no per-call bridging problem to solve. Wiring BatchSampler in would mean calling `study.ask()` q times just to hand back what one function call already returns — pure indirection, no benefit.
3. **Shape mismatch.** BatchSampler assumes a continuous search space (`DimSpec(type="float", low=..., high=...)`, suggest a brand-new point). meta-ads-demo's problem is discrete candidate-pool selection — q-EI picks indices out of a fixed, pre-embedded pool of ad combinations. `call_modal_api`'s `candidates=` argument already models that directly; Optuna's trial/param model has no native "pick from this enumerable pool" concept.
4. **Ask/tell timing mismatch.** BatchSampler's natural use (see `quantecarlo/demos/demo.py`, `demo7.py`) is ask-and-tell seconds apart in one process (`ThreadPoolExecutor`). meta-ads-demo's actual ask/tell is: push a combo now, wait days for real Meta/Google CTR via ingest, then that becomes an observation for the next round. Doing this "for real" with Optuna would require a persistent `Study` (RDBStorage/JournalStorage) surviving across requests and process restarts — replacing what `scored_observations` + `selector.py` already do today in a simpler, domain-specific way.

**What a real adoption would require (option "C", not chosen):** replace `scored_observations`/`selector.py`'s hand-rolled ask-tell with an actual persistent Optuna `Study` + storage backend, candidates modeled as an int-index `DimSpec`, `ask()`/`tell()` wired to the push route and the ingest route respectively. Touches `selector.py`, `storage.py`, `pipeline.py`, `cross_platform.py`, possibly `ad_generators` tables. Rejected as too large relative to benefit — the current stateless-per-request rebuild-from-`scored_observations` design already works and is easier to reason about.

**What was rejected as insufficient:** a "shallow" per-request wrap — build an ephemeral in-memory `Study` inside `run_bo()`, seed it from `scored_observations`, wrap `BatchSampler`, call `ask()` q times, discard the Study. Gets none of BatchSampler's real benefit (no cross-request persistence, no concurrent-worker cache reuse) — adds a layer of indirection for nothing.

**BatchSampler still lives on** as a `quantecarlo`-demo-only pattern (`quantecarlo/demos/demo.py`, `demo7.py`), loaded via `optunahub.load_module("samplers/batch_sampler")` from the official registry now that the PR is merged (previously `load_local_module` against a private fork at `~/projects/optunahub-registry`). That's a separate, low-risk cleanup independent of this decision.

**Revisit if:** meta-ads-demo's BO ever moves to a long-running, concurrent-worker ask/tell model instead of the current stateless-per-request design — at that point BatchSampler's lock/cache would earn its keep.

---

### T9 — `CONVERGENCE_MIN_DAYS` documented but not enforced

`bo_pipeline/config.py` reads `MIN_CONVERGENCE_DAYS` (default 3). The convergence checkers `_check_meta_convergence` and `_check_google_convergence` only check impression count against `_required_impressions()`. `days_running` is computed and stored at ingest but never compared against `CONVERGENCE_MIN_DAYS`.

**Impact:** A clone receiving a traffic spike in its first few hours could converge the same day it was pushed, before CTR has stabilised across different audiences and times of day.

**Fix:** In both convergence checkers, add `AND days_running >= CONVERGENCE_MIN_DAYS` to the eligibility query, or check `row["days_running"] >= CONVERGENCE_MIN_DAYS` inside the loop before calling `_write_convergence_observation`.

---

### T11 — Stripe webhook handler not built (tier is admin-only for now)

**What exists:** `backend/permissions.py` (`READ_ONLY_TIERS = {free, trial, beta, basic}`, `WRITE_TIERS = {premium, enterprise}`, `tier_can_write()`, `meta_oauth_scopes()`), `require_write_access()`/`require_admin_key()` dependencies in `main.py` gating every push/activate/pause route, and `users.tier_source`/`stripe_customer_id`/`stripe_subscription_id` columns reserved for billing. Every new signup gets `tier='beta'` (read-only, no expiry) — wide open until an admin closes or upgrades the account via the mini admin screen at `/admin` (`GET`/`POST /api/admin/users*`, static `ADMIN_API_KEY` header). `users.tier_expires_at` is admin-set and purely informational (a reminder to act on, not auto-enforced) and `last_login_at`/`login_count` are tracked on every login — all shown in that screen.

**What's missing:** the actual Stripe integration — Checkout session creation, and a `POST /api/webhooks/stripe` handler consuming `checkout.session.completed` / `customer.subscription.updated` / `customer.subscription.deleted` / `invoice.payment_failed` to map a Stripe price/product ID → tier and write `tier_source='stripe'`. `trial` is intended to be a real no-CC Stripe subscription (`payment_method_collection: 'if_required'` + `trial_settings.end_behavior.missing_payment_method`), not a hand-rolled expiry check — see `BACKEND.md` §`permissions.py`. Note `trial`/`basic` stay read-only even once Stripe exists — only `premium`/`enterprise` are write tiers.

**Must handle when built:** webhook signature verification (forged requests must not grant tier); idempotency (Stripe redelivers events; key off `event.id` or subscription status, don't blindly overwrite); reconciliation — any Stripe subscription event should overwrite `tier_source` to `'stripe'` even if a row was previously `'admin'`-granted, so a comped account that later actually subscribes doesn't stay shadowed by the manual grant.

**Also note:** a tier upgrade (via either path) doesn't retroactively widen an already-issued Meta OAuth token's scope — the user must reconnect Meta after upgrading from a read-only tier to get `ads_management`.

---

### T12 — GP pick confidence computed but never surfaced or gated (novel-input handling)

**Status: implementation drafted 2026-07-05, testing the approach — NOT SIGNED OFF, do not close this item or move it to CHANGES.md until the user explicitly signs off.** From a pre-beta audit pass (2026-07-03) covering multi-output GPR/embeddings behavior on novel inputs.

**Full reasoning (why these thresholds, why binary tiering, why PCA over PLS, why the warning reuses the existing channel): see `GP_CONFIDENCE.md`.** This entry is the status/punch-list; that doc is the thought process.

**What was built (draft, pending sign-off):**
1. `confidence_label(gpr_std, nearest_known)` in `bo_pipeline/gpr.py` — returns `"low"` when `gpr_std >= LOW_CONFIDENCE_GPR_STD` (default 0.85) or the nearest scored observation's cosine distance `>= LOW_CONFIDENCE_COSINE_DISTANCE` (default 0.35), else `None`. Both thresholds are env-var tunable in `bo_pipeline/config.py`. Only two states (`"low"` / `None`) — no medium/high tiers yet.
2. New `confidence` field on `BOPick`/`CrossPlatformBOPick` (`main.py`), wired through both `_make_pick` copies (`pipeline.py`, `cross_platform.py`).
3. `BOPickCard.jsx` shows a "⚠ Novel combination — no close precedent" badge in the GP-model-estimates block when `confidence === "low"`.
4. `fit_pca` (`bo_pipeline/modal_bo.py`) now returns `(pca, X_reduced, warning)` — `warning` is set when the requested `n_components` got capped. Threaded through all 5 call sites (`pipeline.py` ×1, `cross_platform.py` ×4) into the same warning channels the Modal-fallback case already used: `run_bo`'s `modal_warning` for the single-platform path, a new `pca_warning` key on each `group_stats`/`CrossPlatformBOGroupStat` entry for the cross-platform paths (per-platform for the multioutput path, shared across all groups for the single-shared-PCA path). Surfaced in `BatchPanel.jsx`'s cross-platform stats row.
5. Tests: `TestConfidenceLabel` (`test_bo_pipeline.py`), PCA-cap-warning tests in `test_modal_bo.py`, and `test_pca_warning_surfaced_when_capped` in both `TestCrossPlatformBO` and `TestUnifiedBOMultioutput` (`test_cross_platform_bo.py`) — the existing `cp_db` fixture's sample counts are small enough that both platforms trigger a real cap, not just a synthetic one.

**Before closing this item:** get explicit user sign-off on (a) the two fixed thresholds (0.85 / 0.35 — not yet validated against real pick history), (b) the binary low/not-low tiering instead of three tiers, and (c) the badge wording/placement in `BOPickCard.jsx`. Once approved, move this entry to `CHANGES.md` per the doc map and remove it from here.

**Not in scope here:** the zero-padding tradeoff for low-data platforms in the multi-output path is a known, already-decided-on issue — see BO-1 above and `MULTIOUTPUT_GP.md`. Don't re-solve it here; the modality-based-output redesign is the real fix.

---

### T13 — Silent failure modes in background pipelines (image gen, embeddings, convergence writes)

**Status: planned, not started.** From the same pre-beta audit pass (2026-07-03), covering silent vs. loud failure modes for external calls.

Ranked by real consequence:

1. **`_run_dynamic_generation`** (`main.py` ~4844-4911): if the whole AI image pipeline throws (deAPI/Azure down, etc.), it's caught, logged, and the job falls back to reusing seed images — then reports `status='complete'`, `images_generated=0`, indistinguishable from "no distinct images were needed." Invisible to the user today.
2. **Fire-and-forget embedding tasks** (`main.py` ~4937-4945): `asyncio.create_task(embed_ad(...))` / `embed_images(...)` / `embed_all_combinations(...)` — these are deliberately designed to never raise and return `True`/`False` (see `embeddings/pipeline.py`'s `embed_ad` docstring), but nothing awaits or records that return value. A failed embed silently means BO later "falls back to random" with zero traceability to the actual cause.
3. **`_write_convergence_observation`** (`main.py` 1916-1964, 6 call sites): DB-write failure is logged and swallowed; the clone's convergence state still advances, but the real CTR observation meant to train the next BO run never lands in `scored_observations` — permanently missing data with no record it happened.
4. `ad_generation/pipeline.py`'s per-variant steps (`_score`, `_poll_and_save`, `_qa`) correctly mark that *variant's* row `status='failed'`, but nothing rolls this up into a job-level count — the user only ever sees the variants that worked, with no "8/10 succeeded" signal.

**Plan (one shared convention, not four one-off patches):**
1. Add a `warnings TEXT` (JSON list) column to `dynamic_generation_jobs`; `_run_dynamic_generation` records entries like `"image_generation_failed: <reason>"` there instead of log-only, extending the same pattern `run_bo`'s `warning` return already established.
2. Keep the embedding tasks fire-and-forget (no added job latency), but wrap each so its `True`/`False` outcome gets written back into that `warnings` column on completion, instead of being discarded.
3. Have `_write_convergence_observation` return a bool; aggregate failures per convergence-checker run into a counter logged at WARNING if nonzero, instead of only ever logging per-call.
4. Add `variants_succeeded`/`variants_failed` to the job-status response, computed from the existing per-variant `status` column — no schema change needed for this one.

**Explicitly not in scope:** the ALTER-TABLE-migration try/excepts (`init_db`) and similar idempotent/retryable internal writes — those are correctly silent by design.

---

### T14 — Google ads in the local ad library are mislabeled "Meta" (not just excluded)

**Status: newly surfaced 2026-07-05 during a documentation audit — not previously tracked as a functional bug. Corrected 2026-07-05 (same day): the initial write-up of this item wrongly claimed `ad_creative_structures` has no `platform` column — it does (added via `ALTER TABLE ... ADD COLUMN platform TEXT NOT NULL DEFAULT 'meta'`, `main.py` ~358), and every writer sets it correctly: Google structural ingest hardcodes `platform='google'`, manual ad creation hardcodes `platform='manual'`, Meta ingest/generation rely on the `'meta'` default. The bug is narrower than a missing column.**

The real gap: `GET /api/ads/local`'s SELECT statement doesn't include `platform` in its column list, and the `LocalAd` Pydantic model has no `platform` field — so correct per-row platform data sitting in the DB never reaches the API response. Frontend-side, `SOURCE_LABELS` in `AdsPage.jsx` maps `data_source='real'` → the label `"Meta"` unconditionally (Google ingest also hardcodes `data_source='real'`, same as Meta), so any Google ad currently ingested and viewed in the Ad Library page displays with an incorrect "Meta" source badge — not because the data isn't there, but because it's dropped between the DB and the UI.

**Fix requires:** add `platform` to the `/api/ads/local` query's SELECT list and the `LocalAd` model (no schema/migration change needed — the column already exists), and give `AdsPage.jsx` a real `google` entry in `SOURCE_LABELS` keyed off that field instead of `data_source`. Small fix — this is a prerequisite for, but distinct from, the still-undecided "unified library vs. separate page" UX question in CLAUDE.md's open-design-decisions table — the mislabeling bug should be fixed regardless of which UX direction is chosen.

---

## Feature backlog

### B1 — AI text generation in Studio

`backend/ad_text_generation/` already exists: `run_text_pipeline(seed_components, n_per_slot, ...)` generates N headline/description/etc. variants from a seed ad using LLM. Wired to Meta/Google flows but not to Studio.

**Idea:** In the Studio template form, after the user enters one seed value per text slot, add a "Generate variants" button that calls `run_text_pipeline` and populates slot values with AI suggestions. User prunes/edits before saving.

**What's already there:** `ad_text_generation/generator.py` has `TEXT_SLOTS` and `generate_all_slots`. A new route `POST /api/manual/ads/{ad_id}/generate-text` wrapping `run_text_pipeline` would be the main addition.

**Effort:** Medium — backend route is straightforward; frontend needs a generate button + loading/replace state.

---

### B2 — Context embedding component (EMB-1)

The ad embedding captures creative content (text + image vectors) but not the *context* in which the ad is shown — segment, budget band, placement, time-of-day. Two ads with identical creative but different segments can have very different CTRs; the GP is blind to this.

**Approach:**
1. Identify all categorical user-control params (at minimum: `segment`; check `user_controls` table for others).
2. One-hot encode each. Fit PCA reducing to ~4 dimensions.
3. Concatenate the 4-dim context vector onto the K-dim PCA ad-embedding after the ad projection step. GP structure unchanged.

**Relation to BO-1:** This is the "future extension" noted there — build independently so it plugs into both single-platform and multi-output GPs.

**Blocked by:** Need enough distinct segment observations to make PCA non-trivial; validate on synthetic data first.

---

### B3 — Google Display ads support (DA-1)

Google Display ads are image-first (banner image with embedded headline/CTA/brand). Adding them extends BO signal across Search (RSA), Display, and Meta in one system.

**Ingestion pipeline:**
1. At structural ingest, detect `ad_type = 'DISPLAY_AD'`. Store image URL alongside creative metadata.
2. Use a multimodal LLM to extract text from the banner image (headline, body, CTA). Better than OCR — handles layout, font, colour contrast.
3. Feed extracted text through existing 1536-dim text embedding pipeline. Combine with image embedding via `ad_embedding_combiner` (3072-dim concat, same as Meta static ads).

**BO integration:** Display ads slot into the same embedding space as Meta static ads. No new GP changes needed. `platform='google'`, `creative_type='display'`; exclude from RSA text-generation pipeline.

**Open questions:** Google Display image URL stability from GAQL; unified vs. separate Ad Library page (see CLAUDE.md open design decisions).

---

## UI/UX gaps

### D2 — Generator auto-naming accumulates duplicates

BatchPanel creates `"Auto meta 2026-05-31"` generators silently on each "Run Generator Analysis" click. Duplicate generators pile up in `ad_generators` with no UI to view or manage them.

**Fix:** Before creating a new generator, check if an identical member set already exists for this user and reuse it. Or add a generator list to the UI. Low priority until generator usage grows.

---

## Lower-priority (also in CLAUDE.md "What's not implemented yet")

- `action="replace"` on confirm — validated but no Meta call; stays `pending_confirmation`
- `POST /api/suggestions/{id}/reject` endpoint
- Meta access token refresh logic
- Google `normalize_creative` stores asset resource names, not resolved URLs
- Google structural ingest does not fire `embed_images` (no URL images from GAQL yet)
- UI dropdown for `target_metric` (backend wired end-to-end; selector not built)
- Edit-after-convergence: both pre- and post-convergence edits → `clone_invalidated`; locked-in CTR should survive post-convergence edits
- Push to Meta requires app in Live mode
- Google cross-platform UI unification (unified ad library, combined push flow)

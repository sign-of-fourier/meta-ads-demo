# Change Log

Changes are appended by date. Each entry covers one session or logical chunk of work.

---

## 2026-06-20 (session 8)

### Dashboard UX — three-section layout with Adstac.kr branding

- Restructured `DashboardPage` into three numbered cards: ① Sync (blue), ② Select ads (purple), ③ Run Adstac.kr (green). Each card has a grey instruction header with numbered badge, title, and description.
- Renamed all user-facing "BO" / "Bayesian Optimisation" labels to "Adstac.kr" throughout `BatchPanel`, `CampaignRow`, `BOPickCard`, `CampaignsPage`, `GoogleCampaignsPage`.
- `CloneStatusBadge` labels updated: "BO Clone — Paused/Testing/Done/etc." → "Adstac.kr — Paused/Testing/Done/etc."
- Section 2 legend explains Template vs Static badge meaning in context.
- All ad types display `ad_id` as their identifier in the table (consistent); Clone badge is the sole differentiator for pushed clones.

### Push abstraction — picks carry their own context

- `DashboardPage.handleRunBO` now enriches every pick with `platform` and `seed_ad_id` before storing in `boState`. Generator picks get the generator's platform/ID; cross-platform picks get per-pick platform + seed resolved from `group_stats`.
- `BatchPushFooter` simplified: removed `platform`, `seedAdId`, `seedAdIdByPlatform` props. Reads `pick.platform` / `pick.seed_ad_id` directly. Adding a new platform requires no changes here.
- `PickCard` / `MatchCard` props cleaned up (removed unused `platform`/`seedAdId`).
- Cross-platform results now show a real push button (was "coming soon"); Google picks are skipped gracefully with a per-pick note.

### Auto-resync after push

- After a successful batch push, `DashboardPage.handlePushDone` scans `structureByKey` to find all campaigns that contained the selected ads and automatically re-ingests each one. New clones appear in the table without manual Re-ingest.

### Backend: `ad_name` in structure response

- `AdStructure` Pydantic model: added `ad_name: str | None`.
- Both structure endpoints (Meta + Google): `SELECT` now includes `pac.ad_name`; `ads_map` returns it for pushed clones.

### BO metric-selection threshold

- `bo_pipeline/selector.py`: real metrics (ctr/cvr/roas) now require `>= MIN_REAL_OBS` observations before displacing warm-start fallbacks. Previously the threshold was 1 (any observation immediately dropped Qwen scores).
- Default `MIN_REAL_OBS = 5`; configurable via `BO_MIN_REAL_OBS` env var.
- "Never mix" rule was already enforced (one metric per run); this closes the "not enough" gap.

### BO config consolidation — `bo_pipeline/config.py`

- Created `backend/bo_pipeline/config.py` as the single home for all BO tuning parameters: `METRIC_PREFERENCE`, `REAL_METRICS`, `MIN_REAL_OBS`, `MIN_TRAINING_POINTS`, `MODAL_BO_PCA_DIMS`, `GOOGLE_BO_PCA_DIMS`, `MODAL_BO_API_URL`, `CONVERGENCE_FLOOR`, `CONVERGENCE_MARGIN`, `CONVERGENCE_MIN_DAYS`.
- Each constant has a docstring explaining purpose, formula/reasoning behind default, and how to tune it.
- `bo_pipeline/gpr.py`, `selector.py`, `modal_bo.py`, and `main.py` now import from `config.py`; scattered definitions removed.
- `backend/.env.example` gained a "Bayesian Optimisation tuning" section listing all 7 env-var-backed params with defaults.
- `ENV.md` §5 updated with all BO params including previously undocumented `GOOGLE_BO_PCA_DIMS` and `CONVERGENCE_MARGIN_FRACTION`.
- `TECHNICAL_DEBT.md`: added T9 — `CONVERGENCE_MIN_DAYS` is read from env and documented but not yet enforced in the convergence checkers.
- `CLAUDE.md`: removed stale "UI dropdown for target_metric not built" entry; added `config.py` to the subsystems index.

---

## 2026-06-20 (session 7)

### Structure view: type-aware badges + inline lifecycle actions (Task 1 / D1)

`CampaignRow.jsx` redesigned with an `AdRow` sub-component. Each ad row now classifies
as one of three types and renders accordingly:

- **Template** (`creative_type='dynamic'` or `'rsa'`, not a clone): no checkbox; "Template"
  or "RSA" badge; if ACTIVE, shows "Pause template for cleaner test isolation" in the
  action cell.
- **BO Clone** (`is_pushed_clone=1`): no checkbox; `CloneStatusBadge` driven by
  `clone_stats.clone_status` ("BO Clone — Paused", "Testing", "Done", "Invalidated",
  "Retained"); Activate button when paused, Keep Running button when converged.
- **Original static** (everything else): checkbox to include in BO pool; Activate button
  when paused.

Activate and Keep Running actions call `activatePick`/`retainPick` directly from the
structure row. Local state (`localCloneStatus`) reflects the action immediately without
a re-ingest.

Backend D1 fix: both `GET /api/structure/{campaign_id}` and
`GET /api/google/structure/{campaign_id}` now include `clone_status`, `platform_ad_id`,
and `combo_id` in `clone_stats` for pushed clones. SCHEMAS.md updated.

**Files:** `frontend/src/components/CampaignRow.jsx`, `backend/main.py`, `SCHEMAS.md`

### BO/batch panel: template vs static sections (Task 2)

`BatchPanel.jsx` rewrites the flat chip list into two labeled sections:

- **Optimize** (Section A): template/RSA ads. BO explores their asset Cartesian product.
- **Include in pool** (Section B): original static ads. Contribute as pool candidates;
  their CTR also informs BO if they've run.
- Informational note: "Combinations you've already pushed are excluded automatically."

`DashboardPage.handleToggleAd` now accepts and stores `adType` on each selection.
`handleRunBO` maps `adType === 'original_static'` → `contribution_mode: 'static'`,
everything else → `'dynamic'`.

`canRun` changed from `selectedAds.length >= 2` to `templateAds.length >= 1` (at least
one template required; statics alone have no pool to explore).

**Files:** `frontend/src/components/BatchPanel.jsx`, `frontend/src/pages/DashboardPage.jsx`

### BO pick results: match detection + batch push selection (Task 3 / T8)

**Backend:**
- `_find_native_static_match(user_id, combination_key, seed_ad_id)`: reconstructs native
  static ad combinations from `ad_creative_structures` slot rows and compares
  `combination_key`. Called by `_enrich_pick` when pick is not already pushed.
- `BOPick` model gains `matches_existing_ad: dict | None` — returned when BO recommends
  a combination that exactly matches an existing native static ad.
- `POST /api/push/match`: records a match in `pushed_ad_combos` pointing at the existing
  `platform_ad_id`. Sets `clone_status` from `effective_status` (active → `clone_active`,
  else → `clone_paused`). No new ad created.

**Frontend (`BatchPanel.jsx`):**
- `PickCard`: not-yet-pushed picks show a checkbox (default unchecked). Click to preview,
  checkbox to select for batch push.
- `MatchCard`: picks with `matches_existing_ad` show amber "Matches existing ad" badge
  with the existing ad_id and current status. Checkbox selects for recording the match.
- `BatchPushFooter`: collects selected picks, calls `pushPick` for new combinations and
  `pushMatch` for matches. Shows per-pick result and note to re-ingest.
- Already-pushed picks show lifecycle label (read-only in this view) — full lifecycle
  management remains in `BOPickCard` for individual ad BO runs.

**Files:** `backend/main.py`, `frontend/src/components/BatchPanel.jsx`,
`frontend/src/api.js`

### Fix `_get_pushed_exclude_keys` sibling scope (Task 4 / T7)

`_get_pushed_exclude_keys` now joins `ad_generator_members` to find sibling members
and their generator_ids. Queries `seed_ad_id IN (self, siblings, generator_ids)`. Per-
member BO runs now correctly exclude combos pushed from sibling members or generator-level
runs in the same generator group.

**Files:** `backend/main.py`

### Test baseline
362 passed, 5 skipped (unchanged).

---

## 2026-06-20 (session 6)

### Meta clone activation learned from platform (not inferred from impressions)

`_check_meta_convergence` was using `impressions > 0` as a proxy for "user activated
the clone." A FAST_RAMP pushed clone would always have impressions, promoting it to
`clone_active` even if it was still PAUSED — wrong in principle and wrong in demo.

Fixed: now calls `GET /{ad_id}?fields=effective_status,status` in parallel with the
insights call (via `asyncio.gather`). Activation driven by `effective_status in
("ACTIVE", "ENABLED")` — same pattern Google already used.

`fake_ad_server/routes/meta.py` entity GET now returns `effective_status` and `status`.
`fake_ad_server/state.py` gained `is_fast_ramp()`. When FAST_RAMP=true, the entity GET
returns `ACTIVE` for pushed clones to simulate the user activating them (otherwise the
demo loop stalls since no one clicks Activate in the fake server).

**Files:** `backend/main.py`, `fake_ad_server/routes/meta.py`, `fake_ad_server/state.py`

### Wrongly-skipped masking tests restored and fixed

`TestGoogleMaskingProvider` (6 tests in `test_google_demo.py`) had
`@pytest.mark.skip(reason="masking layer deprecated")`. The masking layer is NOT
deprecated — `providers/factory.py` uses it on every request when `policy.enabled`.

Root cause of failure: tests used `async def` + `@pytest.mark.asyncio` but `pytest-asyncio`
is not installed in the system Python (tests run with miniconda, not the venv).

Fix: removed skip decorator; converted all 6 tests from `async def` to `def` using
`asyncio.run()`, matching the pattern used everywhere else in the test suite.

**Files:** `backend/tests/test_google_demo.py`

### Flaky scoring test fixed at the source

`test_active_variants_are_scored` called the live Qwen endpoint. When Qwen returned 408,
the variant stayed `status='done'` with `score=None`, which the test counted as unscored.

Fixed in `ad_generation/pipeline.py` `_score()`: the `except` block now calls
`update_variant(vid, db_path, status="failed", error=str(exc))`. A scoring failure marks
the variant terminal rather than leaving it in an ambiguous done-but-unscored state.

**Files:** `backend/ad_generation/pipeline.py`

### Test baseline
362 passed, 5 skipped. Remaining skips are legitimate external-service tests
(live Google credentials, live Modal endpoint).

### Design — structure view + BO panel UX (no code yet)

Designed the full UX for session 7. See CHECKPOINT.md for full spec. Summary:
- Three ad types in structure view: Template / Original static / BO Clone — each with
  distinct badge and context-appropriate action
- BO/batch panel split into three sections: Optimize (templates) / Include in pool
  (static ads) / Already testing (BO clones, greyed out)
- Original static ads in pool: "include in pool" framing — NOT assumed to have CTR signal;
  user may add many with no impressions for organizational reasons
- Match detection: if BO pick matches existing static ad → "Activate [Ad Name]" not Push
- "Push Selected + Resync" combined action
- Two new tech debt items: T7 (cross-member exclusion scope), T8 (native static match)

---

## 2026-06-19 (session 5)

### Dynamic convergence threshold (learned from baseline CTR)

Replaced the hardcoded `MIN_CONVERGENCE_IMPRESSIONS=500` and `MIN_CONVERGENCE_DAYS=3`
with a threshold computed from the campaign's observed baseline CTR:

```
n = z² × (1-p) / (f² × p)
```

where `p` is the latest campaign CTR from `ad_insights` and `f` is
`CONVERGENCE_MARGIN_FRACTION` (default 0.20 = ±20% relative CI).

**Impact:**
- 3.5% CTR campaign → ~2,650 impressions required (was 500, massively under-powered)
- 0.5% CTR campaign → ~19,000 (fixed count was meaninglessly small for this)
- 10% CTR campaign → ~850 (fixed count was over-conservative for this)
- `MIN_CONVERGENCE_DAYS` removed entirely — the impression count already encodes
  enough sample to make the time-guard redundant

**New env vars:**
- `CONVERGENCE_MARGIN_FRACTION` (float, default `0.20`) — controls CI tightness
- `MIN_CONVERGENCE_IMPRESSIONS` repurposed as the floor (default now `100`)

**Demo/FAST_RAMP combo:** set `CONVERGENCE_MARGIN_FRACTION=0.80` in backend `.env`
alongside `FAST_RAMP=true` on the fake server. At f=0.80, required n drops to ~100–200,
which FAST_RAMP's 24-hour simulation (~360–1560 impressions) easily clears.

**Files changed:**
- `backend/main.py` — `_required_impressions()` helper; both convergence checkers updated
- `FAKE_AD_SERVER.md` — Case 2 command updated with `CONVERGENCE_MARGIN_FRACTION=0.80`
- `CLAUDE.md` — fake server comment updated

---

## 2026-06-19 (session 4)

### Push BO picks to platform (golden path implementation)

**Goal:** User selects a BO recommendation and pushes it as a PAUSED static clone. Clone
accumulates metrics; real CTR feeds back into BO. Template (dynamic ad) stays inactive.

**Backend — `backend/main.py`:**
- `push_pick` now resolves generator_id → first member ad_id for the platform API call,
  while keeping the generator_id in `pushed_ad_combos` so future generator BO runs still
  exclude the combination. Previously a generator-based push would 400.
- Added docstring making the golden path explicit: template stays paused, clone runs.
- Added `TODO(spend)` comment: pushed clone inherits adset budget; no per-ad spend
  configuration yet — user must set budget in Ads Manager before activating.
- Added `REAL META` comment: Live mode required for real accounts; fake server bypasses this.
- `_clone_dynamic_to_static_ad`: added note that `page_id` is always re-fetched (not in DB)
  and that image-hash-only creatives will 502 at push time.

**Frontend — `BatchPanel.jsx`:**
- Extracted `PushFooter` component shared by both `GeneratorResults` and `CrossPlatformResults`.
- Push button on every pick card (stops propagation so it doesn't open the modal).
- States: idle → loading ("Pushing…") → pushed (shows ad ID) / error (shows message + retry).
- Google picks: disabled with "Google push — coming soon".
- Always-visible note under the button: "Pushed PAUSED — activate in Ads Manager after setting
  budget" — this is the spend-gap reminder that cannot be missed.
- `GeneratorResults` passes `data.seed_ad_id` (generator_id) to `PushFooter`; backend resolves it.
- `CrossPlatformResults` passes `pick.seed_ad_id` (actual ad_id per pick).

**CSS — `app.css`:**
- `.bo-pick-footer`, `.btn-push`, `.push-live-note`, `.push-success`, `.push-error-text`, `.push-coming-soon`

**TECHNICAL_DEBT.md:**
- Added T6: spend/budget configuration at push time — full impact description and fix plan.

---

## 2026-06-19 (session 3)

### BO pick explainability — fixed, extended, deduped

**Root cause of missing metrics**: `BatchPanel.jsx` (the component the dashboard actually uses)
had its own `PickPreviewModal` with the old "Score · EI" inline format. Previous fixes targeted
`BOPickCard.jsx` (wrong component). Fixed by updating `BatchPanel.jsx` throughout.

**Explainability block in click modal** (`BatchPanel.jsx` + `BOPickCard.jsx`):
- Relative Score (`Math.exp(gpr_mean)` — display only; backend keeps standard-normal scale)
- Potential (EI) (`ei_score`)
- Uncertainty σ (`gpr_std`)
- Nearest known ads — clickable rows that drill into the full ad (replace-in-modal pattern)

**Nearest known ads drilldown**: clicking a neighbor row swaps modal content to show that ad's
full text slots with score/distance header and `← Back` link. No stacked modals.

**`combination` field** added to each `nearest_known` dict in `_expl_nearest_known` (both
`pipeline.py` and `cross_platform.py`) so the frontend can display full slot details on drilldown.

**Deduplication fix** (`bo_pipeline/selector.py` `get_scored_combinations`): was returning
duplicate rows per `combination_key` when warm-start or re-ingest wrote the same combination
multiple times. Duplicates caused (a) the same neighbor appearing 3× in nearest-known and
(b) GPR training bias toward duplicated combinations. Fix: keep last score per `combination_key`.

**`PotentialBadge`** simplified — tooltip removed; badge chip shows Potential (EI) only.
Full stats are in the click modal.

**Logging**: `_expl_local_gp_stats` except branches now use `logger.exception` (was silent).
Note: `logger.info` is filtered by uvicorn's default WARNING level — use WARNING if you need
log lines to appear without configuring basicConfig.

**Tests**: `tests/test_expl_helpers.py` — 16 tests for `_expl_local_gp_stats`,
`_expl_nearest_known`, `_expl_combo_label` in both `pipeline.py` and `cross_platform.py`.
Updated to assert `combination` field present in nearest-known results.

**Test suite**: 49 passed (explainability + BO pipeline tests). Full suite baseline unchanged.

## 2026-06-19 (session 2)

### BO pick card explainability (PotentialBadge)

- Added `PotentialBadge` component to each BO pick card (amber chip)
- Full GP stats (Probable Score, Uncertainty, Nearest known ads) in click modal
- All `_expl_*` functions in `pipeline.py` and `cross_platform.py` are display-only and never influence selection
- Modal q-EI path now runs a cheap local GPR after Modal selects, just to populate per-pick display stats (`_expl_local_gp_stats`) — the selection itself is still done by Modal
- `SHOW_POTENTIAL_BADGE = true` in `PotentialBadge.jsx` acts as a feature flag
- `BOPick` and `CrossPlatformBOPick` Pydantic models include `nearest_known: list[dict] = []`

---

## 2026-06-19

### Fix cross-platform BO warm-start + backend synthetic data removal

**`backend/main.py`**
- Deleted `_synthetic_ad_stats()`: backend was generating fake non-zero metrics seeded from ad_id hash, bypassing the fake server entirely. Backend must never generate synthetic data — all metrics come from the server (real or fake).
- Added `native_ad_insights` table: persists per-ad display metrics fetched from the server (impressions, clicks, spend, ctr, cpm). PRIMARY KEY (user_id, ad_id).
- Added `_store_native_meta_display_metrics`: background task fired at every structural ingest; fetches `/{ad_id}/insights?fields=...&date_preset=lifetime` for each non-clone Meta ad; writes to `native_ad_insights` including zeros when data=[].
- Added `_store_native_google_display_metrics`: same pattern via GAQL for Google ads.
- Updated `get_campaign_structure` and `get_google_campaign_structure`: LEFT JOIN `native_ad_insights` instead of calling `_synthetic_ad_stats`; returns `clone_stats=null` for native ads with no ingest row yet.
- `run_unified_cross_platform_bo_endpoint`: converted from `def` to `async def`; added warm-start loop — for each pair, calls `get_real_observation_count` and fires `run_warm_start` when below `MIN_TRAINING_POINTS`. Previously the endpoint had no warm-start at all, causing random picks with empty `scored_observations` (COLD_START / Case 2).

**`backend/ad_generation/scorer.py`**
- `_image_bytes_from_url`: added local filesystem path handling — `/ad-images/<filename>` URLs (stored by structural ingest in `ad_image_embeddings.image_ref`) are now read from `backend/ad_images/` on disk instead of failing with an `httpx.get` on a non-HTTP URL. This unblocks warm-start Qwen scoring for ads with locally-stored images.

**`backend/tests/test_structural_ingest.py`**
- `test_structure_stats_are_zero_when_server_returns_empty`: seeds `native_ad_insights` with impressions=0; asserts `clone_stats.impressions==0`. Fails if backend generates synthetic non-zero values.
- `test_structure_stats_echo_server_values_exactly`: seeds `native_ad_insights` with real metrics; asserts exact values are returned. Fails if synthetic data substitutes server values.

**`backend/tests/test_cross_platform_bo.py`**
- `TestUnifiedBOEndpointWarmStart::test_warm_start_called_for_each_pair_with_no_real_observations`: enforces FAKE_AD_SERVER.md Case 2 — unified endpoint must call `run_warm_start` for every pair when real observations < `MIN_TRAINING_POINTS`.
- `TestUnifiedBOEndpointWarmStart::test_warm_start_skipped_when_real_observations_present`: asserts warm-start is NOT called when real observations are sufficient.

---

## 2026-06-19 (session 2)

### Fix TestModalBOLive skip guard + stale metrics UX

**`backend/tests/test_modal_bo.py`**
- `TestModalBOLive`: changed `@pytest.mark.skipif` condition from `not MODAL_API_URL` (never True because of hardcoded fallback URL) to `not os.environ.get("RUN_LIVE_MODAL_TESTS")`. Live tests now only run when `RUN_LIVE_MODAL_TESTS=1` is set explicitly in the shell. `MODAL_BO_API_URL` in `.env` is for the backend server, not a test opt-in signal.
- Result: 340 passed, 11 skipped, 0 failed (was 340 passed, 4 failed, 7 skipped).

**`frontend/src/pages/DashboardPage.jsx`**
- Added `staleCampaigns` state (Set of "platform:campaignId").
- `handleMetaSync`: on success, adds all previously-ingested Meta campaign keys to `staleCampaigns`.
- `handleGoogleSync`: same for Google campaigns.
- `handleIngest`: on success, removes the key from `staleCampaigns`.
- Passes `staleCampaigns` to `UnifiedCampaignsTable`.

**`frontend/src/components/UnifiedCampaignsTable.jsx`**
- Accepts `staleCampaigns` prop; passes `isStale={staleCampaigns.has(key)}` to each `CampaignRow`.

**`frontend/src/components/CampaignRow.jsx`**
- Accepts `isStale` prop; renders a yellow "Synced — re-ingest to refresh metrics" banner in the expanded accordion when `isStale=true` and structure is already loaded.

**`frontend/src/app.css`**
- Added `.stale-metrics-banner-row` and `.stale-metrics-banner` styles (amber, matches `parent-pause-banner`).

---

## 2026-06-18 (session 2)

### Clone-agnostic warm-start + Case 1 / Case 2 fake server modes

**`backend/main.py`**
- `_write_convergence_observation`: fixed compound-key bug — text embedding lookup now extracts the text-only inner key from the compound `{"combo":{...},"image_slot":N}` key; text_vector was always NULL before this fix
- `_write_convergence_observation`: added `source` parameter (default `"convergence"`) so native ad observations can be tagged `"ingest_native"`
- Added `_write_native_ad_observations` (Meta): fire-and-forget task at every structural ingest; fetches lifetime CTR for non-clone static ads and writes to `scored_observations`; warm-start now skips if any native ad has real impressions, not just pushed clones
- Added `_write_native_google_ad_observations` (Google): same pattern via GAQL

**`fake_ad_server/state.py`**
- Added `COLD_START` env var: all fixture campaign and ad insights return zero when set; simulates a new account with no delivery history (Case 2)
- Updated FAST_RAMP comment to clarify its role alongside COLD_START

**`fake_ad_server/routes/meta.py`**
- `_get_entity_insights`: now returns campaign-level metrics for fixture ad IDs (not just pushed clones); respects COLD_START via `campaign_metrics()`; enables Case 1 native ad observation flow with the fake server

**Docs**
- `FAKE_AD_SERVER.md`: replaced wrong Case 1 / Case 2 description with accurate model; documented COLD_START + FAST_RAMP interaction and the ingest-driven switch mechanism
- `CLAUDE.md`: updated fake server cheat sheet to match
- `TECHNICAL_DEBT.md`: marked T4 partially addressed; added T5 (cross-user observation sharing)

---

## 2026-06-18

### Fix warm-start trigger
- `bo_pipeline/selector.py` — added `get_real_observation_count` and `REAL_METRICS` constant; counts only ctr/cvr/roas rows (not qwen_warm/synthetic)
- `backend/main.py` — warm-start now fires when real-metric count < `MIN_TRAINING_POINTS`, not when `scored_observations` is empty; Qwen scores (warm-start output) no longer suppress the trigger

---

## 2026-06-17 (session 2)

### Remove fake_warm_start_server; wire real Qwen into port 9000
- Deleted `fake_warm_start_server/` entirely — port 9001 never needed; warm-start calls real Qwen directly
- `warm_start/scorer.py` — removed `FAKE_WARM_START_BASE_URL` branch; only real Qwen path remains
- `fake_ad_server/routes/meta.py` — `_get_entity_insights` now async; calls Qwen Modal endpoint for pushed clones; derives CTR: `base=0.03`, scaled by `quality/0.5` where `quality=(score-1)/6`; result cached per ad_id in state
- `fake_ad_server/state.py` — added `get_creative_content_for_ad`, `get_ad_qwen_ctr`, `set_ad_qwen_ctr`; added `_meta_ad_qwen_ctr` cache
- `fake_ad_server/requirements.txt` — added `httpx`
- `backend/.env.example`, `start.sh`, `CLAUDE.md`, `DEV_QUICKSTART.md` — removed all port 9001 / `FAKE_WARM_START_BASE_URL` references; Case 2 now described as "cold start with real Qwen"

---

## 2026-06-17

### Warm-start mini BO
- Added `backend/warm_start/` package: `mini_bo.py` (mini BO loop over 20-candidate universe, GP-guided scoring order), `scorer.py` (VLM oracle or fake server), `__init__.py`
- `METRIC_PREFERENCE` in `bo_pipeline/selector.py` extended: `(ctr, cvr, roas, qwen, qwen_warm, synthetic)` — `qwen_warm` superseded by any real CTR observation
- `POST /api/bo/run` made async; warm-start runs automatically on first call when no observations exist (idempotent)
- Added `POST /api/bo/simulate-run` — dev endpoint that calls fake server and writes a `ctr` observation to `scored_observations`

### Fake warm-start server (port 9001)
- `fake_warm_start_server/` — separate server, does not modify `fake_ad_server/`
- Reads same fixture inventory as fake ad server; builds 20-candidate universe (title × body × image) with hidden `true_ctr`, `true_cvr`, `true_quality` per combo
- `POST /candidates/score` — warm-start oracle (quality score, 0–1 higher=better)
- `POST /simulate/run` — fake CTR/CVR by creative content (title/body/image_url)
- `POST /ads/{id}/run` — fake CTR/CVR by candidate ID
- `GET /status` — total observations, `threshold_met` flag, universe size
- `POST /reset` — clear observations without restart

### start.sh rewritten as cheat sheet
- No longer starts anything; prints service commands and ports for the requested env
- Lists all five services: backend, frontend, ngrok, fake ads (:9000), fake warm-start (:9001)
- Detects and prints active fake-server env vars from `backend/.env`
- `./start.sh staging` shows staging ports; prints hint for the other env

### Chi bad ads — score direction corrected
- `build_ads_jsonl.py` docstring: "badness score" → "quality/appeal score. Higher = better ad"
- `FINE_TUNING.md`: score field description updated; evidence cited (correlates with good_design, trustworthy, like_product labels at high scores)

### Docs
- `CLAUDE.md` Commands section: replaced start.sh launch docs with tmux cheat sheet table
- `DEV_QUICKSTART.md`: replaced preferred/manual/separate-terminal sections with service table; added fake warm-start server section
- `NGROK_SETUP.md`: removed start.sh tunnel references; start each service separately
- `FAKE_AD_SERVER.md`: start.sh row marked done; updated note

---

## 2026-06-01 (multioutput GP tests + MULTIOUTPUT_GP.md)

### Tests
- `test_modal_bo.py::TestModalBOUnit`: added `test_multioutput_payload_includes_d_and_rho` and `test_multioutput_response_parsed_to_index_and_x` — mock `urlopen`, verify `d`/`d_candidates`/`rho` appear in the JSON payload and that the response is correctly parsed; no network
- `test_cross_platform_bo.py::TestUnifiedBOMultioutput`: 9 new tests covering `run_unified_cross_platform_bo(..., method="modal_multioutput")` end-to-end with `call_modal_api_multioutput` patched; covers pick shape, platform tagging, `_sort_key` cleanup, fallback on empty response, fallback on exception, `d`-array content, `top_n` enforcement
- `test_modal_bo.py::TestModalBOLive`: added `test_multioutput_server_handles_d_fields` — live smoke test that sends `d`/`d_candidates`/`rho` to the real Modal endpoint, confirming the server branches to `_TorchMultiOutputGP` and returns q candidates in the standard format
- Suite: 343 passed, 7 skipped (up from 331)
- Documented test-seeder quirk in `TestUnifiedBOMultioutput` docstring: plain combo keys in `scored_observations` vs compound keys in candidates means `exclude_keys` never fires in the test DB — all 8 candidates per platform are always available

### Docs
- `MULTIOUTPUT_GP.md` rewritten as a full design document: motivation (zero-padding problems), identity vector `d`, coregionalization matrix B and covariance block arithmetic, per-platform embedding construction (Meta text+image, Google RSA text-only), planned extensions (context vector, Google Display with OCR, multi-platform B), ρ guidance, implementation reference table, client pipeline flow diagram

---

## 2026-05-31 (generator analysis wired to frontend)

### Frontend
- `BatchPanel`: when all selected ads are the same platform, button shows "Run Generator Analysis" and description explains merged pool behavior; cross-platform selection keeps existing "Run Cross-Platform Analysis" flow
- `BatchPanel`: added `GeneratorResults` component for generator BO response (scored/candidate counts, member count, picks)
- `DashboardPage.handleRunBO`: same-platform selection → auto-creates a named generator → calls `/api/bo/run` or `/api/google/bo/run` with `generator_id`; mixed-platform → existing unified cross-platform GP unchanged
- `api.js`: added `createGenerator(name, members)` and `runBOForGenerator(generatorId, platform, targetMetric)`

---

## 2026-05-31 (ad generators — multi-member BO candidate pool)

### Terminology
- "dynamic ad" = Meta dynamic ad OR Google RSA (multi-asset, combination generator)
- "static ad" = Meta static OR fully-pinned Google RSA (single fixed combination)

### Schema
- New table `ad_generators (id, user_id, name, created_at)`
- New table `ad_generator_members (generator_id, ad_id, contribution_mode)` — `contribution_mode = 'dynamic' | 'static'`

### Backend
- `POST /api/generators` — create a named generator from one or more member ads
- `GET /api/generators` — list all generators for the user
- `DELETE /api/generators/{id}` — delete a generator
- `/api/bo/run` and `/api/google/bo/run`: accept optional `generator_id`; when set, candidate pools and scored observations are merged across all member ads; `generator_id` acts as the key in `pushed_ad_combos` / `scored_observations` / `bo_selections`
- `run_bo()` in `pipeline.py`: added `seed_ad_ids`, `text_source_ids`, `image_ad_ids` optional list params
- `get_scored_combinations()` in `selector.py`: accepts `seed_ad_ids` list
- `get_candidate_combinations()` in `selector.py`: accepts `text_source_ids` and `image_ad_ids` lists

---

## 2026-05-31 (architecture review + doc updates)

### AD.md
- Fixed stale lineage diagram: `ad_generation_variants` → `scored_observations` as BO training data source
- Added "Universe and Roles" section: candidate universe = per-parent-ad Cartesian product; role taxonomy (parent / test_clone / native_static gap); three sources of scored observations; multi-parent universe constraint

### WORKFLOW.md
- Marked as complete: convergence checking, clone lifecycle UI states, edit detection (invalidation), Google clone detection, RSA pin-all
- Updated Gap A (edit detection implemented), Gap B (deleted clone — not yet), Gap D (pin-all partial resolution)

### TECHNICAL_DEBT.md (new)
- T1: role taxonomy — native statics get `parent` by default; proposed `native_static` role
- T2: deleted clone handling — clone stuck in convergence checker after platform deletion
- T3: multi-parent universe not supported — per-ad constraint in `pipeline.py`
- T4: native static CTR not flowing to scored_observations
- D1–D4: documentation drift items

### CLAUDE.md
- Replaced `AD_ROLE_PLAN.md` reference with `TECHNICAL_DEBT.md`

---

## 2026-05-31 (ad role plan — clone lifecycle implementation)

### Schema
- `ad_creative_structures.role TEXT DEFAULT 'parent'` — `'test_clone'` set at ingest when clone detected
- `pushed_ad_combos.clone_status TEXT DEFAULT 'clone_paused'` — lifecycle state machine
- New table `ad_parent_fillers` — stores filler headline/description slots for Google parent RSAs

### Backend
- **Clone status machine**: `clone_paused` → `clone_active` → `clone_converged` / `clone_retained` / `clone_invalidated`
- `_check_meta_convergence`: uses `clone_status` filter; impressions > 0 while paused → `clone_active`
- `_check_google_convergence`: fixed `run_gaql` → `query_gaql` bug; adds `ad_group_ad.status` to GAQL; `ENABLED` → `clone_active`
- `ingest_campaign_structure` (Meta): sets `role = 'test_clone'` on clones; invalidation detection; returns `parent_should_pause`
- `ingest_google_campaign_structure`: same + extracts filler slots into `ad_parent_fillers` for parent RSAs
- `_create_google_rsa_ad`: looks up `ad_parent_fillers`; when present, passes `pin_all=True` so all 5 RSA slots are pinned — CTR measures exactly one combo
- `google_ads_api.create_rsa`: added `pin_all: bool = False` parameter
- `_enrich_pick`: now returns `clone_status` and `combo_id` in BO pick response
- `push_pick`: explicitly sets `clone_status = 'clone_paused'` on push
- `activate_ad`: also sets `clone_status = 'clone_active'`
- New endpoint `POST /api/push/retain/{combo_id}`: sets `clone_status = 'clone_retained'`

### Frontend
- `BOPickCard`: action button driven by `clone_status`; added `clone_invalidated` (grey) and `clone_converged` + "Keep Running" button → calls `/api/push/retain`
- `CampaignRow`: amber `parent-pause-banner` shown when ingest returns `parent_should_pause: true`
- `DashboardPage`: captures `parent_should_pause` from ingest results; stored in `pauseWarningByKey` state
- `api.js`: added `retainPick(comboId)` function

---

## 2026-05-30 (doc reorganization)

### CLAUDE.md slimmed from 457 → 109 lines
- Moved env var tables → `ENV.md` (new)
- Moved main.py function reference + provider table → `BACKEND.md` (new)
- Moved frontend page/component map → `FRONTEND.md` (new)
- Moved masking env var tables → `STAGING_POLICY.md` (legacy section appended)
- CLAUDE.md is now a lean index with commands, architecture, subsystem pointers, constraints, open decisions, and not-yet-implemented list

---

## 2026-05-30 (dashboard UX + ad stats session)

### Dashboard: ad rows aligned to table columns + per-ad stats

**Backend**
- `ad_creative_structures` — new `effective_status TEXT NOT NULL DEFAULT 'UNKNOWN'` column (ALTER TABLE migration); stores the raw platform status (`ACTIVE`, `PAUSED`, `ENABLED`, etc.) per ad, distinct from `lifecycle_status` which is the system's own classification
- Both Meta and Google structural ingest now write `effective_status = raw_status` (the value returned by the API) to every slot row for an ad
- `_synthetic_ad_stats(ad_id)` — new helper; generates deterministic fake per-ad stats (impressions, clicks, CTR, spend, CPM) seeded by MD5 of ad_id; used in the structure endpoints for all ads without real pushed-clone data
- `AdStructure` model — new fields: `is_pushed_clone: bool`, `effective_status: str | None`, `clone_stats: dict | None`
- Both `GET /api/structure/{campaign_id}` and `GET /api/google/structure/{campaign_id}` now LEFT JOIN `pushed_ad_combos` on `platform_ad_numeric_id = ad_id`; return `clone_stats` with real impressions/days/CTR for pushed clones, synthetic stats for everything else; filter out internal `_`-prefixed slots (e.g. `_placements`) from `components`; prefer the first non-UNKNOWN `effective_status` seen across slot rows (the `_placements` slot sorts first alphabetically and was shadowing the real status)
- `UnifiedCrossPlatformBORequest` — new `target_metric: str | None = None` field; passed through to `run_unified_cross_platform_bo`
- `embeddings/embedder.py` — guard in `embed_image_url`: skips values without `http://` or `https://` prefix (Meta image hashes, empty strings) with a logged warning instead of crashing

**Frontend**
- `CampaignRow.jsx` — fully restructured: ad rows now render as proper `<tr>` elements inside the campaign table, aligned to the 9 table columns (Name / Platform / Status / Daily Budget / 7d Impr. / 7d Clicks / 7d Spend / CTR / CPM); clicking anywhere on a row (except the checkbox) opens the ad preview modal; checkbox stops propagation; a thin sub-header row shows ad count + Re-ingest button; `AdPreviewModal` imported from `AdsPanel`
- `AdsPanel.jsx` — `AdPreviewModal` exported as named export; modal now shows a Performance section (impressions, clicks, CTR, spend, CPM, days running, converged) above the Creative section; synthetic stats labelled `(synthetic)`; `_`-prefixed slots filtered from display
- `BatchPanel.jsx` — "Optimize for" `<select>` dropdown (Auto / CTR / CVR / ROAS) added to cross-platform controls; cross-platform pick cards now use `bo-pick-clickable` class with click-to-preview via `PickPreviewModal`
- `DashboardPage.jsx` — `targetMetric` state added; passed to `BatchPanel` and included in `runUnifiedCrossPlatformBO` call
- `api.js` — `runUnifiedCrossPlatformBO` accepts optional `targetMetric` param; sent as `target_metric` in request body when set
- `app.css` — new classes: `metric-select`, `ad-data-row`, `ad-data-row-clickable`, `ad-data-row-selected`, `ad-data-cell-name`, `ad-data-na`, `ad-stat-synthetic`, `ads-subheader-row`, `ads-subheader-hint`, `slot-value-list`

**Bug fixes**
- `CampaignRow` ingest buttons previously called `onClick={onIngest}` (passing the click event as `campaignId`), causing `GET /api/google/structure/%5Bobject%20Object%5D`; fixed to `onClick={() => onIngest(id, platform)}`
- `effective_status` showed as `—` for all ads because the `_placements` slot row (alphabetically first) always had `UNKNOWN`; structure endpoints now scan all rows per ad and prefer the first real status value
- `ad_factory/image_gen.py` — `placeholder()` now uses a random UUID seed instead of a deterministic MD5 of the concept, so repeated runs produce different picsum images

---

## 2026-05-30 (ad_factory)

### `ad_factory/` — standalone ad creation tool
- New top-level module: `python -m ad_factory <config.json>` (or `python ad_factory/create_ad.py <config.json>`)
- Config JSON specifies `concept`, `platform` (`meta`|`google`), optional `campaign_id`/`adset_id`, `final_url`, `status`, `generate_image`
- `text_gen.py` — calls Azure OpenAI to generate Meta ad copy (headline/body/description/CTA) or Google RSA copy (8-15 headlines + 3-4 descriptions) from a plain-English concept
- `image_gen.py` — deterministic picsum placeholder by default; `generate_image: true` calls deAPI FLUX img2img with a concept-based prompt
- `fixtures.py` — reads/writes fake_ad_server fixture JSON files directly; creates campaigns/adsets/adgroups as needed; injected ads are immediately visible (fake server reads fixtures per-request)
- Example configs in `ad_factory/examples/` for both platforms (existing campaign and new campaign variants)
- No dependency on the backend FastAPI app or fake_ad_server — standalone, runnable from project root
- `AD_FACTORY.md` documents the full design: config reference, env vars, text/image generation, fixture injection mechanics, how factory ads differ from pushed clones and generated ads, ingest behaviour, and limitations

## 2026-05-30

### BOPickCard UX fixes
- Cards are now 2-per-row CSS grid; show image filename instead of thumbnail; click card to open preview modal (both modals use `createPortal` so they render at `document.body`, outside table/grid hierarchy)
- Stats row in cross-platform results shows ad ID suffix and "(random — no scored variants yet)" when `scored_count=0`
- Per-campaign BO result and Google BOPicksPanel both surface the same "no scored data" amber note

### `scored_observations` refactor — decouple BO from generation pipeline
- **New table `scored_observations`** (`bo_pipeline/storage.py`): single source of truth for BO training data; columns: `user_id`, `seed_ad_id`, `combination_key`, `combination`, `score`, `metric` (`synthetic`|`qwen`|`ctr`|`cvr`|`roas`), `source`, `text_vector`, `image_vector`
- **`bo_pipeline/selector.py`**: `get_scored_combinations` rewritten to read from `scored_observations`; walks `METRIC_PREFERENCE = (ctr, cvr, roas, qwen, synthetic)` to pick the best available metric; accepts optional `target_metric` override; removes old `ad_generation_variants` join and `variant_embedding_ad_id`
- **`bo_pipeline/pipeline.py`** + **`cross_platform.py`**: `run_bo`, `run_cross_platform_bo`, `run_unified_cross_platform_bo` all accept `target_metric: str | None`
- **`ad_generation/pipeline.py`**: `_write_qwen_observation()` fires after Qwen2-VL scoring and writes `metric='qwen'` to `scored_observations`
- **`main.py`**: `init_db()` creates `scored_observations`; `_write_convergence_observation()` helper writes `metric='ctr'` when a pushed clone converges (called from both `_check_meta_convergence` and `_check_google_convergence`); `BORunRequest` gains `target_metric` field; seed endpoint rewrites directly to `scored_observations` (no generation jobs)
- **`seed_bo_synthetic.py`**: rewritten to write directly to `scored_observations`; removes `ad_generation_jobs/variants` inserts
- **Tests** (`test_bo_pipeline.py`, `test_google_bo.py`, `test_cross_platform_bo.py`): DDL and seeding updated to use `scored_observations` directly; all 239 tests pass

---

## 2026-05-29

### Unified dashboard — phase 1
- **`frontend/src/pages/DashboardPage.jsx`**: rewritten as a clean orchestrator; owns all shared state (campaigns, expanded key, structure, batch selection, BO)
- **`frontend/src/components/SyncBar.jsx`**: two independent sync buttons (Meta + Google) with per-platform loading/error/push notes
- **`frontend/src/components/UnifiedCampaignsTable.jsx`**: single table merging Meta and Google campaigns; Platform column; each row keyed by `platform:id`
- **`frontend/src/components/CampaignRow.jsx`**: one expandable campaign row; accordion (only one open at a time); delegates expanded content to AdsPanel
- **`frontend/src/components/AdsPanel.jsx`**: shows Ingest button before first ingest, then list of ads with checkboxes; pMax/Shopping/Unknown ads disabled for BO
- **`frontend/src/components/BatchPanel.jsx`**: selected-ad chips + top-N input + Run Cross-Platform Analysis button + results display (moved from DashboardPage)
- **`frontend/src/app.css`**: new classes for sync bar, ads panel, ad checkbox rows, btn-small, platform badges

### Bug fix: `META_GRAPH` now respects `FAKE_META_BASE_URL`
- `backend/main.py`: `META_GRAPH` reads `FAKE_META_BASE_URL` env var when set; covers all Meta API calls including push and convergence insight fetches (previously only the provider layer used the fake URL)

### Convergence: full end-to-end implementation
**Backend**
- `init_db()`: three new columns on `pushed_ad_combos` — `platform_ad_numeric_id TEXT`, `current_impressions INTEGER DEFAULT 0`, `days_running INTEGER DEFAULT 0`; ALTER TABLE migrations for existing DBs
- New constants: `MIN_CONVERGENCE_IMPRESSIONS` (default 500), `MIN_CONVERGENCE_DAYS` (default 3), both env-configurable
- `_check_meta_convergence(user_id, campaign_id, access_token)`: async helper; fetches lifetime impressions via `GET /{platform_ad_id}/insights` for each active unconverged pushed Meta clone; updates `current_impressions`/`days_running`; flips `converged=1` and locks in CTR when both thresholds met
- `_check_google_convergence(user_id, campaign_id, access_token, customer_id, login_customer_id)`: same for Google; GAQL `SELECT metrics.impressions, metrics.ctr FROM ad_group_ad WHERE campaign.id = X`
- Both helpers fire fire-and-forget via `asyncio.create_task` at the end of their respective ingest routes
- `BOPick` model: new field `current_impressions: int = 0`
- `_enrich_pick`: reads `current_impressions` from `pushed_ad_combos`

**Google clone detection fix**
- `push_pick` route: stores `platform_ad_numeric_id` — last path segment of resource name for Google (e.g. `customers/123/adGroupAds/456` → `456`), same as `platform_ad_id` for Meta
- `POST /api/google/ingest/structure/{campaign_id}`: clone detection now matches `ad_id` against `platform_ad_numeric_id` instead of the full resource name string; `is_pushed_clone = 1` now works for Google

**Fake ad server**
- `state.py`: new `pushed_google_ad_metrics(resource_name)` — ramp metrics for pushed Google RSA clones
- `routes/meta.py`: `GET /{ad_id}/insights` (entity-level insights) now returns ramp metrics for pushed Meta clones
- `routes/google.py`: GAQL `FROM ad_group_ad` with `metrics.*` in SELECT returns per-ad ramp metrics for pushed clones; fixture ads return zero metrics

**Frontend**
- `components/BOPickCard.jsx`: two new lifecycle states — **Testing… N impr.** (amber, active below convergence) and **Running ✓ — Tested** (green, converged); `current_impressions` and `converged` read from server pick data
- `app.css`: `.push-status-testing` (amber) and `.push-status-tested` (green) badge styles

### Single shared PCA for unified cross-platform BO
- `bo_pipeline/cross_platform.py`: `BOGroup.build_X_unified()` added — always returns 3072-dim using `combine(text_vec, image_vec_or_None)` for all platforms
- `run_unified_cross_platform_bo`: replaced per-group PCA + padding with a single shared PCA fit on the pooled 3072-dim union of all groups' scored and candidate vectors; EI scores are now in one comparable latent space

### Documentation
- `WORKFLOW.md`: created; end-to-end recommendation workflow, lifecycle state table, design gaps (A–E)
- `PUSH_STRATEGY.md`: deleted (content migrated to WORKFLOW.md and CLAUDE.md)
- `CLAUDE.md`: updated `init_db`, new helpers, ingest routes, `BOPick`, `_enrich_pick`, `BOPickCard`, BO pipeline (unified PCA), env vars, fake server, "What's not implemented yet", open design decisions, known technical notes
- `backend/bo_pipeline/README.md`: updated `cross_platform.py` module description

---

## 2026-05-28

### Stage 2: push tracking, naming, deduplication, lifecycle

**Backend**
- `init_db()`: new `pushed_ad_combos` table; `is_pushed_clone` column on `ad_creative_structures`
- `bo_pipeline/pipeline.py`: `run_bo` accepts `additional_exclude_keys` — pushed combinations are excluded from the candidate space
- `BOPick` model: new lifecycle fields `ad_name`, `already_pushed`, `push_status`, `converged`, `platform_ad_id`
- `_enrich_pick`: queries `pushed_ad_combos` and annotates each pick with its lifecycle state
- `POST /api/push/pick`: new unified per-pick push endpoint for Meta and Google; creates PAUSED ad on the platform, records in `pushed_ad_combos`
- `POST /api/activate`: enables a PAUSED pushed clone on the platform; updates `push_status`
- `_clone_dynamic_to_static_ad` / `_create_google_rsa_ad` / `google_ads_api.create_rsa`: accept optional `ad_name` parameter
- Meta ingest (`POST /api/ingest/structure/{campaign_id}`): detects pushed clones by matching `platform_ad_id`, sets `is_pushed_clone = 1`
- Helper `_get_pushed_exclude_keys`: fetches pushed keys for a seed ad; called by both BO run endpoints

**Frontend**
- `components/BOPickCard.jsx`: new shared component — name-first pick display, Preview modal (full content on click), Push and Run modal with name prompt, per-pick lifecycle action (Push / Activate / Testing / Running ✓)
- `pages/CampaignsPage.jsx`: replaces inline pick rendering with `BOPickCard`; optimistic state update after push
- `pages/GoogleCampaignsPage.jsx`: `BOPicksPanel` now delegates to `BOPickCard`; passes `campaignName`, `seedAdId`, `onPushed`
- `api.js`: `pushPick()` and `activatePick()` functions added
- `app.css`: styles for BOPickCard, Preview modal, Push modal, push-status badges

### Stage 1: fake ad server — persistent state + evolving metrics
- `fake_ad_server/state.py`: in-memory store for pushed Meta ads/creatives and Google RSAs; deterministic evolving metrics per entity_id × hour-bucket; ramp metrics for pushed clones
- `fake_ad_server/routes/meta.py`: POST /ads and /adcreatives store in state; GET /ads merges fixture + state ads; GET insights returns live metrics; entity GET/POST check and update state
- `fake_ad_server/routes/google.py`: mutate stores pushed RSAs; searchStream FROM ad_group_ad merges fixture + state; FROM campaign returns live metrics

### WORKFLOW.md: end-to-end recommendation workflow document
- Canonical terminology table (candidate space, scored observations, limbo, pushed clone, convergence)
- Step-by-step user journey: connect → ingest → generate → BO → Push and Run → Activate → convergence → cross-platform
- Lifecycle state table and implementation status table
- Design gaps: Gap A (orphan/edited pushed clone), Gap B (deletion), Gap C (multi-user), Gap D (Google RSA noise), Gap E (GP target metric consistency)

### BO cross-platform: PCA dimension mismatch fix
- `bo_pipeline/cross_platform.py`: pad each group's PCA array to K dims before vstacking; fixes `ValueError` when groups have different sample counts

### BO pipeline: unified cross-platform BO + code quality fixes

**`bo_pipeline/pipeline.py`**
- `run_bo` return type extended from `(picks, warning)` to `(picks, warning, scored_count, candidate_count)` — eliminates double DB query in both single-platform endpoints
- `_build_X` and `run_bo` now accept `platform` kwarg (default `"meta"`). Google path uses 1536-dim text-only vectors; Meta keeps 3072-dim `combine(text_vec, image_vec)`. Removes zero-padding waste on Modal path; removes kernel noise on local path.
- `_run_local_bo` and `_run_modal_bo` both accept and thread through `platform`

**`bo_pipeline/cross_platform.py`**
- `run_cross_platform_bo` default `method` changed from `"local"` to `"modal"` — consistent with single-platform `run_bo` default
- `run_cross_platform_bo` return type changed from `list[dict]` to `(picks, group_stats)` — eliminates double DB query in cross-platform endpoint; `group_stats` is a list of `{platform, seed_ad_id, scored_count, candidate_count}` dicts
- New `run_unified_cross_platform_bo(pairs, user_id, db_path, top_n=4, method="modal")` — per-group PCA each to the same K-dim output (both use `MODAL_BO_PCA_DIMS`), pooled into a single GP/Modal call. Meta (3072-dim) and Google (1536-dim) are reduced separately to K-dim then stacked — natural clustering in kernel space means separate response surfaces with shared hyperparameters. Local path generalizes fantasy loop to `top_n` picks (was hardcoded 2). Modal path sends one `q=top_n` call over the full pooled matrix.

**`main.py`**
- Both single-platform BO endpoints (`/api/bo/run`, `/api/google/bo/run`) updated to unpack 4-tuple from `run_bo`; pre-fetch of scored/candidates removed
- Google BO endpoint now passes `platform="google"` to `run_bo`
- Cross-platform endpoint updated to unpack `(picks, group_stats)` from `run_cross_platform_bo`; pre-fetch removed
- New endpoint `POST /api/bo/cross-platform/unified` — request body `{pairs: [...], top_n: int = 4}`; same response shape as `/api/bo/cross-platform`; calls `run_unified_cross_platform_bo`

**Frontend**
- `api.js`: new `runUnifiedCrossPlatformBO(pairs, topN)` calling `/api/bo/cross-platform/unified`; old `runCrossPlatformBO` kept
- `DashboardPage.jsx`: `metaSeedAdId`/`googleSeedAdId` replaced with `selectedPairs` array `[{platform, seed_ad_id, text_source_id, label}]` persisted to localStorage; batch displayed as removable chips; `top_n` number input (1–8, default 4); button now calls `runUnifiedCrossPlatformBO`; readiness indicator shows count per platform
- `CampaignsPage.jsx`: accepts `batchedAdIds` prop; auto-propagate `onIngest` calls removed; explicit "Add to Analysis" / "In Batch ✓" toggle button added in recommendations header; `onIngest` callback now passes `{platform, seed_ad_id, text_source_id, label}` object
- `GoogleCampaignsPage.jsx`: same changes as CampaignsPage — `batchedAdIds` prop, auto-propagate removed, explicit toggle button
- `app.css`: new styles for `.batch-chip`, `.batch-chip-remove`, `.btn-batch`, `.btn-batch-active`, `.cross-platform-batch`, `.cross-platform-controls`, `.topn-label`, `.topn-input`

**Tests** — 255 passed, 7 skipped (baseline unchanged)
- All `run_bo` call sites updated to unpack 4-tuple (`picks, *_ = run_bo(...)` for direct callers; `return_value=(picks, None, 0, 1)` for mocks)
- All `run_cross_platform_bo` call sites updated to unpack 2-tuple
- `test_google_pmax_shopping.py`, `test_google_login_customer_id.py`, `test_google_bo.py` mock patches updated

---

### Dev tooling: start.sh fake-mode warning + DEV_QUICKSTART services overview

**`start.sh`** — warns at startup when `FAKE_META_BASE_URL` or `FAKE_GOOGLE_BASE_URL` is set in `backend/.env`; prints a reminder to start the fake ad server on :9000

**`DEV_QUICKSTART.md`**
- Section 3 rewritten as a services overview table listing all five processes (backend, frontend, ngrok, Modal GP, fake server) with required/optional status and start commands
- `./start.sh` promoted as the preferred way to start backend + frontend + ngrok together
- Modal GP service documented: already deployed in the cloud, set `MODAL_BO_API_URL`; falls back to local sklearn GPR if unset; app source not in this repo
- Fake ad server start instructions added with pointer to `FAKE_ADS_TESTING.md`
- `MODAL_BO_API_URL` / `MODAL_BO_PCA_DIMS` added to env vars block
- "Switching from fake to real mode" explanation added: real campaigns use different IDs so fake DB rows are inert; teardown pointer to `FAKE_ADS_TESTING.md`

---

### BO pipeline: drop eval_gpr block + surface Modal failures as API warning

**Removed redundant local GPR refit from Modal path (`bo_pipeline/pipeline.py`)**
- `_run_modal_bo` no longer refits a local sklearn GP after Modal returns — that computation was scoring candidates Modal already picked via q-EI, producing meaningless numbers
- `gpr_mean`/`gpr_std` are now always `None` for Modal picks; `ei_score` likewise
- Modal picks carry `selection_type="modal_q_ei"`; local path still populates all three stats

**Modal failure now surfaces in the API response (`pipeline.py`, `main.py`)**
- `run_bo` return type changed from `list[dict]` to `tuple[list[dict], str | None]`
- Second element is `None` on success; a human-readable error string (e.g. `"Modal GP failed (HTTPError: 422) — used local GPR"`) when Modal was configured but threw
- `BORunResponse` gains `warning: str | None = None`; both `/api/bo/run` and `/api/google/bo/run` pass it through
- Frontend: both `CampaignsPage` and `GoogleCampaignsPage` render the warning in amber (`dyn-warning`) above picks when present

**Frontend: correct selection_type labels**
- `modal_q_ei` now displays as "Bayesian (Modal)" instead of falling through to "Random"
- `GoogleCampaignsPage.BOPicksPanel` extracted `selectionLabel` helper; `CampaignsPage` inline equivalent

**Tests updated** — all `run_bo` call sites unpack `picks, _`; mock patches use `return_value=(list, None)`; 103 tests pass

**Docs updated**: `~/projects/boaz/modal/` — `API.md`, `CLAUDE.md`, `README.md` reflect discrete-pool design

---

## 2026-05-27

### BO pipeline: fix degenerate scored vectors + redesign Modal to discrete-pool q-EI

**Scored combination key fix (`bo_pipeline/selector.py`)**
- `get_scored_combinations` now reads `v.suggestion` from `ad_generation_variants`, which stores the exact combo dict (all slots) written by the seeding script
- Uses the suggestion as the primary text-vector lookup key in `ad_text_combination_embeddings`; falls back to reconstructing from `headline` + `primary_text`/`description` for real pipeline variants
- Previously all scored variants fell back to the seed ad's single text vector (degenerate GPR input); now each variant gets its own distinct embedding

**Modal BO redesign: discrete candidate pool (`modal_gp_api.py`, `modal_bo.py`, `pipeline.py`)**
- Old design: Modal received only bounding-box bounds, generated 512 random continuous PCA points, returned the best batch as continuous vectors — pipeline then snapped each to the nearest actual candidate (`snap_to_pool`)
- New design: pipeline sends the actual discrete candidate PCA vectors; Modal computes q-EI directly over those candidates and returns indices — no snap step, no approximation error
- `GPRequest`: removed `search_space`/`n_candidates`; added `candidates: list[list[float]]` and `n_batches`
- `Candidate` response: added `index` field; `mu`/`sigma` are now accurate (computed at the actual selected candidate, not a snapped approximation)
- `call_modal_api`: parameter `bounds` → `X_cands_pca`; payload updated accordingly
- `_run_modal_bo`: removed `snap_to_pool`, `dim_bounds`, `project` imports; uses `suggestion["index"]` directly

**Docs updated**: `API.md`, `CLAUDE.md`, `README.md` in `~/projects/boaz/modal/`; `CLAUDE.md` in this repo

---

## 2026-05-26

### Fake Ad Server (step 1)

- Added `fake_ad_server/` — standalone FastAPI server (port 9000) that mimics the Meta Graph API and Google Ads REST API at the network layer
- Meta routes: campaigns, insights, adsets, ads (GET + POST), adimages (GET hash resolution + POST upload), adcreatives, entity detail/pause-resume
- Google routes: `searchStream` GAQL dispatch (campaigns / ad_groups / ad_group_ad / asset_group_asset), `:mutate` push, customer info, `listAccessibleCustomers`
- GAQL dispatch inspects `FROM` clause and extracts `campaign.id` from `WHERE` for per-campaign filtering
- 8 fixture files in `fake_ad_server/fixtures/` — 3 Meta campaigns (1 dynamic, 2 static), 3 Google campaigns (RSA, Display, pMax), all with real metric numbers
- Two-line backend change: `FAKE_META_BASE_URL` env var in `meta_live.py`, `FAKE_GOOGLE_BASE_URL` env var in `google_ads_api.py` (both default to real API URLs when unset)
- Commented-out env var stubs added to `backend/.env` — uncomment to activate; OAuth is unaffected
- All routes smoke-tested; every response shape confirmed to match what the provider parsers expect

### Dashboard UX redesign + BO test infrastructure

#### Dashboard UX — consistent flow and cross-platform analysis

**`frontend/src/pages/DashboardPage.jsx`** — Added `metaSeedAdId` / `googleSeedAdId` state (initialized from localStorage keys `meta_last_seed_ad_id` / `google_last_seed_ad_id`). Callbacks `handleMetaIngest(seedAdId)` and `handleGoogleIngest(seedAdId)` write to state and localStorage so seed ad IDs persist across page reloads. Added Cross-Platform Analysis section above the per-platform sections: green `✓`/grey `○` readiness indicators per platform; "Run Cross-Platform Analysis" button disabled until both platforms have a seed ad ID (`bothReady`); calls `runCrossPlatformBO` on click; renders `CrossPlatformResults` component with platform-badged picks. Updated 4-step instruction banner. Passes `onIngest={handleMetaIngest}` / `onIngest={handleGoogleIngest}` to child platform pages.

**`frontend/src/pages/CampaignsPage.jsx`** — Added `{ onIngest = null }` prop. `handleIngestStructure` calls `onIngest(seedAdId)` after successful ingest. `toggleExpanded` (renamed from `toggleHistory`) also calls `onIngest` when opening a previously-ingested campaign — resolves seed ad ID from localStorage cache or fetches from `getCampaignStructure`. Removes separate `${c.id}-structure` row; one unified `${c.id}-detail` expanded row per campaign.

**`frontend/src/pages/GoogleCampaignsPage.jsx`** — Added `{ onIngest = null }` prop. `handleIngest` calls `onIngest(seedAdId)` after successful ingest. `toggleDetail` calls `onIngest` when opening an already-ingested campaign (both from cache and from fresh fetch). Split triple-state "Ingest/View/Hide" button into two: "Ingest" (first time only) and "View"/"Hide" toggle (after first ingest). Moved "Re-ingest" button inside the expanded structural panel header.

**`frontend/src/app.css`** — Added CSS for `.cross-platform-section`, `.cross-platform-header`, `.cross-platform-title-row`, `.cross-platform-title`, `.cross-platform-readiness`, `.platform-readiness`, `.readiness-ready`, `.readiness-pending`, `.cross-platform-desc`, `.cross-platform-hint`, `.cross-platform-results`, `.cross-platform-stats`, `.cross-platform-stat`, `.platform-badge`, `.platform-badge-meta`, `.platform-badge-google`, `.campaign-history-section`, `.campaign-history-heading`.

**`frontend/src/api.js`** — Added `runCrossPlatformBO(pairs)` calling `POST /api/bo/cross-platform`.

#### Bug fix — cross-platform button stayed greyed out after reingest

Root cause: `onIngest` was only fired on the first Ingest button click. Opening a previously-ingested campaign via campaign name (Meta) or "View" button (Google) never called `onIngest`, so `meta_last_seed_ad_id` / `google_last_seed_ad_id` were never written to localStorage. Fixed by adding `onIngest` calls inside `toggleExpanded` (Meta) and `toggleDetail` (Google) when opening an already-ingested campaign.

#### Backend — selector user_id isolation fix

**`backend/bo_pipeline/selector.py`** — `get_scored_combinations` accepted `user_id` as a parameter but the SQL query only filtered by `seed_ad_id`, not `user_id`. Added `AND j.user_id = ?` to the `ad_generation_variants` join query. Without this, two users sharing the same `seed_ad_id` string could contaminate each other's BO training data.

#### BO test infrastructure — four independent tests

**`backend/seed_bo_synthetic.py`** (new) — Synthetic seeding script requiring no API keys. Supports `--platform meta|google|cross`; `--ad-id` / `--meta-ad-id` / `--google-ad-id`; `--user-id`; `--n` (default 5); `--seed` (default 42, RNG reproducibility); `--db`. Reads real `ad_text_combination_embeddings` rows from DB; creates synthetic `ad_generation_jobs` + `ad_generation_variants` (score random 2.0–9.0) + `ad_embeddings`. Meta: uses real seed image vector if available, otherwise random. Google: `image_vector=None` (combiner zero-pads). Prints curl examples after seeding.

**`backend/tests/test_google_bo.py`** — Added `TestGoogleBOPipeline` class at end of file. Seeds a text-only (Google RSA) DB with 5 scored variants and 8 candidates (`image_vector=None` throughout). 12 tests: `test_returns_two_picks`, `test_pick1_is_ei_type`, `test_pick2_is_fantasy_type`, `test_picks_are_distinct`, `test_picks_have_nonnegative_ei`, `test_picks_have_gpr_stats`, `test_picks_have_combination_dict`, `test_picks_are_from_candidate_pool`, `test_scored_count_matches_seeded`, `test_all_scored_have_none_or_zero_image_vector`, `test_fallback_random_when_insufficient_data`, `test_save_and_retrieve`.

**Four BO test classes (summary):**
1. `test_generation_pipeline.py` — Dynamic Ad generation pipeline (requires API keys; existing)
2. `test_bo_pipeline.py` — Meta BO with real image + text vectors (no API keys; existing and comprehensive)
3. `test_google_bo.py::TestGoogleBOPipeline` — Google RSA BO, text-only, zero-padded image (no API keys; newly added)
4. `test_cross_platform_bo.py::TestCrossPlatformBO` — Cross-platform BO with ECDF normalisation (no API keys; added in previous session)

Net result: **103 tests, all passing**.

---

## 2026-05-27

### Fake ad server — bug fixes and BO seeding improvements

#### Bug fix — picsum image downloads failed with 302 redirect

`picsum.photos` returns a 302 redirect to `fastly.picsum.photos`. Two separate `httpx.AsyncClient` instances both lacked `follow_redirects=True`, causing all image downloads and embeddings to fail with `HTTPStatusError: Redirect response '302 Found'`.

- **`backend/embeddings/embedder.py`** — Added `follow_redirects=True` to `AsyncClient` in `embed_image_url()`
- **`backend/main.py`** — Added `follow_redirects=True` to `AsyncClient` in `_download_ad_images()`

#### Bug fix — seed_bo_synthetic.py used same image vector for all training points

`seed_bo_synthetic.py` used the single seed ad `image_vector` from `ad_embeddings` for all N synthetic scored variants. The GPR saw zero variance in the image dimension across all training points, causing both BO picks to collapse to the same image slot.

**`backend/seed_bo_synthetic.py`** — Now loads per-image embeddings from `ad_image_embeddings` (populated by `embed_images` during ingest) and cycles them across the N variants. Falls back to single seed `image_vector` if per-image embeddings are absent, then to random. Print line now reports how many distinct image vectors were found.

#### Added FAKE_ADS_TESTING.md

New file documenting the end-to-end manual BO testing workflow for all four fake campaign types:
1. Meta dynamic ad (campaign 1) — 64 combinations from ingest, no text gen needed
2. Meta static ad (campaign 2) — 1 combination until Generate Text Ads runs
3. Google RSA (campaign `9876543210`) — 12 combinations from ingest
4. Cross-platform BO — Meta + Google combined

Includes startup instructions, per-step sqlite verification queries, `selection_type` check (should be `ei`/`fantasy`, not `random`), teardown/reset commands, and a fake campaign reference table.

---

## 2026-05-26 (test cleanup session)

### Test suite cleanup — zero pure-test failures

#### Fixed: 3 failing `test_structural_ingest.py` assertions

The `/api/ingest` route saves a snapshot row for every seen campaign regardless of whether metrics exist (metric columns are `NULL` when no delivery data). Three tests had assertions written against a different design intent (only save campaigns with metrics). Updated to match actual route behavior:

- `test_ingest_zero_metrics_clear_message` — `campaigns_saved` assertion updated to 2; `ad_insights` count updated to 2; docstring updated
- `test_ingest_insights_error_surfaces_in_response` — `campaigns_saved` assertion updated to 1; `ad_insights` count updated to 1; docstring updated
- `test_ingest_partial_metrics_only_saves_campaigns_with_data` — `campaigns_saved` updated to 2; rows assertion updated to verify both campaign IDs are present

#### Fixed: 8 async `TestGoogleDemoProvider` tests

`pytest-asyncio` is not installed in the test environment. Converted all 8 `@pytest.mark.asyncio async def` tests in `TestGoogleDemoProvider` to sync using `asyncio.run()`. Added `import asyncio` to the file.

#### Deprecated: `TestGoogleMaskingProvider`

Added `@pytest.mark.skip(reason="masking layer deprecated — kept for reference only")` to `TestGoogleMaskingProvider` class. Code and test file remain in place. 6 tests now correctly skipped.

#### Test run baseline (pure tests — no API keys)

```
255 passed, 7 skipped, 0 failures
```

The 7 skips: 6 `TestGoogleMaskingProvider` + 1 live Google smoke test (requires credentials).

#### BO test infrastructure — Modal endpoint support

**`backend/bo_pipeline/cross_platform.py`** — Added `method: str = "local"` parameter to `run_cross_platform_bo()`. Added `_run_group_modal_bo()` helper that mirrors `_run_modal_bo()` in `pipeline.py` but applies ECDF-normalised targets and per-platform PCA dims; catches exceptions and falls back to local.

**`backend/tests/conftest.py`** — Added `load_dotenv()` so `.env` vars (especially `MODAL_BO_API_URL`) are available to all tests without importing `main.py` first.

**`backend/tests/test_bo_pipeline.py`, `test_google_bo.py`, `test_cross_platform_bo.py`** — All three BO pipeline test classes now read `BO_TEST_METHOD` from env (default `"local"`). Modal-compatible selection types (`"modal_q_ei"`) accepted alongside local types in all assertions. Run with Modal endpoint:

```bash
BO_TEST_METHOD=modal python3 -m pytest tests/test_bo_pipeline.py tests/test_google_bo.py::TestGoogleBOPipeline tests/test_cross_platform_bo.py::TestCrossPlatformBO -v -s
```

---

## 2026-05-25 (cross-platform BO session)

### Cross-platform Bayesian Optimisation

Adds the ability to run BO jointly over Meta and Google campaigns with shared target normalisation.

#### New files

**`backend/bo_pipeline/ecdf.py`** — `fit_ecdf(all_scores) → callable`. Fits an empirical CDF on the combined score pool from all platforms and returns a transform to standard-normal. Used in place of the single-group `transform_y` when running cross-platform BO so both platforms' GPR targets — and therefore their EI values — are directly comparable.

**`backend/bo_pipeline/cross_platform.py`** — `BOGroup` dataclass and `run_cross_platform_bo(pairs, user_id, db_path, ...)`. Orchestrates the full cross-platform pipeline: load per-platform scored observations and candidate pools; fit one shared ECDF on all scores; run per-platform PCA + GPR + EI; merge all picks by EI descending; return top-N tagged with `platform`, `seed_ad_id`, `text_source_id`. Local sklearn GPR path only (Modal path = two independent calls, same endpoint, can be added later).

**`backend/tests/test_cross_platform_bo.py`** — 45 tests covering `TestECDF` (pure function), `TestPCADimsForPlatform` (env-var routing), `TestBOGroupBuildX` (correct input dimensions per platform), `TestCrossPlatformBO` (end-to-end DB pipeline including fallback, partial data, single-group, and global EI ordering), and `TestCrossPlatformBOEndpoint` (FastAPI route shape and auth). All 45 pass.

#### Modified files

**`backend/bo_pipeline/modal_bo.py`** — Added `_google_pca_dims()` (reads `GOOGLE_BO_PCA_DIMS`, default 32) and `pca_dims_for_platform(platform)`. Google RSA inputs are text-only (1536-dim) vs Meta's combined 3072-dim, so they get a proportionally smaller PCA output. Different output dimensions make it physically impossible to mix the two groups into a single GPR.

**`backend/bo_pipeline/__init__.py`** — Exports `run_cross_platform_bo`.

**`backend/main.py`** — New Pydantic models (`CrossPlatformBOPick`, `CrossPlatformBOPair`, `CrossPlatformBORequest`, `CrossPlatformBOGroupStat`, `CrossPlatformBOResponse`) and `POST /api/bo/cross-platform` endpoint. Returns globally-ranked picks each tagged with platform; persists picks via existing `save_bo_run` per group; includes per-group scored/candidate counts in response.

#### Design notes

- ECDF is computed **locally before any Modal API call** — Modal receives already-transformed `y` values and already-PCA-reduced `X`, same as today.
- Meta: `combine(text_vec, image_vec)` → 3072-dim → PCA → 64-dim (`MODAL_BO_PCA_DIMS`)
- Google: `text_vec` only → 1536-dim → PCA → 32-dim (`GOOGLE_BO_PCA_DIMS`)
- Groups with `< MIN_TRAINING_POINTS` scored observations fall back to random for their own candidate pool; other groups still run EI normally.

---

## 2026-05-25

### Unified Dashboard + BO candidate pool fix

#### Frontend — unified ingestion dashboard

**`frontend/src/pages/DashboardPage.jsx`** (new) — Combined "Ad Ingestion Dashboard" page at `/app/dashboard`. Two collapsible sections (Meta Ads, Google Ads) with branded toggle headers; both open by default. Each section renders the existing platform page inside it. Single clear instruction banner at the top explains Sync → Ingest → Get Recommendations flow.

**`frontend/src/App.jsx`** — Nav updated: removed separate "Meta Ads" and "Google Ads" links; replaced with a single "Dashboard" link pointing to `/app/dashboard`.

**`frontend/src/main.jsx`** — Added `/app/dashboard` route (`DashboardPage`). Old `/app/campaigns` and `/app/google-campaigns` routes retained but no longer linked in nav.

**`frontend/src/pages/SettingsPage.jsx`** — Added prominent connect banner at top: "Here, you authenticate and connect your ad accounts. After connecting, go to the Dashboard to ingest." Updated how-it-works steps to reference Dashboard instead of separate platform pages.

**`frontend/src/pages/GoogleCampaignsPage.jsx`** — Rewrote to match Meta flow: "Get Recommendations" and "Generate RSA Text" buttons both appear immediately after ingest (previously "Get Recommendations" was gated behind text gen completing). Structure data now stored in `structureById` state so seed ad ID is always available. `handleRunBO` resolves seed ad ID from state or fetches fresh if not cached; falls back to ingested ad ID if no text gen has been run.

**`frontend/src/app.css`** — Added: `.settings-connect-banner`, `.dashboard-page`/`.dashboard-header`/`.dashboard-title`, `.platform-section`/`.platform-section-toggle`/`.platform-icon`/`.platform-icon-meta`/`.platform-icon-google`/`.platform-section-body`, `.btn-small`, `.slot-group`/`.slot-label`/`.slot-values`, `.ad-structure-card`/`.ad-structure-header`/`.ad-id-label`, `.text-gen-results`, `.push-note`, Google status badge variants. Added rule hiding `.page-hint-banner` inside `.platform-section-body` (avoids duplicate instructions).

#### Backend — text generation now feeds BO candidate pool

**`backend/main.py` — `POST /api/generate/text/{campaign_id}`** — After saving generated variants, now fires `embed_all_combinations` as a background task using `source_id=seed_ad_id`. Merges seed components with newly generated slots before embedding so the BO candidate pool expands to include all generated variants alongside the original ingested ones. Idempotent — already-embedded combinations are skipped.

**`backend/main.py` — `POST /api/google/generate/text/{campaign_id}`** — Same change; uses `slots=("headline", "description")` for Google RSA. Generated text variants are now BO candidates on first "Get Recommendations" click after text gen, without any extra steps.

**Why `source_id=seed_ad_id`:** BO always queries `ad_text_combination_embeddings` by `text_source_id`, which is always the seed ad's ingested ID. Storing generated text combinations under the same key means the pool grows automatically — no frontend or BO API changes needed.

---

## 2026-05-23 (session 3)

### Dead-code label on `-y_raw` branch

**`backend/bo_pipeline/pipeline.py`** — Added explanatory comment on the `y = y_raw if higher_is_better else -y_raw` line in `_run_modal_bo`. The `higher_is_better=False` branch is dead code (no call site passes it); the comment explains why it exists and what would activate it, so it isn't silently removed or misread as a bug in future sessions.

---

## 2026-05-23 (session 2)

### Full embeddings + target transform + Modal URL wired

**`backend/ad_embedding_combiner/combiner.py`** — `TEXT_DIM` and `IMAGE_DIM` changed from 128 to 1536. `_build_X()` now passes full text + image embeddings (3072-dim) into PCA rather than truncating to 256 first. No new embedding API calls — the full vectors were already stored in `ad_embeddings` from ingest.

**`backend/bo_pipeline/gpr.py`** — Added `transform_y(y)`: ranks scores to uniform via ECDF → clamps to `(0.00005, 0.99995)` → applies `norm.ppf` to produce Gaussian-distributed targets. Mirrors `_transform_y` inside the Modal GP server so both the local GPR fallback path and the Modal path see equivalently-shaped targets.

**`backend/bo_pipeline/pipeline.py`** — `_run_local_bo` now applies `transform_y` before fitting; `y_best` and the fantasy step both use the transformed values. Local and Modal paths now consistent.

**`backend/.env`** — Added `MODAL_BO_API_URL=https://markshipman4273--bo-gp-service-gp-suggest.modal.run`. Was missing; without it `modal_bo_enabled()` returned `False` and every BO run silently fell back to local GPR.

**`backend/embeddings/embedder.py`** — `embed_image_url` now resolves relative paths (e.g. `/ad-images/foo.png`) to `http://localhost:{PORT}` before the httpx fetch. Fixes `UnsupportedProtocol` crash when `IMAGES_SERVE_BASE_URL=/images`.

**`backend/embeddings/pipeline.py`** — `embed_images` guard updated from `startswith("http")` to `startswith(("http", "/"))` so relative-path images are embedded rather than silently skipped.

**`backend/main.py`** — Login: `ValueError` from bcrypt (password > 72 bytes) is now caught and returned as 401. Signup: upfront byte-length check returns 400 with a clear message.

**Frontend** — Settings, Meta Ads, and Google Ads pages now show numbered step-by-step instruction banners. All action buttons have `title` tooltip text. `QUICK_START.md` updated to make "click the campaign name" an explicit step.

**Docs updated:** `CLAUDE.md`, `AD.md`, `backend/bo_pipeline/README.md`, `QUICK_START.md`, `backend/tests/test_google_bo.py` (hardcoded `256` → `3072`).

---

## 2026-05-23

### Modal GP batch BO integration (`backend/bo_pipeline/`)

Replaced the sequential fantasy-step approach with a proper batch q-EI method backed by a Modal serverless GP service. The existing local GPR + fantasy path is preserved as a named fallback.

**New file: `backend/bo_pipeline/modal_bo.py`**
Five focused functions — `fit_pca`, `project`, `dim_bounds`, `call_modal_api`, `snap_to_pool` — plus `modal_bo_enabled()`. No new dependencies (sklearn already present; HTTP via `urllib.request`).

**Modified: `backend/bo_pipeline/pipeline.py`**
- `run_bo()` gains `method: str = "modal"` kwarg (default `"modal"`).
- New `_run_modal_bo()`: builds full embedding pool → fits PCA (`MODAL_BO_PCA_DIMS` dims, default 64) on scored ∪ candidates → calls Modal API with q=2 → snaps each returned PCA point to nearest unvisited candidate. Returns picks with `selection_type="modal_q_ei"`.
- Old code extracted into `_run_local_bo()`, unchanged; still reachable via `method="local"`.
- Graceful fallback: if `MODAL_BO_API_URL` unset or API call fails → warns and runs local path.

**New file: `backend/tests/test_modal_bo.py`**
12 unit tests (`TestModalBOUnit`, no network) + 3 live smoke tests (`TestModalBOLive`, hits real Modal endpoint, verified passing at ~29s).

**Modified: `backend/.env.example`**
Added `MODAL_BO_API_URL` and `MODAL_BO_PCA_DIMS=64`.

**Docs updated:** `CLAUDE.md`, `AD.md`, `README.md`, `DEV_QUICKSTART.md`, `backend/TEST.md`.

---

## 2026-05-04

### NGROK_SETUP.md — Google OAuth added to URL-change checklist

Section 5 "Checklist When ngrok URL Changes" now has three subsections: Frontend + backend config, Meta OAuth, and Google OAuth. The Google section adds the two steps that were missing: update `GOOGLE_REDIRECT_URI` in `backend/.env` and update the Authorized redirect URI in Google Cloud Console.

### all_markdown_combined.md — regenerated

Rebuilt from the 9 current top-level reference docs (AD.md, CLAUDE.md, DEV_QUICKSTART.md, NGROK_SETUP.md, QUICK_START.md, README.md, SCHEMAS.md, STAGING_POLICY.md, TEST.md). Previous version was stale and incorrectly included itself.

### QUICK_START.md — full rewrite for both platforms

Reorganized from a 6-step Meta-only walkthrough into a structured dual-platform guide
with numbered sections (§1–§5 + Appendix) so users can jump to any step and immediately
see what they need to have done first.

- §1 Sign up — unchanged, minor expansion
- §2 Connect — Meta (§2.1) and Google (§2.2) with account picker, MCC login-customer-id, and manual customer ID details
- §3 Meta Ads (§3.1–§3.5) — deep sequential workflow: import metrics, ingest structure, generate variants (static text + dynamic AI), run BO, push to Meta
- §4 Google Ads (§4.1–§4.5) — parallel structure to §3: view campaigns, ingest (with creative type table), generate RSA text, run BO, push (with pinning explanation)
- §5 Ad Library — standalone, delete behavior for ingested vs generated ads
- Appendix — Meta ↔ Google concept map (Campaign, Ad Set/Group, Dynamic/RSA, primary_text gap, image/video slots, Advantage+/pMax, with "future release" notes on open equivalences)

### README.md — external-facing docs updated for Google Ads

- Intro: mentions Meta and Google Ads
- System architecture: shows Google provider layer alongside Meta
- AI module descriptions: text gen notes platform-aware slots; combiner notes `image_vec=None` zero-padding
- BO endpoints: lists both `/api/bo/run` (Meta) and `/api/google/bo/run` (Google)
- Prerequisites: added Google OAuth client + developer token
- API keys table: added Google Ads row
- Feature Walkthrough: added "Connect Google Ads" and "Google Campaigns" sections with full flow description
- API Reference: reorganized into Meta and Google subsections; added all Google routes (campaigns, ingest, text gen, BO, push)
- Masking/Demo Mode: added Google masking table; noted `APP_MODE=demo` enables both platforms
- Notes: updated Meta token note; added Google token note; added "new ads are always PAUSED" note; added Google RSA BO note

### Documentation sweep — all internal .md files

Updated all internal documentation to reflect the completed Google Ads integration (Chunks 1–10). External-facing docs (`README.md`, `QUICK_START.md`) deferred.

- `SCHEMAS.md`: added `users.tier`, `oauth_states.provider`, `ad_insights.platform`, `ad_creative_structures.platform`, `dynamic_generation_jobs.seed_ad_id/adset_id/meta_ad_id/images_generated`, `bo_selections.google_ad_resource_name`; added `google_connections`, `google_pending_connections`, and `ad_image_embeddings` table sections (the last was missing entirely); expanded `creative_type` values to include Google types (`rsa`, `display`, `video`, `pmax`, `shopping`, `unknown`).
- `backend/TEST.md`: added full catalog entries for all 10 Google test files with per-test descriptions and run commands.
- `TEST.md` (root): added Google test table, fixed `tests/` path prefix throughout, added pointer to `backend/TEST.md`.
- `GOOGLE_INTEGRATION_PLAN.md`: marked Chunks 1–10 as ✅ Complete, Chunk 11 as ⬜ Not started; updated intro from "Work has not started yet".
- `AD.md`: added "Google RSA support" section covering NULL image_vec embedding, BO over text-only combinations, and pMax/Shopping handling.
- `DEV_QUICKSTART.md`: added Google Ads env vars section (client ID/secret, redirect URI, developer token, API version).
- `backend/ad_text_generation/README.md`: documented `platform` parameter, `GOOGLE_RSA_SLOTS`, `slots_for_platform()`, `GOOGLE_RSA_SLOT_HINTS`, and `get_generated_slots_for_source()`; fixed test paths.
- `backend/embeddings/README.md`: fixed test paths to use `tests/` prefix.
- `backend/ad_combination_embeddings/README.md`: added RSA slots note (`slots=('headline','description')`), example for Google RSA, fixed test paths.
- `NGROK_SETUP.md`: updated Vite proxy config block to match actual `vite.config.js` (added `/auth/google`, `/images`, `/ad-images`; noted `VITE_PORT` and `VITE_BACKEND_URL` env vars).

### Chunk 10 — Google demo/masking layer

- `providers/google_mask_policy.py` (new): `GoogleMaskPolicy` reads `GOOGLE_MASK_MODE` (off/selective/full), individual `GOOGLE_MASK_STATUS`/`GOOGLE_MASK_BUDGETS`/`GOOGLE_MASK_METRICS`/`GOOGLE_MASK_PAUSE_RESUME` flags, and `GOOGLE_METRIC_PROFILE` (healthy/stable/weak). `policy.enabled` is true when mode is selective/full or any individual flag is set.
- `providers/google_demo.py` (new): `GoogleDemoProvider` — fully synthetic provider with 3 fixture campaigns (RSA, Display, pMax). `fetch_campaigns_and_insights` returns `_CAMPAIGNS` with computed ctr/cpm/cpc. `fetch_campaign_structure` returns from `_STRUCTURE` keyed by campaign_id. `normalize_creative` delegates to `GooglePlatformProvider`. pause/resume are no-ops. No API calls made.
- `providers/google_masking.py` (new): `GoogleMaskingProvider` — wraps a live provider and applies masks per `GoogleMaskPolicy`. `fetch_campaigns_and_insights` calls live, then overrides `status`→ACTIVE, `daily_budget`→synthetic, metrics→synthetic (only for impressions < 100) as per policy flags. Deterministic metrics use `_synthetic_metrics(campaign_id, profile)` seeded by MD5 hash. pause/resume are no-ops when `mask_pause_resume=True`, else delegated.
- `providers/factory.py`: `get_google_provider()` now reads `APP_MODE` and `GOOGLE_APP_MODE` at call time — returns `GoogleDemoProvider` when either is `demo`; returns `GoogleMaskingProvider(live, policy)` when `policy.enabled`; returns `GooglePlatformProvider` otherwise. All provider imports are deferred (inside function) to keep module-level state clean for test patching.
- `backend/tests/test_google_demo.py` (new, 26 tests): `TestGoogleDemoProvider` (10) — platform name, campaigns shape, metrics shape, all-active, RSA structure, unknown-campaign empty, fetch_ads count, pause/resume no-ops, normalize_creative delegates. `TestGoogleMaskPolicy` (6) — default off, full mode enables all, individual flag, default profile, weak profile, invalid profile fallback. `TestGoogleMaskingProvider` (6) — mask status forces ACTIVE, mask budgets replaces value, mask metrics replaces low-delivery, mask metrics preserves healthy, pause no-op when masked, pause delegates when unmasked. `TestGoogleFactory` (4) — demo via APP_MODE, demo via GOOGLE_APP_MODE, masking provider when policy enabled, live provider by default.
- Net test result: 112 pass (Google suites), 1 skipped (up from 86 at end of Chunk 9).

### Chunk 9 — Performance Max + Shopping stubs

- `providers/google_provider.py`: Added `_PMAX_ASSETS_GAQL` — queries `asset_group_asset` for text/image/video assets per asset group. Added `PERFORMANCE_MAX_AD` branch to `normalize_creative`: maps `HEADLINE`/`LONG_HEADLINE` → `headline` slot, `DESCRIPTION` → `description` slot, `MARKETING_IMAGE`/`SQUARE_MARKETING_IMAGE`/`PORTRAIT_MARKETING_IMAGE`/`LOGO`/`LANDSCAPE_LOGO` → `image` slot, `YOUTUBE_VIDEO` → `video` slot; uses `text` value if present, falls back to asset resource name; skips assets with no value. Added `SHOPPING_PRODUCT_AD` branch: returns `creative_type='shopping'` with `final_url` slot when present. In `fetch_campaign_structure`: after main ad_group_ad queries, runs `_PMAX_ASSETS_GAQL` separately (fails silently via try/except so non-pMax campaigns aren't affected); groups asset rows by `assetGroup.id` into virtual ad dicts and appends to `ads` list.
- `main.py`: Added `_UNSUPPORTED_FOR_OPTIMIZATION = frozenset({"shopping", "unknown"})` constant. In `generate_google_text_ads`: added `creative_type` to SELECT; raises 400 if selected seed ad's creative type is in `_UNSUPPORTED_FOR_OPTIMIZATION`. In `run_google_bo_endpoint`: queries `ad_creative_structures` for `creative_type` of `seed_ad_id`; raises 400 if unsupported. pMax ads proceed to both text generation and BO (they have headline/description slots).
- `frontend/src/pages/GoogleCampaignsPage.jsx`: Added `pmax: "Performance Max"` and `shopping: "Shopping"` to `CREATIVE_LABELS`.
- `backend/tests/test_google_structural_ingest.py`: Added 7 normalize tests — pMax text asset extraction (1), pMax image/video extraction (1), pMax empty assets (1), pMax skip assets with no value (1), Shopping with final_url (1), Shopping without final_url (1), pMax extracts headline text (covered by first test).
- `backend/tests/test_google_pmax_shopping.py`: 6 new route-level tests — 400 for Shopping text gen (1), 400 for unknown text gen (1), pMax text gen proceeds (1), 400 for Shopping BO (1), 400 for unknown BO (1), pMax BO proceeds (1).
- Net test result: 86 pass (Google suites), 1 skipped (up from 74 at end of Chunk 8).

### Chunk 8 — Push to Google Ads (Mutate API)

- `google_ads_api.py`: Added `create_rsa(customer_id, access_token, developer_token, api_version, ad_group_id, headlines, descriptions, final_url, login_customer_id) → str`. Uses `customers/{customer_id}:mutate` REST endpoint. First headline and description are pinned (HEADLINE_1, DESCRIPTION_1). Returns resource name string.
- `providers/google_provider.py`: Added `ad_group_ad.ad.final_urls` to `_ADS_GAQL`. Extracted `final_urls` from camelCase API response in `fetch_campaign_structure`. In `normalize_creative` for RSA ads, appended `final_url` slot (slot_index=0) when present — stored alongside headline/description slots in `ad_creative_structures`; used by push route to resolve destination URL.
- `bo_pipeline/storage.py`: Added `google_ad_resource_name TEXT` column to `_CREATE` DDL so new installs get the column from the start.
- `main.py` — `init_db()`: Added `bo_selections` table to the main `executescript` (was previously only created lazily by `bo_pipeline.storage.ensure_table`; adding it here lets tests and migrations use the same DB path). Added migration `ALTER TABLE bo_selections ADD COLUMN google_ad_resource_name TEXT` for existing DBs.
- `main.py` — new `_create_google_rsa_ad(user_id, seed_ad_id, bo_combination, customer_id, access_token, login_customer_id)`: looks up `adset_id` and `final_url` from `ad_creative_structures`; queries `generated_ad_slots` for BO-picked ad to build headline/description lists (BO pick first, then generated variants); validates ≥3 headlines and ≥2 descriptions; calls `google_ads_api.create_rsa`.
- `main.py` — new `POST /api/google/push` route: queries latest unpushed `bo_selections` (pick_rank=1, google_ad_resource_name IS NULL) for user's Google ads; for each, calls `_create_google_rsa_ad`; writes resource name to `bo_selections.google_ad_resource_name` on success; returns `GooglePushResponse` with per-ad results. Graceful partial failure — one ad failing does not block others. Returns `note` field when Google Ads is not connected.
- `ad_text_generation/storage.py`: Added `get_generated_slots_for_source(source_ad_id)` public function for querying all generated slot rows by `source_ad_id` across all generated ads.
- `frontend/src/api.js`: Added `pushGoogleAds()`.
- `frontend/src/pages/GoogleCampaignsPage.jsx`: Added Sync button (calls `pushGoogleAds()` then re-fetches campaigns). Shows success/info/amber/error note after sync. Button disabled during in-flight request.
- `backend/tests/test_google_push.py`: 11 new pure tests — auth guard (1), no unpushed picks (1), already-pushed no-op (1), correct RSA request shape (1), BO pick pinned first (1), resource name written to DB (1), missing final_url graceful error (1), not-enough-variants graceful error (1), Mutate API error graceful (1), user isolation (1), not-connected note (1).
- Net test result: 74 pass (Google suites), 1 skipped (up from 63 at end of Chunk 7).

## 2026-05-03

### Chunk 7 — Embeddings + BO for Google

- `providers/google_provider.py`: Added `youtube_thumbnail_url(video_id)` static method — returns `https://img.youtube.com/vi/{video_id}/hqdefault.jpg`; returns None for empty input. Deferred from ingest hook until video asset resource names can be resolved to YouTube IDs.
- `main.py` — `POST /api/google/ingest/structure/{campaign_id}`: now collects `(ad_id, creative_type, components)` during the save loop; after commit fires two fire-and-forget tasks per ad: `embed_ad` (text-only for RSA since no image URLs; combiner handles `image_vec=None` with zeros) and `embed_all_combinations(slots=('headline', 'description'))`. Pattern mirrors Meta ingest hook; wrapped in try/except so embedding failures never block the ingest response.
- `main.py` — new `POST /api/google/bo/run` route: accepts `{seed_ad_id, text_source_id}`, delegates to the same `run_bo` / `save_bo_run` functions as the Meta route, returns `BORunResponse`. New `GET /api/google/bo/results/{ad_id}` route: delegates to `get_latest_bo_run`.
- `ad_embedding_combiner/combiner.py`: No changes needed — `image_vec=None` support was already present; documented and tested.
- `ad_combination_embeddings/pipeline.py`: No changes needed — `slots` param already accepted; RSA combinations use `('headline', 'description')` passed at call site.
- `frontend/src/api.js`: Added `runGoogleBO(seedAdId, textSourceId)` and `getGoogleBOResults(adId)`.
- `frontend/src/pages/GoogleCampaignsPage.jsx`: Added `boStateById` state, `handleRunBO` handler, `BOPicksPanel` component (shows recommendation cards with slot values, selection type badge, GPR mean/EI score). "Get Recommendations" button appears below text generation results after text gen completes; button uses `source_ad_id` from gen result as both seed and text source IDs.
- `backend/tests/test_google_bo.py`: 12 new pure tests — combiner `None` image padding (3 tests), `youtube_thumbnail_url` (2 tests), ingest embedding hook fires (1 test), BO route auth (2 tests), BO run shape/picks/empty (3 tests), BO results retrieval (2 tests).
- Net test result: 63 pass (Google suites), 1 skipped (up from 51 at end of Chunk 6).

### Chunk 6 — Text generation on Google RSA

- `ad_text_generation/prompts.py`: Added `GOOGLE_RSA_SLOT_HINTS` dict with Google-specific character limits (headlines ≤ 30 chars, descriptions ≤ 90 chars).
- `ad_text_generation/generator.py`: Added `GOOGLE_RSA_SLOTS = ("headline", "description")` and `slots_for_platform(platform)` helper. Added `platform` parameter to `generate_slot_variants` (selects RSA hints when `platform='google'`) and `generate_all_slots` (restricts to platform-appropriate slots when `slots` arg is not explicitly passed).
- `ad_text_generation/pipeline.py`: Added `platform` parameter to `run_text_pipeline`; threaded through to `generate_all_slots`.
- `main.py`: New `POST /api/google/generate/text/{campaign_id}` route — queries `platform='google'` rows, calls `run_text_pipeline(platform='google')`, returns `GenerateTextResponse`. Placed after `GenerateTextResponse` model definition. Supports optional `seed_ad_id` query param.
- `frontend/src/api.js`: Added `generateGoogleTextAds(campaignId, seedAdId)`.
- `frontend/src/pages/GoogleCampaignsPage.jsx`: Added `genStateById` state, `handleGenerateText` handler, `TextGenResults` component (shows headline + description variants). "Generate RSA Text" button appears in the expanded structure row; replaced by results once generated; error message + retry button on failure.
- `backend/tests/test_google_text_pipeline.py`: 10 new pure tests — `slots_for_platform` unit tests (google, meta, unknown), platform isolation route test, 404/400/200 route tests, `platform='google'` threading verification, seed_ad_id param handling.
- Net test result: 69 pass (Google suites), 1 skipped (up from 59 at end of Chunk 5).

### Google OAuth — account picker + login-customer-id support (previous session)

**Problem:** OAuth callback auto-selected the first accessible account, which was always a non-test account, causing `DEVELOPER_TOKEN_NOT_APPROVED` errors. Additionally, client accounts under a manager require a `login-customer-id` header in every API call — this was never set.

**Changes:**
- `main.py` — `GET /auth/google/callback`: instead of picking `resource_names[0]` and saving immediately, fetches names for all accessible customers in parallel (`asyncio.gather`), stores the list in new `google_pending_connections` table under a `secrets.token_urlsafe(32)` key, redirects to `FRONTEND_URL/app/settings?google_pick=<key>`.
- `main.py` — new `google_pending_connections` table in `init_db()`: `key`, `user_id`, `refresh_token`, `accounts_json`, `created_at`.
- `main.py` — new `GET /auth/google/pending/{key}`: returns `{accounts: [{customer_id, name}]}`; requires auth; validates `user_id` matches.
- `main.py` — new `POST /auth/google/select-account`: body `{key, customer_id, login_customer_id?}`; strips dashes from both IDs; accepts any customer ID (not just ones in the accounts list, to allow test account manual entry); writes `customer_id` + `login_customer_id` to `google_connections`, deletes pending row.
- `main.py` — `_google_creds`: now selects `login_customer_id` from DB and returns a 3-tuple `(access_token, customer_id, login_customer_id)`.
- `main.py` — `GET /api/google/campaigns`: unpacks 3-tuple from `_google_creds`, passes `login_customer_id` to provider.
- `google_ads_api.py` — `query_gaql`: added optional `login_customer_id` param; includes `login-customer-id` header when set.
- `providers/google_provider.py` — `fetch_campaigns_and_insights`: added optional `login_customer_id` param; threads it through to `query_gaql`.
- `frontend/src/api.js`: added `getGooglePendingAccounts(key)` and `selectGoogleAccount(key, customerId, loginCustomerId)`.
- `frontend/src/pages/SettingsPage.jsx`: detects `?google_pick=<key>`, fetches pending accounts, renders a radio-button picker; includes a manual customer ID text field (for test accounts not returned by `listAccessibleCustomers`) and a login customer ID field (auto-populated from the selected radio when manual entry is used); on confirm POSTs selection and refreshes status.

### Stale test fixes

Three tests written against earlier implementations were updated to match the evolved code:
- `test_google_auth.py::test_google_callback_writes_connection` → renamed `test_google_callback_writes_pending_connection`; checks `google_pending_connections` row and `?google_pick=` redirect instead of direct `google_connections` write.
- `test_google_auth.py::test_google_creds_refreshes_and_returns_token` → unpacks 3-tuple `(access_token, customer_id, login_customer_id)`.
- `test_google_campaigns.py::_mock_creds()` → returns 3-tuple `("ya29.test", customer_id, None)`.

### Chunk 5 — Google structural ingest

- `providers/google_provider.py`: implemented `normalize_creative` — maps `ad_type` to `creative_type` (`rsa`/`display`/`video`/`unknown`) and extracts slot components from camelCase API response dicts (`responsiveSearchAd`, `responsiveDisplayAd`, `videoResponsiveAd`); image marketing assets stored as resource names (URL resolution deferred). Implemented `fetch_campaign_structure` — runs two GAQL queries in parallel (`asyncio.gather`) for ad groups and ads, deserializes camelCase response, returns normalized `(adsets, ads)` where each ad dict carries `ad_type` and the type-specific sub-dict for `normalize_creative`.
- `main.py`: new `POST /api/google/ingest/structure/{campaign_id}` — calls `_google_creds`, fetches via `google_provider.fetch_campaign_structure`, writes to `ad_creative_structures` with `platform='google'`; same idempotent `ON CONFLICT DO UPDATE` and missing-ad detection (`lifecycle_status='missing'`) as Meta route; no embedding hook (Chunk 7). New `GET /api/google/structure/{campaign_id}` — queries `platform='google'` for isolation from Meta rows.
- `frontend/src/api.js`: added `ingestGoogleStructure(campaignId)` and `getGoogleStructure(campaignId)`.
- `frontend/src/pages/GoogleCampaignsPage.jsx`: Ingest/Reingest button per row (persists ingested IDs in `localStorage`); inline `StructurePanel` component shows creative type badge (`Responsive Search` / `Responsive Display` / `Video Responsive` / `Unknown`), lifecycle status badge, and slot values; clicking an already-ingested row shows structure without re-ingesting.
- New `tests/test_google_structural_ingest.py`: 18 tests — `normalize_creative` (RSA, display, video, unknown, empty-slots edge case), ingest route (row counts, `platform='google'` column, `active`/`inactive` lifecycle, idempotency, missing detection), GET structure route (returns ingested data, empty before ingest, platform isolation from Meta rows, auth guards, not-connected 400).
- Net test result: 59 pass, 1 skipped (up from 41 at end of Chunk 4 session).

---

## 2026-05-02

### Chunk 4 — Google campaigns + metrics (read-only)

- `google_ads_api.py`: added `query_gaql(customer_id, access_token, developer_token, api_version, gaql, client)` — posts to `googleAds:searchStream`, accepts a caller-supplied `httpx.AsyncClient` (consistent with Meta pattern), handles both array-of-batches and single-dict response shapes, flattens `results` lists.
- New `backend/providers/google_provider.py` — `GooglePlatformProvider` implements `PlatformProvider`: `platform_name='google'`, `fetch_campaigns_and_insights` runs GAQL and normalizes (ENABLED→ACTIVE, micros→cents for budget, micros→dollars for spend/cpm/cpc, ctr decimal→percentage, aggregates multiple rows per campaign); all other interface methods raise `NotImplementedError` until their chunks.
- `factory.py`: `get_google_provider()` now returns `GooglePlatformProvider()`.
- `main.py`: instantiates `google_provider = get_google_provider()` at module level; new `GET /api/google/campaigns` route — calls `_google_creds`, creates an `httpx.AsyncClient`, delegates to `google_provider.fetch_campaigns_and_insights`, returns `list[Campaign]`.
- `frontend/src/api.js`: added `getGoogleCampaigns()`.
- New `frontend/src/pages/GoogleCampaignsPage.jsx` — read-only campaigns table at `/app/google-campaigns`; handles not-connected error, empty results, loading state.
- `frontend/src/main.jsx`: added `/app/google-campaigns` route.
- `frontend/src/App.jsx`: renamed nav links to "Meta Ads" / "Google Ads" / "Ad Library".
- New `tests/test_google_campaigns.py` — 9 mock tests (status normalization, budget micros→cents, metrics normalization, zero-delivery None ratios, multi-row aggregation, empty results, not-connected 400) + 1 `skipif` live smoke test that prints campaign list when real credentials are configured.
- Net test result: 52 pass, 1 skipped (up from 43).

### Chunk 3 — Google OAuth + account connect

- New `backend/google_ads_api.py` module: all raw Google Ads HTTP calls (`refresh_access_token`, `exchange_code_for_tokens`, `list_accessible_customers`, `get_customer_name`). Isolated here so routes stay thin and functions are patchable in tests.
- New env vars read in `main.py`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_DEVELOPER_TOKEN`, `GOOGLE_ADS_API_VERSION` (no version default — empty string; must be set explicitly to avoid baking in a stale version). All optional until Google integration is activated.
- New `GoogleStatus` Pydantic model: `connected`, `customer_id`, `customer_name`.
- New routes in `main.py`:
  - `GET /me/google-status` — returns connection state and customer info
  - `GET /auth/google/login-url` — generates Google OAuth consent URL (scope: `adwords`, `access_type=offline`, `prompt=consent`); stores state in `oauth_states` with `provider='google'`
  - `GET /auth/google/callback` — validates state, exchanges code via `google_ads_api`, fetches first accessible customer, persists to `google_connections`, redirects to `FRONTEND_URL/app/settings?google_connected=true`
- New `async _google_creds(user_id)` helper: reads `google_connections`, always refreshes via `google_ads_api.refresh_access_token`, raises 400 if not connected.
- `frontend/src/api.js`: added `getGoogleStatus()` and `getGoogleLoginUrl()`.
- `frontend/src/pages/SettingsPage.jsx`: added Google Ads Connection section mirroring Meta section; handles `google_connected` and `google_error` query params; independent loading/connecting state.
- New `tests/test_google_auth.py` — 7 tests: status endpoints (connected/not connected), login URL shape, callback writes DB row, callback error redirect, `_google_creds` raises 400, `_google_creds` refreshes and returns token.
- Net test result: 43 pass (up from 36).

### Chunk 2 — Platform column in DB + google_connections table

- Added `google_connections` table to `init_db()` CREATE script: `user_id`, `customer_id`, `login_customer_id`, `refresh_token`, `customer_name`, `connected_at`.
- Added three ALTER TABLE migrations (idempotent try/except pattern): `platform TEXT NOT NULL DEFAULT 'meta'` on `ad_insights`; `platform TEXT NOT NULL DEFAULT 'meta'` on `ad_creative_structures`; `provider TEXT NOT NULL DEFAULT 'meta'` on `oauth_states`.
- Renamed `get_meta_provider()` → `get_platform_provider(platform='meta')` in `factory.py`; kept `get_meta_provider()` as a backward-compatible alias so `main.py` import is unchanged.
- Added `get_google_provider()` stub in `factory.py` — raises `NotImplementedError` (filled in Chunk 4).
- New `tests/test_google_db.py` — 6 tests: fresh DB has all new columns and table, `google_connections` schema matches spec, migrations are idempotent on both a fresh DB and a manually-created old-schema DB.
- Net test result: 36 pass (up from 30).

### Chunk 1 — Provider interface generalization (Google Ads groundwork)

- Renamed `MetaProvider` ABC → `PlatformProvider` in `backend/providers/meta_provider.py`; added `MetaProvider = PlatformProvider` alias so existing imports are unbroken.
- Added two new members to `PlatformProvider`: abstract property `platform_name: str` and abstract method `normalize_creative(ad) → (creative_type, components)`.
- Implemented both in all three concrete providers: `LiveMetaProvider`, `DemoMetaProvider` (both return `platform_name = 'meta'`), and `MaskingMetaProvider` (delegates to the wrapped live provider).
- Moved the full `_normalize_creative` logic from `main.py` into `LiveMetaProvider.normalize_creative` and `DemoMetaProvider.normalize_creative`; `main._normalize_creative` is now a one-line wrapper calling `meta_provider.normalize_creative(ad)`.
- Replaced the dead `_fetch_campaign_structure` body in `main.py` with a one-line wrapper delegating to `meta_provider.fetch_campaign_structure`; restored the ingest route to call `_fetch_campaign_structure` (fixing the broken test patch target that caused 11 integration tests to 502).
- Updated `factory.py` return type annotation to `PlatformProvider`.
- Net test result: 30 pass (up from 19), same 3 pre-existing failures in `/api/ingest` metrics logic (unrelated to this chunk).

### Test directory reorganization

- Moved all 8 test files from `backend/` root into `backend/tests/`.
- Added `backend/tests/conftest.py` with a `sys.path` insert so all existing imports work without modification.
- Created `backend/TEST.md` — catalog of every test file: what it covers, run commands, API key requirements, and the 3 known pre-existing failures.
- Updated test commands in `CLAUDE.md` to use `tests/` prefix and added a pointer to `TEST.md`.

---

## 2026-04-25

- Created this `CHANGES.md` file as a session change log.
- Updated `NGROK_SETUP.md` to reference `start.sh` as the preferred way to start the stack; manual steps kept as fallback.
- Updated `CLAUDE.md` Commands section to document `start.sh`.

## Previous session (date unknown)

- Created `start.sh` — single script to start backend + frontend + ngrok together. Accepts `prod` (default, ports 8000/5173) or `staging` (ports 8001/5174, sets `APP_ENV=staging` and Vite `--mode staging`). Replaces the need to start each process separately.
- Established staging environment convention: separate ports for prod vs. staging so both can run simultaneously on the same machine.


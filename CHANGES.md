# Change Log

Changes are appended by date. Each entry covers one session or logical chunk of work.

---

## 2026-05-04

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


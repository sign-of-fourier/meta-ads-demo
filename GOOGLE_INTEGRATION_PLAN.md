# Google Ads + YouTube Integration Plan

This document captures the full implementation plan for adding Google Ads (including YouTube)
alongside the existing Meta integration. Work has not started yet. Each chunk is
independently testable before moving to the next.

---

## Guiding principles

- **Do not break Meta.** Every chunk must leave all existing pytest tests green and
  Meta human-testing working before it can be considered done.
- **Keep Google and Meta UX completely independent** until an explicit cross-platform
  chunk at the end. No unified views, no shared pages, no combined tables until that step.
- **Add new test files per chunk.** Never modify existing Meta test files.
  The existing suite (`test_structural_ingest.py`, `test_suggestions.py`,
  `test_combination_embeddings.py`, `test_bo_pipeline.py`) is the regression baseline
  and must pass after every chunk.
- **New test files follow the existing pattern**: `tmp_db` fixture, monkeypatched
  env vars, `TestClient`, mocked external HTTP. No live API calls in pure tests.

---

## Chunks

---

### Chunk 1 — Provider interface generalization

**Goal:** Move Meta-specific logic out of `main.py` into the provider layer so that a
Google provider can slot in with no changes to route code.

**What changes:**
- Rename `MetaProvider` → `PlatformProvider` (or add as alias; rename is preferred).
- Add two abstract methods to `PlatformProvider`:
  - `async fetch_campaign_structure(client, creds, campaign_id) → (adsets, ads)`
  - `normalize_creative(ad) → (creative_type, components)`
- Move `_normalize_creative()` and `_fetch_campaign_structure()` from `main.py` into
  `LiveMetaProvider` (and `DemoMetaProvider`, `MaskingMetaProvider`) as implementations.
  `main.py` delegates to `meta_provider.fetch_campaign_structure(...)` and
  `meta_provider.normalize_creative(...)` — no logic left in main.
- Add `platform_name: str` property to the interface (returns `'meta'`).
- `factory.py` function name stays `get_meta_provider()` for now — rename happens in Chunk 2.

**What does NOT change:** All method signatures, all routes, all DB schema.
`_normalize_creative` is still importable from `main` as a thin wrapper (for the existing
test that imports it directly).

**Tests:**
- Automated: no new test file needed, but the existing `test_structural_ingest.py`
  test `test_ingest_then_read_roundtrip` exercises the moved code path. Must still pass.
- If the thin wrapper in main is removed, update the two `_normalize_creative` import
  lines in `test_structural_ingest.py` to import from the new location. This is the
  only allowed modification to an existing test file, and only if the import breaks.
- Acceptance: run full pure pytest suite, all green.

---

### Chunk 2 — Platform column in DB + google_connections table

**Goal:** Give the DB a first-class notion of platform so data from Meta and Google can
coexist without collision.

**What changes:**
- `ALTER TABLE` migrations (via existing migration pattern in `init_db`):
  - `ad_insights`: add `platform TEXT NOT NULL DEFAULT 'meta'`
  - `ad_creative_structures`: add `platform TEXT NOT NULL DEFAULT 'meta'`
- New table `google_connections`:
  ```sql
  CREATE TABLE IF NOT EXISTS google_connections (
      user_id             INTEGER PRIMARY KEY REFERENCES users(id),
      customer_id         TEXT NOT NULL,        -- e.g. '123-456-7890'
      login_customer_id   TEXT,                 -- MCC account if agency
      refresh_token       TEXT NOT NULL,
      customer_name       TEXT,
      connected_at        TEXT NOT NULL DEFAULT (datetime('now'))
  )
  ```
- `oauth_states` table: add `provider TEXT NOT NULL DEFAULT 'meta'` column
  (migration only; reuse the same CSRF state table for both providers).
- Rename `get_meta_provider()` → `get_platform_provider(platform='meta')` in
  `factory.py`. Add a `get_google_provider()` stub that raises `NotImplementedError`
  (fills in Chunk 4).
- No route changes. `_meta_creds()` is unchanged.

**Tests:**
- New `test_google_db.py`: verifies migrations run cleanly on a fresh DB and on an
  existing DB that already has the old schema (idempotent).
- Acceptance: all pure tests green; `sqlite3 app.db ".schema"` shows new columns and table.

---

### Chunk 3 — Google OAuth + account connect

**Goal:** A user can connect their Google Ads account. The connection is stored and
readable via `/me/google-status`.

**What changes:**
- New env vars: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`,
  `GOOGLE_DEVELOPER_TOKEN` (required for Google Ads API), `GOOGLE_ADS_API_VERSION`
  (default `v17`).
- New helper `_google_creds(user_id)` → `(access_token, customer_id)`. Always refreshes
  via `https://oauth2.googleapis.com/token`. Raises 400 if not connected.
- New routes in `main.py`:
  - `GET /me/google-status` → `{connected: bool, customer_name, customer_id}`
  - `GET /auth/google/login-url` → `{url}` (Google OAuth consent URL with scopes
    `https://www.googleapis.com/auth/adwords`)
  - `GET /auth/google/callback` → exchanges code, fetches accessible customers,
    saves to `google_connections`, redirects to `FRONTEND_URL/settings?google_connected=true`
- New `backend/google_ads_api.py` module: all raw Google Ads HTTP calls live here
  (keeps `main.py` clean). First function: `refresh_access_token(refresh_token) → str`.
- Frontend `SettingsPage.jsx`: Google connect section (mirrors Meta section).

**Tests:**
- New `test_google_auth.py`: mocked OAuth exchange, verifies `google_connections` row
  written, `_google_creds` raises 400 when not connected, refreshes token correctly.
- Acceptance (human): connect a real Google account, confirm settings page shows
  "Connected as <account name>".

---

### Chunk 4 — Google campaigns + metrics (read-only)

**Goal:** See Google campaigns with 7-day metrics. Completely separate page/tab in the UI.

**What changes:**
- `google_ads_api.py`: add `query_gaql(customer_id, access_token, developer_token, gaql) → list[dict]`.
- New `GooglePlatformProvider` class in `backend/providers/google_provider.py`.
  Implements `PlatformProvider`. Implements `fetch_campaigns_and_insights`:
  ```gaql
  SELECT campaign.id, campaign.name, campaign.status,
         campaign_budget.amount_micros,
         metrics.impressions, metrics.clicks, metrics.cost_micros,
         metrics.ctr, metrics.average_cpm, metrics.average_cpc
  FROM campaign
  WHERE segments.date DURING LAST_7_DAYS
  ```
  Normalizes budget micros → same integer-cents format as Meta (`amount_micros / 10000`).
  Normalizes status: `ENABLED` → `ACTIVE`.
- `factory.py`: `get_google_provider()` returns `GooglePlatformProvider()`.
- New route `GET /api/google/campaigns`.
- Frontend: new `GoogleCampaignsPage.jsx` (separate page at `/google/campaigns`, not
  mixed with Meta). Nav link "Google Ads" added alongside "Meta Ads". No shared state
  with `CampaignsPage.jsx`.

**Tests:**
- New `test_google_campaigns.py`: mocked GAQL responses, verifies normalization
  (micros, status string, metrics shape), route returns correct JSON.
- Acceptance (human): `/google/campaigns` shows your campaigns with spend/impressions.

---

### Chunk 5 — Google structural ingest

**Goal:** Ingest Google campaign structure into `ad_creative_structures` tagged with
`platform='google'`. Normalizes RSA, Responsive Display, and Video ad types.

**What changes:**
- `GooglePlatformProvider.fetch_campaign_structure(...)`: two GAQL queries —
  one for ad groups, one for ads with expanded creative fields.
  ```gaql
  -- Ad groups
  SELECT ad_group.id, ad_group.name, ad_group.status, campaign.id
  FROM ad_group WHERE campaign.id = '{campaign_id}'

  -- Ads
  SELECT ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.status,
         ad_group_ad.ad.type,
         ad_group_ad.ad.responsive_search_ad.headlines,
         ad_group_ad.ad.responsive_search_ad.descriptions,
         ad_group_ad.ad.responsive_display_ad.headlines,
         ad_group_ad.ad.responsive_display_ad.descriptions,
         ad_group_ad.ad.responsive_display_ad.marketing_images,
         ad_group_ad.ad.video_responsive_ad.headlines,
         ad_group_ad.ad.video_responsive_ad.descriptions,
         ad_group_ad.ad.video_responsive_ad.videos
  FROM ad_group_ad WHERE campaign.id = '{campaign_id}'
  ```
- `GooglePlatformProvider.normalize_creative(ad)`:
  - RSA → `creative_type='rsa'`, slots: `headline[]`, `description[]`
    (no `primary_text`, no `image` by default)
  - Responsive Display → `creative_type='display'`, slots: `headline[]`, `description[]`,
    `image[]` (image URLs from `marketing_images[].url`)
  - Video (YouTube) → `creative_type='video'`, slots: `headline[]`, `description[]`,
    `video[]` (YouTube video IDs from `videos[].asset`)
  - Unknown/other → `creative_type='unknown'`, empty components
- New route `POST /api/google/ingest/structure/{campaign_id}`. Uses same DB write path
  as Meta ingest but with `platform='google'`. Lifecycle logic (active/inactive/missing)
  applies unchanged.
- New route `GET /api/google/structure/{campaign_id}`.
- `ad_creative_structures.slot` gains new valid values: `video` (YouTube video ID).
  Existing `headline`, `description`, `image` already exist in the schema.
- Frontend `GoogleCampaignsPage.jsx`: Ingest button per campaign, structure panel
  (same card layout as Meta but aware of `rsa`/`display`/`video` creative types and
  `video` slot).

**Implementation note:** RSA is always "dynamic" in Google's model (Google rotates
headlines/descriptions). The `creative_type='rsa'` label replaces the Meta
`static`/`dynamic` distinction. The `creative_type` column has no constraint; the
values are open-ended.

**Tests:**
- New `test_google_structural_ingest.py`: mirrors `test_structural_ingest.py` structure.
  Test `normalize_creative` for RSA, display, video, unknown types. Test endpoint
  with mocked GAQL responses. Test idempotency. Test lifecycle status. Test
  `platform='google'` isolation (Meta rows not returned by Google route).
- Acceptance (human): ingest a real Google campaign, read back structure, confirm
  RSA headlines/descriptions appear in correct slots with `creative_type='rsa'`.

---

### Chunk 6 — Text generation on Google RSA

**Goal:** Generate new RSA headline and description variants from a seed RSA ad.

**What changes:**
- `ad_text_generation/generator.py`: the `TEXT_SLOTS` list currently has
  `['headline', 'primary_text', 'description', 'cta']`. Add a platform-aware slot
  filter: when `platform='google'` (RSA), only generate `headline` and `description`
  (no `primary_text`, no `cta`). Pass `platform` into `run_text_pipeline(...)`.
- Prompt update for RSA format: headlines are 30 chars max, descriptions are 90 chars max.
  The existing prompt is Meta-oriented; a Google RSA system prompt variant is needed.
- New route `POST /api/google/generate/text/{campaign_id}`.
- Frontend `GoogleCampaignsPage.jsx`: "Generate RSA Text" button, shows generated
  headline[] + description[] variants (no primary_text column).

**Tests:**
- New `test_google_text_pipeline.py` (API-keys-required bucket): seed a mock RSA ad,
  run text pipeline with `platform='google'`, confirm only `headline` and `description`
  slots are generated and character limits are respected.
- No-API pure test: verify the slot filter logic in isolation (pass platform flag,
  assert correct slots selected).
- Acceptance (human): generate RSA variants for a real campaign, review in UI.

---

### Chunk 7 — Embeddings + BO for Google

**Goal:** Run the BO pipeline on Google RSA variants. This is the highest-risk chunk
because it touches shared BO/embedding code used by Meta backtesting.

**What changes:**
- Embedding strategy per creative type:
  - `rsa`: text vector only (headline + description as JSON, same format as Meta
    combination embeddings). `image_vector` is NULL. Combined vector is text-only
    (combiner must handle NULL image gracefully — add `image_vec=None` path).
  - `display`: text + image (same as Meta).
  - `video`: text + thumbnail. `GooglePlatformProvider` must expose a method to
    resolve a YouTube video ID to a thumbnail URL
    (`https://img.youtube.com/vi/{video_id}/hqdefault.jpg` — public, no API key).
- `ad_embedding_combiner/combiner.py`: `combine(text_vec, image_vec=None)` — if
  `image_vec` is None, pad the image half with zeros. `output_dim()` stays 256.
  This is backward-compatible: existing callers always pass both.
- `ad_combination_embeddings`: RSA combinations are `headline × description` (no
  `primary_text`). The combination key and embedding format stay identical — just
  fewer slots in the JSON.
- BO pipeline: `selector.py` already works per `seed_ad_id` — no change needed if
  embeddings are stored correctly. The pipeline is platform-agnostic at this level.
- New route `POST /api/google/bo/run`, `GET /api/google/bo/results/{ad_id}`.
- Frontend `GoogleCampaignsPage.jsx`: "Get Recommendations" button, shows picks.

**Tests:**
- `test_bo_pipeline.py` must still pass without modification (regression gate).
- New `test_google_bo.py`: seed RSA scored variants with NULL image vectors, run BO,
  confirm picks are returned and have correct structure. Test combiner with
  `image_vec=None`.
- Acceptance (human): run BO on a Google campaign that has been ingested and has
  generated text variants. Compare picks to Meta behavior in a separate campaign.

---

### Chunk 8 — Push to Google Ads (Mutate API)

**Goal:** Push a BO-selected RSA ad configuration to Google Ads as a new PAUSED ad.

**What changes:**
- `google_ads_api.py`: add `create_rsa(customer_id, access_token, developer_token, ad_group_id, headlines, descriptions, final_url) → ad_resource_name`.
  Uses `GoogleAdsService.mutate` (REST endpoint:
  `https://googleads.googleapis.com/v{ver}/customers/{customer_id}:mutate`).
- New helper `_create_google_ad(user_id, job_id)` in `main.py` (mirrors
  `_clone_dynamic_to_static_ad`). Resolves seed ad's `final_url` from the ingested
  structure (store it during ingest — add `final_url` column to `dynamic_generation_jobs`
  or resolve from `ad_creative_structures`).
- `dynamic_generation_jobs`: add `google_ad_resource_name TEXT` column (parallel to
  `meta_ad_id`). Migration-only, no existing rows affected.
- Route `POST /api/google/push`.
- Frontend `GoogleCampaignsPage.jsx`: Sync button triggers push then re-fetches
  campaigns. Amber note if push is blocked (e.g. insufficient permissions).

**Tests:**
- New `test_google_push.py`: mock Mutate API, verify correct request shape (headlines
  list format, pinned vs unpinned), verify `google_ad_resource_name` written to DB.
- Acceptance (human): push a generated RSA and confirm it appears PAUSED in Google
  Ads UI.

---

### Chunk 9 — Performance Max + Shopping stubs

**Goal:** Ingest pMax and Shopping campaigns without crashing. Not fully optimizable yet.

**What changes:**
- `GooglePlatformProvider.normalize_creative`: pMax → `creative_type='pmax'`, extract
  text assets (headlines, descriptions) + image assets + video assets into their
  respective slots. All assets treated as multi-variant.
- Shopping → `creative_type='shopping'`, extract `final_url` only. No creative slots.
- Text generation and BO pipelines: add an early-exit guard — if `creative_type` in
  `('shopping', 'unknown')`, return 400 with `"creative type not supported for optimization"`.
  `pmax` can proceed to text generation (it has headline/description slots).
- No frontend changes needed beyond the structure panel rendering `creative_type='pmax'`
  and `creative_type='shopping'` labels gracefully.

**Tests:**
- Add pMax and Shopping cases to `test_google_structural_ingest.py` normalize tests.
- Verify 400 returned for Shopping campaign at text generation endpoint.
- Acceptance (human): ingest a pMax or Shopping campaign, confirm it appears in
  structure panel with correct type label, confirm generation returns a clear error.

---

### Chunk 10 — Google Demo / Masking layer

**Goal:** Match the Meta masking pattern so Google can be demo'd without a live account.

**What changes:**
- `GoogleDemoProvider` in `providers/google_demo.py`: returns synthetic fixtures for
  campaigns, structure, etc. Activated by `APP_MODE=demo` or
  `GOOGLE_APP_MODE=demo`.
- `GoogleMaskingProvider` in `providers/google_masking.py`: wraps `GooglePlatformProvider`,
  overrides status/budget/metrics. `GOOGLE_MASK_*` env vars mirror Meta's.
- Update `factory.py` `get_google_provider()` to check these env vars.

**Tests:**
- New `test_google_demo.py`: verify demo fixtures return plausible data shapes.
- Acceptance: `GOOGLE_APP_MODE=demo` → `/api/google/campaigns` returns synthetic data.

---

### Chunk 11 — Cross-platform unification (UX)

**Goal:** Unified views that show both platforms together. This is the step where
the separate Google and Meta pages merge.

**What changes:**
- New `DashboardPage.jsx` (or extend `CampaignsPage.jsx`): shows all campaigns from
  both platforms in one table with a `platform` badge column.
- `AdsPage.jsx`: combined local ad library with platform filter.
- `/api/campaigns/all` endpoint: fetches Meta + Google in parallel, merges, returns
  `platform` field on each campaign object. Graceful partial failure (one platform
  erroring doesn't blank the whole page).
- Campaign IDs can collide across platforms — all campaign-scoped routes that currently
  take `campaign_id` must also accept a `platform` query param (or encode it in the ID).
  Decision point: probably `platform` query param is cleaner than ID encoding.
- Cross-platform BO comparison view: side-by-side picks from both platforms for the
  same product/advertiser.

**Tests:**
- New `test_cross_platform.py`: unified endpoint returns correct merged shape, Meta
  error doesn't suppress Google results, platform isolation on structure routes.
- Acceptance (human): see both Meta and Google campaigns on one page with correct
  platform badges.

---

## Slot taxonomy — cross-platform reference

| Slot name | Meta | Google RSA | Google Display | Google Video |
|---|---|---|---|---|
| `headline` | title / link_data.name | up to 15 | 1 short + 1 long | 1+ |
| `primary_text` | body / message | — (not used) | — | — |
| `description` | link_data.description | up to 4 | 1 | 1+ |
| `image` | CDN URL / hash | — (extensions only) | marketing_images URLs | — |
| `video` | — | — | — | YouTube video IDs |
| `cta` | call_to_action_type | — | — | — |

---

## Key gaps and differences

### Meta capabilities not applicable on Google

| Meta concept | Google gap |
|---|---|
| `primary_text` / body slot | RSA has no body equivalent. Slot simply absent for search ads. |
| `dynamic` vs `static` creative type | RSA is always dynamic (Google rotates). The `static`/`dynamic` distinction collapses to `creative_type='rsa'`. |
| Image-first ads on search | Search ads carry no image by default. Image extensions exist but are supplemental and read-only from creative perspective. |
| `asset_feed_spec` + `object_story_spec` | No equivalent. RSA structure is `responsive_search_ad.headlines/descriptions`. |
| Short-lived token stored at rest | Google tokens are properly refreshable. Requires `_google_creds()` to always call the token refresh endpoint before use. |
| `page_id` in static ad creation | Google uses `final_url` directly. No page/post concept. |
| Demo/Masking layer parity | Needs its own `GoogleDemoProvider` / `GoogleMaskingProvider` (Chunk 10). |

### Additional capabilities Google has that Meta doesn't

| Capability | Implementation notes |
|---|---|
| YouTube video ads | New `video` slot (YouTube video ID). New metrics: view rate, quartile views (25/50/75/100%). Embed via thumbnail URL (`img.youtube.com/vi/{id}/hqdefault.jpg`). |
| Ad Strength score | Google's built-in RSA quality signal (POOR/AVERAGE/GOOD/EXCELLENT). Available in GAQL: `ad_group_ad.ad_strength`. Can feed into BO as a pre-scored observation. |
| Search term report | Which queries triggered which creative. GAQL: `search_term_view`. Unique signal not available in Meta. |
| Responsive Display Ads | Headline + description + image[] + logo — more flexible image asset slots than Meta static. |
| Performance Max | Multi-channel (Search, Display, YouTube, Discover) in one campaign. Single asset group with all slot types. Handled as `creative_type='pmax'`. |
| Shopping / feed-based | Product title + description from product feed. `creative_type='shopping'`. Not optimizable in current pipeline (no creative template). |
| Demand Gen campaigns | Social-style ads in YouTube feed, Discover, Gmail. Closest Google equivalent to Meta placements. |
| Auto-applied Recommendations API | Google's first-party recommendation surface. Could display alongside BO picks for comparison. |
| MCC / Manager Account hierarchy | `login_customer_id` in `google_connections`. Agency setups have a manager account above the client account. |

---

## Implementation notes for when starting

### Google Ads API mechanics
- Base URL: `https://googleads.googleapis.com/v{version}/customers/{customer_id}/googleAds:searchStream`
- Auth header: `Authorization: Bearer {access_token}`, `developer-token: {developer_token}`,
  optionally `login-customer-id: {login_customer_id}` for MCC.
- GAQL responses: `{"results": [{...}, ...]}`. Each row is a dict keyed by resource type
  (`campaign.id`, `metrics.impressions`, etc.) — must flatten before use.
- Token refresh: `POST https://oauth2.googleapis.com/token` with
  `grant_type=refresh_token`, `client_id`, `client_secret`, `refresh_token`. Returns
  `access_token` valid for 1 hour.
- Developer token: needs to be approved by Google. In test mode ("test account"),
  the developer token can only access test accounts. Real accounts require a token
  with Standard Access.
- Customer ID format: `123-456-7890` in the UI, but API uses `1234567890` (no hyphens).
  Strip hyphens before passing to API.

### Required new env vars
| Variable | Description |
|---|---|
| `GOOGLE_CLIENT_ID` | OAuth2 client ID (from Google Cloud Console) |
| `GOOGLE_CLIENT_SECRET` | OAuth2 client secret |
| `GOOGLE_REDIRECT_URI` | Must match Cloud Console settings (e.g. `http://localhost:8000/auth/google/callback`) |
| `GOOGLE_DEVELOPER_TOKEN` | From Google Ads API Center; needs Standard Access for real accounts |
| `GOOGLE_ADS_API_VERSION` | Default `v17` (current stable as of mid-2025) |

### What `google_ads_api.py` should contain
Functions (all `async`):
- `refresh_access_token(client_id, client_secret, refresh_token) → str`
- `search_gaql(customer_id, access_token, developer_token, gaql, login_customer_id=None) → list[dict]`
- `mutate(customer_id, access_token, developer_token, operations, login_customer_id=None) → dict`

`search_gaql` should flatten each result row from `{"campaign": {"id": "x"}, "metrics": {"impressions": 100}}`
to `{"campaign_id": "x", "impressions": 100}` before returning, to match the shape
callers expect.

### Test isolation for Google tests
Google tests that mock GAQL should follow the same pattern as Meta:
```python
with patch("main._fetch_google_campaigns_and_insights",
           new=AsyncMock(return_value=(campaigns_raw, metrics_by_campaign, 0))):
    resp = client.get("/api/google/campaigns", headers={"Authorization": f"Bearer {token}"})
```
The `tmp_db` and `user_token` fixtures from `test_structural_ingest.py` can be copied
(or extracted to a `conftest.py`) — consider making a shared `conftest.py` in `backend/`
during Chunk 1 or 2 to avoid duplication across Google test files.

### Where `_normalize_creative` is tested
After Chunk 1, `normalize_creative` is a method on the provider. The existing test
`test_structural_ingest.py` imports `_normalize_creative` directly from `main`. The
migration path: keep a thin wrapper in `main.py` that delegates to
`meta_provider.normalize_creative(ad)` so the import doesn't break. Remove the wrapper
only when/if the test is updated.

### conftest.py opportunity
`test_structural_ingest.py`, `test_suggestions.py`, and all future Google tests share
the same `tmp_db`, `client`, and `user_token` fixtures. In Chunk 2 or 3, extract these
to `backend/conftest.py` to avoid copy-paste. The existing test files need no change —
pytest picks up `conftest.py` automatically.

---

## UX note

Until Chunk 11, Google and Meta are **entirely separate** in the UI:

| Nav item | Route | Component |
|---|---|---|
| Meta Ads | `/campaigns` | `CampaignsPage.jsx` (unchanged) |
| Google Ads | `/google/campaigns` | `GoogleCampaignsPage.jsx` (new) |
| Ad Library | `/ads` | `AdsPage.jsx` — Meta only until Chunk 11 |
| Google Ad Library | `/google/ads` | `GoogleAdsPage.jsx` (new, Chunk 5+) |

`GoogleCampaignsPage.jsx` is a fresh file built in parallel to `CampaignsPage.jsx`.
No props, context, or state is shared between them until Chunk 11.
This keeps each chunk's scope tight and avoids regressions in the Meta UX.

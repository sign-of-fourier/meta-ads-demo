# Backend Reference

## `backend/main.py` — key functions and routes

Single-file FastAPI application. All routes, Pydantic models, DB schema, and business logic live here.

| Section | What it does |
|---|---|
| `init_db()` | Creates all SQLite tables; runs ALTER TABLE migrations for columns added after initial schema |
| `get_current_user_id()` | FastAPI dependency; decodes Bearer JWT |
| `_meta_creds(user_id)` | Loads `(access_token, ad_account_id)` from `meta_connections` |
| `_fetch_campaigns_and_insights()` | Campaigns + 7d insights from Meta; returns `(campaigns_raw, metrics_by_campaign, insights_error_count)` |
| `_fetch_campaign_structure()` | Fetches adsets + ads with expanded creative fields; includes `effective_status` |
| `_normalize_creative(ad)` | Pure function; detects dynamic (presence of `asset_feed_spec`) vs static; extracts slots |
| `_download_ad_images(components)` | Downloads Meta CDN image URLs to `backend/ad_images/` synchronously during ingest before expiry |
| `_upload_image_to_meta()` | Resolves local image URL to file, uploads to `/{ad_account_id}/adimages`, returns hash |
| `_clone_dynamic_to_static_ad()` | Creates new static Meta ad from chosen components; inherits `page_id` + link URL from source |
| `_check_meta_convergence(...)` | Fire-and-forget at end of Meta ingest; checks lifetime impressions for pushed clones; writes CTR to `scored_observations` |
| `_check_google_convergence(...)` | Same for Google; GAQL `SELECT ad_group_ad.status, metrics.impressions, metrics.ctr FROM ad_group_ad WHERE campaign.id = X`; `ENABLED` → `clone_active`; uses `query_gaql` (not the defunct `run_gaql`) |
| `_write_convergence_observation(...)` | Called by both convergence checkers; looks up vectors, writes to `scored_observations` with `source='convergence'` |
| `_enrich_pick(p, seed_ad_id, user_id)` | Annotates BO pick with `ad_name`, `already_pushed`, `converged`, `current_impressions`, `clone_status`, `combo_id` |
| `_suggestion_from_row()` | Converts DB row → `SuggestionResponse` Pydantic model |
| `_resolve_generator(generator_id, user_id)` | Returns `(all_member_ad_ids, dynamic_member_ad_ids)` for a generator; used by BO run endpoints |
| `_google_creds(user_id)` | Reads `google_connections`, refreshes token, returns `(access_token, customer_id, login_customer_id)` |
| `require_write_access()` | FastAPI dependency; 403s with `{"error": "upgrade_required", ...}` unless the caller's tier is in `permissions.WRITE_TIERS`. Gates every route that pushes/launches/pauses ads on Meta or Google — see `permissions.py` |
| `require_admin_key()` | FastAPI dependency; checks `X-Admin-Key` against `ADMIN_API_KEY` (constant-time compare). Gates the admin tier-set route only |
| `GET /me` | Returns user's email and tier (`free`/`trial`/`beta`/`basic`/`premium`/`enterprise`) |
| `POST /api/admin/users/{user_id}/tier` | Admin-only (`X-Admin-Key` header); sets any user's tier directly, bypassing Stripe — the escape hatch for beta testers and comped/sales-assisted deals. Sets `tier_source='admin'` |
| `POST /api/ingest/structure/{campaign_id}` | Meta: writes creative structures; fires clone detection, convergence check, embeddings |
| `POST /api/push` | Pushes all completed generation jobs (no `meta_ad_id` yet) to Meta as PAUSED static ads |
| `POST /api/push/pick` | Unified per-pick push (Meta + Google); records in `pushed_ad_combos` with `clone_status='clone_paused'` |
| `POST /api/push/retain/{combo_id}` | Sets `clone_status='clone_retained'` for a converged clone the user wants to keep running |
| `POST /api/google/ingest/structure/{campaign_id}` | Google: parallel GAQL queries; writes structures; fires convergence + embeddings |
| `POST /api/generators` | Create a named ad generator from member ads (`GeneratorCreateRequest`) |
| `GET /api/generators` | List all generators for the current user |
| `DELETE /api/generators/{id}` | Delete a generator and its members |
| `POST /api/bo/run` | Run BO for a single ad (`seed_ad_id`) or generator (`generator_id`) — Meta |
| `POST /api/google/bo/run` | Same for Google |
| `POST /api/google/push` | Creates PAUSED RSA for each latest unpushed BO pick; writes resource name to `bo_selections` |
| `GET /auth/google/callback` | Validates OAuth state, fetches customer names, stores in `google_pending_connections` |
| `POST /auth/google/select-account` | Confirms account selection; strips dashes; accepts manual IDs for test accounts |

Image serving: `main.py` mounts `StaticFiles` at `/images` → `backend/generated_images/` and `/ad-images` → `backend/ad_images/`.

Full route table: `README.md`. Full schema: `SCHEMAS.md`.

## `backend/permissions.py` — tier taxonomy + write-access rules

Single source of truth for which tiers may push/launch/pause ads on a connected platform:

- `READ_ONLY_TIERS = {"free", "trial", "beta", "basic"}` — `free` is the base zero-state; `beta` is what every new signup gets today (no Stripe yet); `trial`/`basic` are placeholders for future Stripe-backed tiers that still won't grant write access
- `WRITE_TIERS = {"premium", "enterprise"}` — the only tiers that can push/activate/pause ads
- `tier_can_write(tier)` — used by `require_write_access()` in `main.py`
- `meta_oauth_scopes(tier)` — read-only tiers request `ads_read,business_management` only (no `ads_management`) at Meta OAuth connect time, so the consent screen itself reflects the account's capability, not just a hidden frontend button. Google Ads has no equivalent narrower scope — the `require_write_access` backend gate is the only enforcement there.

**New signups default to `tier='beta'`** (`signup()`, explicit in the `INSERT`, not just the column default) — wide open (read-only, no expiry) until an admin closes or upgrades the account. There's no Stripe integration yet (see `TECHNICAL_DEBT.md` T11); `users.tier_source` (`'default' | 'admin' | 'stripe'`) tracks who last set `tier` — today it's only ever `'default'` or `'admin'` (via the admin screen). When the Stripe webhook lands, it should treat any subscription event as authoritative and overwrite `tier_source` to `'stripe'` regardless of current value.

`users.tier_expires_at` is **informational only** — nothing reads it to auto-revoke access. It exists so an admin can see when a beta/comped account is meant to end and manually downgrade the tier at that point (`POST /api/admin/users/{id}/tier`), per an explicit decision to keep this manual rather than build auto-expiry.

`users.last_login_at` / `login_count` are updated on every successful `/auth/login` (not on signup) and shown in the admin screen.

Note: upgrading a user's tier does not retroactively widen an already-issued Meta OAuth token's scope — the user must reconnect Meta to get a write-scoped token after an upgrade.

## Admin screen — `frontend/src/pages/AdminPage.jsx`

Internal-only user management UI at `/admin` (not linked from any nav, not nested under the logged-in-user `App` shell — its auth is independent of regular user JWTs). Gated by pasting `ADMIN_API_KEY` into a one-time prompt; the key is kept in `localStorage` (not session-only, so you don't retype a long secret every visit) and sent as `X-Admin-Key` on every call. Lists all users (email, tier, expiry, source, last login, login count, created) via `GET /api/admin/users`, with an inline per-row tier dropdown + expiry date input + Save, backed by `POST /api/admin/users/{id}/tier`.

## `backend/providers/` — provider layer

| File | Role |
|---|---|
| `meta_provider.py` | `PlatformProvider` ABC; `MetaProvider` alias for backward compat |
| `meta_live.py` | Calls real Meta Graph API |
| `meta_masking.py` | Wraps `LiveMetaProvider`; overrides fields per `MaskPolicy` |
| `mask_policy.py` | Reads `MASK_*` env vars → `MaskPolicy` config object |
| `meta_demo.py` | Fully synthetic fixtures; used when `APP_MODE=demo` |
| `google_provider.py` | `GooglePlatformProvider` — GAQL campaign + structure queries; `normalize_creative`; `fetch_campaign_structure` runs two parallel GAQL queries |
| `google_demo.py` | `GoogleDemoProvider` — 3 synthetic campaigns (RSA, Display, pMax); used when `APP_MODE=demo` or `GOOGLE_APP_MODE=demo` |
| `google_mask_policy.py` | `GoogleMaskPolicy` — reads `GOOGLE_MASK_*` flags |
| `google_masking.py` | `GoogleMaskingProvider` — wraps live; deterministic synthetic metrics seeded by MD5 of campaign_id |
| `factory.py` | `get_meta_provider()` / `get_google_provider()` — selects provider from `APP_MODE` + mask flags |

`main.py` calls providers through `PlatformProvider` interface — no knowledge of which concrete provider is active.

## BO pipeline signatures (critical — mocks must match)

- `run_bo(seed_ad_id, text_source_id, user_id, db_path, method="modal", platform="meta", target_metric=None, seed_ad_ids=None, text_source_ids=None, image_ad_ids=None)` → **4-tuple** `(picks, warning, scored_count, candidate_count)`
- `run_cross_platform_bo(...)` → **2-tuple** `(picks, group_stats)`
- `run_unified_cross_platform_bo(...)` → **2-tuple** `(picks, group_stats)`

When `seed_ad_ids` / `text_source_ids` / `image_ad_ids` are provided (generator run), pools are merged across all member ads. Pass `None` for all three when running single-ad BO (existing behaviour).

### Pick confidence + PCA-cap warning (T12 — drafted, NOT SIGNED OFF)

Testing an approach, not a finished feature — see `TECHNICAL_DEBT.md` T12 and `GP_CONFIDENCE.md` for status and full reasoning.

- `confidence_label(gpr_std, nearest_known)` (`bo_pipeline/gpr.py`) — returns `"low"` or `None`. Thresholds `LOW_CONFIDENCE_GPR_STD` (default 0.85) and `LOW_CONFIDENCE_COSINE_DISTANCE` (default 0.35) live in `bo_pipeline/config.py`, both env-var tunable.
- `confidence` field on `BOPick` / `CrossPlatformBOPick`, set by both `_make_pick` copies (`pipeline.py`, `cross_platform.py`).
- `fit_pca` (`bo_pipeline/modal_bo.py`) returns a **3-tuple** `(pca, X_reduced, warning)`, not 2 — `warning` is set when the requested `n_components` got capped. Threads into `run_bo`'s existing `modal_warning` slot (single-platform) and a new `pca_warning` key on `group_stats`/`CrossPlatformBOGroupStat` entries (cross-platform paths).

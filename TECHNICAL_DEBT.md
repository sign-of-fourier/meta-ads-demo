# Technical Debt — Checkpoint

Open gaps and known imprecisions as of 2026-05-31. Pick up here next session.
Resolved items are recorded in CHANGES.md, not here.

---

## Architecture gaps

### T1 — Role taxonomy: native static ads get `role='parent'` by default

`ad_creative_structures.role` defaults to `'parent'` for every non-clone ad. A native static Meta ad (one fixed combination, not pushed by us) gets `role='parent'` even though it is not a universe generator — it has a candidate pool of size 1.

**Impact today:** Low. BO seed selection is user-driven (checkboxes). The pause-banner logic only fires when a campaign has active clones. Nothing auto-selects seeds by role.

**What needs resolving first:** Should a native static ad ever be a BO seed? It could be a legitimate starting point if you want to generate variants derived from it. Until that design question is settled, don't add a `native_static` role — the wrong default is safer than the wrong explicit value.

**When ready:** Add `role='native_static'` set at ingest for `creative_type='static'` Meta ads and single-slot-index Google RSAs. Exclude from BO seed picker. Include as pre-scored observation source (see T4).

---

### T2 — Deleted clone handling

When a user deletes a pushed clone in Meta/Google Ads Manager, it shows as `lifecycle_status='missing'` at next ingest. The `pushed_ad_combos` record persists indefinitely. The convergence checker keeps querying metrics for a deleted ad (returns empty, no CTR written, but the row never settles).

**Proposed fix:** At ingest, if a clone's `ad_id` transitions to `lifecycle_status='missing'` and its `clone_status` is `clone_paused` or `clone_active`, set `clone_status='clone_deleted'`. Add that status to the convergence checker's skip list.

---

### T4 — Native static CTR → `scored_observations` ✓ partially addressed

Native static ads (Meta and Google) now write their lifetime CTR to `scored_observations`
with `source='ingest_native'` at every structural ingest. This makes warm-start
clone-agnostic: any ad with real impressions contributes training data, not just pushed
clones. T1 dependency removed — non-clone detection uses `is_pushed_clone=0` filter.

**Remaining gap:** Dynamic/RSA ads with multiple asset slots cannot produce a
single-combination observation. Their overall CTR is an average across all served
combinations and cannot be attributed to one row in `scored_observations`. Observations
for dynamic seeds still come only from pushed-clone convergence.

---

### T5 — Cross-user observation sharing

`scored_observations` is scoped to `user_id`. In a multi-user or multi-account scenario,
a user with a large history of scored observations (real CTR, qwen_warm, synthetic)
cannot share that training data with a new user or a different account.

**What is needed:**
- `source_user_id` / `donor_account_id` to track where an observation originated
- A `visibility` concept: private / shared / global
- `get_real_observation_count` and `get_scored_combinations` to optionally draw from a
  shared pool, with appropriate trust weighting (donor real CTR vs local real CTR)
- Schema migration to add provenance columns without breaking single-user queries

**Why deferred:** Design not yet settled (which users can see which pools, how trust is
weighted, whether this is account-level or user-level). Implement alongside any
multi-tenant or white-label expansion.

---

## Documentation drift

### D1 — `clone_status` in `clone_stats` ✓ resolved

Both structure GET handlers now include `clone_status`, `platform_ad_id`, and `combo_id`
in `clone_stats` for pushed clones. SCHEMAS.md updated. CampaignRow reads these fields
to drive lifecycle badges and activate/retain actions inline in the structure view.

---

### D2 — Generator auto-naming is opaque

When BatchPanel creates a generator automatically (`"Auto meta 2026-05-31"`), that generator persists in `ad_generators` but the user has no UI to see or manage it. Duplicate generators accumulate on each "Run Generator Analysis" click.

**Fix:** Before creating a new generator, check if an identical member set already exists for this user and reuse it. Or add a generators list to the UI. Low priority until generator usage grows.

---

### T6 — Spend/budget configuration at push time

**Golden path gap.** When a BO pick is pushed via `POST /api/push/pick`, the new ad
inherits the parent adset's budget. There is no mechanism to set a per-ad spend cap or
daily budget for the test clone before activating it. The user must go to Meta/Google
Ads Manager and configure budget manually.

**Impact:** Material. Without budget control at push time, users either over-spend
(clone runs on the full adset budget) or under-activate (they forget to configure it and
the clone never gets impressions). Both paths corrupt the CTR signal that feeds back into
`scored_observations`.

**What is needed:**
- `daily_budget_cents: int | None` field on `PushPickRequest`
- Thread it through `push_pick` → `POST /act_{account_id}/ads` (Meta field: `daily_budget`)
  or a subsequent `POST /{ad_id}` to set budget after creation
- Frontend `PushFooter`: optional budget input (e.g. "$5/day") before pushing
- Validation: warn if no budget set and the adset has a high existing budget

**Tracked in code:** `TODO(spend)` in `backend/main.py` → `push_pick` and in
`frontend/src/components/BatchPanel.jsx` → `PushFooter`.

---

### T7 — `_get_pushed_exclude_keys` scope ✓ resolved

`_get_pushed_exclude_keys` now joins `ad_generator_members` to find sibling members
and their generator IDs. Queries `seed_ad_id IN (self, siblings, generator_ids)` so
per-member BO runs correctly exclude combos pushed by sibling members or generator-level
runs.

---

### T8 — Native static ad match detection ✓ resolved

`_find_native_static_match` reconstructs native static ad combinations from
`ad_creative_structures` and compares against the BO pick's `combination_key`.
`_enrich_pick` calls it when the pick has not already been pushed, returning
`matches_existing_ad: {ad_id, effective_status}` in the BOPick response.

`POST /api/push/match` writes to `pushed_ad_combos` pointing at the existing ad
(no new ad created); sets `clone_status` from the ad's current effective_status.

BatchPanel PickCard/MatchCard display "Matches existing ad" with a checkbox;
`BatchPushFooter` calls `pushMatch` for match picks instead of `pushPick`.

---

### T9 — `CONVERGENCE_MIN_DAYS` documented but not enforced

`bo_pipeline/config.py` reads `MIN_CONVERGENCE_DAYS` (default 3) and documents it. The
convergence checkers `_check_meta_convergence` and `_check_google_convergence` in `main.py`
only check impression count against `_required_impressions()`. The `days_running` column is
computed and stored at ingest but never compared against `CONVERGENCE_MIN_DAYS`.

**Impact:** A clone that receives a traffic spike in its first few hours could converge the
same day it was pushed, before the CTR has had time to stabilise across different audiences
and times of day.

**Fix:** In both convergence checkers, add `AND days_running >= CONVERGENCE_MIN_DAYS` to
the eligibility query, or check `row["days_running"] >= CONVERGENCE_MIN_DAYS` inside the
loop before calling `_write_convergence_observation`.

---

## Lower-priority (already in CLAUDE.md)

These are in CLAUDE.md under "What's not implemented yet." Listed here to avoid confusion:

- `action="replace"` on confirm — no Meta call yet
- `POST /api/suggestions/{id}/reject` endpoint
- Meta access token refresh
- Google `normalize_creative` stores asset resource names, not resolved URLs
- Google structural ingest does not fire `embed_images`
- UI dropdown for `target_metric`
- Edit-after-convergence edge case: currently treated same as pre-convergence edit (both → `clone_invalidated`); locked-in CTR should be preserved
- Push to Meta requires app in Live mode

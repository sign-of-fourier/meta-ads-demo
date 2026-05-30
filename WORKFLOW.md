# AdStac.kr — Recommendation Workflow

Last updated: 2026-05-28

This document describes the end-to-end user journey from ingesting a campaign to pushing
a BO-recommended ad to the platform. See `CLAUDE.md` for the technical reference.

---

## Terminology

| Term | Definition |
|---|---|
| **Dynamic ad** | A platform ad with multiple component variants (Meta dynamic ad, Google RSA). Its combinations define the candidate space. |
| **Native static ad** | A static ad that existed on the platform before this app created anything. Valid candidate; becomes a scored observation once it has converged. |
| **Candidate space** | All combinations the GPR can recommend next: every untested combination from a dynamic ad + every native static ad below convergence. |
| **Scored observation** | An (embedding, score) pair used to fit the GPR. Sources: AI-scored variants, converged pushed clones, converged native static ads. |
| **Pushed clone** | A static ad created by this app from a BO recommendation. Enters limbo on push; never re-enters the candidate space. |
| **Limbo** | A pushed clone that is running but has not yet converged. Excluded from both candidate space and scored observations. |
| **Convergence** | A pushed clone is converged when `impressions >= MIN_CONVERGENCE_IMPRESSIONS` AND `days_running >= MIN_CONVERGENCE_DAYS` (defaults: 500, 3). At convergence its CTR is locked in as a scored observation. |

---

## The two-sided pool

The GPR sees one global pool, divided into two sides:

```
┌─────────────────────────────────────────────────────────┐
│  SCORED OBSERVATIONS           │  CANDIDATE SPACE        │
│  (GPR fits on these)           │  (GPR picks from here)  │
│                                │                         │
│  AI-scored variants            │  Dynamic ad combos      │
│  Converged pushed clones       │  (headline × desc × img)│
│  Converged native static ads   │                         │
│                                │  Native static ads      │
│                                │  (below convergence)    │
└────────────────────────────────┴─────────────────────────┘
                        LIMBO
                (pushed, running, not yet converged)
                (excluded from both sides)
```

---

## Step-by-step user journey

### 1. Connect your ad account
In **Settings**, authenticate with Meta or Google. For Google, select which ad account
to connect (or enter a customer ID manually).

---

### 2. Ingest a campaign
In **Dashboard**, open the Meta Ads or Google Ads section. Click **Ingest** on a campaign
row. This:
- Fetches ad groups and ads for the campaign via the platform API
- Normalises creative fields into typed slots (`headline`, `description`, `primary_text`, `image`, etc.)
- Writes to `ad_creative_structures` (idempotent — safe to re-run)
- Fires background embedding tasks: text + image embeddings per ad, combination embeddings
  for every headline × description × image cross-product

After ingesting, the button label changes to **Reingest**.

---

### 3. Generate text variants (optional)
Click **Generate RSA Text** (Google) or **Static Text Ads** (Meta). This calls the text
generation pipeline, which produces ~10 new variants per slot from the seed ad's copy.
Generated variants are immediately added to the combination embedding pool — they become
BO candidates without needing to be manually tested first.

For Meta: you can also click **Dynamic Ad (AI Images)** to generate 4 image variants via
FLUX img2img. These become the image half of the candidate pool.

---

### 4. Get recommendations (run BO)
Click **Get Recommendations**. This runs the Bayesian Optimisation pipeline:

1. Fetches all scored observations for the seed ad (AI scores, converged CTRs)
2. Fetches all candidate combinations (text × image cross-product)
3. Excludes any combination already pushed (`pushed_ad_combos.combination_key`)
4. Fits a GPR on the scored observations
5. Picks the highest Expected Improvement candidate (pick 1 — exploit)
6. Re-fits with a fantasy observation at pick 1 to select a diverse second candidate (pick 2 — explore)
7. Returns up to 2 picks, each with `combination`, `gpr_mean`, `ei_score`, `selection_type`

If fewer than 2 scored observations exist, falls back to random selection (labelled "Random"
in the UI). This is normal on first run before any AI scores are available.

Each pick is shown as a **BOPickCard** with a Preview button and a **Push and Run** button.

---

### 5. Push and Run
Click **Push and Run** on a pick card. A modal opens showing:
- A compact preview of the combination (first 3 slots)
- A name input, pre-filled as `{CampaignName} — {headline preview} ({date})`

Confirm to push. The backend:
- **Meta**: uploads any image to `/{ad_account_id}/adimages`, then calls
  `_clone_dynamic_to_static_ad` to create a PAUSED static ad inheriting `page_id` and
  destination URL from the source ad's `object_story_spec`
- **Google**: calls `_create_google_rsa_ad` to create a PAUSED RSA with the picked
  headline + description pinned as the lead assets
- Writes a row to `pushed_ad_combos` with `push_status = 'paused'`
- The combination is permanently excluded from future BO candidate spaces

The button on the card changes to **Activate**.

---

### 6. Activate
Click **Activate**. The backend sets the ad to ACTIVE on the platform and updates
`push_status = 'active'`. The button changes to **Running ✓**.

The ad is now in **limbo**: running on the platform, accumulating impressions, but not
yet a scored observation. BO will not recommend this combination again.

---

### 7. Convergence (not yet implemented)
During each ingest, the backend will check every row in `pushed_ad_combos` with
`push_status = 'active'` and `converged = 0`. When both convergence thresholds are met:
- `converged = 1`, `converged_at`, `convergence_metric` (CTR) are written
- A scored observation is added for the GPR (CTR rank-normalised alongside AI scores)
- UI shows: **Running ✓ — Tested**

Once converged, the pushed clone never re-enters the candidate space but its CTR guides
future recommendations toward similar combinations.

---

### 8. Cross-platform analysis
In the **Dashboard**, after ingesting at least one Meta and one Google campaign:
- Click **Add to Analysis** on each recommended campaign to add it to the batch
- Adjust `top_n` (default 4) — how many picks to return across platforms
- Click **Run Cross-Platform Analysis**

This calls the unified BO endpoint (`POST /api/bo/cross-platform/unified`), which:
- Pools all candidate combinations from all selected ads across both platforms
- Reduces all embeddings to the same PCA dimension
- Runs a single GP over the unified pool
- Returns `top_n` picks ranked globally by EI, with platform labels

Each pick shows which platform and ad it came from. Push works the same as the per-platform flow.

---

## Lifecycle state table

| State | `push_status` | `converged` | Candidate? | Scored? | UI button |
|---|---|---|---|---|---|
| Not yet pushed | — | — | Yes | No | Push and Run |
| Pushed, paused | `paused` | 0 | No | No | Activate |
| Pushed, active, below convergence | `active` | 0 | No | No | Running ✓ (limbo) |
| Pushed, active, converged | `active` | 1 | No | Yes | Running ✓ — Tested |
| Push failed | `failed` | 0 | No | No | Push failed — retry |
| Invalidated (mid-test edit detected) | `invalidated` | 0 | No | No | (hidden) |

---

## What is and isn't implemented

| Feature | Status |
|---|---|
| Ingest (Meta + Google) | ✓ |
| Text generation (Meta + Google) | ✓ |
| Dynamic ad / AI image generation (Meta) | ✓ |
| Combination embeddings | ✓ |
| BO (per-platform) | ✓ |
| BO (cross-platform unified) | ✓ |
| Push and Run modal + per-pick push | ✓ |
| Activate | ✓ |
| Fake ad server (for testing push end-to-end) | ✓ |
| Convergence checking during ingest | ✗ not yet |
| "Testing… N impr." UI intermediate state | ✗ not yet |
| Mid-test edit detection (orphan ad handling) | ✗ not yet |
| Google clone detection via resource name | ✗ not yet |
| Ad Library showing pushed clones | ✗ not yet |

---

## Open design decisions

**Gap A — User edits a pushed clone on the platform:**
Detected at ingest by comparing ingested creative fields against `pushed_ad_combos.combination`.
- Edit before convergence (mid-test): mark `push_status = 'invalidated'`, discard metrics. Modified ad becomes a fresh native static ad.
- Edit after convergence: locked-in CTR remains valid (reflects original combination). Modified ad spawns as a new native static ad going forward.
Original combination stays permanently excluded from the candidate space in both cases.

**Gap B — User deletes a pushed clone on the platform:**
If `platform_ad_id` is absent for N consecutive ingests, mark `push_status = 'deleted'`. Combination stays excluded permanently.

**Gap C — Multiple users sharing one ad account:**
`pushed_ad_combos` is keyed by `user_id`. Combinations pushed by user A are invisible to user B — they could push duplicates. Acceptable for single-tenant; needs resolution before multi-user accounts.

**Gap D — Google RSA CTR is noisy:**
We pin headline 1 + description 1; Google freely rotates remaining slots. CTR reflects our pick as lead plus Google's rotation of supporting copy. Accepted: the GPR's uncertainty widens where noise is high, which schedules more exploration there. Revisit post-launch.

**Gap E — GP target metric consistency:**
All training targets fed to the GP must be on the same scale and measure the same thing. Qwen2-VL scores (1–7, synthetic) and real CTR/CVR/ROAS cannot be mixed — they measure different things and rank-normalisation does not fix the semantic mismatch. Current state: Qwen2-only until real metrics dominate. Future feature: user-selectable KPI (CTR / CVR / ROAS); once selected, only real converged observations on that metric enter the training set.

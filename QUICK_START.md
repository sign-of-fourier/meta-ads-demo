# Quick Start — AdStac.kr

AdStac.kr connects to your Meta and Google Ads accounts, ingests your live campaigns,
generates new ad variants using AI, and uses Bayesian Optimisation to surface the
next best combination to test. This guide walks through the full workflow for both
platforms. Each section notes what you need to have done first, so you can jump
ahead if you're picking up mid-stream.

---

## §1 — Sign up

1. Go to the app URL and click **Sign Up** in the top nav
2. Enter your email address and a password — no verification email, you're in immediately
3. You'll be logged in automatically and land on the Meta Ads page

Your session is stored as a JWT in the browser. It lasts 24 hours; after that, log
back in at the same URL.

---

## §2 — Connect your ad platforms

You can connect Meta, Google Ads, or both. The two platforms are independent — connecting
one does not affect the other.

### §2.1 — Meta Ads

1. Click **Settings** in the top nav
2. Under **Meta Ads Connection**, click **Connect Meta Account**
3. Complete Meta's OAuth screen — you'll be asked to grant access to your ad accounts
4. You'll be redirected back to Settings with your ad account name shown as connected

**Token note:** Meta issues a short-lived access token. If API calls start failing after a
few hours, return to Settings and reconnect. There is no automatic refresh.

### §2.2 — Google Ads

1. Click **Settings** in the top nav
2. Under **Google Ads Connection**, click **Connect Google Account**
3. Complete Google's OAuth consent screen — grant access to your Google Ads data
4. If your Google account has access to more than one ad account, you'll see an
   **account picker**: a list of accessible accounts with radio buttons
   - Select the account you want to connect
   - If your target account is a client account under an **MCC manager account**,
     enter the **Login Customer ID** (the manager account ID) in the field provided —
     this is required for API calls to route correctly through the MCC hierarchy
   - If the account you need isn't in the list (e.g. a Google test account), enter the
     **Customer ID** manually in the text field
5. Click **Confirm** — you'll be returned to Settings with the account name shown

**Token note:** Google tokens refresh automatically on every API call. You should not
need to reconnect unless you revoke access in your Google account.

---

## §3 — Meta Ads

The Meta Ads workflow lives entirely on the **Meta Ads** page (`/app/campaigns`).
All steps in this section require a connected Meta account (§2.1).

---

### §3.1 — Import campaign metrics

The first thing to do on the Meta Ads page is pull down your live campaigns and their
last 7 days of metrics.

1. Click **Meta Ads** in the top nav
2. Click the **Sync** button in the page header
3. AdStac.kr calls Meta's API, saves a metric snapshot to the local database, and
   populates the campaigns table with:
   - Campaign name and status
   - Daily budget
   - 7-day spend, impressions, clicks, CTR, CPM

**Zero metrics are normal** for campaigns that haven't served impressions in the last
7 days — the campaign still appears in the table, just without metric values.

Each time you click Sync, a new snapshot row is written. The **History** panel for each
campaign shows how metrics have changed across snapshots over time.

---

### §3.2 — Ingest creative structure

> **Before this step:** you need at least one campaign visible in the table (§3.1).

Ingesting creative structure imports the actual ad components — headlines, body texts,
descriptions, and images — from a campaign's ads into AdStac.kr's local database.
This is the step that enables everything downstream (text generation, BO, push).

1. Find the campaign row you want to work with
2. Click **Ingest** (first time) or **Reingest** (if you've ingested this campaign before)
3. AdStac.kr fetches all ads in the campaign from Meta and normalises each one into
   component slots stored in a local table:

   | Ad type | What gets stored |
   |---|---|
   | Static ad | One value per slot: headline, primary text, description, image |
   | Dynamic Creative | Multiple values per slot — one row per variant (slot_index 0, 1, 2…) |

4. The **Structure panel** expands below the campaign row showing each ad with:
   - A **creative type badge** (`static` or `dynamic`)
   - A **lifecycle badge** (`active`, `inactive`, or `no longer in Meta`)
   - The component values for each slot

5. In the background (takes a few seconds), AdStac.kr also:
   - Downloads Meta CDN images locally before they expire
   - Generates text embeddings for the seed ad
   - Generates per-image embeddings for each image slot
   - Embeds every Cartesian combination of text slots (e.g. 4 headlines × 4 primary
     texts × 4 descriptions = 64 combinations) — this is what the BO pipeline searches over

**Reingest is safe.** It uses an upsert — rows are updated in place, not duplicated.
If an ad that was previously ingested is no longer returned by Meta, its lifecycle
badge changes to `no longer in Meta` rather than being deleted.

---

### §3.3 — Generate new ad variants

> **Before this step:** creative structure must be ingested for the campaign (§3.2).

AdStac.kr offers two generation modes. You can use either or both — they are independent.

#### Static text variants

This mode generates 10 new copy variants per text slot from your seed ad's existing
copy, using GPT-4o. It is synchronous — results appear immediately.

1. Click the **campaign name** in the table to expand the history panel below it
2. Click **Static Text Ads**
3. AdStac.kr sends your seed ad's existing values to GPT-4o with instructions to
   generate new variants in the same tone and style
4. Results appear as a table with columns for each slot:
   - **Headline** — 10 variants
   - **Primary text** — 10 variants
   - **Description** — 10 variants
   - **CTA** — 10 variants (generated from scratch since Meta doesn't expose CTA text
     as an editable creative field)

These variants are stored locally and used as the candidate pool for Bayesian
Optimisation (§3.4).

#### Dynamic AI ads (text + images)

This mode generates a full dynamic ad: 4 AI-generated image variants plus 4 text
variants per slot. It runs as a background job and takes 2–5 minutes.

1. Click the **campaign name** to expand it (if not already open), then click **Dynamic Ad (AI Images)**
2. AdStac.kr starts a background job and returns immediately — you'll see a progress
   indicator that polls every 5 seconds
3. The pipeline runs 7 steps internally:
   - **Analyze** — GPT-4o inspects the seed ad image and produces 10 edit suggestions
     (e.g. "warmer lighting", "show product in use", "remove background clutter")
   - **Generate** — each suggestion is submitted to a FLUX img2img model (via deAPI)
     as an image-to-image generation request
   - **Poll** — AdStac.kr polls for each result until done
   - **Score** — a fine-tuned model scores each generated image for ad quality (0–10,
     higher is better)
   - **QA check** — GPT-4o inspects each scored image for severe defects (extra limbs,
     melted product, large garbled text)
   - **Correct** — flagged images get a targeted correction pass; the original is marked
     defunct and replaced by the corrected version
   - **Select top 4** — the 4 highest-scoring active variants become the image pool
4. Simultaneously, 4 text variants per slot are generated (same GPT-4o pipeline as
   Static Text Ads above, with `n_per_slot=4`)
5. When complete, the UI shows:
   - A 2×2 image grid with the 4 generated images
   - Text variant panels for headline, primary text, description, and CTA
6. The new dynamic ad is stored locally as `gen_dyn_*` in `ad_creative_structures`
   and its embeddings fire in the background — it is now ready for BO and push

---

### §3.4 — Get recommendations (Bayesian Optimisation)

> **Before this step:** creative structure must be ingested (§3.2) and at least one
> round of text variants must have been generated (§3.3). BO searches over the
> combination embeddings built during those steps.

Bayesian Optimisation (BO) fits a Gaussian Process Regressor on any scored ad
variants and returns the two text+image combinations most likely to outperform
your current best. On first run, with no scored observations yet, it returns two
random combinations as a starting point.

1. Click the **campaign name** to expand it (if not already open), then click **Get Recommendations**
2. AdStac.kr runs the BO pipeline and returns 2 picks:
   - **Pick 1 (EI)** — the combination with the highest Expected Improvement over
     your current best observed score
   - **Pick 2 (Diversity)** — a second combination chosen to be maximally useful
     *given that you're already testing Pick 1*, using a fantasy refitting step that
     prevents the two picks from being near-duplicates
3. Each pick shows:
   - The recommended headline, primary text, and description values
   - The image to pair with them (with a thumbnail preview)
   - GPR mean score and uncertainty — higher mean = more confident it will perform well;
     higher uncertainty = more to learn from testing it
   - Selection type badge: `ei`, `fantasy`, or `random` (fallback when no scored data)

**How scores improve over time:** as you test combinations and feed performance data
back in, the GPR fits more accurately and EI picks become more targeted. The first
run is exploratory; subsequent runs get sharper.

---

### §3.5 — Push to Meta

> **Before this step:** you need at least one generated dynamic ad (§3.3 dynamic mode)
> or BO picks (§3.4) that haven't been pushed yet.

Pushing sends your locally generated ads to Meta as new PAUSED static ads, each
using the BO-recommended component values.

1. Click the **Sync** button in the page header (same button as metric import — it
   does both: pushes unpushed ads first, then pulls updated campaign metrics)
2. AdStac.kr finds all generated ads that haven't been pushed yet and for each one:
   - Uploads the recommended image to Meta's ad image library
   - Creates a new ad creative using the recommended headline, primary text,
     description, and image
   - Creates a new ad in the source campaign's adset with `status = PAUSED`
   - Records the new Meta ad ID so it won't be pushed again
3. A success note appears with the count of ads pushed; any per-ad errors are
   shown inline without blocking the others

**PAUSED is intentional.** New ads are always created paused — review them in Meta
Ads Manager and activate manually when you're ready to serve them.

**Dev mode note:** if your Meta app is in Development mode (not Live), the push will
be blocked. You'll see an amber note explaining this. Switch your Meta app to Live
mode in the Meta Developer dashboard to enable pushes.

---

## §4 — Google Ads

The Google Ads workflow lives on the **Google Ads** page (`/app/google-campaigns`).
All steps in this section require a connected Google account (§2.2).

---

### §4.1 — View campaigns

1. Click **Google Ads** in the top nav
2. Your campaigns load automatically from the Google Ads API with 7-day metrics

The table shows the same metric columns as Meta (spend, impressions, clicks, CTR, CPM),
with a few normalizations applied:
- `ENABLED` status is displayed as `ACTIVE` (matching Meta's convention)
- Budgets are shown in cents (Google stores them in micros — millionths of the
  currency unit)
- Metrics use the same units as Meta for easy visual comparison

**Cross-platform metric equivalence** — the metric columns are intentionally aligned
between the Meta and Google pages so you can compare performance side by side. A more
integrated unified view across both platforms is planned for a future release.

---

### §4.2 — Ingest Google creative structure

> **Before this step:** at least one campaign must be visible in the Google Ads table (§4.1).

Ingesting pulls your Google ad creative components into AdStac.kr's local database,
tagged with `platform = 'google'` so they never mix with Meta data.

1. Find the campaign row you want to work with
2. Click **Ingest** (first time) or **Reingest** to update
3. AdStac.kr runs two parallel queries against the Google Ads API — one for ad groups,
   one for ads — and normalises each ad into component slots

Google has several creative types and each is handled differently:

| Creative type | Label | Slots ingested | Optimizable? |
|---|---|---|---|
| Responsive Search Ad | RSA | headline (up to 15), description (up to 4) | Yes |
| Responsive Display Ad | Display | headline, description, image | Yes |
| Video Responsive Ad | Video | headline, description, video | Partially — text only |
| Performance Max | pMax | headline, description, image, video (from asset groups) | Yes — text only |
| Shopping | Shopping | final_url only | No — no creative template |
| Other / unknown | Unknown | — | No |

4. The **Structure panel** expands showing each ad with its creative type badge,
   lifecycle status (`active`, `inactive`, `no longer in Google Ads`), and slot values

5. In the background, AdStac.kr generates embeddings:
   - A seed text embedding for each ad
   - Text combination embeddings for headline × description pairs

**Image embeddings and AI image generation for Google Ads are planned for a future
release.** RSA and pMax ads currently optimise over text combinations only.

**Reingest is safe** — uses an upsert, no duplicates.

---

### §4.3 — Generate RSA text variants

> **Before this step:** creative structure must be ingested for the campaign (§4.2),
> and the ad must be RSA or pMax type. Shopping and unknown types are not supported.

AdStac.kr generates 10 new headline and description variants for your RSA ad using
GPT-4o, with prompts tuned to Google's character limits.

1. After ingesting a campaign, find the campaign row and click **Generate RSA Text**
2. AdStac.kr selects the first ingested RSA ad as the seed (or a specific one if you
   pass a seed ad ID) and generates:
   - **10 headline variants** — each ≤ 30 characters (Google's hard limit)
   - **10 description variants** — each ≤ 90 characters (Google's hard limit)
3. Results appear as two columns: Headlines and Descriptions

These are stored locally and used as the BO candidate pool (§4.4).

**Primary text / body copy:** Google RSA has no equivalent to Meta's `primary_text`
slot — search ads don't have a standalone body field. The generated variants are
headline + description only. How this maps conceptually to Meta's copy structure will
be addressed in a future release as cross-platform ad equivalence is formalised.

**AI image generation for Google Ads** (equivalent to Meta's Dynamic AI Ads) is
planned for a future release. For now, text-only BO is the optimisation path for RSA.

---

### §4.4 — Get recommendations (Bayesian Optimisation)

> **Before this step:** RSA text variants must have been generated for this campaign (§4.3).

The BO pipeline is identical to Meta's (§3.4) — same GPR, same acquisition function,
same two-pick output. The only difference is that the candidate pool is built from
headline × description combinations instead of headline × primary_text × description × image.

1. After generating text variants, click **Get Recommendations**
2. AdStac.kr runs BO and returns 2 picks:
   - **Pick 1 (EI)** — best headline + description combination by Expected Improvement
   - **Pick 2 (Diversity)** — second combination chosen for complementary coverage
3. Each pick shows the recommended headline and description values, plus GPR statistics

**Image recommendations:** because RSA ads have no image slot in the BO model, picks
show text only. When image generation for Google arrives, the BO pipeline will
incorporate image embeddings the same way it does for Meta — no architectural change
needed, just new data.

**Cross-platform BO comparison** — comparing BO picks between your Meta and Google
campaigns for the same product is planned for a future release.

---

### §4.5 — Push to Google Ads

> **Before this step:** BO recommendations must exist for the campaign (§4.4) and
> must not have been pushed yet.

Pushing sends your BO-selected RSA configuration to Google Ads as a new PAUSED ad.

1. Click the **Sync** button in the Google Ads page header
2. AdStac.kr finds all unpushed BO picks and for each one:
   - Resolves the target ad group from the ingested structure
   - Resolves the destination URL (`final_url`) from the ingested structure
   - Builds the RSA headlines list: the BO-recommended headline is pinned first
     (position 1), followed by the other generated variants
   - Builds the RSA descriptions list: the BO-recommended description is pinned first,
     followed by the other generated variants
   - Calls the Google Ads Mutate API to create a new PAUSED RSA ad
   - Writes the returned resource name to the database so this pick won't be pushed again
3. A summary note shows how many ads were pushed, with per-ad errors shown inline

**PAUSED is intentional.** Review the new ad in Google Ads Manager and activate it
manually when ready.

**Pinning explained:** Google RSA allows "pinning" an asset to a specific position,
guaranteeing it always shows. AdStac.kr pins the BO pick to position 1 so the
recommended combination is always served together, rather than Google mixing it with
other assets freely.

---

## §5 — Ad Library

> Accessible at any time — no prerequisites.

The **Ad Library** page (`/app/ads`) shows every ad stored locally in AdStac.kr,
regardless of platform or origin. This includes both ingested ads (pulled from Meta or
Google) and generated ads (created by AdStac.kr's AI pipeline).

**What you see:**
- A card grid, one card per ad
- Each card shows a source badge (`meta` / `generated`) and a status badge
- Click a card to open a detail modal with:
  - The full image grid (for ads with image slots)
  - All text slot values across every slot index
  - The data source (`real`, `masked`, `generated`) and lifecycle status

**Deleting an ad:**
1. Open the detail modal and click **Delete**
2. Confirm the deletion dialog
3. The ad and all its embeddings are removed from the local database

Note that deleting a Meta-sourced ad only removes the local copy — the ad still
exists in Meta and will reappear in AdStac.kr the next time you sync or reingest that
campaign. Deleting a generated ad (`data_source = 'generated'`) is permanent — there
is no copy in Meta or Google to restore from.

---

## Appendix — Meta ↔ Google concept map

The table below shows how Meta and Google concepts map to each other within AdStac.kr.
Some equivalences are direct; others are approximate and will be formalised as the
cross-platform unification work (planned for a future release) is completed.

| Concept | Meta | Google | Notes |
|---|---|---|---|
| Top-level grouping | Campaign | Campaign | Direct equivalent |
| Campaign sub-unit | Ad Set | Ad Group | Functionally equivalent for routing and budgeting; some differences in targeting model |
| Rotated multi-variant ad | Dynamic Creative | Responsive Search Ad (RSA) | Both let the platform rotate through variant pools; Google rotates headlines/descriptions, Meta rotates all slots |
| Single fixed-combination ad | Static Ad | Responsive Display Ad (approximate) | Display is technically also multi-variant but behaves more like a fixed creative in practice |
| Headline slot | `headline` | `headline` (≤ 30 chars) | Same name; Google has a hard character limit |
| Body / message copy | `primary_text` | *(no direct equivalent)* | RSA has no standalone body field; how this maps semantically is planned for a future release |
| Short descriptor | `description` | `description` (≤ 90 chars) | Same name; Google has a hard character limit |
| Image asset | `image` | *(via Display or pMax only, not RSA)* | RSA is text-only; image support for Google BO is planned for a future release |
| Video asset | *(not supported)* | `video` (YouTube ID) | Google-only slot; ingested as a resource name for Display and Video ad types |
| Destination URL | Inherited from page/post | `final_url` (explicit per ad) | Google requires an explicit URL per ad; Meta derives it from the linked Page post |
| Multi-channel automation | Advantage+ | Performance Max | Rough equivalent; both remove creative control in exchange for algorithmic placement. Full equivalence will be addressed in a future release |
| Product feed ads | Catalog / Collection | Shopping | Both pull from a product feed; neither is optimizable in AdStac.kr today beyond ingestion |
| AI image generation | Dynamic AI Ads | Coming in a future release | FLUX img2img pipeline exists for Meta; Google image generation is planned |
| Cross-platform BO comparison | — | — | Planned for a future release |
| Unified campaign view | — | — | Planned for a future release |

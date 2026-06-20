# Backlog & TODO

Living document. Resolved items go to CHANGES.md, not here.  
Items are ordered by priority within each section. Status: `[ ]` open · `[~]` in progress · `[x]` done.

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
- `d` vector = integer array, same length as the pooled candidate set: `0=Meta, 1=Google`
  - This is NOT a feature column — it indexes into B for the coregionalization kernel

Acquisition function — greedy qEI from the joint posterior (Option B):
1. `mean, cov = gp.predict(X_all_cands, d_all_cands, return_cov=True)` → full N×N covariance
2. Pick 1: closed-form single-point EI from diagonal
3. Pick 2: for each remaining candidate j, evaluate `E[max(f(pick₁), f(j)) − f_best]` using
   the 2×2 submatrix `cov[[pick₁,j], :][:,[pick₁,j]]` — bivariate MC or closed form
4. This is genuinely different from what the Modal API does (Modal uses a standard RBF GP;
   this uses the multi-output GP's ρ-weighted cross-platform covariance for pick 2)

**Blocked by**: `scored_observations` is empty — no real cross-platform CTR data yet.  
**Before promoting to production**: seed observations via `POST /api/bo/seed`, compare `run_cross_platform_bo` vs `run_multioutput_bo` recommendations side-by-side.

**Future extension**: context features (segment, budget band, placement) concatenated onto the K-dim PCA vector after projection. GP structure unchanged.

**Reference**: `MULTIOUTPUT_GP.md` (experiment report from prior session), `chi_bad_ads/multioutput_gp.py` (the `MultiOutputGP` class).

---

## P1 — Architecture gaps

### EMB-1 — Context embedding component

**What and why**  
The ad embedding currently captures creative content (text + image vectors). It does not capture the *context* in which the ad is shown — the user-control parameters set at BO time (segment, budget band, placement, time-of-day, etc.). Two ads with identical creative but different segments can have very different CTRs; ignoring this makes the GP blind to the distinction.

**Approach**  
1. Identify all categorical user-control params (at minimum: `segment`; check `user_controls` table for others — budget band, placement type, objective).
2. One-hot encode each categorical. Collect all one-hot vectors into a matrix and fit PCA, reducing to ~4 dimensions. PCA is fitted **once** on the full observed population (static). It could be refitted periodically as new segment combinations are seen (dynamic version — defer until data volume warrants it).
3. Concatenate the 4-dim context vector onto the K-dim PCA ad-embedding **after** the ad projection step. GP structure unchanged.

**Relation to BO-1**: this is exactly the "Future extension" noted in BO-1 — `context features concatenated onto the K-dim PCA vector after projection`. Build EMB-1 independently so it can be plugged into both the single-platform and multi-output GPs.

**Blocked by**: need enough distinct segment observations to make PCA non-trivial; validate on synthetic data first.

---

### DA-1 — Display ads support (Google)

**What and why**  
Google Display ads are image-first — a banner image with embedded text (headline, CTA, brand). Adding them to the ad universe gives the BO more candidate signal and lets us optimise across Search (RSA), Display, and Meta in one system.

**Ingestion pipeline**  
1. At structural ingest, detect `ad_type = 'DISPLAY_AD'` (or equivalent GAQL enum). Store image URL alongside creative metadata.
2. **Text extraction**: use a multimodal LLM (Vision) to read the text directly from the banner image. OCR is unnecessary — display ads are designed for legibility, and a multimodal model handles layout, font, and colour contrast better. Prompt: extract headline, body text, CTA from the image.
3. Feed extracted text through the existing text embedding pipeline (1536-dim). Combine with image embedding via `ad_embedding_combiner` (same 3072-dim concat as Meta static ads).

**BO integration**  
- Display ads slot into the same embedding space as Meta static ads (text + image vector). No new GP changes needed.
- `platform='google'`, `creative_type='display'`; exclude from RSA text-generation pipeline.

**Open questions**  
- Google Display image URLs: confirm they're stable/accessible from GAQL or require asset API fetch.
- Text extraction quality: validate multimodal extraction vs. rendered copy from the campaign asset library if available.
- Ad Library UI: currently undecided (unified library with "Google Display" badge vs. separate page — see CLAUDE.md open design decisions).

---

### T2 — Deleted clone handling

When a user deletes a pushed clone in Meta/Google Ads Manager it shows as `lifecycle_status='missing'` at next ingest. The `pushed_ad_combos` record persists and the convergence checker keeps querying metrics for a dead ad indefinitely.

**Fix**: at ingest, if a clone transitions to `lifecycle_status='missing'` with `clone_status` of `clone_paused` or `clone_active`, set `clone_status='clone_deleted'`. Add that status to the convergence checker skip list.

---

### T4 — Native static CTR not flowing to `scored_observations`

Pre-existing static ads have real CTR data for their specific combination. That's a free scored observation — BO starts cold unnecessarily.

**Dependency**: blocked by T1 (need to identify native statics cleanly first).  
**When T1 resolved**: at structural ingest, for each `native_static` ad, check if its combination exists in `ad_text_combination_embeddings`; if so, write lifetime CTR to `scored_observations` with `source='ingest_ctr'`.

---

### T1 — Native static ads get `role='parent'` by default

`ad_creative_structures.role` defaults to `'parent'` for every non-clone ad, including native static Meta ads (candidate pool of size 1) and single-slot-index Google RSAs.

**Design question still open**: should a native static ad ever be a BO seed?  
**When ready**: add `role='native_static'` set at ingest for `creative_type='static'` Meta ads and single-slot-index Google RSAs. Exclude from BO seed picker. Include as pre-scored observation source (see T4).

---

## P2 — UI / UX gaps

### D2 — Generator auto-naming accumulates duplicates

BatchPanel creates `"Auto meta 2026-05-31"` generators silently on each "Run Generator Analysis" click. Duplicate generators pile up in `ad_generators` with no UI to view or manage them.

**Fix**: before creating a new generator, check if an identical member set already exists for this user and reuse it. Or add a generator list to the UI.

---

### D1 — `clone_status` missing from structure API response

`GET /api/structure/{campaign_id}` builds `clone_stats` from `push_status` and `converged` only. The structure panel can't show clone lifecycle state without a separate BO pick fetch.

**Fix**: add `clone_status` to the `clone_stats` dict in both structure GET handlers. Update SCHEMAS.md.

---

## P3 — Lower priority (confirmed not yet implemented)

These are also in CLAUDE.md "What's not implemented yet" — listed here to avoid confusion.

- `action="replace"` on confirm — validated but no Meta call; stays `pending_confirmation`
- `POST /api/suggestions/{id}/reject` endpoint
- Meta access token refresh logic
- Google `normalize_creative` stores asset resource names, not resolved URLs
- Google structural ingest does not fire `embed_images` (no URL images from GAQL yet)
- UI dropdown for `target_metric` (backend wired end-to-end; selector not built)
- Edit-after-convergence: both pre- and post-convergence edits → `clone_invalidated`; locked-in CTR should survive post-convergence edits
- Push to Meta requires app in Live mode

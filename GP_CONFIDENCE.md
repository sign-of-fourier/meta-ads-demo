# Pick Confidence and PCA-Cap Warnings (T12)

**Status: drafted 2026-07-05, NOT SIGNED OFF.** This doc records the reasoning behind the
implementation, so the sign-off decision can be made against the *why*, not just the diff.
See `TECHNICAL_DEBT.md` T12 for the status line and punch list; this doc is where the
thinking behind those choices lives.

## The problem

Every BO pick already carries two signals nobody without a GP background can read:

- `gpr_std` — posterior standard deviation at the picked point.
- `nearest_known` — cosine distance (pre-PCA space) to the closest scored observation.

A genuinely novel combination — nothing like it has ever been scored — looks identical in
the UI to a well-supported pick. The numbers are there (`BOPickCard.jsx` already renders a
raw "Uncertainty (σ)" figure) but there's no interpretation layer. Separately, `fit_pca`
silently caps `n_components` when there isn't enough data, and low-data platforms in the
multioutput path get zero-padded to match — both are silent degradations with no signal to
the user that anything happened.

## Why combine gpr_std and nearest_known instead of picking one

Either signal alone can mislead in a different direction:

- **gpr_std alone**: a point can have artificially low posterior variance from kernel
  hyperparameters fit on a small, clustered dataset, even far from any real precedent —
  the GP can look confidently wrong.
- **nearest_known alone**: cosine distance in the *raw* embedding space doesn't account
  for which directions the GP's kernel lengthscale actually treats as "close" — two points
  can be geometrically near but the GP still assigns them uncorrelated outcomes.

Checking both and flagging low-confidence if *either* crosses its threshold is a
conservative OR, not an AND — the badge is meant to catch either failure mode, not just
cases where both agree. False positives (flagging a pick that was actually fine) cost a
glance at a badge; false negatives (a truly novel pick shown as normal) cost trust in the
whole system. The asymmetry argues for OR over AND.

## Why gpr_std is comparable call-to-call (and why that makes a fixed threshold OK)

`y` is always rank-transformed to ~N(0,1) via `transform_y` (empirical CDF → clamp →
`norm.ppf`) before any GPR fit — see `bo_pipeline/gpr.py`. That means the *target* scale is
identical across every BO call regardless of the underlying metric (CTR, Qwen score,
synthetic). A posterior σ of 0.85 means the same thing in run N as it does in run N+1000,
which is what makes a fixed numeric threshold meaningful instead of needing per-call
calibration. This is also why PCA (see below) doesn't undermine this — PCA changes the
*input* space, not the *target* space that gpr_std lives in.

## Why binary (low / not-low) instead of three tiers

A three-tier system (high/medium/low) reads well in a spec but in practice the middle tier
tends to get ignored — users either act on a clear warning or they don't act on an
ambiguous "medium" one, so it adds a state without adding a decision. Starting binary is
also cheaper to validate: one threshold pair per signal, not two, and it's easier to walk
back a binary badge to nothing than to renegotiate three tier boundaries later. If real
pick history later shows the low tier is too noisy or two coarse, splitting it further is a
threshold change, not a redesign.

## Threshold values: 0.85 (gpr_std) and 0.35 (cosine distance)

These are *not* derived from real pick history — there isn't any real distribution to
calibrate against yet, since this is the first session BO picks have carried any labeled
outcome at all. They're deliberately round, conservative starting points:

- `LOW_CONFIDENCE_GPR_STD = 0.85`: close to the marginal variance of the rank-transformed
  target (~1). A posterior σ that close to the prior variance means the GP learned little
  from training data at that point — the kernel didn't pull the estimate away from the
  prior, which is the definition of "no real information about this point."
- `LOW_CONFIDENCE_COSINE_DISTANCE = 0.35`: roughly a third of the way to maximally
  dissimilar (distance 1.0 = orthogonal). Chosen to catch "nothing like this has been
  tried" without flagging routine embedding variance between similar copy.

Both are env-var tunable (`bo_pipeline/config.py`) specifically so they can be adjusted
without a code change once there's real data to check them against. **This is the main
thing that needs sign-off** — these numbers are informed guesses, not validated statistics.

## Why the PCA-cap warning reuses the existing warning channel

`run_bo` already returns a `warning: str | None` for the Modal-API-failure fallback case,
and the frontend (`BatchPanel.jsx`, `CampaignsPage.jsx`) already renders it. Rather than
inventing a second warning mechanism for "PCA got capped," `fit_pca` now returns
`(pca, X_reduced, warning)` and every call site threads that warning into the *same*
channel the Modal-failure case already populates (`modal_warning` for the single-platform
path, a new `pca_warning` key alongside each cross-platform `group_stats` entry). One
warning surface to look at, not two — consistent with how `run_bo`'s existing warning
already models "something is degraded but we're not blocking on it."

The cross-platform paths attach the warning differently depending on what's shared:

- `_run_group_bo` / `_run_group_modal_bo` (per-platform-group PCA) → one `pca_warning` per
  `group_stats` entry for that specific group.
- `_run_unified_modal_bo_multioutput` (separate PCA per platform) → one warning per
  *platform*, applied to every `group_stats` entry sharing that platform.
- The single-shared-PCA path in `run_unified_cross_platform_bo` (one PCA fit over the
  pooled union of all groups) → the same warning applied to *every* `group_stats` entry,
  since there's only one PCA fit to talk about. This is also literally the silent
  zero-pad-degradation case named in the original T12 finding — the warning now fires
  exactly where that gap was.

## PCA vs. PLS — considered and rejected (for now)

While scoping this item, PLS (partial least squares — a supervised alternative to PCA that
uses the target `y` during fitting, rather than only the covariance of `X`) came up as a
possible replacement for PCA in the capped-dimensionality case: PLS can sometimes extract a
useful low-dimensional signal from fewer samples than PCA needs, because it's guided by the
outcome instead of purely by input variance.

**Decision: keep PCA, don't switch to PLS.** The user ran an empirical comparison in
`~/projects/quantecarlo/demos/demo7_categorical_vs_embedding.py` (part of a demo7 trio —
`demo7.py`, `demo7_pca_vs_pls.py`, `demo7_categorical_vs_embedding.py`, on git branch
`demo7-pca-pls-embedding-docs`) and found PCA held up: the embedding-blind baseline did not
outperform the PCA-based qEI arm, so there's no empirical case right now for the added
complexity of a supervised reduction. PLS also has a real downside PCA doesn't: since it's
fit using `y`, refitting it per-BO-call (as `fit_pca` already does, from scratch, every
call) means the reduction itself is entangled with the same small-sample noise the score
estimates already suffer from — PCA's unsupervised fit is at least noise-independent of
`y`. Revisit only if real pick history shows PCA capping is a frequent, practically painful
problem (not just a logged warning) — the warning added in this item is what will make that
visible in the first place, having previously been invisible entirely.

## Open question not yet resolved

Should `demo7_categorical_vs_embedding.py` and its two demo7 siblings physically live in
`~/projects/chi_bad_ads` instead of `~/projects/quantecarlo`, since all three hardcode data
paths into `chi_bad_ads/chi-bad-ads-data` and `embedding_cache/`? Not decided — see the
`project_pca_vs_pls_t12` memory note for the reasoning (the two older siblings already
predate this question and stayed in quantecarlo with a docstring disclaiming the
dependency; moving only the newest file would break consistency).

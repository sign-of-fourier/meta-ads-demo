# Multi-Output GP for Cross-Platform Bayesian Optimisation

## Why this approach

Each advertising platform defines an "ad" differently:

| Platform | Embedding components |
|---|---|
| Meta (dynamic) | text_vec (1536) ‖ image_vec (1536) → 3072-dim |
| Google RSA | text_vec (1536) only → 1536-dim (no image) |
| Google Display *(future)* | image_vec (1536) + OCR text_vec (1536) → 3072-dim |
| Any platform *(future)* | text_vec ‖ image_vec ‖ context_vec (segment, audience, bid strategy, …) |

A naïve approach is to zero-pad shorter embeddings to match the longest (3072-dim), then
run a single GP over the padded pool. That has three problems:

1. **Structural zeros pollute the kernel.** Half the Google RSA feature space is forced to
   zero. PCA wastes components "learning to ignore" them. The RBF kernel treats the padded
   half as a meaningful dimension where all points happen to coincide.

2. **Cross-platform correlation is implicit and uncontrolled.** The zero-padding implies
   Meta and Google ads are identical except in the text half — a fiction.

3. **It doesn't scale.** Adding a third platform (e.g. TikTok with video embeddings) means
   re-padding everything again and re-fitting from scratch.

The multi-output GP solves all three by giving each platform its own PCA projection into a
shared k-dimensional space, then modelling cross-platform correlation explicitly through a
coregionalization matrix.

---

## How it works

### The identity vector `d`

Every scored observation and every candidate carries an integer output index `d`:

```
d = 0  →  Meta
d = 1  →  Google RSA
d = 2  →  (future platform)
```

`d` is not a feature — it is a label that tells the GP which covariance "block" an
observation belongs to. It is passed alongside the PCA-projected embedding `x`.

### The coregionalization kernel

The joint kernel over all platforms is:

```
K((x₁, d₁), (x₂, d₂))  =  B[d₁, d₂]  ×  k_rbf(x₁, x₂)
```

- `x` is a point in the shared k-dim PCA space.
- `k_rbf(x₁, x₂) = exp(−‖x₁ − x₂‖² / 2ℓ²)` is a standard RBF kernel with
  learned lengthscale ℓ.
- `B` is a D×D positive-definite *coregionalization matrix* where D = number of platforms.
  `B[i, i] = 1` always. `B[0, 1] = B[1, 0] = ρ` is the Meta–Google correlation.

With two platforms, B is simply:

```
B = [[1,   ρ],
     [ρ,   1]]
```

`ρ ∈ (−1, 1)` controls how much information transfers between platforms. `ρ = 0` means
independent GPs. `ρ = 0.5` (current default) is a conservative prior: we expect some
correlation (same creative content, same brand) but don't assume the signals are identical.

### Covariance arithmetic: from scored observations to batch covariance

Given a set of N scored observations `{(xᵢ, dᵢ, yᵢ)}` and M candidates `{(x̃ⱼ, d̃ⱼ)}`:

1. **Build the N×N training covariance** `K_train`:
   `K_train[i, j] = B[dᵢ, dⱼ] × k_rbf(xᵢ, xⱼ)`

   The block structure is visible:
   - Meta–Meta block: `1 × k_rbf(x_meta_i, x_meta_j)`
   - Google–Google block: `1 × k_rbf(x_google_i, x_google_j)`
   - Meta–Google cross-block: `ρ × k_rbf(x_meta_i, x_google_j)`

2. **Condition on observations** to get the GP posterior mean and variance at each
   candidate. The posterior mean `μ(x̃, d̃)` pulls in signal from all platforms, weighted
   by `ρ` for cross-platform observations.

3. **Batch q-EI** selects the top-q candidates from the discrete pool by maximising the
   expected improvement over the current best score, jointly across all q picks. This is
   the same q-EI used in the single-output path; the multioutput kernel just changes the
   covariance structure.

The key property: a well-performing Meta creative (high score, low uncertainty) raises the
GP's posterior mean for *similar* Google RSA text combinations via the ρ cross-block. This
is the information transfer the zero-padding approach cannot express cleanly.

---

## Per-platform embedding construction

### Current platforms

**Meta (dynamic ad)**
```
text_vec   (1536-dim)  — OpenAI text-embedding-3-small on headline + body copy
image_vec  (1536-dim)  — NVIDIA embed-v-4-0 on the ad image
combined   (3072-dim)  — np.concatenate([text_vec, image_vec])
→ PCA → k-dim (k=64 by default), d=0
```

**Google RSA**
```
text_vec   (1536-dim)  — OpenAI text-embedding-3-small on all headline/description slots
(no image)
→ PCA → k-dim (k=64 by default), d=1
```

Note: the two PCAs are fitted separately — Meta's PCA is over 3072-dim vectors, Google's
over 1536-dim vectors. Both projections land in the same R^k space, which is what lets
them share the RBF kernel.

### Future platforms

**Context vector** (planned)
Every ad eventually carries a context component capturing targeting and delivery context:
audience segment, bid strategy, placement, time-of-day bucket, etc. This becomes a third
sub-vector concatenated onto the platform embedding before PCA:

```
combined  =  text_vec ‖ image_vec ‖ context_vec
```

Context allows the GP to learn that "the same creative works better for segment A than
segment B" — a signal that is currently invisible because all observations for an ad are
pooled regardless of who saw it.

**Google Display** (planned)
Display ads are image-first: the image is the primary creative, but text may appear
overlaid on the image (logo, headline, CTA). That overlay text will be extracted via OCR
and embedded separately:

```
image_vec   (1536-dim)  — embed-v-4-0 on the display image
ocr_vec     (1536-dim)  — text-embedding-3-small on OCR-extracted overlay text
combined    (3072-dim)  — np.concatenate([image_vec, ocr_vec]), d=2
```

This keeps the 3072-dim structure consistent with Meta while correctly representing that
the "text" component is derived from the image rather than written as copy.

**Adding new platforms**
Each new platform adds one row and column to B. With three platforms (Meta, Google RSA,
Google Display):

```
B = [[1,    ρ₀₁,  ρ₀₂],
     [ρ₀₁,  1,    ρ₁₂],
     [ρ₀₂,  ρ₁₂,  1  ]]
```

Each ρ can be estimated independently. The covariance arithmetic is unchanged; the server's
`_TorchMultiOutputGP` already handles D×D B matrices.

---

## The ρ parameter

`ρ = 0.5` is hardcoded as a conservative prior. Options when real scored observations
accumulate:

1. **Estimate from residuals**: fit separate GPs per platform, compute the Pearson
   correlation of their residuals on held-out observations.
2. **Learn jointly**: treat ρ as a hyperparameter and optimise the log marginal likelihood
   of the joint model. The gpytorch implementation in `_TorchMultiOutputGP` supports this.
3. **Keep the prior**: `ρ = 0.3–0.5` is reasonable until you have >20 scored observations
   per platform. Below that, the residual estimate is noisy and learning ρ risks overfitting.

**Lengthscale initialisation** matters more than ρ. The RBF lengthscale should be set to
the median pairwise distance in PCA space (the median heuristic), not left at the default
of 1.0. With the real embeddings from this project, the correct values are ~76 for Meta
and ~49 for Google RSA, giving a shared initialisation of ~63. A default of 1.0 makes all
RBF values ≈ 0 and predictions collapse to the prior mean.

---

## Implementation

### Where the code lives

| Location | What |
|---|---|
| `~/projects/boaz/modal/modal_gp_api.py` | Modal server — `GPRequest` + `_TorchMultiOutputGP` |
| `backend/bo_pipeline/modal_bo.py` | Client — `call_modal_api_multioutput` |
| `backend/bo_pipeline/cross_platform.py` | Pipeline — `_run_unified_modal_bo_multioutput`, `run_unified_cross_platform_bo(..., method="modal_multioutput")` |
| `~/projects/chi_bad_ads/multioutput_gp.py` | Standalone pure-numpy reference implementation |
| `~/projects/chi_bad_ads/demos/demo2.py` | End-to-end proof-of-concept using real embeddings from this repo's `app.db` |

### Server contract

`GPRequest` accepts three additional optional fields:

```json
{
  "X":            [[...], ...],
  "y":            [...],
  "candidates":   [[...], ...],
  "d":            [0, 0, 1, 1, 0],
  "d_candidates": [0, 0, 1, 1, 1],
  "rho":          0.5,
  "q":            2
}
```

When `d` and `d_candidates` are both present, the server uses `_TorchMultiOutputGP`.
When absent, it falls back to the existing single-output `ExactGP` path — fully backwards
compatible.

Response format is identical to the single-output path:

```json
{
  "candidates": [
    {"index": 3, "x": [...], "mu": 0.82, "sigma": 0.14},
    {"index": 7, "x": [...], "mu": 0.71, "sigma": 0.19}
  ]
}
```

`index` is into the `candidates` array sent in the request.

### Client pipeline flow

```
run_unified_cross_platform_bo(..., method="modal_multioutput")
  │
  ├─ load scored + candidates per group from DB (same as shared-PCA path)
  ├─ fit ECDF on combined score pool
  │
  └─ _run_unified_modal_bo_multioutput(groups, ecdf_transform, ...)
       │
       ├─ per platform: stack scored + candidates, fit PCA → k-dim
       ├─ pad each to K=64 dims (in case n_components < K due to small sample)
       ├─ build d_train / d_cands integer arrays
       ├─ call call_modal_api_multioutput(X_train, y, X_cands, d_train, d_cands, rho)
       └─ map returned indices → pick dicts with platform/seed_ad_id/combination_key

  On failure or empty response → falls back to shared-PCA path automatically.
```

### Testing locally before deploy

```bash
# Terminal 1: serve the updated endpoint
cd ~/projects/boaz/modal
modal serve modal_gp_api.py

# Terminal 2: comparison smoke test (single-output vs multioutput, same data)
cd /home/ubuntu/projects/meta-ads-demo
MODAL_BO_API_URL=<url-from-terminal-1> \
    python experiments/gp_comparison/test_modal_endpoint.py
```

Unit tests (no network — `call_modal_api_multioutput` mocked):

```bash
cd backend
python -m pytest tests/test_modal_bo.py -v -k "TestModalBOUnit"
python -m pytest tests/test_cross_platform_bo.py::TestUnifiedBOMultioutput -v
```

# Bayesian Optimisation — Design Notes

This document describes the probabilistic model, kernel, acquisition function, and
batch strategy used in `bo_pipeline/`. It is meant as a reference for anyone extending
the BO layer or tuning its hyperparameters.

---

## Overview

The goal is to select the next text+image combination to test, given a small number of
already-scored combinations. We treat ad quality as an unknown smooth function over the
combined embedding space and fit a Gaussian Process Regressor (GPR) to approximate it.
The GPR gives us both a mean prediction and an uncertainty estimate at every point in
that space, which we exploit via the Expected Improvement acquisition function.

Higher score = better ad (0–10 scale after inversion from the fine-tuned scorer's
0–1 defect output).

---

## Feature space

Each combination is represented as a **256-dimensional float32 vector**:

```
[text_128 | image_128]
```

- `text_128`: first 128 dimensions of the OpenAI text embedding (1536-dim originally)
- `image_128`: first 128 dimensions of the Azure image embedding, or zeros if no image
  embedding is available for that variant

Truncation is done by `ad_embedding_combiner/combiner.py` (`TEXT_DIM=128`, `IMAGE_DIM=128`).

### Why 128 + 128 and not the full vectors?

This is primarily a **computational decision**, not a claim that the remaining dimensions
carry no information. GPR inference scales as O(n³) in the number of training points, and
the squared-exponential (RBF) kernel's hyperparameter optimisation (marginal likelihood
maximisation) becomes expensive as dimensionality grows. At 256 dimensions with the small
training sets we operate with (typically 5–20 scored variants), fitting is fast and the
length scale is identifiable.

Underdetermination is less of a concern here than it might appear, because **the RBF
kernel implicitly operates in an infinite-dimensional feature space** (via the kernel
trick). The effective complexity of the function class is controlled by the learned length
scale, not by the input dimension. Truncating to 128 dimensions does discard information
encoded in the tail of each embedding, but the leading dimensions of both OpenAI and Azure
embeddings capture the bulk of semantic variance by construction (their training objectives
push the most discriminative signal into early dimensions).

If training set size grows substantially (100+ scored variants), revisiting the truncation
point or switching to a sparse GPR approximation would be reasonable.

---

## Kernel

```python
ConstantKernel(1.0) * RBF(length_scale=1.0) + WhiteKernel(noise_level=0.1)
```

| Component | Role |
|---|---|
| `ConstantKernel` | Amplitude — scales the overall covariance; absorbs differences in score magnitude |
| `RBF` | Squared-exponential kernel — assumes the quality function is smooth and stationary; two combinations close in embedding space are expected to have similar scores |
| `WhiteKernel` | Models i.i.d. observation noise — essential because scorer outputs are not exact (model temperature, image rendering stochasticity) |

All three kernel parameters (amplitude, length scale, noise level) are treated as
hyperparameters and optimised by maximising the log marginal likelihood during `fit_gpr`.
`n_restarts_optimizer=5` runs the L-BFGS-B optimiser from five random starting points to
reduce the chance of a poor local optimum.

### Length scale

The RBF length scale controls **how far apart two points in embedding space must be
before their scores are treated as essentially uncorrelated**. A small length scale means
the function is jagged and local; a large one means it is nearly flat. The initial value
of 1.0 is in the StandardScaler-normalised space (see below), so it is roughly "one
standard deviation of spread in each embedding dimension." The optimiser adjusts this
during fitting.

### Feature scaling

X is passed through `sklearn.StandardScaler` before being given to the GPR. This matters
because the RBF kernel computes Euclidean distances: without scaling, dimensions with
larger variance would dominate the distance calculation and the length scale would not
be comparable across dimensions. Scaling is fit on the training set and applied to both
training points and candidates at inference time.

---

## Acquisition function: Expected Improvement

```
EI(x) = (μ(x) − y_best − ξ) · Φ(z) + σ(x) · φ(z)
z     = (μ(x) − y_best − ξ) / σ(x)
```

Where `μ` and `σ` are the GPR posterior mean and standard deviation, `y_best` is the
highest score seen so far, `Φ` is the standard normal CDF, and `φ` is its PDF.

`ξ=0.01` is a small exploration bonus. The first BO pick is the candidate with the
highest EI.

---

## Batch BO: the fantasy step

We return **two** picks per BO call to allow a pair of variants to be tested in the
next round. Naively choosing the top-2 EI would often select very similar combinations
(the two highest-EI points tend to be neighbours in embedding space).

Instead, we use a **fantasy step**:

1. Take pick 1 (highest EI candidate).
2. Predict its score using the current GPR — this is the "fantasized" label.
3. Add `(pick_1_X, fantasized_y)` to the training set and **refit** the GPR.
4. Compute EI over the remaining candidates under the updated posterior and pick the
   highest — this is pick 2.

The fantasy posterior accounts for the information we expect to gain by running pick 1,
so pick 2 is chosen to be maximally useful *given that we are already running pick 1*.
In practice this drives the two picks to explore different regions of the embedding space.

---

## Fallback

When fewer than `MIN_TRAINING_POINTS = 2` scored observations exist, the GPR is not
fitted — there is insufficient data to estimate the length scale and the kernel would
overfit. In this case the pipeline falls back to random selection (`selection_type =
"random"`). This is expected behaviour during the first round before any variants have
been scored.

---

## Candidate pool

The candidate pool is a **cross-product of text combinations × image embeddings**:

- Text dimension: all rows in `ad_text_combination_embeddings` for `text_source_id` (N combinations)
- Image dimension: all rows in `ad_image_embeddings` for the seed ad (M image slots)
- Total candidates: N × M (e.g., 64 text combos × 4 images = 256)

Falls back to the seed ad's single `image_vector` from `ad_embeddings` if no per-image embeddings exist (e.g., embed step not yet run or ad has no URL image slots).

Each candidate's `combination` dict includes `image_url` (the local `/ad-images/...` URL) when a per-image embedding is present, allowing the frontend to display the recommended image alongside text slots.

Compound key format:
```json
{"combo": {"headline": "...", "primary_text": "..."}, "image_slot": 0}
```

---

## Files

| File | Role |
|---|---|
| `gpr.py` | Pure numpy/sklearn — `fit_gpr`, `predict_with_std`, `expected_improvement`, `fantasize` |
| `selector.py` | DB access only — loads scored variants and candidate combinations as numpy arrays; cross-products text × image embeddings |
| `pipeline.py` | Orchestration — calls selector → gpr → returns picks |
| `storage.py` | Persistence — `save_bo_run`, `get_latest_bo_run` |

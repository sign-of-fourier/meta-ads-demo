"""
GP Comparison Experiment: Pooled vs. Pooled+Flag vs. Multi-Output

Three holdout strategies × three models, using synthetic cross-platform scores
built from the real embeddings in app.db.

Synthetic score design
----------------------
Meta and Google text vectors share the same 1536-dim embedding space (both
encoded by the same text model). A single "text quality" weight vector w is
applied to the text half of every candidate. For Meta, an independent image
quality signal is mixed in. This creates genuine cross-platform correlation via
shared text quality — exactly the structure the multi-output GP is designed to
exploit.

  score_meta(i)   = sigmoid( rho * (text_i @ w) + sqrt(1-rho²) * (image_i @ w_img) + noise )
  score_google(j) = sigmoid( text_j @ w + noise )

True cross-platform correlation ≈ rho (0.5 by default).

Holdout strategies
------------------
1. Random (20% test)          — basic sanity check
2. Google-sparse (80% Google  — train on 30 Meta + 5 Google; test on rest of Google;
   test)                        tests cross-platform borrowing under data scarcity
3. Time-proxy (last 40% test) — index order as a proxy for time; simulates
                                 predicting future creative performance

Metrics (per-platform and overall)
-----------------------------------
RMSE, MAE, Spearman rank corr, top-5 hit rate (|predicted top-5 ∩ true top-5| / 5),
95% coverage (fraction of test points where true score ∈ [mean ± 2σ]).

Batch-level covariance output (Section 4)
------------------------------------------
For a 4-point mixed batch (2 Meta + 2 Google), prints:
  - predicted mean vector
  - 4×4 posterior covariance matrix with Meta/Google block labels
  - batch score (sum of means as a simple aggregate)
  - 3 Thompson samples from the joint posterior

Run
---
    cd /home/ubuntu/projects/meta-ads-demo
    python3 experiments/gp_comparison/run_experiment.py
"""

import sys
import numpy as np
from pathlib import Path
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).parent))
from data_loader import load_candidates
from models import PooledGP, PooledGPWithFlag, MultiOutputGPModel

TRUE_RHO    = 0.5   # cross-platform correlation baked into synthetic scores
MODEL_RHO   = 0.5   # rho passed to MultiOutputGP (matches true rho here)
NOISE_STD   = 0.3   # Gaussian noise on logit
SEED        = 42
TOP_K       = 5     # for hit-rate metric
N_TRAIN_META_SPARSE   = 30
N_TRAIN_GOOGLE_SPARSE = 5


# ── Synthetic score generation ────────────────────────────────────────────────

def make_synthetic_scores(
    meta_cands: list[dict],
    google_cands: list[dict],
    rng: np.random.Generator,
    rho: float = TRUE_RHO,
    noise: float = NOISE_STD,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Build cross-platform correlated scores using a shared text quality signal.

    Both platforms' text vectors are 1536-dim (same model), so a single weight
    vector w creates a genuine shared quality axis.

    Returns (y_meta, y_google) as float32 arrays in [0, 1].
    """
    TEXT_DIM = 1536

    w_text  = rng.standard_normal(TEXT_DIM).astype(np.float32)
    w_text /= np.linalg.norm(w_text)
    w_img   = rng.standard_normal(TEXT_DIM).astype(np.float32)
    w_img  /= np.linalg.norm(w_img)

    # Meta: shared text component + independent image component
    meta_text_sig  = np.array([c["vec"][:TEXT_DIM] @ w_text  for c in meta_cands],   dtype=np.float64)
    meta_img_sig   = np.array([c["vec"][TEXT_DIM:] @ w_img   for c in meta_cands],   dtype=np.float64)
    meta_logit     = rho * meta_text_sig + np.sqrt(1 - rho**2) * meta_img_sig

    # Google: shared text component only
    google_text_sig = np.array([c["vec"][:TEXT_DIM] @ w_text for c in google_cands], dtype=np.float64)
    google_logit    = google_text_sig

    def to_score(logit):
        logit = (logit - logit.mean()) / (logit.std() + 1e-8)
        logit += rng.normal(0, noise, size=len(logit))
        return (1.0 / (1.0 + np.exp(-logit))).astype(np.float32)

    return to_score(meta_logit), to_score(google_logit)


# ── Metrics ───────────────────────────────────────────────────────────────────

def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray, y_std: np.ndarray, k: int = TOP_K) -> dict:
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    y_std  = np.asarray(y_std,  dtype=np.float64)

    rmse     = float(np.sqrt(np.mean((y_pred - y_true) ** 2)))
    mae      = float(np.mean(np.abs(y_pred - y_true)))
    spear    = float(spearmanr(y_pred, y_true).statistic) if len(y_true) > 1 else float("nan")
    true_topk = set(np.argsort(y_true)[-k:])
    pred_topk = set(np.argsort(y_pred)[-k:])
    topk_hit  = len(true_topk & pred_topk) / k
    lo, hi    = y_pred - 2 * y_std, y_pred + 2 * y_std
    coverage  = float(np.mean((y_true >= lo) & (y_true <= hi)))

    return {
        "rmse":     rmse,
        "mae":      mae,
        "spearman": spear,
        "topk_hit": topk_hit,
        "coverage": coverage,
        "n":        len(y_true),
    }


# ── Print helpers ─────────────────────────────────────────────────────────────

def _print_metrics_row(model_name: str, split_name: str, platform: str, m: dict):
    print(
        f"  {model_name:<22s} | {split_name:<18s} | {platform:<8s}"
        f" | RMSE={m['rmse']:.4f}  MAE={m['mae']:.4f}"
        f"  ρ={m['spearman']:+.3f}  top-{TOP_K}={m['topk_hit']:.2f}"
        f"  cov95={m['coverage']:.2f}  (n={m['n']})"
    )


def _print_matrix(label: str, M: np.ndarray, row_labels: list[str], col_labels: list[str]):
    print(f"\n{label}:")
    header = "         " + "  ".join(f"{cl:>8s}" for cl in col_labels)
    print(header)
    for rl, row in zip(row_labels, M):
        print(f"  {rl:>6s}  " + "  ".join(f"{v:8.4f}" for v in row))


# ── Single holdout run ────────────────────────────────────────────────────────

def run_split(
    split_name: str,
    X_meta_all: np.ndarray,
    X_google_all: np.ndarray,
    y_meta: np.ndarray,
    y_google: np.ndarray,
    train_meta_idx: np.ndarray,
    test_meta_idx: np.ndarray,
    train_google_idx: np.ndarray,
    test_google_idx: np.ndarray,
):
    print(f"\n{'='*90}")
    print(f"HOLDOUT: {split_name}")
    print(
        f"  Train: {len(train_meta_idx)} Meta + {len(train_google_idx)} Google | "
        f"Test: {len(test_meta_idx)} Meta + {len(test_google_idx)} Google"
    )
    print(f"{'='*90}")

    X_meta_tr    = X_meta_all[train_meta_idx]
    X_meta_te    = X_meta_all[test_meta_idx]
    X_google_tr  = X_google_all[train_google_idx]
    X_google_te  = X_google_all[test_google_idx]

    y_meta_tr    = y_meta[train_meta_idx]
    y_meta_te    = y_meta[test_meta_idx]
    y_google_tr  = y_google[train_google_idx]
    y_google_te  = y_google[test_google_idx]

    models = {
        "Pooled GP":         PooledGP(),
        "Pooled GP + flag":  PooledGPWithFlag(),
        "Multi-output GP":   MultiOutputGPModel(rho=MODEL_RHO),
    }

    print()
    print(f"  {'Model':<22s} | {'Split':<18s} | {'Platform':<8s} | Metrics")
    print(f"  {'-'*22}   {'-'*18}   {'-'*8}   {'-'*55}")

    for name, model in models.items():
        model.fit(X_meta_tr, X_google_tr, y_meta_tr, y_google_tr, X_meta_all, X_google_all)

        mean, std = model.predict(X_meta_te, X_google_te)
        n_m = len(X_meta_te)
        n_g = len(X_google_te)

        overall_true = np.concatenate([y_meta_te, y_google_te])
        m_all = compute_metrics(overall_true, mean, std)
        _print_metrics_row(name, split_name, "overall", m_all)

        if n_m > 0:
            m_meta = compute_metrics(y_meta_te, mean[:n_m], std[:n_m])
            _print_metrics_row(name, split_name, "meta", m_meta)

        if n_g > 0:
            m_google = compute_metrics(y_google_te, mean[n_m:], std[n_m:])
            _print_metrics_row(name, split_name, "google", m_google)

        print()

    return models  # return for reuse in batch section


# ── Section 4: batch-level covariance ────────────────────────────────────────

def batch_covariance_section(
    models: dict,
    X_meta_all: np.ndarray,
    X_google_all: np.ndarray,
    rng: np.random.Generator,
):
    """
    Pick 2 Meta + 2 Google test points (held out from the random split),
    and for each model print the full 4×4 posterior covariance.
    """
    print(f"\n{'='*90}")
    print("SECTION 4: batch-level covariance output — mixed batch of 4 (2 Meta + 2 Google)")
    print(f"{'='*90}")
    print(
        "\nFor a real BO run you'd query the posterior over all candidates in a mixed"
        " batch.\nHere we inspect the 4×4 joint covariance for 2 Meta + 2 Google test"
        " points.\nThe Meta–Google off-diagonal block should be near-zero for the pooled"
        " models\nand non-zero for the multi-output GP (scaled by B[0,1]=ρ).\n"
    )

    # Pick 2 random indices from each pool
    meta_idx   = rng.choice(len(X_meta_all),   2, replace=False)
    google_idx = rng.choice(len(X_google_all), 2, replace=False)

    Xm_batch = X_meta_all[meta_idx]
    Xg_batch = X_google_all[google_idx]

    labels = ["Meta-0", "Meta-1", "Goog-0", "Goog-1"]

    for name, model in models.items():
        print(f"\n── {name} ──────────────────────────────────────────────────")
        mean, cov = model.predict_cov(Xm_batch, Xg_batch)
        std = np.sqrt(np.maximum(np.diag(cov), 0.0))

        print(f"  Mean:  {['%.4f'%v for v in mean]}")
        print(f"  Std:   {['%.4f'%v for v in std]}")
        print(f"  Batch score (sum of means): {mean.sum():.4f}")

        _print_matrix("  Covariance (4×4)", cov, labels, labels)

        # Highlight the cross-platform block
        cross = cov[:2, 2:]
        print(f"\n  Meta–Google cross block (2×2):")
        for row in cross:
            print("    " + "  ".join(f"{v:8.4f}" for v in row))
        print(f"  (Should be near B[0,1]×RBF ≈ ρ×k for multi-output GP; near 0 for pooled)")

        # Thompson samples
        samples = model.sample(Xm_batch, Xg_batch, n=3, rng=rng)
        print(f"\n  3 Thompson samples from joint posterior (each row = one draw):")
        for i, s in enumerate(samples):
            print(f"    sample {i}: {['%.4f'%v for v in s]}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    rng = np.random.default_rng(SEED)

    # ── 1. Load raw embeddings ────────────────────────────────────────────────
    print("=" * 90)
    print("SECTION 1: load embeddings")
    print("=" * 90)

    meta_cands, google_cands = load_candidates()
    print(f"Meta   candidates: {len(meta_cands)}  (3072-dim each)")
    print(f"Google candidates: {len(google_cands)}  (1536-dim each)")

    X_meta_all   = np.vstack([c["vec"] for c in meta_cands]).astype(np.float32)
    X_google_all = np.vstack([c["vec"] for c in google_cands]).astype(np.float32)

    # ── 2. Synthetic scores ───────────────────────────────────────────────────
    print(f"\n{'='*90}")
    print("SECTION 2: synthetic scores")
    print(f"{'='*90}")
    print(
        f"True cross-platform correlation rho={TRUE_RHO} (shared text-quality weight vector)."
    )
    print(
        "Both platforms use the same w applied to their 1536-dim text vectors.\n"
        "Meta also has an image component. Google is text-only.\n"
        "Scores are sigmoid of a normalised logit + Gaussian noise.\n"
        f"[FAKE] scored_observations is empty — these numbers are synthetic.\n"
    )

    y_meta, y_google = make_synthetic_scores(meta_cands, google_cands, rng)
    print(f"  Meta   n={len(y_meta):4d}  min={y_meta.min():.3f}  mean={y_meta.mean():.3f}  max={y_meta.max():.3f}")
    print(f"  Google n={len(y_google):4d}  min={y_google.min():.3f}  mean={y_google.mean():.3f}  max={y_google.max():.3f}")

    # ── 3. Holdout splits ─────────────────────────────────────────────────────
    N_meta   = len(y_meta)
    N_google = len(y_google)

    # ── Holdout 1: random 80/20 ───────────────────────────────────────────────
    all_meta_idx   = np.arange(N_meta)
    all_google_idx = np.arange(N_google)
    rng.shuffle(all_meta_idx)
    rng.shuffle(all_google_idx)

    cut_m = int(0.8 * N_meta)
    cut_g = int(0.8 * N_google)

    last_models = run_split(
        "Random 80/20",
        X_meta_all, X_google_all, y_meta, y_google,
        train_meta_idx   = all_meta_idx[:cut_m],
        test_meta_idx    = all_meta_idx[cut_m:],
        train_google_idx = all_google_idx[:cut_g],
        test_google_idx  = all_google_idx[cut_g:],
    )

    # ── Holdout 2: Google-sparse ──────────────────────────────────────────────
    rng.shuffle(all_meta_idx)
    rng.shuffle(all_google_idx)

    run_split(
        "Google-sparse (5 train)",
        X_meta_all, X_google_all, y_meta, y_google,
        train_meta_idx   = all_meta_idx[:N_TRAIN_META_SPARSE],
        test_meta_idx    = all_meta_idx[N_TRAIN_META_SPARSE:N_TRAIN_META_SPARSE + 50],  # 50 Meta test pts
        train_google_idx = all_google_idx[:N_TRAIN_GOOGLE_SPARSE],
        test_google_idx  = all_google_idx[N_TRAIN_GOOGLE_SPARSE:],
    )

    # ── Holdout 3: time-proxy (last 40% test) ─────────────────────────────────
    # Indices are in DB insertion order; treat later indices as "newer" batches.
    cut_m_time = int(0.6 * N_meta)
    cut_g_time = int(0.6 * N_google)

    run_split(
        "Time-proxy (last 40%)",
        X_meta_all, X_google_all, y_meta, y_google,
        train_meta_idx   = np.arange(cut_m_time),
        test_meta_idx    = np.arange(cut_m_time, N_meta),
        train_google_idx = np.arange(cut_g_time),
        test_google_idx  = np.arange(cut_g_time, N_google),
    )

    # ── 4. Batch-level covariance (reuse last_models from random split) ───────
    batch_covariance_section(last_models, X_meta_all, X_google_all, rng)

    print(f"\n{'='*90}")
    print("Experiment complete.")
    print(
        "\nSuccess criteria (from plan):"
        "\n  - better prediction on sparse-platform holdout"
        "\n  - better ranking of mixed-platform candidates"
        "\n  - sensible non-zero cross-platform covariance in multi-output GP"
        "\n  - no major numerical stability issues"
        "\n"
        "If multi-output GP shows notably better Spearman or top-k on the Google-sparse"
        " holdout,\nthat is the cross-platform borrowing effect working as intended."
    )
    print(f"{'='*90}\n")


if __name__ == "__main__":
    main()

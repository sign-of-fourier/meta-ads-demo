"""
Test the Modal multioutput GP endpoint before deploying.

Loads real embeddings from app.db, builds synthetic cross-platform scores,
then calls the endpoint with BOTH the existing single-output path and the new
multioutput path.  Prints picks, posterior stats, and a side-by-side summary
so you can verify correctness without touching the deployed endpoint.

Usage
-----
1. Start a local serve session in a separate terminal:
       cd ~/projects/boaz/modal
       modal serve modal_gp_api.py

2. Copy the printed URL (something like https://markshipman4273--bo-gp-service-dev-gp-suggest.modal.run)

3. Run this script:
       cd /home/ubuntu/projects/meta-ads-demo
       MODAL_BO_API_URL=<url-from-step-2> python experiments/gp_comparison/test_modal_endpoint.py

   Or point at the deployed endpoint to compare live:
       MODAL_BO_API_URL=https://info-29741--bo-gp-service-gp-suggest.modal.run \
           python experiments/gp_comparison/test_modal_endpoint.py
"""

import os
import sys
import json
import urllib.request
import numpy as np
from pathlib import Path
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).parent))
from data_loader import load_candidates

SEED     = 42
PCA_DIMS = 16      # keep small for fast local testing
RHO      = 0.5     # cross-platform correlation passed to the server
Q        = 4       # batch size
N_TRAIN  = 20      # scored observations per platform (synthetic)
NOISE    = 0.25    # synthetic score noise


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fit_pca(X_all: np.ndarray, n_components: int):
    nc = min(n_components, X_all.shape[0], X_all.shape[1])
    sc = StandardScaler()
    pca = PCA(n_components=nc, random_state=SEED)
    X_reduced = pca.fit_transform(sc.fit_transform(X_all)).astype(np.float32)
    return sc, pca, X_reduced


def _project(sc, pca, X: np.ndarray) -> np.ndarray:
    return pca.transform(sc.transform(X)).astype(np.float32)


def _pad_to(arr: np.ndarray, k: int) -> np.ndarray:
    if arr.shape[1] == k:
        return arr
    return np.hstack([arr, np.zeros((len(arr), k - arr.shape[1]), dtype=arr.dtype)])


def _make_synthetic_scores(X_meta_pca, X_google_pca, rng, rho=RHO, noise=NOISE):
    """Shared-weight synthetic scores with known cross-platform correlation."""
    k = X_meta_pca.shape[1]
    w = rng.standard_normal(k).astype(np.float32)
    w /= np.linalg.norm(w)
    w_img = rng.standard_normal(k).astype(np.float32)
    w_img /= np.linalg.norm(w_img)

    meta_text = X_meta_pca @ w
    meta_img  = X_meta_pca @ w_img
    meta_logit = rho * meta_text + np.sqrt(1 - rho**2) * meta_img

    google_logit = X_google_pca @ w

    def to_score(logit, n):
        logit = (logit - logit.mean()) / (logit.std() + 1e-8)
        logit = logit + rng.normal(0, noise, n).astype(np.float32)
        return (1.0 / (1.0 + np.exp(-logit))).astype(np.float32)

    return to_score(meta_logit, len(X_meta_pca)), to_score(google_logit, len(X_google_pca))


def _post(url: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _rank_normal(y: np.ndarray) -> np.ndarray:
    from scipy.stats import norm
    n = len(y)
    ranks = np.argsort(np.argsort(y)).astype(np.float64)
    u = (ranks + 1.0) / (n + 1.0)
    u = u * 0.9999 + 0.00005
    return norm.ppf(u).astype(np.float32)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    api_url = os.environ.get("MODAL_BO_API_URL", "").strip()
    if not api_url:
        print("ERROR: set MODAL_BO_API_URL to the modal serve URL before running.")
        sys.exit(1)

    print(f"Endpoint: {api_url}")
    print(f"PCA_DIMS={PCA_DIMS}  Q={Q}  N_TRAIN={N_TRAIN}  RHO={RHO}\n")

    rng = np.random.default_rng(SEED)

    # ── 1. Load embeddings ────────────────────────────────────────────────────
    print("Loading embeddings from app.db …")
    meta_cands, google_cands = load_candidates()
    print(f"  Meta: {len(meta_cands)} candidates (3072-dim)")
    print(f"  Google: {len(google_cands)} candidates (1536-dim)")

    X_meta_raw   = np.vstack([c["vec"] for c in meta_cands]).astype(np.float32)
    X_google_raw = np.vstack([c["vec"] for c in google_cands]).astype(np.float32)

    # ── 2. Separate PCA per platform ─────────────────────────────────────────
    print(f"\nFitting PCA (k={PCA_DIMS}) per platform …")
    sc_m, pca_m, X_meta_pca_all   = _fit_pca(X_meta_raw,   PCA_DIMS)
    sc_g, pca_g, X_google_pca_all = _fit_pca(X_google_raw, PCA_DIMS)
    print(f"  Meta   variance explained: {pca_m.explained_variance_ratio_.sum():.1%}")
    print(f"  Google variance explained: {pca_g.explained_variance_ratio_.sum():.1%}")

    # ── 3. Synthetic scores ───────────────────────────────────────────────────
    y_meta_all, y_google_all = _make_synthetic_scores(X_meta_pca_all, X_google_pca_all, rng)

    # Train/candidate split
    n_m = min(N_TRAIN, len(X_meta_pca_all) - Q)
    n_g = min(N_TRAIN, len(X_google_pca_all) - Q)

    idx_m = rng.choice(len(X_meta_pca_all), len(X_meta_pca_all), replace=False)
    idx_g = rng.choice(len(X_google_pca_all), len(X_google_pca_all), replace=False)

    train_m_idx = idx_m[:n_m];  cand_m_idx = idx_m[n_m:]
    train_g_idx = idx_g[:n_g];  cand_g_idx = idx_g[n_g:]

    X_train_m = X_meta_pca_all[train_m_idx]
    X_cand_m  = X_meta_pca_all[cand_m_idx]
    y_train_m = y_meta_all[train_m_idx]

    X_train_g = X_google_pca_all[train_g_idx]
    X_cand_g  = X_google_pca_all[cand_g_idx]
    y_train_g = y_google_all[train_g_idx]

    print(f"\nTrain: {n_m} Meta + {n_g} Google  |  Candidates: {len(cand_m_idx)} Meta + {len(cand_g_idx)} Google")

    # ── 4. Shared ECDF → rank-normal targets ─────────────────────────────────
    all_y_train = np.concatenate([y_train_m, y_train_g])
    y_rn_m = _rank_normal(y_train_m)
    y_rn_g = _rank_normal(y_train_g)
    # Shared ECDF: rank within the joint pool
    from scipy.stats import norm as _norm
    sorted_pool = np.sort(all_y_train)
    def shared_ecdf(vals):
        ranks = np.searchsorted(sorted_pool, vals, side="right").astype(np.float64)
        u = ranks / (len(sorted_pool) + 1.0)
        u = u * 0.9999 + 0.00005
        return _norm.ppf(u).astype(np.float32)

    y_shared_m = shared_ecdf(y_train_m)
    y_shared_g = shared_ecdf(y_train_g)

    # ── 5. Build shared-PCA pool (current production approach) ───────────────
    # Zero-pad Google to 3072, fit one PCA, stack
    X_google_padded_tr = np.hstack([X_train_g, np.zeros((n_g, PCA_DIMS), dtype=np.float32)])
    X_google_padded_ca = np.hstack([X_cand_g,  np.zeros((len(cand_g_idx), PCA_DIMS), dtype=np.float32)])
    X_all_unified = np.vstack([X_train_m, X_google_padded_tr, X_cand_m, X_google_padded_ca])
    sc_u, pca_u, X_all_unified_pca = _fit_pca(X_all_unified, PCA_DIMS)

    n_train_total = n_m + n_g
    X_train_unified = X_all_unified_pca[:n_train_total]
    X_cands_unified = X_all_unified_pca[n_train_total:]
    y_train_unified = np.concatenate([y_shared_m, y_shared_g])

    # ── 6. Call single-output endpoint ───────────────────────────────────────
    print("\n" + "="*60)
    print("CALL A: single-output gpytorch (current production path)")
    print("="*60)

    payload_single = {
        "X": X_train_unified.tolist(),
        "y": y_train_unified.tolist(),
        "candidates": X_cands_unified.tolist(),
        "q": Q,
        "n_batches": 256,
        "mode": "debug",
    }

    print("  Sending request …")
    resp_single = _post(api_url, payload_single)
    print(f"  Picks ({len(resp_single['candidates'])}):")
    for c in resp_single["candidates"]:
        platform = "Meta  " if c["index"] < len(cand_m_idx) else "Google"
        local_idx = c["index"] if c["index"] < len(cand_m_idx) else c["index"] - len(cand_m_idx)
        true_score = (y_meta_all[cand_m_idx[local_idx]] if c["index"] < len(cand_m_idx)
                      else y_google_all[cand_g_idx[local_idx]])
        print(f"    idx={c['index']:4d}  {platform}  μ={c['mu']:+.3f}  σ={c['sigma']:.3f}  true_score={true_score:.3f}")
    if resp_single.get("ei_scores"):
        print(f"  Best batch EI: {resp_single['ei_scores'][0]:.6f}")

    # ── 7. Call multioutput endpoint ─────────────────────────────────────────
    print("\n" + "="*60)
    print("CALL B: multioutput GP (separate PCA per platform, rho=%.2f)" % RHO)
    print("="*60)

    # Stack per-platform PCA outputs (same k-dim) with output indices
    X_train_mo = np.vstack([X_train_m, X_train_g])
    y_train_mo = np.concatenate([y_shared_m, y_shared_g])
    d_train_mo = np.concatenate([
        np.zeros(n_m, dtype=np.int32),
        np.ones(n_g,  dtype=np.int32),
    ])

    X_cands_mo = np.vstack([X_cand_m, X_cand_g])
    d_cands_mo = np.concatenate([
        np.zeros(len(cand_m_idx), dtype=np.int32),
        np.ones(len(cand_g_idx),  dtype=np.int32),
    ])

    payload_multi = {
        "X": X_train_mo.tolist(),
        "y": y_train_mo.tolist(),
        "candidates": X_cands_mo.tolist(),
        "d": d_train_mo.tolist(),
        "d_candidates": d_cands_mo.tolist(),
        "rho": RHO,
        "q": Q,
        "n_batches": 256,
        "mode": "debug",
    }

    print("  Sending request …")
    resp_multi = _post(api_url, payload_multi)
    print(f"  Picks ({len(resp_multi['candidates'])}):")
    for c in resp_multi["candidates"]:
        platform = "Meta  " if c["index"] < len(cand_m_idx) else "Google"
        local_idx = c["index"] if c["index"] < len(cand_m_idx) else c["index"] - len(cand_m_idx)
        true_score = (y_meta_all[cand_m_idx[local_idx]] if c["index"] < len(cand_m_idx)
                      else y_google_all[cand_g_idx[local_idx]])
        print(f"    idx={c['index']:4d}  {platform}  μ={c['mu']:+.3f}  σ={c['sigma']:.3f}  true_score={true_score:.3f}")
    if resp_multi.get("ei_scores"):
        print(f"  Best batch EI: {resp_multi['ei_scores'][0]:.6f}")

    # ── 8. Summary ────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    def _true_scores(resp, cand_m_idx, cand_g_idx):
        scores = []
        for c in resp["candidates"]:
            if c["index"] < len(cand_m_idx):
                scores.append(float(y_meta_all[cand_m_idx[c["index"]]]))
            else:
                li = c["index"] - len(cand_m_idx)
                scores.append(float(y_google_all[cand_g_idx[li]]))
        return scores

    ts_single = _true_scores(resp_single, cand_m_idx, cand_g_idx)
    ts_multi  = _true_scores(resp_multi,  cand_m_idx, cand_g_idx)

    print(f"  Single-output picks — mean true score: {np.mean(ts_single):.3f}  max: {np.max(ts_single):.3f}")
    print(f"  Multioutput  picks — mean true score: {np.mean(ts_multi):.3f}  max: {np.max(ts_multi):.3f}")
    print()
    print("(Scores are synthetic — this checks mechanics, not real performance.)")
    print("If both calls return picks without errors, the endpoint is working correctly.")
    print("="*60)


if __name__ == "__main__":
    main()

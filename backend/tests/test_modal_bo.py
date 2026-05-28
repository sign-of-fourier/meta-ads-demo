"""
Tests for bo_pipeline.modal_bo (PCA helpers, snap, and live Modal API).

Unit tests (no network):
  TestModalBOUnit — fit_pca, project, dim_bounds, snap_to_pool

Live test (requires MODAL_BO_API_URL to be set and the Modal service to be running):
  TestModalBOLive — calls the real Modal GP endpoint and checks the response shape

Run all:
    python -m pytest tests/test_modal_bo.py -v

Run only units (no network):
    python -m pytest tests/test_modal_bo.py -v -k "TestModalBOUnit"

Run live smoke test:
    python -m pytest tests/test_modal_bo.py::TestModalBOLive -v -s
"""

import os

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Unit tests — no network
# ---------------------------------------------------------------------------

class TestModalBOUnit:
    """PCA helpers and snap_to_pool — pure numpy/sklearn, no network calls."""

    def _make_X(self, n=30, d=256, seed=42):
        return np.random.default_rng(seed).random((n, d)).astype(np.float32)

    def test_fit_pca_output_shape(self):
        from bo_pipeline.modal_bo import fit_pca
        X = self._make_X()
        pca, X_pca = fit_pca(X, n_components=16)
        assert X_pca.shape == (30, 16)
        assert X_pca.dtype == np.float32

    def test_fit_pca_capped_at_n_samples(self):
        from bo_pipeline.modal_bo import fit_pca
        X = self._make_X(n=5, d=256)
        # Asking for more components than samples — should be capped to 5
        pca, X_pca = fit_pca(X, n_components=64)
        assert X_pca.shape[1] <= 5

    def test_fit_pca_capped_at_n_features(self):
        from bo_pipeline.modal_bo import fit_pca
        X = self._make_X(n=100, d=10)
        pca, X_pca = fit_pca(X, n_components=64)
        assert X_pca.shape[1] <= 10

    def test_project_consistent_with_fit(self):
        from bo_pipeline.modal_bo import fit_pca, project
        X = self._make_X()
        pca, X_pca = fit_pca(X, n_components=8)
        X_proj = project(pca, X)
        np.testing.assert_allclose(X_pca, X_proj, atol=1e-4)

    def test_dim_bounds_length(self):
        from bo_pipeline.modal_bo import dim_bounds
        X_pca = np.random.default_rng(0).random((20, 16)).astype(np.float32)
        bounds = dim_bounds(X_pca)
        assert len(bounds) == 16
        for lo, hi in bounds:
            assert lo <= hi

    def test_dim_bounds_min_max_correct(self):
        from bo_pipeline.modal_bo import dim_bounds
        X_pca = np.array([[0.0, 1.0], [0.5, 0.2], [1.0, 0.8]], dtype=np.float32)
        bounds = dim_bounds(X_pca)
        assert bounds[0] == (pytest.approx(0.0), pytest.approx(1.0))
        assert bounds[1] == (pytest.approx(0.2), pytest.approx(1.0))

    def test_snap_to_pool_returns_correct_indices(self):
        from bo_pipeline.modal_bo import snap_to_pool
        # 5 candidate PCA points; suggest exactly point 2 and point 4
        X_cands = np.eye(5, dtype=np.float32)  # each row is a unit vector
        cands = [{"id": i} for i in range(5)]
        suggestions = [X_cands[2], X_cands[4]]
        picked = snap_to_pool(suggestions, X_cands, cands)
        assert picked == [2, 4]

    def test_snap_to_pool_deduplicates(self):
        from bo_pipeline.modal_bo import snap_to_pool
        # Both suggestions point to index 0 — second pick should take nearest other
        X_cands = np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]], dtype=np.float32)
        cands = [{"id": i} for i in range(3)]
        # Both suggestions are close to index 0; after dedup, second takes index 1
        suggestions = [X_cands[0], X_cands[0] + 0.01]
        picked = snap_to_pool(suggestions, X_cands, cands)
        assert len(picked) == 2
        assert picked[0] != picked[1]

    def test_snap_to_pool_fewer_candidates_than_suggestions(self):
        from bo_pipeline.modal_bo import snap_to_pool
        X_cands = np.eye(1, dtype=np.float32)
        cands = [{"id": 0}]
        suggestions = [X_cands[0], X_cands[0]]  # 2 suggestions, 1 candidate
        picked = snap_to_pool(suggestions, X_cands, cands)
        assert len(picked) == 1

    def test_modal_bo_disabled_when_no_env_var(self, monkeypatch):
        monkeypatch.delenv("MODAL_BO_API_URL", raising=False)
        from bo_pipeline.modal_bo import modal_bo_enabled
        assert modal_bo_enabled() is False

    def test_modal_bo_enabled_when_env_var_set(self, monkeypatch):
        monkeypatch.setenv("MODAL_BO_API_URL", "https://example.modal.run")
        from bo_pipeline import modal_bo
        import importlib
        importlib.reload(modal_bo)  # reload so env is re-read
        from bo_pipeline.modal_bo import modal_bo_enabled
        assert modal_bo_enabled() is True

    def test_pca_dims_env_var_applied(self, monkeypatch):
        from bo_pipeline.modal_bo import fit_pca
        X = self._make_X(n=50, d=256)
        monkeypatch.setenv("MODAL_BO_PCA_DIMS", "32")
        # Pass n_components=None to trigger env-var read
        _, X_pca = fit_pca(X, n_components=None)
        assert X_pca.shape[1] == 32


# ---------------------------------------------------------------------------
# Live smoke test — requires MODAL_BO_API_URL
# ---------------------------------------------------------------------------

MODAL_API_URL = os.environ.get(
    "MODAL_BO_API_URL",
    "https://markshipman4273--bo-gp-service-gp-suggest.modal.run",
)

@pytest.mark.skipif(
    not MODAL_API_URL,
    reason="MODAL_BO_API_URL not set — skipping live Modal test",
)
class TestModalBOLive:
    """
    Calls the live Modal GP endpoint.  Requires network access and a running
    Modal deployment.  These tests are skipped when MODAL_BO_API_URL is unset.

    Run with:
        python -m pytest tests/test_modal_bo.py::TestModalBOLive -v -s
    """

    N_OBS   = 10
    N_CANDS = 20
    N_DIMS  = 256  # combined embedding dim
    PCA_DIM = 16   # reduced for speed in tests

    def _make_synthetic_data(self, seed=0):
        """Return (scored_list, candidate_list) as modal_bo inputs expect."""
        rng = np.random.default_rng(seed)
        # scored: N_OBS rows with text+image vectors and a score
        scored = [
            {
                "text_vector": rng.random(1536).astype(np.float32),
                "image_vector": rng.random(1024).astype(np.float32),
                "score": float(rng.uniform(1.0, 7.0)),
                "combination_key": f"scored_{i}",
                "combination": {"headline": f"headline_{i}"},
            }
            for i in range(self.N_OBS)
        ]
        # candidates: N_CANDS rows (no score)
        candidates = [
            {
                "text_vector": rng.random(1536).astype(np.float32),
                "image_vector": rng.random(1024).astype(np.float32),
                "combination_key": f"cand_{i}",
                "combination": {"headline": f"cand_headline_{i}"},
            }
            for i in range(self.N_CANDS)
        ]
        return scored, candidates

    def _build_pca_arrays(self, scored, candidates):
        from bo_pipeline.modal_bo import fit_pca
        from ad_embedding_combiner import combine
        X_scored = np.vstack([combine(s["text_vector"], s["image_vector"]) for s in scored]).astype(np.float32)
        X_cands  = np.vstack([combine(c["text_vector"], c["image_vector"]) for c in candidates]).astype(np.float32)
        X_all    = np.vstack([X_scored, X_cands])
        _, X_all_pca = fit_pca(X_all, n_components=self.PCA_DIM)
        return X_all_pca[: len(scored)], X_all_pca[len(scored):]

    def test_call_modal_api_returns_q_candidates(self):
        """API returns exactly q=2 candidate dicts, each with a valid index and x vector."""
        from bo_pipeline.modal_bo import call_modal_api

        scored, candidates = self._make_synthetic_data()
        X_train_pca, X_cands_pca = self._build_pca_arrays(scored, candidates)
        y = np.array([s["score"] for s in scored], dtype=np.float32)

        suggestions = call_modal_api(
            api_url=MODAL_API_URL,
            X_train_pca=X_train_pca,
            y=y,
            X_cands_pca=X_cands_pca,
            q=2,
            n_batches=64,
            train_steps=20,
        )

        assert len(suggestions) == 2
        for s in suggestions:
            assert isinstance(s, dict)
            assert "index" in s and "x" in s
            assert 0 <= s["index"] < len(candidates)
            assert s["x"].shape == (self.PCA_DIM,)

    def test_suggestions_within_bounds(self):
        """Each returned candidate x vector lies within the range of the candidate pool."""
        from bo_pipeline.modal_bo import call_modal_api

        scored, candidates = self._make_synthetic_data(seed=1)
        X_train_pca, X_cands_pca = self._build_pca_arrays(scored, candidates)
        y = np.array([s["score"] for s in scored], dtype=np.float32)

        suggestions = call_modal_api(
            api_url=MODAL_API_URL,
            X_train_pca=X_train_pca,
            y=y,
            X_cands_pca=X_cands_pca,
            q=2,
            n_batches=64,
            train_steps=20,
        )

        lo = X_cands_pca.min(axis=0)
        hi = X_cands_pca.max(axis=0)
        for s in suggestions:
            for i in range(self.PCA_DIM):
                assert lo[i] - 0.01 <= float(s["x"][i]) <= hi[i] + 0.01, (
                    f"dim {i}: {s['x'][i]:.4f} outside [{lo[i]:.4f}, {hi[i]:.4f}]"
                )

    def test_snap_produces_distinct_valid_candidates(self):
        """End-to-end: API returns 2 distinct valid candidate indices."""
        from bo_pipeline.modal_bo import call_modal_api

        scored, candidates = self._make_synthetic_data(seed=2)
        X_train_pca, X_cands_pca = self._build_pca_arrays(scored, candidates)
        y = np.array([s["score"] for s in scored], dtype=np.float32)

        suggestions = call_modal_api(
            api_url=MODAL_API_URL,
            X_train_pca=X_train_pca,
            y=y,
            X_cands_pca=X_cands_pca,
            q=2,
            n_batches=64,
            train_steps=20,
        )

        indices = [s["index"] for s in suggestions]
        assert len(indices) == 2
        assert indices[0] != indices[1]
        for idx in indices:
            assert 0 <= idx < len(candidates)

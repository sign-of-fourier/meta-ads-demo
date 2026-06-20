"""
Tests for bo_pipeline.modal_bo (PCA helpers and live Modal API).

Unit tests (no network):
  TestModalBOUnit — fit_pca, project, dim_bounds,
                    call_modal_api_multioutput payload shape (urlopen mocked)

Live test (requires MODAL_BO_API_URL to be set and the Modal service to be running):
  TestModalBOLive — calls the real Modal GP endpoint and checks the response shape

Run all:
    python -m pytest tests/test_modal_bo.py -v

Run only units (no network):
    python -m pytest tests/test_modal_bo.py -v -k "TestModalBOUnit"

Run live smoke test:
    python -m pytest tests/test_modal_bo.py::TestModalBOLive -v -s
"""

import json
import os

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Unit tests — no network
# ---------------------------------------------------------------------------

class TestModalBOUnit:
    """PCA helpers — pure numpy/sklearn, no network calls."""

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

    # ── call_modal_api_multioutput — payload and response (urlopen mocked) ────

    def _mock_urlopen(self, fake_candidates: list[dict]):
        """Return a fake_urlopen callable that captures the request body and
        returns a response containing fake_candidates."""
        from unittest.mock import MagicMock

        captured = {}
        fake_resp_bytes = json.dumps({"candidates": fake_candidates}).encode()

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = fake_resp_bytes

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return mock_resp

        return fake_urlopen, captured

    def test_multioutput_payload_includes_d_and_rho(self):
        """call_modal_api_multioutput sends d, d_candidates, and rho in the JSON body."""
        from unittest.mock import patch
        from bo_pipeline.modal_bo import call_modal_api_multioutput

        rng = np.random.default_rng(7)
        n_dims = 8
        X_train = rng.random((5, n_dims)).astype(np.float32)
        y       = rng.random(5).astype(np.float32)
        X_cands = rng.random((10, n_dims)).astype(np.float32)
        d_train = np.array([0, 0, 1, 1, 0], dtype=np.int32)
        d_cands = np.array([0]*5 + [1]*5, dtype=np.int32)

        fake_urlopen, captured = self._mock_urlopen(
            [{"index": 0, "x": X_cands[0].tolist()}]
        )
        with patch("quantecarlo._modal_api.urllib.request.urlopen", fake_urlopen):
            call_modal_api_multioutput(
                api_url="https://fake.modal.run",
                X=X_train,
                y=y,
                candidates=X_cands,
                d_train=d_train,
                d_cands=d_cands,
                rho=0.7,
                q=1,
            )

        body = captured["body"]
        assert "d" in body,            "payload missing 'd'"
        assert "d_candidates" in body, "payload missing 'd_candidates'"
        assert "rho" in body,          "payload missing 'rho'"
        assert body["d"]            == d_train.tolist()
        assert body["d_candidates"] == d_cands.tolist()
        assert body["rho"]          == pytest.approx(0.7)

    def test_multioutput_response_parsed_to_index_and_x(self):
        """call_modal_api_multioutput returns dicts with int index and float32 x array."""
        from unittest.mock import patch
        from bo_pipeline.modal_bo import call_modal_api_multioutput

        rng = np.random.default_rng(8)
        n_dims = 8
        x_vec = rng.random(n_dims).tolist()

        fake_urlopen, _ = self._mock_urlopen(
            [{"index": 2, "x": x_vec, "mu": 0.5, "sigma": 0.1}]
        )
        with patch("quantecarlo._modal_api.urllib.request.urlopen", fake_urlopen):
            result = call_modal_api_multioutput(
                api_url="https://fake.modal.run",
                X=rng.random((3, n_dims)).astype(np.float32),
                y=rng.random(3).astype(np.float32),
                candidates=rng.random((5, n_dims)).astype(np.float32),
                d_train=np.zeros(3, dtype=np.int32),
                d_cands=np.zeros(5, dtype=np.int32),
                rho=0.5,
                q=1,
            )

        assert len(result) == 1
        assert result[0]["index"] == 2
        assert result[0]["x"].shape == (n_dims,)
        assert result[0]["x"].dtype == np.float32
        assert result[0]["mu"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Live smoke test — requires MODAL_BO_API_URL
# ---------------------------------------------------------------------------

MODAL_API_URL = os.environ.get(
    "MODAL_BO_API_URL",
    "https://markshipman4273--bo-gp-service-gp-suggest.modal.run",
)

@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_MODAL_TESTS"),
    reason="RUN_LIVE_MODAL_TESTS not set — skipping live Modal test",
)
class TestModalBOLive:
    """
    Calls the live Modal GP endpoint.  Requires network access and a running
    Modal deployment.  Skipped unless RUN_LIVE_MODAL_TESTS=1 is set in the shell.
    (MODAL_BO_API_URL in .env is not sufficient — that var is for the backend server.)

    Run with:
        RUN_LIVE_MODAL_TESTS=1 python -m pytest tests/test_modal_bo.py::TestModalBOLive -v -s
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

    def test_multioutput_server_handles_d_fields(self):
        """
        Live smoke test for the multioutput GP path.

        Sends d / d_candidates / rho to the Modal endpoint so the server
        branches to _TorchMultiOutputGP.  Data is synthetic random — the
        point is that the server accepts the new fields and returns q
        candidates in the same format as the single-output path.

        10 scored observations: 5 tagged d=0 (Meta), 5 tagged d=1 (Google).
        20 candidates: 10 per platform.  All in a shared 16-dim PCA space.
        """
        from bo_pipeline.modal_bo import call_modal_api_multioutput

        rng = np.random.default_rng(99)
        n_scored_per_platform = 5
        n_cands_per_platform  = 10
        k = self.PCA_DIM  # 16

        X_train = rng.random((n_scored_per_platform * 2, k)).astype(np.float32)
        y       = rng.random(n_scored_per_platform * 2).astype(np.float32)
        X_cands = rng.random((n_cands_per_platform * 2, k)).astype(np.float32)

        d_train = np.array([0]*n_scored_per_platform + [1]*n_scored_per_platform, dtype=np.int32)
        d_cands = np.array([0]*n_cands_per_platform  + [1]*n_cands_per_platform,  dtype=np.int32)

        suggestions = call_modal_api_multioutput(
            api_url=MODAL_API_URL,
            X_train_pca=X_train,
            y=y,
            X_cands_pca=X_cands,
            d_train=d_train,
            d_cands=d_cands,
            rho=0.5,
            q=2,
            n_batches=64,
            train_steps=20,
        )

        assert len(suggestions) == 2
        for s in suggestions:
            assert isinstance(s, dict)
            assert "index" in s and "x" in s
            assert 0 <= s["index"] < len(X_cands)
            assert s["x"].shape == (k,)

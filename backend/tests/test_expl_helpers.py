"""
Unit tests for explainability helpers in bo_pipeline.

Tests _expl_local_gp_stats and _expl_nearest_known from both
pipeline.py and cross_platform.py independently of the full BO run.
No DB, no Modal API, no embeddings — pure numpy.
"""

from __future__ import annotations

import numpy as np
import pytest

from bo_pipeline.pipeline import (
    _expl_local_gp_stats as pipeline_expl_stats,
    _expl_nearest_known as pipeline_nearest_known,
    _expl_combo_label,
)
from bo_pipeline.cross_platform import (
    _expl_local_gp_stats as cross_expl_stats,
    _expl_nearest_known as cross_nearest_known,
)
from bo_pipeline.gpr import transform_y


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RNG = np.random.default_rng(42)
N_SCORED = 8
N_CANDS = 5
DIM = 20  # small PCA-projected dimension


@pytest.fixture()
def scored_data():
    X = RNG.standard_normal((N_SCORED, DIM)).astype(np.float64)
    y_raw = RNG.uniform(0.1, 1.0, size=N_SCORED)
    y = transform_y(y_raw)
    return X, y


@pytest.fixture()
def cand_data():
    return RNG.standard_normal((N_CANDS, DIM)).astype(np.float64)


# ---------------------------------------------------------------------------
# _expl_local_gp_stats
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("impl", [pipeline_expl_stats, cross_expl_stats])
def test_expl_local_gp_stats_returns_all_fields(impl, scored_data, cand_data):
    X_train, y = scored_data
    X_cands = cand_data
    picked = [0, 2]

    result = impl(picked, X_train, y, X_cands, xi=0.01)

    assert len(result) == len(picked), "one dict per picked index"
    for d in result:
        assert d["gpr_mean"] is not None, "gpr_mean must not be None"
        assert d["gpr_std"]  is not None, "gpr_std must not be None"
        assert d["ei_score"] is not None, "ei_score must not be None"


@pytest.mark.parametrize("impl", [pipeline_expl_stats, cross_expl_stats])
def test_expl_local_gp_stats_std_positive(impl, scored_data, cand_data):
    X_train, y = scored_data
    result = impl([1], X_train, y, cand_data, xi=0.01)
    assert result[0]["gpr_std"] > 0, "GP uncertainty must be positive for unseen point"


@pytest.mark.parametrize("impl", [pipeline_expl_stats, cross_expl_stats])
def test_expl_local_gp_stats_graceful_failure(impl):
    """Degenerate input (1 training point) must return None-dicts, not raise."""
    X_train = np.array([[1.0, 2.0]])
    y = np.array([0.5])
    X_cands = np.array([[1.1, 2.1], [3.0, 0.5]])
    result = impl([0], X_train, y, X_cands)
    # Should not raise; either returns values or all-None fallback
    assert len(result) == 1
    assert set(result[0].keys()) == {"gpr_mean", "gpr_std", "ei_score"}


# ---------------------------------------------------------------------------
# _expl_nearest_known
# ---------------------------------------------------------------------------

def _make_scored(X):
    return [
        {
            "combination": {"headline": f"Ad {i}", "primary_text": f"Body {i}"},
            "score": float(i) / len(X),
        }
        for i in range(len(X))
    ]


@pytest.mark.parametrize("impl", [pipeline_nearest_known, cross_nearest_known])
def test_expl_nearest_known_returns_k(impl, scored_data, cand_data):
    X_train, _ = scored_data
    scored = _make_scored(X_train)
    pick_vec = cand_data[0]

    result = impl(pick_vec, scored, X_train, k=3)

    assert len(result) == 3
    for r in result:
        assert "label" in r
        assert "score" in r
        assert "cosine_distance" in r
        assert "combination" in r
        assert 0.0 <= r["cosine_distance"] <= 2.0  # cosine distance bounded [0, 2]


@pytest.mark.parametrize("impl", [pipeline_nearest_known, cross_nearest_known])
def test_expl_nearest_known_sorted_ascending(impl, scored_data, cand_data):
    X_train, _ = scored_data
    scored = _make_scored(X_train)
    result = impl(cand_data[0], scored, X_train, k=5)
    dists = [r["cosine_distance"] for r in result]
    assert dists == sorted(dists), "nearest-known must be sorted by cosine_distance ascending"


@pytest.mark.parametrize("impl", [pipeline_nearest_known, cross_nearest_known])
def test_expl_nearest_known_empty_scored(impl, cand_data):
    result = impl(cand_data[0], [], np.empty((0, DIM)), k=3)
    assert result == [], "empty scored → empty result"


# ---------------------------------------------------------------------------
# _expl_combo_label
# ---------------------------------------------------------------------------

def test_combo_label_headline():
    assert _expl_combo_label({"headline": "Buy now", "primary_text": "Offer"}) == "Buy now"


def test_combo_label_fallback_primary_text():
    assert _expl_combo_label({"primary_text": "Great deal"}) == "Great deal"


def test_combo_label_truncates():
    long = "x" * 60
    label = _expl_combo_label({"headline": long})
    assert len(label) <= 53  # 50 + "…"
    assert label.endswith("…")


def test_combo_label_empty():
    assert _expl_combo_label({}) == "—"

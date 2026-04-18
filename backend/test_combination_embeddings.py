"""
Tests for ad_combination_embeddings (AdStac.kr).

Structure:
  TestBuildCombinations   — pure combinatorics, no API calls
  TestCombinationKey      — determinism and stability of the key function
  TestEmbedAllCombinations — integration: real Azure embeddings stored in temp DB
  TestRetrieval           — get_embeddings_for_source returns correct data

Only TestEmbedAllCombinations and TestRetrieval require AZURE_INFERENCE_KEY.
TestBuildCombinations and TestCombinationKey always run.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

# Simulates a dynamic ad with 2 headlines, 2 primary_texts, 1 description
DYNAMIC_COMPONENTS_SMALL = [
    {"slot": "headline",     "slot_index": 0, "value": "Wool Socks"},
    {"slot": "headline",     "slot_index": 1, "value": "Warm Feet Forever"},
    {"slot": "primary_text", "slot_index": 0, "value": "Hand made in Switzerland."},
    {"slot": "primary_text", "slot_index": 1, "value": "Premium wool since 1952."},
    {"slot": "description",  "slot_index": 0, "value": "Free shipping worldwide."},
    # image slot should be ignored
    {"slot": "image",        "slot_index": 0, "value": "https://example.com/img.png"},
]
# Expected: 2 × 2 × 1 = 4 combinations

# Static ad: one value per slot
STATIC_COMPONENTS = [
    {"slot": "headline",     "slot_index": 0, "value": "Best Socks Ever"},
    {"slot": "primary_text", "slot_index": 0, "value": "Made with love."},
    {"slot": "image",        "slot_index": 0, "value": "https://example.com/img.png"},
]
# Expected: 1 × 1 = 1 combination (description absent)

# Larger dynamic ad: 3 headlines × 2 primary_texts × 2 descriptions = 12 combinations
DYNAMIC_COMPONENTS_LARGE = [
    {"slot": "headline",     "slot_index": 0, "value": "Headline A"},
    {"slot": "headline",     "slot_index": 1, "value": "Headline B"},
    {"slot": "headline",     "slot_index": 2, "value": "Headline C"},
    {"slot": "primary_text", "slot_index": 0, "value": "Body text one."},
    {"slot": "primary_text", "slot_index": 1, "value": "Body text two."},
    {"slot": "description",  "slot_index": 0, "value": "Desc one."},
    {"slot": "description",  "slot_index": 1, "value": "Desc two."},
]

SOURCE_ID_SMALL = "test_combo_small_001"
SOURCE_ID_STATIC = "test_combo_static_001"
SOURCE_ID_LARGE = "test_combo_large_001"


# ---------------------------------------------------------------------------
# Pure unit tests — no API calls
# ---------------------------------------------------------------------------

class TestBuildCombinations:
    def test_correct_count_dynamic(self):
        from ad_combination_embeddings.combinations import build_combinations
        combos = build_combinations(DYNAMIC_COMPONENTS_SMALL)
        assert len(combos) == 4   # 2 × 2 × 1

    def test_correct_count_large(self):
        from ad_combination_embeddings.combinations import build_combinations
        combos = build_combinations(DYNAMIC_COMPONENTS_LARGE)
        assert len(combos) == 12  # 3 × 2 × 2

    def test_correct_count_static(self):
        from ad_combination_embeddings.combinations import build_combinations
        combos = build_combinations(STATIC_COMPONENTS)
        assert len(combos) == 1   # 1 headline × 1 primary_text (no description)

    def test_image_slot_excluded(self):
        from ad_combination_embeddings.combinations import build_combinations
        combos = build_combinations(DYNAMIC_COMPONENTS_SMALL)
        for combo in combos:
            assert "image" not in combo

    def test_all_combos_are_dicts(self):
        from ad_combination_embeddings.combinations import build_combinations
        combos = build_combinations(DYNAMIC_COMPONENTS_SMALL)
        for c in combos:
            assert isinstance(c, dict)
            assert all(isinstance(k, str) and isinstance(v, str) for k, v in c.items())

    def test_all_slot_values_present(self):
        from ad_combination_embeddings.combinations import build_combinations
        combos = build_combinations(DYNAMIC_COMPONENTS_SMALL)
        headlines = {c["headline"] for c in combos}
        bodies = {c["primary_text"] for c in combos}
        assert headlines == {"Wool Socks", "Warm Feet Forever"}
        assert bodies == {"Hand made in Switzerland.", "Premium wool since 1952."}

    def test_no_duplicate_combos(self):
        from ad_combination_embeddings.combinations import build_combinations, combination_key
        combos = build_combinations(DYNAMIC_COMPONENTS_LARGE)
        keys = [combination_key(c) for c in combos]
        assert len(keys) == len(set(keys)), "Duplicate combinations produced"

    def test_empty_components_returns_empty(self):
        from ad_combination_embeddings.combinations import build_combinations
        assert build_combinations([]) == []

    def test_only_image_slot_returns_empty(self):
        from ad_combination_embeddings.combinations import build_combinations
        assert build_combinations([{"slot": "image", "slot_index": 0, "value": "https://x.com"}]) == []

    def test_deduplicates_identical_values(self):
        from ad_combination_embeddings.combinations import build_combinations
        components = [
            {"slot": "headline", "slot_index": 0, "value": "Same Headline"},
            {"slot": "headline", "slot_index": 1, "value": "Same Headline"},  # duplicate
            {"slot": "primary_text", "slot_index": 0, "value": "Body."},
        ]
        combos = build_combinations(components)
        assert len(combos) == 1   # only 1 unique headline × 1 body

    def test_combination_count_helper(self):
        from ad_combination_embeddings.combinations import combination_count
        assert combination_count(DYNAMIC_COMPONENTS_SMALL) == 4
        assert combination_count(DYNAMIC_COMPONENTS_LARGE) == 12
        assert combination_count(STATIC_COMPONENTS) == 1
        assert combination_count([]) == 0

    def test_restrict_slots(self):
        from ad_combination_embeddings.combinations import build_combinations
        combos = build_combinations(DYNAMIC_COMPONENTS_SMALL, slots=["headline"])
        assert len(combos) == 2
        for c in combos:
            assert "primary_text" not in c
            assert "description" not in c


class TestCombinationKey:
    def test_deterministic(self):
        from ad_combination_embeddings.combinations import combination_key
        combo = {"headline": "Wool Socks", "primary_text": "Hand made."}
        assert combination_key(combo) == combination_key(combo)

    def test_order_independent(self):
        from ad_combination_embeddings.combinations import combination_key
        a = {"headline": "Wool Socks", "primary_text": "Hand made."}
        b = {"primary_text": "Hand made.", "headline": "Wool Socks"}
        assert combination_key(a) == combination_key(b)

    def test_different_values_different_keys(self):
        from ad_combination_embeddings.combinations import combination_key
        a = {"headline": "Wool Socks"}
        b = {"headline": "Warm Feet"}
        assert combination_key(a) != combination_key(b)

    def test_is_valid_json(self):
        from ad_combination_embeddings.combinations import combination_key
        key = combination_key({"headline": "Test", "primary_text": "Body."})
        parsed = json.loads(key)
        assert parsed["headline"] == "Test"
        assert parsed["primary_text"] == "Body."


# ---------------------------------------------------------------------------
# Integration tests — require AZURE_INFERENCE_KEY
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_db(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("db") / "test_combo_embeddings.db"


@pytest.fixture(scope="session")
def embedded_small(test_db) -> int:
    """Embed the small dynamic ad (4 combinations). Returns count stored."""
    from ad_combination_embeddings import embed_all_combinations
    return asyncio.run(embed_all_combinations(
        source_id=SOURCE_ID_SMALL,
        components=DYNAMIC_COMPONENTS_SMALL,
        db_path=test_db,
    ))


@pytest.fixture(scope="session")
def embedded_static(test_db) -> int:
    from ad_combination_embeddings import embed_all_combinations
    return asyncio.run(embed_all_combinations(
        source_id=SOURCE_ID_STATIC,
        components=STATIC_COMPONENTS,
        db_path=test_db,
    ))


@pytest.fixture(scope="session")
def embedded_large(test_db) -> int:
    from ad_combination_embeddings import embed_all_combinations
    return asyncio.run(embed_all_combinations(
        source_id=SOURCE_ID_LARGE,
        components=DYNAMIC_COMPONENTS_LARGE,
        db_path=test_db,
    ))


class TestEmbedAllCombinations:
    def test_small_dynamic_stores_correct_count(self, embedded_small):
        assert embedded_small == 4, f"Expected 4 embeddings, got {embedded_small}"

    def test_static_stores_one_embedding(self, embedded_static):
        assert embedded_static == 1

    def test_large_stores_twelve_embeddings(self, embedded_large):
        assert embedded_large == 12

    def test_idempotent_rerun(self, test_db):
        """Running again on same source_id should upsert, not duplicate."""
        from ad_combination_embeddings import count_embeddings_for_source, embed_all_combinations
        asyncio.run(embed_all_combinations(
            source_id=SOURCE_ID_SMALL,
            components=DYNAMIC_COMPONENTS_SMALL,
            db_path=test_db,
        ))
        count = count_embeddings_for_source(SOURCE_ID_SMALL, test_db)
        assert count == 4, f"Expected 4 after re-run (upsert), got {count}"

    def test_different_source_ids_isolated(self, embedded_small, embedded_static, test_db):
        from ad_combination_embeddings import count_embeddings_for_source
        assert count_embeddings_for_source(SOURCE_ID_SMALL, test_db) == 4
        assert count_embeddings_for_source(SOURCE_ID_STATIC, test_db) == 1


class TestRetrieval:
    def test_returns_all_combinations(self, embedded_small, test_db):
        from ad_combination_embeddings import get_embeddings_for_source
        rows = get_embeddings_for_source(SOURCE_ID_SMALL, test_db)
        assert len(rows) == 4

    def test_each_row_has_vector(self, embedded_small, test_db):
        from ad_combination_embeddings import get_embeddings_for_source
        for row in get_embeddings_for_source(SOURCE_ID_SMALL, test_db):
            assert isinstance(row["vector"], np.ndarray)
            assert row["vector"].dtype == np.float32
            assert row["vector"].shape[0] > 0

    def test_each_row_has_combination_dict(self, embedded_small, test_db):
        from ad_combination_embeddings import get_embeddings_for_source
        for row in get_embeddings_for_source(SOURCE_ID_SMALL, test_db):
            combo = row["combination"]
            assert isinstance(combo, dict)
            assert "headline" in combo or "primary_text" in combo

    def test_all_headline_values_covered(self, embedded_small, test_db):
        from ad_combination_embeddings import get_embeddings_for_source
        rows = get_embeddings_for_source(SOURCE_ID_SMALL, test_db)
        headlines = {r["combination"].get("headline") for r in rows}
        assert headlines == {"Wool Socks", "Warm Feet Forever"}

    def test_all_body_values_covered(self, embedded_small, test_db):
        from ad_combination_embeddings import get_embeddings_for_source
        rows = get_embeddings_for_source(SOURCE_ID_SMALL, test_db)
        bodies = {r["combination"].get("primary_text") for r in rows}
        assert bodies == {"Hand made in Switzerland.", "Premium wool since 1952."}

    def test_vectors_are_not_identical(self, embedded_small, test_db):
        """Different text combinations must produce different vectors."""
        from ad_combination_embeddings import get_embeddings_for_source
        rows = get_embeddings_for_source(SOURCE_ID_SMALL, test_db)
        vecs = [r["vector"] for r in rows]
        for i, a in enumerate(vecs):
            for b in vecs[i + 1:]:
                assert not np.allclose(a, b), "Two different combinations produced identical vectors"

    def test_combination_key_roundtrip(self, embedded_small, test_db):
        """combination_key should deserialise back to combination exactly."""
        from ad_combination_embeddings import get_embeddings_for_source
        for row in get_embeddings_for_source(SOURCE_ID_SMALL, test_db):
            roundtripped = json.loads(row["combination_key"])
            assert roundtripped == row["combination"]

    def test_model_name_stored(self, embedded_small, test_db):
        from ad_combination_embeddings import get_embeddings_for_source
        for row in get_embeddings_for_source(SOURCE_ID_SMALL, test_db):
            assert row["model"] is not None

    def test_nonexistent_source_returns_empty(self, test_db):
        from ad_combination_embeddings import get_embeddings_for_source
        assert get_embeddings_for_source("does_not_exist_xyz", test_db) == []

    def test_large_all_combinations_retrieved(self, embedded_large, test_db):
        from ad_combination_embeddings import get_embeddings_for_source
        rows = get_embeddings_for_source(SOURCE_ID_LARGE, test_db)
        assert len(rows) == 12
        headlines = {r["combination"].get("headline") for r in rows}
        assert headlines == {"Headline A", "Headline B", "Headline C"}

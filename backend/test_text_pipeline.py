"""
Integration tests for the ad text generation pipeline (AdStac.kr).

Covers:
  1. generate_slot_variants  — produces the right number of strings for one slot
  2. generate_all_slots      — all text slots produced in parallel
  3. assemble_dynamic_ad     — slot indexing, source labels, image URL handling
  4. run_text_pipeline       — full round-trip including DB storage
  5. seed-only images        — image_urls=None preserves seed image slots
  6. combined pipeline       — text pipeline receiving image URLs

Tests make real API calls to Azure OpenAI.
All required env vars must be set (see ad_text_generation/README.md).
No FastAPI server or HTTP server needed.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Test seed data
# Dynamic-style seed: multiple values per slot so the model has good context.
# ---------------------------------------------------------------------------

SEED_COMPONENTS = [
    {"slot": "headline",     "slot_index": 0, "value": "Wool Socks"},
    {"slot": "headline",     "slot_index": 1, "value": "Warm Feet Forever"},
    {"slot": "primary_text", "slot_index": 0, "value": "Hand made in Switzerland. Very warm and durable."},
    {"slot": "primary_text", "slot_index": 1, "value": "Premium wool craftsmanship since 1952."},
    {"slot": "description",  "slot_index": 0, "value": "Free shipping worldwide."},
    {"slot": "image",        "slot_index": 0, "value": "https://i.ibb.co/GvNwWwMw/wool-socks4.png"},
]

SEED_AD_ID = "test_text_seed_ad_001"

FAKE_IMAGE_URLS = [
    "https://i.ibb.co/GvNwWwMw/wool-socks4.png",   # reuse as stand-in for generated images
    "https://i.ibb.co/GvNwWwMw/wool-socks4.png",
    "https://i.ibb.co/GvNwWwMw/wool-socks4.png",
]

N_PER_SLOT = 3   # keep small so tests run fast


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_db(tmp_path_factory) -> Path:
    db_path = tmp_path_factory.mktemp("db") / "test_text.db"
    # generated_ads tables are created lazily by ensure_tables
    return db_path


@pytest.fixture(scope="session")
def pipeline_result(test_db) -> int:
    """Run the full text pipeline once; all tests share this result."""
    from ad_text_generation import run_text_pipeline
    generated_ad_id = asyncio.run(run_text_pipeline(
        seed_components=SEED_COMPONENTS,
        n_per_slot=N_PER_SLOT,
        image_urls=FAKE_IMAGE_URLS,
        source_ad_id=SEED_AD_ID,
        db_path=test_db,
    ))
    return generated_ad_id


@pytest.fixture(scope="session")
def pipeline_result_seed_images(test_db) -> int:
    """Run the pipeline with image_urls=None to verify seed images are preserved."""
    from ad_text_generation import run_text_pipeline
    return asyncio.run(run_text_pipeline(
        seed_components=SEED_COMPONENTS,
        n_per_slot=N_PER_SLOT,
        image_urls=None,
        source_ad_id=SEED_AD_ID,
        db_path=test_db,
    ))


# ---------------------------------------------------------------------------
# Tests — generate_slot_variants (unit-level)
# ---------------------------------------------------------------------------

class TestGenerateSlotVariants:
    def test_produces_requested_count(self):
        from ad_text_generation.generator import generate_slot_variants
        variants = asyncio.run(generate_slot_variants(
            slot="headline",
            existing_values=["Wool Socks", "Warm Feet Forever"],
            n_variants=N_PER_SLOT,
        ))
        assert len(variants) == N_PER_SLOT, f"Expected {N_PER_SLOT}, got {len(variants)}"

    def test_returns_strings(self):
        from ad_text_generation.generator import generate_slot_variants
        variants = asyncio.run(generate_slot_variants(
            slot="headline",
            existing_values=["Wool Socks"],
            n_variants=2,
        ))
        assert all(isinstance(v, str) and v.strip() for v in variants)

    def test_no_exact_duplicates_of_seed(self):
        from ad_text_generation.generator import generate_slot_variants
        seed_values = ["Wool Socks", "Warm Feet Forever"]
        variants = asyncio.run(generate_slot_variants(
            slot="headline",
            existing_values=seed_values,
            n_variants=N_PER_SLOT,
        ))
        for v in variants:
            assert v not in seed_values, f"Model returned a seed value verbatim: {v!r}"

    def test_primary_text_variants(self):
        from ad_text_generation.generator import generate_slot_variants
        variants = asyncio.run(generate_slot_variants(
            slot="primary_text",
            existing_values=["Hand made in Switzerland. Very warm and durable."],
            n_variants=2,
        ))
        assert len(variants) == 2
        # Primary text should be longer than a headline
        for v in variants:
            assert len(v) > 10, f"primary_text variant seems too short: {v!r}"


# ---------------------------------------------------------------------------
# Tests — generate_all_slots (parallel generation)
# ---------------------------------------------------------------------------

class TestGenerateAllSlots:
    @pytest.fixture(scope="class")
    def all_slots_result(self):
        from ad_text_generation.generator import generate_all_slots
        return asyncio.run(generate_all_slots(
            seed_components=SEED_COMPONENTS,
            n_per_slot=N_PER_SLOT,
        ))

    def test_all_text_slots_present(self, all_slots_result):
        assert "headline" in all_slots_result
        assert "primary_text" in all_slots_result
        assert "description" in all_slots_result

    def test_each_slot_has_correct_count(self, all_slots_result):
        for slot, variants in all_slots_result.items():
            assert len(variants) == N_PER_SLOT, \
                f"Slot {slot!r}: expected {N_PER_SLOT} variants, got {len(variants)}"

    def test_image_slot_not_generated(self, all_slots_result):
        assert "image" not in all_slots_result, \
            "image slot should not be generated by generate_all_slots"


# ---------------------------------------------------------------------------
# Tests — assemble_dynamic_ad (pure function, no API calls)
# ---------------------------------------------------------------------------

class TestAssembleDynamicAd:
    @pytest.fixture(scope="class")
    def assembled(self):
        from ad_text_generation.assembler import assemble_dynamic_ad
        return assemble_dynamic_ad(
            seed_components=SEED_COMPONENTS,
            generated_text={
                "headline":     ["New Headline A", "New Headline B", "New Headline C"],
                "primary_text": ["New body 1.", "New body 2.", "New body 3."],
                "description":  ["Free returns.", "Ships today.", "Guaranteed warm."],
            },
            image_urls=FAKE_IMAGE_URLS,
        )

    def test_seed_headlines_present(self, assembled):
        headlines = [c for c in assembled if c["slot"] == "headline"]
        seed_values = {c["value"] for c in headlines if c["source"] == "seed"}
        assert "Wool Socks" in seed_values
        assert "Warm Feet Forever" in seed_values

    def test_generated_headlines_present(self, assembled):
        generated = [c for c in assembled if c["slot"] == "headline" and c["source"] == "generated_text"]
        assert len(generated) == 3

    def test_no_slot_index_collisions(self, assembled):
        from collections import Counter
        keys = Counter((c["slot"], c["slot_index"]) for c in assembled)
        dups = [(k, v) for k, v in keys.items() if v > 1]
        assert not dups, f"Duplicate (slot, slot_index) pairs: {dups}"

    def test_generated_indices_continue_from_seed(self, assembled):
        headlines = sorted(
            [c for c in assembled if c["slot"] == "headline"],
            key=lambda c: c["slot_index"],
        )
        # First two are seed (indices 0, 1); generated start at 2
        seed = [c for c in headlines if c["source"] == "seed"]
        generated = [c for c in headlines if c["source"] == "generated_text"]
        assert max(c["slot_index"] for c in seed) < min(c["slot_index"] for c in generated)

    def test_image_urls_used(self, assembled):
        images = [c for c in assembled if c["slot"] == "image"]
        assert len(images) == len(FAKE_IMAGE_URLS)
        assert all(c["source"] == "generated_image" for c in images)

    def test_seed_images_preserved_when_no_urls(self):
        from ad_text_generation.assembler import assemble_dynamic_ad
        assembled = assemble_dynamic_ad(
            seed_components=SEED_COMPONENTS,
            generated_text={},
            image_urls=None,
        )
        images = [c for c in assembled if c["slot"] == "image"]
        assert len(images) == 1
        assert images[0]["source"] == "seed"
        assert images[0]["value"] == "https://i.ibb.co/GvNwWwMw/wool-socks4.png"

    def test_slot_counts_helper(self, assembled):
        from ad_text_generation.assembler import slot_counts
        counts = slot_counts(assembled)
        # 2 seed + 3 generated = 5 headlines
        assert counts["headline"] == 5
        assert counts["image"] == len(FAKE_IMAGE_URLS)


# ---------------------------------------------------------------------------
# Tests — full pipeline with DB storage
# ---------------------------------------------------------------------------

class TestRunTextPipeline:
    def test_returns_integer_id(self, pipeline_result):
        assert isinstance(pipeline_result, int)
        assert pipeline_result > 0

    def test_db_row_exists(self, pipeline_result, test_db):
        from ad_text_generation import get_generated_ad_meta
        meta = get_generated_ad_meta(pipeline_result, test_db)
        assert meta is not None
        assert meta["source_ad_id"] == SEED_AD_ID

    def test_slots_stored(self, pipeline_result, test_db):
        from ad_text_generation import get_generated_ad
        slots = get_generated_ad(pipeline_result, test_db)
        assert slots, "No slots stored for generated ad"

    def test_all_text_slots_in_db(self, pipeline_result, test_db):
        from ad_text_generation import get_generated_ad
        slots = get_generated_ad(pipeline_result, test_db)
        slot_names = {c["slot"] for c in slots}
        assert "headline" in slot_names
        assert "primary_text" in slot_names
        assert "description" in slot_names
        assert "image" in slot_names

    def test_seed_plus_generated_counts(self, pipeline_result, test_db):
        from ad_text_generation import get_generated_ad
        from ad_text_generation.assembler import slot_counts
        slots = get_generated_ad(pipeline_result, test_db)
        counts = slot_counts(slots)
        # 2 seed headlines + N_PER_SLOT generated
        assert counts["headline"] == 2 + N_PER_SLOT
        # 2 seed primary_text + N_PER_SLOT generated
        assert counts["primary_text"] == 2 + N_PER_SLOT
        # 1 seed description + N_PER_SLOT generated
        assert counts["description"] == 1 + N_PER_SLOT
        # image slots replaced by FAKE_IMAGE_URLS
        assert counts["image"] == len(FAKE_IMAGE_URLS)

    def test_source_labels_in_db(self, pipeline_result, test_db):
        from ad_text_generation import get_generated_ad
        slots = get_generated_ad(pipeline_result, test_db)
        sources = {c["source"] for c in slots}
        assert "seed" in sources
        assert "generated_text" in sources
        assert "generated_image" in sources

    def test_no_duplicate_slot_indices(self, pipeline_result, test_db):
        from collections import Counter
        from ad_text_generation import get_generated_ad
        slots = get_generated_ad(pipeline_result, test_db)
        keys = Counter((c["slot"], c["slot_index"]) for c in slots)
        dups = [(k, v) for k, v in keys.items() if v > 1]
        assert not dups, f"Duplicate (slot, slot_index) in DB: {dups}"

    def test_seed_images_preserved_pipeline(self, pipeline_result_seed_images, test_db):
        from ad_text_generation import get_generated_ad
        slots = get_generated_ad(pipeline_result_seed_images, test_db)
        images = [c for c in slots if c["slot"] == "image"]
        assert len(images) == 1
        assert images[0]["source"] == "seed"

    def test_no_empty_values_stored(self, pipeline_result, test_db):
        from ad_text_generation import get_generated_ad
        slots = get_generated_ad(pipeline_result, test_db)
        for c in slots:
            assert c["value"].strip(), f"Empty value found: {c}"

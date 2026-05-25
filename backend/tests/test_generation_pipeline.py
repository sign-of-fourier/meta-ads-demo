"""
Integration tests for the ad generation + embedding pipeline.

These tests make real API calls (Azure OpenAI, deAPI) — all required env vars
must be set. See ad_generation/README.md and embeddings/README.md for the
full list.

What is covered:
  1. Embed a fake seed ad               → ad_embeddings row created with correct metadata
  2. Run the full generation pipeline   → job reaches 'done', variants scored and QA'd
  3. Active pool excludes defunct       → get_active_variants filters correctly
  4. Embed a generated variant          → new ad_embeddings row links back to campaign
  5. Metadata chain integrity           → SQL verifies campaign → job → variants → embeddings

No FastAPI app or HTTP server is needed — the pipeline modules run directly.
A temporary SQLite DB is created per session; a lightweight HTTP server
serves generated images so the scoring and QA models can fetch them.
"""

from __future__ import annotations

import asyncio
import http.server
import os
import sqlite3
import threading
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Test identity constants
# Change these if you want to target a different test ad or campaign.
# ---------------------------------------------------------------------------

TEST_USER_ID    = 99001
TEST_CAMPAIGN_ID = "test_campaign_gen_001"
TEST_ADSET_ID    = "test_adset_gen_001"
TEST_AD_ID       = "test_ad_gen_001"

# A publicly accessible seed image used as the generation input.
# Swap this for any stable image URL you control.
TEST_SEED_IMAGE_URL = "https://i.ibb.co/GvNwWwMw/wool-socks4.png"
TEST_HEADLINE       = "Wool Socks"
TEST_SHORT_TEXT     = "Hand made socks from Switzerland. Very warm and durable."

# Fake ad components representing the seed ad (used for embedding test)
TEST_COMPONENTS = [
    {"slot": "headline",     "slot_index": 0, "value": TEST_HEADLINE},
    {"slot": "primary_text", "slot_index": 0, "value": TEST_SHORT_TEXT},
    {"slot": "image",        "slot_index": 0, "value": TEST_SEED_IMAGE_URL},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_test_db(db_path: Path) -> None:
    """Create the minimal schema needed by both pipelines."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY,
            email      TEXT UNIQUE NOT NULL,
            pw_hash    TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ad_creative_structures (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          INTEGER NOT NULL,
            ad_account_id    TEXT NOT NULL,
            campaign_id      TEXT NOT NULL,
            adset_id         TEXT NOT NULL,
            ad_id            TEXT NOT NULL,
            creative_type    TEXT NOT NULL,
            slot             TEXT NOT NULL,
            slot_index       INTEGER NOT NULL DEFAULT 0,
            value            TEXT,
            ingested_at      TEXT NOT NULL DEFAULT (datetime('now')),
            lifecycle_status TEXT NOT NULL DEFAULT 'active',
            data_source      TEXT NOT NULL DEFAULT 'real',
            mask_profile     TEXT,
            UNIQUE (user_id, ad_id, slot, slot_index)
        );
    """)
    conn.execute(
        "INSERT OR IGNORE INTO users (id, email, pw_hash) VALUES (?, ?, ?)",
        (TEST_USER_ID, "test_pipeline@example.com", "testhash"),
    )
    # Insert seed ad structure rows
    for comp in TEST_COMPONENTS:
        conn.execute(
            """INSERT OR REPLACE INTO ad_creative_structures
               (user_id, ad_account_id, campaign_id, adset_id, ad_id,
                creative_type, slot, slot_index, value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (TEST_USER_ID, "act_test", TEST_CAMPAIGN_ID, TEST_ADSET_ID, TEST_AD_ID,
             "static", comp["slot"], comp["slot_index"], comp["value"]),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_images_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("generated_images")
    os.environ["GENERATED_IMAGES_DIR"] = str(d)
    return d


@pytest.fixture(scope="session")
def image_server(test_images_dir) -> str:
    """
    Start a simple HTTP server on a random port serving test_images_dir.
    Returns the base URL (e.g. 'http://localhost:54321').
    """
    import functools
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler,
        directory=str(test_images_dir),
    )
    server = http.server.HTTPServer(("localhost", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://localhost:{port}"
    os.environ["IMAGES_SERVE_BASE_URL"] = base_url
    yield base_url
    server.shutdown()


@pytest.fixture(scope="session")
def test_db(tmp_path_factory, image_server) -> Path:
    """Create a temp DB and return its path."""
    db_path = tmp_path_factory.mktemp("db") / "test_pipeline.db"
    _init_test_db(db_path)
    return db_path


@pytest.fixture(scope="session")
def seed_embedding(test_db):
    """
    Fixture for test 1: embed the seed ad.
    Returns (user_id, ad_id, db_path).
    """
    from embeddings.pipeline import embed_ad

    ok = asyncio.run(embed_ad(
        user_id=TEST_USER_ID,
        ad_id=TEST_AD_ID,
        campaign_id=TEST_CAMPAIGN_ID,
        components=TEST_COMPONENTS,
        db_path=test_db,
    ))
    assert ok, "embed_ad returned False for seed ad — check AZURE_INFERENCE_KEY and endpoint"
    return TEST_USER_ID, TEST_AD_ID, test_db


@pytest.fixture(scope="session")
def completed_job(test_db, image_server):
    """
    Fixture for tests 2–4: run the full generation pipeline once.
    Returns job_id.
    """
    from ad_generation.pipeline import create_job, run_generation_job
    from ad_generation.storage import ensure_tables

    ensure_tables(test_db)
    job_id = create_job(
        user_id=TEST_USER_ID,
        campaign_id=TEST_CAMPAIGN_ID,
        adset_id=TEST_ADSET_ID,
        seed_ad_id=TEST_AD_ID,
        seed_image_url=TEST_SEED_IMAGE_URL,
        headline=TEST_HEADLINE,
        short_text=TEST_SHORT_TEXT,
        db_path=test_db,
    )
    asyncio.run(run_generation_job(job_id, db_path=test_db))
    return job_id


@pytest.fixture(scope="session")
def variant_embedding(test_db, completed_job, image_server):
    """
    Fixture for test 3: embed the best active variant as a new synthetic ad.
    Returns the synthetic ad_id used.
    """
    from ad_generation.pipeline import get_active_variants
    from embeddings.pipeline import embed_ad

    variants = get_active_variants(completed_job, test_db)
    scored = [v for v in variants if v.get("score") is not None and v.get("local_filename")]
    assert scored, "No scored variants with local images found — cannot embed"

    best = min(scored, key=lambda v: v["score"])
    image_url = f"{image_server}/{best['local_filename']}"
    synthetic_ad_id = f"gen_{completed_job}_{best['id']}"

    components = [
        {"slot": "headline",     "slot_index": 0, "value": TEST_HEADLINE},
        {"slot": "primary_text", "slot_index": 0, "value": TEST_SHORT_TEXT},
        {"slot": "image",        "slot_index": 0, "value": image_url},
    ]
    ok = asyncio.run(embed_ad(
        user_id=TEST_USER_ID,
        ad_id=synthetic_ad_id,
        campaign_id=TEST_CAMPAIGN_ID,
        components=components,
        db_path=test_db,
    ))
    assert ok, "embed_ad returned False for generated variant"
    return synthetic_ad_id


# ---------------------------------------------------------------------------
# Tests — Seed embedding (step 1)
# ---------------------------------------------------------------------------

class TestSeedEmbedding:
    def test_row_exists(self, seed_embedding):
        user_id, ad_id, db_path = seed_embedding
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT * FROM ad_embeddings WHERE user_id=? AND ad_id=?",
            (user_id, ad_id),
        ).fetchone()
        conn.close()
        assert row is not None, "No ad_embeddings row for seed ad"

    def test_combined_vector_present(self, seed_embedding):
        user_id, ad_id, db_path = seed_embedding
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT combined_vector FROM ad_embeddings WHERE user_id=? AND ad_id=?",
            (user_id, ad_id),
        ).fetchone()
        conn.close()
        assert row[0] is not None and len(row[0]) > 0

    def test_campaign_id_stored(self, seed_embedding):
        user_id, ad_id, db_path = seed_embedding
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT campaign_id FROM ad_embeddings WHERE user_id=? AND ad_id=?",
            (user_id, ad_id),
        ).fetchone()
        conn.close()
        assert row[0] == TEST_CAMPAIGN_ID

    def test_image_url_stored(self, seed_embedding):
        user_id, ad_id, db_path = seed_embedding
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT image_url FROM ad_embeddings WHERE user_id=? AND ad_id=?",
            (user_id, ad_id),
        ).fetchone()
        conn.close()
        assert row[0] == TEST_SEED_IMAGE_URL


# ---------------------------------------------------------------------------
# Tests — Generation pipeline (steps 2–7)
# ---------------------------------------------------------------------------

class TestGenerationPipeline:
    def test_job_completes(self, completed_job, test_db):
        from ad_generation.pipeline import get_job_status
        status = get_job_status(completed_job, test_db)
        assert status is not None
        assert status["status"] == "done", f"Job ended with status: {status['status']} — error: {status.get('error')}"

    def test_suggestions_generated(self, completed_job, test_db):
        from ad_generation.pipeline import get_job_status
        status = get_job_status(completed_job, test_db)
        assert isinstance(status["suggestions"], list)
        assert len(status["suggestions"]) == 10

    def test_variants_created(self, completed_job, test_db):
        from ad_generation.pipeline import get_job_variants
        variants = get_job_variants(completed_job, test_db)
        assert len(variants) >= 10, f"Expected ≥10 variants, got {len(variants)}"

    def test_active_variants_excludes_defunct(self, completed_job, test_db):
        from ad_generation.pipeline import get_active_variants, get_job_variants
        all_v = get_job_variants(completed_job, test_db)
        active = get_active_variants(completed_job, test_db)
        defunct = [v for v in all_v if v["status"] == "defunct"]
        assert all(v["status"] != "defunct" for v in active), "get_active_variants returned a defunct variant"
        # If any were flagged, corrections must be present as active replacements
        if defunct:
            assert len(active) >= len(defunct), "Fewer active variants than defunct — corrections may have failed"

    def test_active_variants_are_scored(self, completed_job, test_db):
        from ad_generation.pipeline import get_active_variants
        active = get_active_variants(completed_job, test_db)
        unscored = [v for v in active if v.get("score") is None and v["status"] not in ("failed", "submitted")]
        assert not unscored, f"{len(unscored)} active variant(s) have no score"

    def test_active_variants_have_local_files(self, completed_job, test_db):
        from ad_generation.pipeline import get_active_variants
        active = [v for v in get_active_variants(completed_job, test_db) if v["status"] == "scored"]
        for v in active:
            assert v.get("local_filename"), f"Variant {v['id']} has no local_filename"
            img_path = Path(os.environ["GENERATED_IMAGES_DIR"]) / v["local_filename"]
            assert img_path.exists(), f"File missing on disk: {img_path}"

    def test_qa_status_set_on_active_variants(self, completed_job, test_db):
        from ad_generation.pipeline import get_active_variants
        active = [v for v in get_active_variants(completed_job, test_db) if v["status"] == "scored"]
        for v in active:
            assert v.get("qa_status") in ("passed", "skipped"), \
                f"Active variant {v['id']} has unexpected qa_status: {v.get('qa_status')}"

    def test_defunct_variants_have_corrections(self, completed_job, test_db):
        from ad_generation.pipeline import get_job_variants
        defunct = [v for v in get_job_variants(completed_job, test_db) if v["status"] == "defunct"]
        for v in defunct:
            assert v.get("qa_status") == "flagged", \
                f"Defunct variant {v['id']} expected qa_status=flagged, got {v.get('qa_status')}"
            assert v.get("qa_corrections"), \
                f"Defunct variant {v['id']} has no qa_corrections"

    def test_correction_variants_link_to_parent(self, completed_job, test_db):
        from ad_generation.pipeline import get_job_variants
        all_v = get_job_variants(completed_job, test_db)
        corrections = [v for v in all_v if v.get("parent_variant_id") is not None]
        defunct_ids = {v["id"] for v in all_v if v["status"] == "defunct"}
        for v in corrections:
            assert v["parent_variant_id"] in defunct_ids, \
                f"Correction variant {v['id']} points to non-defunct parent {v['parent_variant_id']}"

    def test_scores_are_valid_range(self, completed_job, test_db):
        from ad_generation.pipeline import get_active_variants
        for v in get_active_variants(completed_job, test_db):
            if v.get("score") is not None:
                assert 0.0 <= v["score"] <= 1.0, f"Score out of range: {v['score']}"

    def test_severity_values_valid(self, completed_job, test_db):
        from ad_generation.pipeline import get_active_variants
        valid = {"low", "medium", "high"}
        for v in get_active_variants(completed_job, test_db):
            if v.get("severity"):
                assert v["severity"] in valid, f"Unexpected severity: {v['severity']}"


# ---------------------------------------------------------------------------
# Tests — Embedding a generated variant (step 3 of the user story)
# ---------------------------------------------------------------------------

class TestGeneratedVariantEmbedding:
    def test_embedding_row_exists(self, variant_embedding, test_db):
        conn = sqlite3.connect(str(test_db))
        row = conn.execute(
            "SELECT id FROM ad_embeddings WHERE user_id=? AND ad_id=?",
            (TEST_USER_ID, variant_embedding),
        ).fetchone()
        conn.close()
        assert row is not None, f"No ad_embeddings row for synthetic ad_id {variant_embedding}"

    def test_campaign_id_matches(self, variant_embedding, test_db):
        conn = sqlite3.connect(str(test_db))
        row = conn.execute(
            "SELECT campaign_id FROM ad_embeddings WHERE user_id=? AND ad_id=?",
            (TEST_USER_ID, variant_embedding),
        ).fetchone()
        conn.close()
        assert row[0] == TEST_CAMPAIGN_ID

    def test_combined_vector_non_empty(self, variant_embedding, test_db):
        import numpy as np
        conn = sqlite3.connect(str(test_db))
        row = conn.execute(
            "SELECT combined_vector FROM ad_embeddings WHERE user_id=? AND ad_id=?",
            (TEST_USER_ID, variant_embedding),
        ).fetchone()
        conn.close()
        vec = np.frombuffer(row[0], dtype=np.float32)
        assert vec.shape[0] > 0
        assert not (vec == 0).all(), "Combined vector is all zeros"

    def test_image_url_is_served_url(self, variant_embedding, test_db, image_server):
        conn = sqlite3.connect(str(test_db))
        row = conn.execute(
            "SELECT image_url FROM ad_embeddings WHERE user_id=? AND ad_id=?",
            (TEST_USER_ID, variant_embedding),
        ).fetchone()
        conn.close()
        assert row[0].startswith(image_server), \
            f"Expected image_url to be served from test server, got: {row[0]}"


# ---------------------------------------------------------------------------
# Tests — Metadata chain integrity
# ---------------------------------------------------------------------------

class TestMetadataChain:
    def test_job_links_to_campaign_and_adset(self, completed_job, test_db):
        conn = sqlite3.connect(str(test_db))
        row = conn.execute(
            "SELECT campaign_id, adset_id, seed_ad_id FROM ad_generation_jobs WHERE id=?",
            (completed_job,),
        ).fetchone()
        conn.close()
        assert row[0] == TEST_CAMPAIGN_ID
        assert row[1] == TEST_ADSET_ID
        assert row[2] == TEST_AD_ID

    def test_variants_link_to_job(self, completed_job, test_db):
        conn = sqlite3.connect(str(test_db))
        count = conn.execute(
            "SELECT COUNT(*) FROM ad_generation_variants WHERE job_id=?",
            (completed_job,),
        ).fetchone()[0]
        conn.close()
        assert count >= 10

    def test_full_chain_query(self, completed_job, test_db):
        """One query traverses campaign → job → variants → scored results."""
        conn = sqlite3.connect(str(test_db))
        rows = conn.execute(
            """
            SELECT j.campaign_id, j.adset_id, j.seed_ad_id,
                   v.suggestion, v.local_filename, v.score
            FROM ad_generation_jobs j
            JOIN ad_generation_variants v ON v.job_id = j.id
            WHERE j.user_id = ?
              AND j.campaign_id = ?
              AND v.status != 'defunct'
              AND v.status = 'scored'
            ORDER BY v.score ASC
            """,
            (TEST_USER_ID, TEST_CAMPAIGN_ID),
        ).fetchall()
        conn.close()
        assert rows, "Full chain query returned no rows"
        assert all(r[0] == TEST_CAMPAIGN_ID for r in rows)
        assert all(r[5] is not None for r in rows), "Some rows missing score in chain query"

    def test_seed_and_variant_embeddings_share_campaign(self, seed_embedding, variant_embedding, test_db):
        """Both the seed ad and the generated variant embed under the same campaign."""
        _, seed_ad_id, db_path = seed_embedding
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT ad_id, campaign_id FROM ad_embeddings WHERE user_id=? AND campaign_id=?",
            (TEST_USER_ID, TEST_CAMPAIGN_ID),
        ).fetchall()
        conn.close()
        ad_ids = {r[0] for r in rows}
        assert seed_ad_id in ad_ids, "Seed ad missing from ad_embeddings"
        assert variant_embedding in ad_ids, "Generated variant missing from ad_embeddings"

"""
Step 3: orchestrate extract → embed → store for a single ad.

Can be called from main.py (fire-and-forget via asyncio.create_task) or run
standalone to (re-)embed ads already in ad_creative_structures:

    # embed all ads not yet embedded
    python -m embeddings.pipeline

    # embed a specific ad
    python -m embeddings.pipeline --ad-id <ad_id>
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path

import numpy as np

from embeddings.embedder import concat_embeddings, embed_image_url, embed_text
from embeddings.extractor import extract_fields, text_as_json

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "app.db"

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ad_embeddings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    ad_id           TEXT NOT NULL,
    campaign_id     TEXT NOT NULL,
    text_model      TEXT,
    image_model     TEXT,
    text_vector     BLOB,
    image_vector    BLOB,
    combined_vector BLOB NOT NULL,
    text_snapshot   TEXT,
    image_url       TEXT,
    embedded_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, ad_id)
)
"""

_UPSERT = """
INSERT INTO ad_embeddings
    (user_id, ad_id, campaign_id,
     text_model, image_model,
     text_vector, image_vector, combined_vector,
     text_snapshot, image_url)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (user_id, ad_id) DO UPDATE SET
    campaign_id     = excluded.campaign_id,
    text_model      = excluded.text_model,
    image_model     = excluded.image_model,
    text_vector     = excluded.text_vector,
    image_vector    = excluded.image_vector,
    combined_vector = excluded.combined_vector,
    text_snapshot   = excluded.text_snapshot,
    image_url       = excluded.image_url,
    embedded_at     = excluded.embedded_at
"""


def _save(
    db_path: Path,
    user_id: int,
    ad_id: str,
    campaign_id: str,
    text_vec: np.ndarray | None,
    image_vec: np.ndarray | None,
    combined: np.ndarray,
    text_snapshot: str,
    image_url: str | None,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(_CREATE_TABLE)
    conn.execute(_UPSERT, (
        user_id, ad_id, campaign_id,
        os.getenv("AZURE_TEXT_MODEL", "embed-v-4-0") if text_vec is not None else None,
        os.getenv("AZURE_IMAGE_MODEL", "embed-v-4-0") if image_vec is not None else None,
        text_vec.tobytes() if text_vec is not None else None,
        image_vec.tobytes() if image_vec is not None else None,
        combined.tobytes(),
        text_snapshot,
        image_url,
    ))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Main entry point (called from ingest hook or standalone)
# ---------------------------------------------------------------------------

async def embed_ad(
    user_id: int,
    ad_id: str,
    campaign_id: str,
    components: list[dict],
    db_path: Path = DB_PATH,
) -> bool:
    """
    Extract → embed → store for one ad.

    Always returns True/False; never raises. Safe to fire-and-forget.
    """
    try:
        fields = extract_fields(components)
        text_str = text_as_json(fields)
        image_url = fields.get("image_url")

        text_vec, image_vec = await asyncio.gather(
            embed_text(text_str),
            embed_image_url(image_url or ""),
        )

        combined = concat_embeddings(text_vec, image_vec)
        if combined is None:
            logger.info("embed_ad: no vectors produced for ad %s — skipping save", ad_id)
            return False

        _save(db_path, user_id, ad_id, campaign_id, text_vec, image_vec, combined, text_str, image_url)
        logger.info(
            "embed_ad: saved ad=%s text=%s image=%s combined_dim=%d",
            ad_id, text_vec is not None, image_vec is not None, combined.shape[0],
        )
        return True

    except Exception:
        logger.warning("embed_ad: failed for ad %s", ad_id, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def _fetch_components(conn: sqlite3.Connection, user_id: int, ad_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT slot, slot_index, value FROM ad_creative_structures WHERE user_id = ? AND ad_id = ?",
        (user_id, ad_id),
    ).fetchall()
    return [{"slot": r[0], "slot_index": r[1], "value": r[2]} for r in rows]


async def _run_standalone(ad_id_filter: str | None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    conn = sqlite3.connect(str(DB_PATH))

    # Ensure the table exists before querying it
    conn.execute(_CREATE_TABLE)
    conn.commit()

    if ad_id_filter:
        rows = conn.execute(
            "SELECT DISTINCT user_id, ad_id, campaign_id FROM ad_creative_structures WHERE ad_id = ?",
            (ad_id_filter,),
        ).fetchall()
    else:
        rows = conn.execute("""
            SELECT DISTINCT s.user_id, s.ad_id, s.campaign_id
            FROM ad_creative_structures s
            LEFT JOIN ad_embeddings e ON e.user_id = s.user_id AND e.ad_id = s.ad_id
            WHERE e.ad_id IS NULL
        """).fetchall()

    print(f"Embedding {len(rows)} ad(s)...")

    for user_id, ad_id, campaign_id in rows:
        components = _fetch_components(conn, user_id, ad_id)
        ok = await embed_ad(user_id, ad_id, campaign_id, components)
        print(f"  {'OK' if ok else 'FAIL'} — ad_id={ad_id}")

    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Embed ads from ad_creative_structures into ad_embeddings")
    parser.add_argument("--ad-id", default=None, help="Embed a specific ad_id (default: all un-embedded)")
    args = parser.parse_args()

    asyncio.run(_run_standalone(args.ad_id))

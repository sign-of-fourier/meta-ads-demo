"""
Seed synthetic scored observations for BO testing.

Reads real text combination embeddings from ad_text_combination_embeddings,
picks N of them as "scored" variants, and inserts the necessary rows into
ad_generation_jobs, ad_generation_variants, and ad_embeddings.

Usage:
    python seed_bo.py --ad-id <ad_id> --user-id <user_id> [--n 5] [--campaign-id <id>]

Find your ad_id and user_id:
    sqlite3 app.db "SELECT DISTINCT ad_id FROM ad_creative_structures;"
    sqlite3 app.db "SELECT id, email FROM users;"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np

DB_PATH = Path(__file__).parent / "app.db"
EMBED_DIM = 1536  # synthetic image vector dimension (matches OpenAI text-embedding-3-small)


def seed(ad_id: str, user_id: int, campaign_id: str, n: int, db_path: Path) -> None:
    rng = np.random.default_rng(seed=42)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Ensure ad_generation tables exist
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ad_generation_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        campaign_id TEXT NOT NULL,
        adset_id TEXT NOT NULL,
        seed_ad_id TEXT,
        seed_image_url TEXT NOT NULL,
        headline TEXT NOT NULL,
        short_text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'done',
        suggestions TEXT,
        error TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ad_generation_variants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        suggestion TEXT NOT NULL,
        deapi_request_id TEXT,
        status TEXT NOT NULL DEFAULT 'scored',
        result_url TEXT,
        local_filename TEXT,
        score REAL,
        severity TEXT,
        score_labels TEXT,
        qa_status TEXT,
        qa_corrections TEXT,
        parent_variant_id INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ad_embeddings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        ad_id TEXT NOT NULL,
        campaign_id TEXT NOT NULL,
        text_model TEXT, image_model TEXT,
        text_vector BLOB, image_vector BLOB, combined_vector BLOB NOT NULL,
        text_snapshot TEXT, image_url TEXT,
        embedded_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (user_id, ad_id)
    );
    """)
    conn.commit()

    # Load N text combinations for this ad
    rows = conn.execute(
        """SELECT combination_key, vector FROM ad_text_combination_embeddings
           WHERE source_id = ? ORDER BY RANDOM() LIMIT ?""",
        (ad_id, n),
    ).fetchall()

    if not rows:
        print(f"No text combination embeddings found for ad_id={ad_id}. Run structure ingest first.")
        conn.close()
        return

    print(f"Seeding {len(rows)} scored observations for ad_id={ad_id}, user_id={user_id}")

    for i, row in enumerate(rows):
        combo = json.loads(row["combination_key"])
        headline = combo.get("headline", "")
        short_text = combo.get("primary_text", "")
        score = round(rng.uniform(2.0, 7.0), 2)  # synthetic score 2–7

        # Insert job
        cur = conn.execute(
            """INSERT INTO ad_generation_jobs
               (user_id, campaign_id, adset_id, seed_ad_id, seed_image_url, headline, short_text, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'done')""",
            (user_id, campaign_id, "synthetic_adset", ad_id, "https://synthetic/image.jpg", headline, short_text),
        )
        job_id = cur.lastrowid

        # Insert variant with score
        cur2 = conn.execute(
            """INSERT INTO ad_generation_variants
               (job_id, suggestion, status, score)
               VALUES (?, ?, 'scored', ?)""",
            (job_id, json.dumps(combo), score),
        )
        variant_id = cur2.lastrowid

        # Insert synthetic embedding with ad_id = gen_{job_id}_{variant_id}
        emb_ad_id = f"gen_{job_id}_{variant_id}"
        text_vec = np.frombuffer(row["vector"], dtype=np.float32)
        image_vec = rng.random(EMBED_DIM).astype(np.float32)  # synthetic image embedding
        combined = np.concatenate([text_vec[:128], image_vec[:128]])

        conn.execute(
            """INSERT OR REPLACE INTO ad_embeddings
               (user_id, ad_id, campaign_id, text_vector, image_vector, combined_vector)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, emb_ad_id, campaign_id,
             text_vec.tobytes(), image_vec.tobytes(), combined.tobytes()),
        )

        print(f"  [{i+1}] job={job_id} variant={variant_id} score={score:.2f} headline='{headline[:40]}'")

    conn.commit()
    conn.close()
    print("Done. Run BO via POST /api/bo/run or python -m bo_pipeline.pipeline")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ad-id", required=True, help="The ad_id to seed (from ad_creative_structures)")
    parser.add_argument("--user-id", required=True, type=int, help="Your user id (from users table)")
    parser.add_argument("--campaign-id", default="", help="Campaign id (optional, for labelling)")
    parser.add_argument("--n", type=int, default=5, help="Number of scored observations to seed (default 5)")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to app.db")
    args = parser.parse_args()

    seed(args.ad_id, args.user_id, args.campaign_id, args.n, Path(args.db))

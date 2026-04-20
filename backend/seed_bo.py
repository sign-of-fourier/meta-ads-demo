"""
Seed scored observations for BO by running real combinations through the
fine-tuned scoring model (AZURE_SCORING_DEPLOYMENT).

Reads text combination embeddings from ad_text_combination_embeddings, scores
each combination against the seed ad's first image URL using the fine-tune
model, and inserts the necessary rows into ad_generation_jobs,
ad_generation_variants, and ad_embeddings.

Usage:
    python seed_bo.py --ad-id <ad_id> --user-id <user_id> [--n 5] [--campaign-id <id>]

Find your ad_id and user_id:
    sqlite3 app.db "SELECT DISTINCT ad_id FROM ad_creative_structures;"
    sqlite3 app.db "SELECT id, email FROM users;"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

import numpy as np

from ad_generation.scorer import score_variant

DB_PATH = Path(__file__).parent / "app.db"


def _get_image_url(conn: sqlite3.Connection, ad_id: str) -> str | None:
    row = conn.execute(
        """SELECT value FROM ad_creative_structures
           WHERE ad_id = ? AND slot = 'image' AND value LIKE 'http%'
           ORDER BY slot_index LIMIT 1""",
        (ad_id,),
    ).fetchone()
    return row["value"] if row else None


def seed(ad_id: str, user_id: int, campaign_id: str, n: int, db_path: Path) -> None:
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

    image_url = _get_image_url(conn, ad_id)
    if not image_url:
        print(f"No image URL found for ad_id={ad_id}. Run structure ingest first.")
        conn.close()
        return

    print(f"Using image URL: {image_url}")

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

    async def _seed_all() -> None:
        for i, row in enumerate(rows):
            combo = json.loads(row["combination_key"])
            headline = combo.get("headline", "")
            short_text = combo.get("primary_text", "")

            result = await score_variant(image_url, headline, short_text)
            # scorer returns 0–1 defect score (higher = worse); invert to a 0–10 quality score
            score = round((1.0 - result.score) * 10.0, 2)

            cur = conn.execute(
                """INSERT INTO ad_generation_jobs
                   (user_id, campaign_id, adset_id, seed_ad_id, seed_image_url, headline, short_text, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'done')""",
                (user_id, campaign_id, "synthetic_adset", ad_id, image_url, headline, short_text),
            )
            job_id = cur.lastrowid

            cur2 = conn.execute(
                """INSERT INTO ad_generation_variants
                   (job_id, suggestion, status, score, severity, score_labels)
                   VALUES (?, ?, 'scored', ?, ?, ?)""",
                (job_id, json.dumps(combo), score, result.severity, json.dumps(result.labels)),
            )
            variant_id = cur2.lastrowid

            emb_ad_id = f"gen_{job_id}_{variant_id}"
            text_vec = np.frombuffer(row["vector"], dtype=np.float32)
            # Use zero image vector — real image embedding not available at seed time
            image_vec = np.zeros(1536, dtype=np.float32)
            combined = np.concatenate([text_vec[:128], image_vec[:128]])

            conn.execute(
                """INSERT OR REPLACE INTO ad_embeddings
                   (user_id, ad_id, campaign_id, text_vector, image_vector, combined_vector, image_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, emb_ad_id, campaign_id,
                 text_vec.tobytes(), image_vec.tobytes(), combined.tobytes(), image_url),
            )

            print(f"  [{i+1}] job={job_id} variant={variant_id} score={score:.2f} "
                  f"severity={result.severity} headline='{headline[:40]}'")

        conn.commit()

    asyncio.run(_seed_all())
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

"""
Seed synthetic scored observations for BO testing — no API keys required.

Scores are random numbers in [2.0, 9.0].  Records are scoped to the given
user_id so they never mix with another user's data (selector now filters
ad_generation_jobs by user_id).

Three modes
-----------
  meta    — seeds Meta-style observations: image + text embeddings.
  google  — seeds Google-style observations: text-only (image half zero-padded).
  cross   — seeds both at once under the same user, enabling the cross-platform
             BO endpoint.

Usage
-----
# Meta alone:
python seed_bo_synthetic.py --platform meta \
    --ad-id <meta_ad_id> --user-id <uid> [--n 5]

# Google alone:
python seed_bo_synthetic.py --platform google \
    --ad-id <google_ad_id> --user-id <uid> [--n 5]

# Cross-platform (both at once):
python seed_bo_synthetic.py --platform cross \
    --meta-ad-id <meta_ad_id> --google-ad-id <google_ad_id> \
    --user-id <uid> [--n 5]

Find your IDs:
    sqlite3 app.db "SELECT id, email FROM users;"
    sqlite3 app.db "SELECT DISTINCT ad_id, platform FROM ad_creative_structures;"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np

DB_PATH = Path(__file__).parent / "app.db"

# Matches the real embedding dimensions used throughout the system
TEXT_EMBED_DIM = 1536
IMAGE_EMBED_DIM = 1536


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _variant_embedding_ad_id(job_id: int, variant_id: int) -> str:
    return f"gen_{job_id}_{variant_id}"


def _ensure_seed_embedding(
    conn: sqlite3.Connection,
    user_id: int,
    ad_id: str,
    campaign_id: str,
    include_image: bool,
    rng: np.random.Generator,
) -> None:
    """
    Ensure ad_embeddings has a row for this user + seed ad.
    If a row already exists (from the real ingest pipeline), leave it.
    If not (e.g. different user_id), create a synthetic one.
    """
    existing = conn.execute(
        "SELECT id FROM ad_embeddings WHERE ad_id = ? AND user_id = ?",
        (ad_id, user_id),
    ).fetchone()
    if existing:
        return

    txt_vec = rng.random(TEXT_EMBED_DIM).astype(np.float32)
    img_vec = rng.random(IMAGE_EMBED_DIM).astype(np.float32) if include_image else None

    from ad_embedding_combiner import combine
    combined = combine(txt_vec, img_vec)

    conn.execute(
        """INSERT OR IGNORE INTO ad_embeddings
           (user_id, ad_id, campaign_id, text_vector, image_vector, combined_vector)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            user_id, ad_id, campaign_id,
            txt_vec.tobytes(),
            img_vec.tobytes() if img_vec is not None else None,
            combined.tobytes(),
        ),
    )


def seed_platform(
    conn: sqlite3.Connection,
    user_id: int,
    ad_id: str,
    campaign_id: str,
    platform: str,          # "meta" | "google"
    n: int,
    rng: np.random.Generator,
) -> bool:
    """
    Seed n scored observations for one platform/ad.
    Returns True on success, False if prerequisites are missing.
    """
    include_image = platform != "google"

    # ── 1. Verify text combination embeddings exist ───────────────────────────
    rows = conn.execute(
        """SELECT combination_key, vector
           FROM ad_text_combination_embeddings
           WHERE source_id = ?
           ORDER BY RANDOM()
           LIMIT ?""",
        (ad_id, n),
    ).fetchall()

    if not rows:
        print(
            f"  [{platform}] No text combination embeddings found for ad_id={ad_id}.\n"
            f"  Run structure ingest (Ingest button) first, then wait for the\n"
            f"  embedding pipeline to finish (a few seconds after ingest)."
        )
        return False

    # ── 2. Ensure seed ad has an ad_embeddings row for this user ─────────────
    _ensure_seed_embedding(conn, user_id, ad_id, campaign_id, include_image, rng)

    # ── 3. Build image vector pool for Meta ───────────────────────────────────
    # Prefer per-image embeddings (ad_image_embeddings) so each scored variant
    # gets a distinct image vector — this gives the GPR real variance in the
    # image dimension and prevents all picks collapsing to the same image.
    # Falls back to the single seed image_vector, then to random.
    image_pool: list[np.ndarray] = []
    if include_image:
        per_image_rows = conn.execute(
            """SELECT vector FROM ad_image_embeddings
               WHERE user_id = ? AND ad_id = ?
               ORDER BY slot_index""",
            (user_id, ad_id),
        ).fetchall()
        image_pool = [
            np.frombuffer(r["vector"], dtype=np.float32)
            for r in per_image_rows
            if r["vector"] is not None
        ]
        if not image_pool:
            # Fall back to single seed image_vector
            seed_row = conn.execute(
                "SELECT image_vector FROM ad_embeddings WHERE ad_id = ? AND user_id = ?",
                (ad_id, user_id),
            ).fetchone()
            if seed_row and seed_row["image_vector"]:
                image_pool = [np.frombuffer(seed_row["image_vector"], dtype=np.float32)]

    print(
        f"  [{platform}] Seeding {len(rows)} scored observation(s) "
        f"for ad_id={ad_id}, user_id={user_id} "
        f"({'%d image vectors' % len(image_pool) if image_pool else 'no image vectors — zero-padded'})"
    )

    from ad_embedding_combiner import combine

    for i, row in enumerate(rows):
        combo = json.loads(row["combination_key"])
        headline = combo.get("headline", f"Synthetic headline {i + 1}")
        # Meta uses primary_text; Google uses description — store whichever is present
        short_text = (
            combo.get("primary_text")
            or combo.get("description")
            or f"Synthetic copy {i + 1}"
        )

        # ── 3a. Create a generation job ───────────────────────────────────────
        cur = conn.execute(
            """INSERT INTO ad_generation_jobs
               (user_id, campaign_id, adset_id, seed_ad_id,
                seed_image_url, headline, short_text, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'done')""",
            (
                user_id, campaign_id, "synthetic_adset", ad_id,
                f"http://synthetic.test/{ad_id}.png",
                headline, short_text,
            ),
        )
        job_id = cur.lastrowid

        # ── 3b. Scored variant with a random quality score ────────────────────
        score = round(float(rng.uniform(2.0, 9.0)), 2)
        cur2 = conn.execute(
            """INSERT INTO ad_generation_variants
               (job_id, suggestion, status, score)
               VALUES (?, ?, 'scored', ?)""",
            (job_id, json.dumps(combo), score),
        )
        variant_id = cur2.lastrowid

        # ── 3c. Embedding for this variant ────────────────────────────────────
        emb_ad_id = _variant_embedding_ad_id(job_id, variant_id)
        text_vec = np.frombuffer(row["vector"], dtype=np.float32)

        # Cycle through the image pool so each variant gets a distinct image vec.
        # Google: None (zero-padded by combiner).
        if not include_image:
            image_vec = None
        elif image_pool:
            image_vec = image_pool[i % len(image_pool)]
        else:
            image_vec = rng.random(IMAGE_EMBED_DIM).astype(np.float32)

        combined = combine(text_vec, image_vec)

        conn.execute(
            """INSERT OR REPLACE INTO ad_embeddings
               (user_id, ad_id, campaign_id, text_vector, image_vector, combined_vector)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                user_id, emb_ad_id, campaign_id,
                text_vec.tobytes(),
                image_vec.tobytes() if image_vec is not None else None,
                combined.tobytes(),
            ),
        )

        print(f"    [{i + 1}/{len(rows)}] score={score:.2f}  headline='{headline[:50]}'")

    conn.commit()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed synthetic BO observations (no API keys needed)."
    )
    parser.add_argument(
        "--platform",
        choices=["meta", "google", "cross"],
        required=True,
        help="Which platform(s) to seed.",
    )
    parser.add_argument(
        "--ad-id",
        help="Ad ID for meta or google mode (from ad_creative_structures.ad_id).",
    )
    parser.add_argument(
        "--meta-ad-id",
        help="Meta ad ID for cross mode.",
    )
    parser.add_argument(
        "--google-ad-id",
        help="Google ad ID for cross mode.",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        required=True,
        help="Your user ID (SELECT id FROM users).",
    )
    parser.add_argument(
        "--campaign-id",
        default="synthetic_campaign",
        help="Campaign ID label (optional).",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=5,
        help="Scored observations to seed per platform (default 5; need ≥ 2 for EI).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="RNG seed for reproducible scores (default 42).",
    )
    parser.add_argument(
        "--db",
        default=str(DB_PATH),
        help="Path to app.db.",
    )
    args = parser.parse_args()

    # ── Validate args ─────────────────────────────────────────────────────────
    if args.platform in ("meta", "google") and not args.ad_id:
        parser.error("--ad-id is required for --platform meta or google")
    if args.platform == "cross" and not (args.meta_ad_id and args.google_ad_id):
        parser.error("--meta-ad-id and --google-ad-id are required for --platform cross")

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return

    rng = np.random.default_rng(args.seed)
    conn = _conn(db_path)

    print(f"\nSeeding synthetic BO observations")
    print(f"  platform   : {args.platform}")
    print(f"  user_id    : {args.user_id}")
    print(f"  n per group: {args.n}")
    print(f"  db         : {db_path}\n")

    ok = True
    if args.platform == "meta":
        ok = seed_platform(
            conn, args.user_id, args.ad_id, args.campaign_id,
            "meta", args.n, rng,
        )
    elif args.platform == "google":
        ok = seed_platform(
            conn, args.user_id, args.ad_id, args.campaign_id,
            "google", args.n, rng,
        )
    elif args.platform == "cross":
        ok1 = seed_platform(
            conn, args.user_id, args.meta_ad_id, args.campaign_id,
            "meta", args.n, rng,
        )
        ok2 = seed_platform(
            conn, args.user_id, args.google_ad_id, args.campaign_id,
            "google", args.n, rng,
        )
        ok = ok1 and ok2

    conn.close()

    if ok:
        print("\nDone. Now run Get Recommendations (or Cross-Platform Analysis) in the UI.")
        if args.platform in ("meta", "google"):
            endpoint = "/api/bo/run" if args.platform == "meta" else "/api/google/bo/run"
            print(f"  Or: POST {endpoint}")
            print(f"      {{ \"seed_ad_id\": \"{args.ad_id}\", \"text_source_id\": \"{args.ad_id}\" }}")
        else:
            print("  Or: POST /api/bo/cross-platform")
            print(f"      {{ \"pairs\": [")
            print(f"          {{ \"platform\": \"meta\",   \"seed_ad_id\": \"{args.meta_ad_id}\", \"text_source_id\": \"{args.meta_ad_id}\" }},")
            print(f"          {{ \"platform\": \"google\", \"seed_ad_id\": \"{args.google_ad_id}\", \"text_source_id\": \"{args.google_ad_id}\" }}")
            print(f"      ] }}")


if __name__ == "__main__":
    main()

"""
DB access layer for the BO pipeline.

This is the only file in bo_pipeline that knows which tables exist and how to
join them.  All lower layers (gpr.py, pipeline.py) receive plain Python
objects and numpy arrays.

Scored combinations
-------------------
Source: ad_generation_variants WHERE score IS NOT NULL AND status != 'defunct'
        joined to ad_generation_jobs (for seed_ad_id, headline, short_text).
image_vector: ad_embeddings WHERE ad_id = variant_embedding_ad_id(job_id, variant_id)
text_vector:  ad_text_combination_embeddings WHERE source_id = text_source_id
              AND combination_key matches {"headline": job.headline, "primary_text": job.short_text}.
              Falls back to ad_embeddings.text_vector for the seed ad if the
              combination is not found in ad_text_combination_embeddings.

Candidate combinations
----------------------
All rows in ad_text_combination_embeddings for text_source_id, each paired with
the seed ad's image_vector (from ad_embeddings WHERE ad_id = seed_ad_id).
Combinations already scored are excluded when exclude_keys is provided.

Per-ad constraint
-----------------
Scored variants are filtered to jobs where seed_ad_id = seed_ad_id.
Candidate text combinations are filtered to text_source_id.
Both parameters must refer to the same ad — enforcement is the caller's
responsibility.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

DB_PATH = Path(__file__).parent.parent / "app.db"


# ---------------------------------------------------------------------------
# Naming convention for generated-variant embeddings
# ---------------------------------------------------------------------------

def variant_embedding_ad_id(job_id: int, variant_id: int) -> str:
    """
    The ad_id used when storing a generated variant's embedding in ad_embeddings.
    Whoever embeds generated variants must use this same convention.
    """
    return f"gen_{job_id}_{variant_id}"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _vec(blob: bytes | None) -> np.ndarray | None:
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32)


# ---------------------------------------------------------------------------
# Scored combinations
# ---------------------------------------------------------------------------

def get_scored_combinations(
    seed_ad_id: str,
    text_source_id: str,
    user_id: int,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Return scored variants for seed_ad_id with their text + image vectors.

    Each dict:
      variant_id       — int
      job_id           — int
      combination_key  — str  (JSON key used in ad_text_combination_embeddings)
      combination      — dict (parsed combination_key)
      score            — float
      text_vector      — np.ndarray float32
      image_vector     — np.ndarray float32

    Variants with no image embedding in ad_embeddings are silently skipped.
    """
    c = _conn(db_path)

    # All scored, non-defunct variants for this seed ad, scoped to this user
    variants = c.execute(
        """
        SELECT v.id, v.job_id, v.score, v.suggestion
        FROM ad_generation_variants v
        JOIN ad_generation_jobs j ON v.job_id = j.id
        WHERE j.seed_ad_id = ?
          AND j.user_id = ?
          AND v.score IS NOT NULL
          AND COALESCE(v.status, '') != 'defunct'
        ORDER BY v.id
        """,
        (seed_ad_id, user_id),
    ).fetchall()

    if not variants:
        c.close()
        return []

    # Seed ad text_vector (fallback)
    seed_row = c.execute(
        "SELECT text_vector FROM ad_embeddings WHERE ad_id = ? AND user_id = ?",
        (seed_ad_id, user_id),
    ).fetchone()
    seed_text_vec = _vec(seed_row["text_vector"]) if seed_row else None

    results = []
    for v in variants:
        variant_id = v["id"]
        job_id = v["job_id"]

        # Image vector
        emb_ad_id = variant_embedding_ad_id(job_id, variant_id)
        emb_row = c.execute(
            "SELECT image_vector FROM ad_embeddings WHERE ad_id = ? AND user_id = ?",
            (emb_ad_id, user_id),
        ).fetchone()
        image_vec = _vec(emb_row["image_vector"]) if emb_row else None
        # None image_vec is allowed — combiner will zero-pad that slot

        # Text vector — prefer exact combination match in ad_text_combination_embeddings.
        # Strategy: try the full combo dict from v.suggestion first (the seeding script stores
        # the exact combination there), then fall back to reconstructing from headline+short_text
        # for real pipeline variants where suggestion is an image prompt string, not JSON.
        job_row = c.execute(
            "SELECT headline, short_text FROM ad_generation_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        text_vec = None
        combo_key = None
        combo = None

        def _try_combo_lookup(candidate_combo: dict) -> bool:
            nonlocal text_vec, combo_key, combo
            candidate_key = json.dumps(candidate_combo, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            tce_row = c.execute(
                """SELECT vector FROM ad_text_combination_embeddings
                   WHERE source_id = ? AND combination_key = ?""",
                (text_source_id, candidate_key),
            ).fetchone()
            if tce_row:
                text_vec = _vec(tce_row["vector"])
                combo_key = candidate_key
                combo = candidate_combo
                return True
            return False

        # 1. Try suggestion field — seeding script stores the full combo dict (all slots).
        suggestion_str = v["suggestion"]
        if suggestion_str:
            try:
                suggestion_combo = json.loads(suggestion_str)
                if isinstance(suggestion_combo, dict) and "headline" in suggestion_combo:
                    _try_combo_lookup(suggestion_combo)
            except (json.JSONDecodeError, TypeError):
                pass

        # 2. Fall back to reconstructing from headline + short_text with platform-native slot names.
        if text_vec is None and job_row:
            for slot_name in ("primary_text", "description"):
                if _try_combo_lookup({"headline": job_row["headline"], slot_name: job_row["short_text"]}):
                    break

        # Fallback: seed ad text_vector
        if text_vec is None:
            text_vec = seed_text_vec

        if text_vec is None:
            continue

        if combo_key is None:
            combo_key = json.dumps({"variant_id": variant_id}, sort_keys=True, separators=(",", ":"))
        if combo is None:
            combo = {}

        results.append({
            "variant_id": variant_id,
            "job_id": job_id,
            "combination_key": combo_key,
            "combination": combo,
            "score": float(v["score"]),
            "text_vector": text_vec,
            "image_vector": image_vec,
        })

    c.close()
    return results


# ---------------------------------------------------------------------------
# Candidate combinations
# ---------------------------------------------------------------------------

def get_candidate_combinations(
    text_source_id: str,
    seed_ad_id: str,
    user_id: int,
    exclude_keys: set[str] | None = None,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Return all (text combination × image) candidates for the seed ad.

    Each text combination from ad_text_combination_embeddings is paired with
    every image embedding in ad_image_embeddings for the seed ad, producing
    N×M candidates. Falls back to the single image_vector in ad_embeddings if
    no per-image embeddings exist.

    Each dict:
      combination_key  — str  (compound JSON of text combo + image_slot)
      combination      — dict (text fields + image_url for display)
      text_vector      — np.ndarray float32
      image_vector     — np.ndarray float32
    """
    c = _conn(db_path)

    # Per-image embeddings for seed ad (one row per image slot)
    image_rows = c.execute(
        """SELECT slot_index, image_ref, vector
           FROM ad_image_embeddings
           WHERE user_id = ? AND ad_id = ?
           ORDER BY slot_index""",
        (user_id, seed_ad_id),
    ).fetchall()

    if image_rows:
        image_slots = [
            {"slot_index": r["slot_index"], "image_ref": r["image_ref"], "image_vec": _vec(r["vector"])}
            for r in image_rows
        ]
    else:
        # Fall back to single image_vector from ad_embeddings
        seed_row = c.execute(
            "SELECT image_vector FROM ad_embeddings WHERE ad_id = ? AND user_id = ?",
            (seed_ad_id, user_id),
        ).fetchone()
        image_slots = [{"slot_index": 0, "image_ref": None, "image_vec": _vec(seed_row["image_vector"]) if seed_row else None}]

    # All text combinations for this source
    text_rows = c.execute(
        """SELECT combination_key, vector
           FROM ad_text_combination_embeddings
           WHERE source_id = ?
           ORDER BY id""",
        (text_source_id,),
    ).fetchall()
    c.close()

    results = []
    for text_row in text_rows:
        text_vec = _vec(text_row["vector"])
        if text_vec is None:
            continue
        text_combo = json.loads(text_row["combination_key"])

        for img in image_slots:
            compound_key = json.dumps(
                {"combo": text_combo, "image_slot": img["slot_index"]},
                sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            )
            if exclude_keys and compound_key in exclude_keys:
                continue
            combination = {**text_combo}
            if img["image_ref"]:
                combination["image_url"] = img["image_ref"]
            results.append({
                "combination_key": compound_key,
                "combination": combination,
                "text_vector": text_vec,
                "image_vector": img["image_vec"],
            })
    return results

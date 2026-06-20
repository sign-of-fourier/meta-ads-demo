"""
DB access layer for the BO pipeline.

Scored observations are read from scored_observations, which is written by:
  - seed_script (metric='synthetic') via seed_bo_synthetic.py or the seed endpoint
  - generation_pipeline (metric='qwen') after Qwen2-VL scoring
  - convergence (metric='ctr'|'cvr'|'roas') after a pushed clone converges

get_scored_combinations walks METRIC_PREFERENCE and returns the first metric
with sufficient rows: real metrics (ctr/cvr/roas) need >= MIN_REAL_OBS to
displace warm-start fallbacks; synthetic/Qwen accept any non-empty result.

Candidate combinations: all rows in ad_text_combination_embeddings for
text_source_id (or all source_ids in text_source_ids), each paired with every
image in ad_image_embeddings for the seed ad (or all image_ad_ids).

Both functions accept optional list overrides for multi-member ad generators.
When lists are provided, results are merged across all member ads.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from bo_pipeline.config import METRIC_PREFERENCE, MIN_REAL_OBS, REAL_METRICS

DB_PATH = Path(__file__).parent.parent / "app.db"


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _vec(blob: bytes | None) -> np.ndarray | None:
    if blob is None:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def get_scored_combinations(
    seed_ad_id: str,
    text_source_id: str,   # kept for API compatibility; not used for filtering
    user_id: int,
    db_path: Path = DB_PATH,
    target_metric: str | None = None,
    seed_ad_ids: list[str] | None = None,
) -> list[dict]:
    """
    Return scored observations for seed_ad_id from scored_observations.

    When seed_ad_ids is provided (multi-member generator), observations are
    merged across all member ad IDs.

    Walks METRIC_PREFERENCE and returns the first metric with enough rows.
    Real metrics (ctr/cvr/roas) require >= MIN_REAL_OBS rows before displacing
    warm-start fallbacks. Synthetic/Qwen metrics accept any non-empty result.
    When target_metric is given explicitly, only that metric is used (no threshold).

    Each returned dict:
      combination_key  — str
      combination      — dict (parsed from JSON)
      score            — float
      text_vector      — np.ndarray float32
      image_vector     — np.ndarray float32 | None
    """
    from bo_pipeline.storage import ensure_scored_observations_table
    ensure_scored_observations_table(db_path)

    ids = seed_ad_ids if seed_ad_ids else [seed_ad_id]
    placeholders = ",".join("?" * len(ids))

    c = _conn(db_path)
    try:
        metrics_to_try = (target_metric,) if target_metric else METRIC_PREFERENCE
        for metric in metrics_to_try:
            rows = c.execute(
                f"""SELECT combination_key, combination, score, text_vector, image_vector
                   FROM scored_observations
                   WHERE user_id = ? AND seed_ad_id IN ({placeholders}) AND metric = ?
                   ORDER BY id""",
                (user_id, *ids, metric),
            ).fetchall()
            # Real metrics need a minimum count before displacing warm-start fallbacks.
            # One observation is not enough for a meaningful GP fit.
            if rows and metric in REAL_METRICS and len(rows) < MIN_REAL_OBS:
                continue
            if rows:
                # Deduplicate by combination_key, keeping the most recent score.
                # Duplicates arise when warm-start or re-ingestion writes the same
                # combination more than once; feeding duplicates to the GPR biases
                # it toward those combinations and pollutes nearest-neighbor results.
                seen: dict[str, dict] = {}
                for r in rows:
                    if r["text_vector"] is not None:
                        seen[r["combination_key"]] = {
                            "combination_key": r["combination_key"],
                            "combination": json.loads(r["combination"]),
                            "score": float(r["score"]),
                            "text_vector": _vec(r["text_vector"]),
                            "image_vector": _vec(r["image_vector"]),
                        }
                if seen:
                    return list(seen.values())
        return []
    finally:
        c.close()


def get_real_observation_count(
    seed_ad_id: str,
    user_id: int,
    db_path: Path = DB_PATH,
    seed_ad_ids: list[str] | None = None,
) -> int:
    """Return count of real-platform (ctr/cvr/roas) observations for this ad."""
    from bo_pipeline.storage import ensure_scored_observations_table
    ensure_scored_observations_table(db_path)
    ids = seed_ad_ids if seed_ad_ids else [seed_ad_id]
    placeholders = ",".join("?" * len(ids))
    metric_placeholders = ",".join("?" * len(REAL_METRICS))
    c = _conn(db_path)
    try:
        row = c.execute(
            f"SELECT COUNT(*) FROM scored_observations "
            f"WHERE user_id=? AND seed_ad_id IN ({placeholders}) AND metric IN ({metric_placeholders})",
            (user_id, *ids, *REAL_METRICS),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        c.close()


def get_candidate_combinations(
    text_source_id: str,
    seed_ad_id: str,
    user_id: int,
    exclude_keys: set[str] | None = None,
    db_path: Path = DB_PATH,
    text_source_ids: list[str] | None = None,
    image_ad_ids: list[str] | None = None,
) -> list[dict]:
    """
    Return all (text combination × image) candidates for the seed ad.

    When text_source_ids is provided (multi-member generator), text combinations
    are merged across all member ads. When image_ad_ids is provided, images are
    pooled across all member ads.

    Each text combination from ad_text_combination_embeddings is paired with
    every image embedding in ad_image_embeddings, producing N×M candidates.
    Falls back to the single image_vector in ad_embeddings if no per-image
    embeddings exist (Google RSA zero-pad path).

    Each dict:
      combination_key  — str  (compound JSON of text combo + image_slot)
      combination      — dict (text fields + image_url for display)
      text_vector      — np.ndarray float32
      image_vector     — np.ndarray float32 | None
    """
    c = _conn(db_path)

    img_ids = image_ad_ids if image_ad_ids else [seed_ad_id]
    img_placeholders = ",".join("?" * len(img_ids))
    image_rows = c.execute(
        f"""SELECT slot_index, image_ref, vector
           FROM ad_image_embeddings
           WHERE user_id = ? AND ad_id IN ({img_placeholders})
           ORDER BY ad_id, slot_index""",
        (user_id, *img_ids),
    ).fetchall()

    if image_rows:
        image_slots = [
            {"slot_index": i, "image_ref": r["image_ref"], "image_vec": _vec(r["vector"])}
            for i, r in enumerate(image_rows)
        ]
    else:
        # Fallback: use seed ad's combined image_vector (zero-padded for Google RSA)
        seed_row = c.execute(
            "SELECT image_vector FROM ad_embeddings WHERE ad_id = ? AND user_id = ?",
            (seed_ad_id, user_id),
        ).fetchone()
        image_slots = [{"slot_index": 0, "image_ref": None, "image_vec": _vec(seed_row["image_vector"]) if seed_row else None}]

    src_ids = text_source_ids if text_source_ids else [text_source_id]
    src_placeholders = ",".join("?" * len(src_ids))
    text_rows = c.execute(
        f"""SELECT combination_key, vector
           FROM ad_text_combination_embeddings
           WHERE source_id IN ({src_placeholders})
           ORDER BY id""",
        (*src_ids,),
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

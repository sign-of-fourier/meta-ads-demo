"""
DB access layer for the BO pipeline.

Scored observations are read from scored_observations, which is written by:
  - seed_script (metric='synthetic') via seed_bo_synthetic.py or the seed endpoint
  - generation_pipeline (metric='qwen') after Qwen2-VL scoring
  - convergence (metric='ctr'|'cvr'|'roas') after a pushed clone converges

get_scored_combinations walks METRIC_PREFERENCE and returns the first metric
with at least one row, unless target_metric is explicitly given.

Candidate combinations are unchanged: all rows in ad_text_combination_embeddings
for text_source_id, each paired with every image in ad_image_embeddings (or the
seed ad's image_vector as fallback).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

DB_PATH = Path(__file__).parent.parent / "app.db"

# Metric resolution order: prefer real signals over synthetic ones.
METRIC_PREFERENCE = ("ctr", "cvr", "roas", "qwen", "synthetic")


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
) -> list[dict]:
    """
    Return scored observations for seed_ad_id from scored_observations.

    Walks METRIC_PREFERENCE and returns the first metric with >= 1 row,
    unless target_metric is given (in which case only that metric is used).

    Each returned dict:
      combination_key  — str
      combination      — dict (parsed from JSON)
      score            — float
      text_vector      — np.ndarray float32
      image_vector     — np.ndarray float32 | None
    """
    from bo_pipeline.storage import ensure_scored_observations_table
    ensure_scored_observations_table(db_path)

    c = _conn(db_path)
    try:
        metrics_to_try = (target_metric,) if target_metric else METRIC_PREFERENCE
        for metric in metrics_to_try:
            rows = c.execute(
                """SELECT combination_key, combination, score, text_vector, image_vector
                   FROM scored_observations
                   WHERE user_id = ? AND seed_ad_id = ? AND metric = ?
                   ORDER BY id""",
                (user_id, seed_ad_id, metric),
            ).fetchall()
            if rows:
                return [
                    {
                        "combination_key": r["combination_key"],
                        "combination": json.loads(r["combination"]),
                        "score": float(r["score"]),
                        "text_vector": _vec(r["text_vector"]),
                        "image_vector": _vec(r["image_vector"]),
                    }
                    for r in rows
                    if r["text_vector"] is not None
                ]
        return []
    finally:
        c.close()


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
      image_vector     — np.ndarray float32 | None
    """
    c = _conn(db_path)

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
        seed_row = c.execute(
            "SELECT image_vector FROM ad_embeddings WHERE ad_id = ? AND user_id = ?",
            (seed_ad_id, user_id),
        ).fetchone()
        image_slots = [{"slot_index": 0, "image_ref": None, "image_vec": _vec(seed_row["image_vector"]) if seed_row else None}]

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

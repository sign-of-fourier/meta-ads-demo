"""
warm_start/mini_bo.py

Mini BO loop for warm-start scoring.

Samples UNIVERSE_SIZE candidates from the n×m pool, scores them using the
warm-start oracle (Qwen or fake server), and writes results to scored_observations
with metric='qwen_warm'.

Scoring order:
  1. INIT_SAMPLE_SIZE random candidates are scored first (cold start).
  2. Remaining candidates are scored in GP-guided order (highest EI next).
  3. When fewer than MIN_TRAINING_POINTS have been scored, falls back to random
     ordering until the GP can be fit.

All 20 candidates in the universe are scored exactly once.
The loop is invisible to the user — it runs as a background task triggered when
a template ad is selected with no existing scored_observations.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
from pathlib import Path

import numpy as np

from ad_embedding_combiner import combine
from bo_pipeline.gpr import MIN_TRAINING_POINTS, expected_improvement, fit_gpr, transform_y
from bo_pipeline.selector import get_candidate_combinations
from bo_pipeline.storage import DB_PATH, ensure_scored_observations_table
from warm_start.scorer import score_candidate

logger = logging.getLogger(__name__)

UNIVERSE_SIZE = 20
INIT_SAMPLE_SIZE = 5
WARM_START_MIN_CTR_OBS = 5   # after this many real CTR obs, switch oracle to CTR
METRIC = "qwen_warm"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_universe(candidates: list[dict], size: int) -> list[dict]:
    """Deterministic sample so the universe is the same on repeated calls."""
    rng = random.Random(42)
    return rng.sample(candidates, min(size, len(candidates)))


def _build_X(candidates: list[dict]) -> np.ndarray:
    vecs = []
    for c in candidates:
        tv = c.get("text_vector")
        iv = c.get("image_vector")
        if tv is None:
            continue
        vecs.append(combine(tv, iv))
    return np.vstack(vecs).astype(np.float64) if vecs else np.empty((0, 1))


def _write_observation(
    seed_ad_id: str,
    candidate: dict,
    score: float,
    user_id: int,
    db_path: Path,
) -> None:
    ensure_scored_observations_table(db_path)
    combo = candidate["combination"]
    key = candidate["combination_key"]
    tv = candidate.get("text_vector")
    iv = candidate.get("image_vector")
    tv_blob = tv.astype(np.float32).tobytes() if tv is not None else None
    iv_blob = iv.astype(np.float32).tobytes() if iv is not None else None

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """INSERT INTO scored_observations
               (user_id, seed_ad_id, combination_key, combination, score,
                metric, source, text_vector, image_vector)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, seed_ad_id, combination_key, metric)
               DO UPDATE SET score=excluded.score""",
            (
                user_id, seed_ad_id, key,
                json.dumps(combo, ensure_ascii=False),
                score, METRIC, "warm_start",
                tv_blob, iv_blob,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _load_warm_scores(
    seed_ad_id: str, user_id: int, db_path: Path
) -> list[tuple[str, float]]:
    """Return (combination_key, score) pairs already scored with qwen_warm."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT combination_key, score FROM scored_observations "
            "WHERE user_id=? AND seed_ad_id=? AND metric=?",
            (user_id, seed_ad_id, METRIC),
        ).fetchall()
        return [(r["combination_key"], float(r["score"])) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_warm_start(
    seed_ad_id: str,
    text_source_id: str,
    user_id: int,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Run the mini BO warm-start loop for seed_ad_id.

    Skips silently if qwen_warm observations already exist (idempotent).
    Returns the full list of scored candidates (combination_key + score).
    """
    # Check if already done
    existing = _load_warm_scores(seed_ad_id, user_id, db_path)
    if existing:
        logger.info("warm_start: %d observations already exist for %s — skipping", len(existing), seed_ad_id)
        return [{"combination_key": k, "score": s} for k, s in existing]

    all_candidates = get_candidate_combinations(
        text_source_id, seed_ad_id, user_id, db_path=db_path
    )
    if not all_candidates:
        logger.warning("warm_start: no candidates found for seed_ad_id=%s", seed_ad_id)
        return []

    universe = _sample_universe(all_candidates, UNIVERSE_SIZE)
    logger.info("warm_start: universe=%d (from %d total) for seed_ad_id=%s",
                len(universe), len(all_candidates), seed_ad_id)

    scored_keys: set[str] = set()
    scored_scores: list[float] = []
    scored_vecs: list[np.ndarray] = []

    # --- Random initialization ---
    init_count = min(INIT_SAMPLE_SIZE, len(universe))
    rng = random.Random()
    init_indices = rng.sample(range(len(universe)), init_count)

    for i in init_indices:
        cand = universe[i]
        score = await score_candidate(cand)
        _write_observation(seed_ad_id, cand, score, user_id, db_path)
        scored_keys.add(cand["combination_key"])
        scored_scores.append(score)
        tv = cand.get("text_vector")
        iv = cand.get("image_vector")
        if tv is not None:
            scored_vecs.append(combine(tv, iv).astype(np.float64))
        logger.debug("warm_start: init scored %s score=%.4f", cand["combination_key"][:20], score)

    # --- GP-guided scoring of remaining ---
    remaining = [i for i in range(len(universe)) if universe[i]["combination_key"] not in scored_keys]

    while remaining:
        if len(scored_scores) >= MIN_TRAINING_POINTS and scored_vecs:
            X_train = np.vstack(scored_vecs)
            y_train = transform_y(np.array(scored_scores, dtype=np.float64))
            gpr, scaler = fit_gpr(X_train, y_train)
            y_best = float(y_train.max())

            X_rem = np.vstack([
                combine(universe[i]["text_vector"], universe[i]["image_vector"]).astype(np.float64)
                for i in remaining
                if universe[i].get("text_vector") is not None
            ])
            ei = expected_improvement(gpr, scaler, X_rem, y_best)
            best_local = int(np.argmax(ei))
            next_idx = remaining.pop(best_local)
        else:
            # Not enough data for GP yet — score randomly
            next_idx = remaining.pop(rng.randrange(len(remaining)))

        cand = universe[next_idx]
        score = await score_candidate(cand)
        _write_observation(seed_ad_id, cand, score, user_id, db_path)
        scored_keys.add(cand["combination_key"])
        scored_scores.append(score)
        tv = cand.get("text_vector")
        iv = cand.get("image_vector")
        if tv is not None:
            scored_vecs.append(combine(tv, iv).astype(np.float64))
        logger.debug("warm_start: gp scored %s score=%.4f", cand["combination_key"][:20], score)

    logger.info("warm_start: complete — scored %d candidates for %s", len(scored_keys), seed_ad_id)
    return _load_warm_scores(seed_ad_id, user_id, db_path)

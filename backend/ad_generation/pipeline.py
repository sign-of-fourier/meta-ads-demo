"""
Orchestrates the full ad generation pipeline for one job:

  1. Analyze seed image → 10 suggested edits          (analyzer)
  2. Submit all 10 to deAPI (img2img, FLUX)             (generator)
  3. Poll each until done                               (poller)
  4. Download and save result images locally            (poller)
  5. Score each via the fine-tuned model                (scorer)
  6. QA check each scored image for major artifacts     (qa_checker)
  7. For flagged images: submit correction to deAPI,
     poll/save the fix, score it, mark original defunct (generator + poller + scorer)

Designed to run as a fire-and-forget async task. All state is persisted to
the DB after each step so the job can be inspected mid-flight.

Variant status lifecycle:
  submitted → done → scored → (qa_status=passed)          ← stays in pool
                           → (qa_status=flagged, status=defunct)
                                        └─▶ child variant: submitted → done → scored → qa_status=passed

Public API:
  create_job(...)          → job_id
  run_generation_job(...)  → None  (call via asyncio.create_task)
  get_job_status(job_id)   → dict
  get_job_variants(job_id) → list[dict]
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path

from ad_generation.analyzer import analyze_image
from ad_generation.generator import submit_generation
from ad_generation.poller import _images_dir, extract_result_url, poll_until_done, save_image_locally
from ad_generation.qa_checker import check_for_artifacts
from ad_generation.scorer import score_variant
from ad_generation.storage import (
    DB_PATH,
    create_job as _db_create_job,
    create_variant,
    get_job,
    get_variants,
    update_job,
    update_variant,
)

logger = logging.getLogger(__name__)


def _images_base_url() -> str:
    return os.getenv("IMAGES_SERVE_BASE_URL", "http://localhost:8000/images")


def _serve_url(filename: str) -> str:
    return f"{_images_base_url()}/{filename}"


def _image_data_url(filename: str) -> str:
    """Read a locally saved image and return a base64 data URL for API calls.

    Azure OpenAI cannot reach localhost URLs, so we embed the image inline.
    Falls back to the serve URL if the file cannot be read.
    """
    try:
        raw = (_images_dir() / filename).read_bytes()
        return "data:image/png;base64," + base64.b64encode(raw).decode()
    except Exception:
        return _serve_url(filename)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_job(
    user_id: int,
    campaign_id: str,
    adset_id: str,
    seed_image_url: str,
    headline: str,
    short_text: str,
    seed_ad_id: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """Create a new generation job record and return its id."""
    return _db_create_job(
        user_id=user_id,
        campaign_id=campaign_id,
        adset_id=adset_id,
        seed_image_url=seed_image_url,
        headline=headline,
        short_text=short_text,
        seed_ad_id=seed_ad_id,
        db_path=db_path,
    )


def get_job_status(job_id: int, db_path: Path = DB_PATH) -> dict | None:
    """Return the job row as a dict, or None if not found."""
    return get_job(job_id, db_path)


def get_job_variants(job_id: int, db_path: Path = DB_PATH) -> list[dict]:
    """Return all variant rows for a job, ordered by id."""
    return get_variants(job_id, db_path)


def get_active_variants(job_id: int, db_path: Path = DB_PATH) -> list[dict]:
    """Return only variants that are in the active pool (status != 'defunct')."""
    return [v for v in get_variants(job_id, db_path) if v["status"] != "defunct"]


async def run_generation_job(job_id: int, db_path: Path = DB_PATH) -> None:
    """
    Run all pipeline steps for an existing job.
    Safe to fire-and-forget; catches and records all errors.
    """
    try:
        await _run(job_id, db_path)
    except Exception as exc:
        logger.exception("generation job %d failed", job_id)
        update_job(job_id, db_path, status="failed", error=str(exc))


# ---------------------------------------------------------------------------
# Internal pipeline
# ---------------------------------------------------------------------------

async def _run(job_id: int, db_path: Path) -> None:
    job = get_job(job_id, db_path)
    if job is None:
        raise ValueError(f"Job {job_id} not found")

    # ── Step 1: Analyze ───────────────────────────────────────────────────────
    update_job(job_id, db_path, status="analyzing")
    logger.info("job %d: analyzing seed image", job_id)

    suggestions = await analyze_image(
        image_url=job["seed_image_url"],
        headline=job["headline"],
        short_text=job["short_text"],
    )
    update_job(job_id, db_path, status="generating", suggestions=json.dumps(suggestions))
    logger.info("job %d: got %d suggestions", job_id, len(suggestions))

    # ── Step 2: Submit all generations ────────────────────────────────────────
    variant_ids: list[int] = []
    for suggestion in suggestions:
        vid = create_variant(job_id, suggestion, db_path=db_path)
        try:
            request_id = submit_generation(
                seed_image_url=job["seed_image_url"],
                prompt=suggestion,
            )
            update_variant(vid, db_path, deapi_request_id=request_id, status="submitted")
            logger.info("job %d variant %d: submitted request_id=%s", job_id, vid, request_id)
        except Exception as exc:
            logger.warning("job %d variant %d: submit failed: %s", job_id, vid, exc)
            update_variant(vid, db_path, status="failed")
        variant_ids.append(vid)

    # ── Steps 3 & 4: Poll + download ─────────────────────────────────────────
    update_job(job_id, db_path, status="polling")
    await asyncio.gather(*[_poll_and_save(vid, job_id, db_path) for vid in variant_ids])

    # ── Step 5: Score ─────────────────────────────────────────────────────────
    update_job(job_id, db_path, status="scoring")
    await asyncio.gather(*[_score(vid, job, db_path) for vid in variant_ids])

    # ── Step 6: QA artifact check ─────────────────────────────────────────────
    update_job(job_id, db_path, status="qa")
    correction_tasks: list[tuple[int, int, str]] = []  # (parent_vid, job_id, correction_prompt)

    async def _qa(vid: int) -> None:
        variant = _get_variant(vid, job_id, db_path)
        if variant is None or variant["status"] != "scored" or not variant.get("local_filename"):
            update_variant(vid, db_path, qa_status="skipped")
            return

        try:
            result = await check_for_artifacts(_image_data_url(variant["local_filename"]))
            if result.passed:
                update_variant(vid, db_path, qa_status="passed")
                logger.info("job %d variant %d: QA passed", job_id, vid)
            else:
                correction_prompt = " and ".join(result.corrections)
                update_variant(
                    vid, db_path,
                    qa_status="flagged",
                    qa_corrections=json.dumps(result.corrections),
                    status="defunct",
                )
                correction_tasks.append((vid, job_id, correction_prompt))
                logger.info(
                    "job %d variant %d: QA flagged — %s",
                    job_id, vid, result.corrections,
                )
        except Exception as exc:
            logger.warning("job %d variant %d: QA check failed: %s", job_id, vid, exc)
            update_variant(vid, db_path, qa_status="skipped")

    await asyncio.gather(*[_qa(vid) for vid in variant_ids])

    # ── Step 7: Submit corrections for flagged variants ───────────────────────
    if not correction_tasks:
        update_job(job_id, db_path, status="done")
        logger.info("job %d: complete (no corrections needed)", job_id)
        return

    update_job(job_id, db_path, status="correcting")
    corrected_ids: list[int] = []

    for parent_vid, jid, correction_prompt in correction_tasks:
        parent = _get_variant(parent_vid, jid, db_path)
        if parent is None or not parent.get("local_filename"):
            continue

        # img2img source is the already-saved (defective) image, not the original seed
        defective_url = _serve_url(parent["local_filename"])
        child_vid = create_variant(jid, parent["suggestion"], parent_variant_id=parent_vid, db_path=db_path)
        try:
            request_id = submit_generation(
                seed_image_url=defective_url,
                prompt=correction_prompt,
                strength=0.5,   # light touch — preserve the image, fix the defect
            )
            update_variant(child_vid, db_path, deapi_request_id=request_id, status="submitted")
            logger.info(
                "job %d variant %d: correction submitted (parent=%d) request_id=%s",
                jid, child_vid, parent_vid, request_id,
            )
        except Exception as exc:
            logger.warning("job %d variant %d: correction submit failed: %s", jid, child_vid, exc)
            update_variant(child_vid, db_path, status="failed")

        corrected_ids.append(child_vid)

    # Poll + save + score corrected variants; mark them qa_status=passed (one round only)
    await asyncio.gather(*[_poll_and_save(vid, job_id, db_path) for vid in corrected_ids])
    await asyncio.gather(*[_score(vid, job, db_path) for vid in corrected_ids])
    for vid in corrected_ids:
        variant = _get_variant(vid, job_id, db_path)
        if variant and variant["status"] == "scored":
            update_variant(vid, db_path, qa_status="passed")

    update_job(job_id, db_path, status="done")
    logger.info("job %d: complete (%d correction(s) applied)", job_id, len(corrected_ids))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_variant(vid: int, job_id: int, db_path: Path) -> dict | None:
    return next((v for v in get_variants(job_id, db_path) if v["id"] == vid), None)


async def _poll_and_save(vid: int, job_id: int, db_path: Path) -> None:
    variant = _get_variant(vid, job_id, db_path)
    if variant is None or variant["status"] == "failed" or not variant.get("deapi_request_id"):
        return
    try:
        status_payload = await poll_until_done(variant["deapi_request_id"])
        result_url = extract_result_url(status_payload)
        if not result_url:
            raise RuntimeError("No result_url in completed payload")
        filename = save_image_locally(result_url)
        update_variant(vid, db_path, status="done", result_url=result_url, local_filename=filename)
        logger.info("job %d variant %d: saved as %s", job_id, vid, filename)
    except Exception as exc:
        logger.warning("job %d variant %d: poll/save failed: %s", job_id, vid, exc)
        update_variant(vid, db_path, status="failed")


def _write_qwen_observation(
    variant_id: int,
    job: dict,
    score: float,
    db_path: Path,
) -> None:
    """Write a Qwen2-VL score to scored_observations for BO training."""
    import sqlite3 as _sqlite3
    seed_ad_id = job.get("seed_ad_id")
    user_id = job.get("user_id")
    job_id = job.get("id")
    if not seed_ad_id or not user_id or not job_id:
        return

    conn = _sqlite3.connect(str(db_path))
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        variant_row = conn.execute(
            "SELECT suggestion FROM ad_generation_variants WHERE id=?", (variant_id,)
        ).fetchone()

        combo_key = None
        combo = None
        text_vec_bytes = None

        # 1. Try suggestion as JSON combination dict
        if variant_row:
            try:
                suggestion_combo = json.loads(variant_row["suggestion"])
                if isinstance(suggestion_combo, dict) and "headline" in suggestion_combo:
                    ck = json.dumps(suggestion_combo, sort_keys=True, separators=(",", ":"))
                    tce = conn.execute(
                        "SELECT vector FROM ad_text_combination_embeddings WHERE source_id=? AND combination_key=?",
                        (seed_ad_id, ck),
                    ).fetchone()
                    if tce:
                        combo_key = ck
                        combo = suggestion_combo
                        text_vec_bytes = tce["vector"]
            except (json.JSONDecodeError, TypeError):
                pass

        # 2. Fall back to headline + short_text reconstruction
        if text_vec_bytes is None:
            for slot_name in ("primary_text", "description"):
                candidate_combo = {
                    "headline": job.get("headline", ""),
                    slot_name: job.get("short_text", ""),
                }
                ck = json.dumps(candidate_combo, sort_keys=True, separators=(",", ":"))
                tce = conn.execute(
                    "SELECT vector FROM ad_text_combination_embeddings WHERE source_id=? AND combination_key=?",
                    (seed_ad_id, ck),
                ).fetchone()
                if tce:
                    combo_key = ck
                    combo = candidate_combo
                    text_vec_bytes = tce["vector"]
                    break

        if text_vec_bytes is None:
            return  # no matching combination embedding — skip silently

        # Image vector for this specific variant
        emb_ad_id = f"gen_{job_id}_{variant_id}"
        emb = conn.execute(
            "SELECT image_vector FROM ad_embeddings WHERE ad_id=? AND user_id=?",
            (emb_ad_id, user_id),
        ).fetchone()
        image_vec_bytes = emb["image_vector"] if emb and emb["image_vector"] else None

        conn.execute(
            """INSERT OR REPLACE INTO scored_observations
               (user_id, seed_ad_id, combination_key, combination,
                score, metric, source, text_vector, image_vector)
               VALUES (?, ?, ?, ?, ?, 'qwen', 'generation_pipeline', ?, ?)""",
            (user_id, seed_ad_id, combo_key, json.dumps(combo),
             score, text_vec_bytes, image_vec_bytes),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("_write_qwen_observation failed for variant %d: %s", variant_id, exc)
    finally:
        conn.close()


async def _score(vid: int, job: dict, db_path: Path) -> None:
    variant = _get_variant(vid, job["id"], db_path)
    if variant is None or variant["status"] != "done" or not variant.get("local_filename"):
        return
    try:
        result = await score_variant(
            image_url=_image_data_url(variant["local_filename"]),
            headline=job["headline"],
            short_text=job["short_text"],
        )
        update_variant(
            vid, db_path,
            status="scored",
            score=result.score,
            severity=result.severity,
            score_labels=json.dumps(result.labels),
        )
        logger.info("job %d variant %d: score=%.3f severity=%s", job["id"], vid, result.score, result.severity)
        _write_qwen_observation(vid, job, result.score, db_path)
    except Exception as exc:
        logger.warning("job %d variant %d: scoring failed: %s", job["id"], vid, exc)
        update_variant(vid, db_path, status="failed", error=str(exc))

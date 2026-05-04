"""
Top-level ad text generation pipeline for AdStac.kr.

Given a seed ad's components, generates N new text variants per slot,
assembles them with optional image URLs into a dynamic ad component list,
and stores the result.

This module is fully standalone — no dependency on the FastAPI app.

Public API:
  run_text_pipeline(seed_components, n_per_slot, image_urls, ...) → generated_ad_id
  get_generated_ad(generated_ad_id)                                → list[dict]
  get_generated_ad_meta(generated_ad_id)                           → dict | None
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ad_text_generation.assembler import assemble_dynamic_ad
from ad_text_generation.generator import TEXT_SLOTS, generate_all_slots
from ad_text_generation.storage import (
    DB_PATH,
    get_generated_ad,
    get_generated_ad_meta,
    save_generated_ad,
)

logger = logging.getLogger(__name__)


async def run_text_pipeline(
    seed_components: list[dict],
    n_per_slot: int = 5,
    image_urls: list[str] | None = None,
    text_slots: list[str] | None = None,
    source_ad_id: str | None = None,
    platform: str = "meta",
    db_path: Path = DB_PATH,
) -> int:
    """
    Generate text variants for each text slot, assemble with images, and store.

    seed_components — list of {slot, slot_index, value} dicts from the seed ad.
                      May be from a static or dynamic ad; all existing values
                      per slot are shown to the model as context.
    n_per_slot      — how many new text variants to generate per slot.
    image_urls      — if provided, replaces seed image slots in the assembled ad.
                      Pass the URLs from the image generation pipeline here.
                      If None, seed image slots are preserved as-is.
    text_slots      — restrict generation to these slots (default: all of
                      headline, primary_text, description that exist in seed).
    source_ad_id    — optional identifier of the seed ad, stored for traceability.

    Returns the generated_ad_id of the stored assembled ad.
    """
    logger.info(
        "text pipeline: source_ad=%s n_per_slot=%d slots=%s images=%d",
        source_ad_id, n_per_slot, text_slots or "all", len(image_urls or []),
    )

    generated_text = await generate_all_slots(
        seed_components=seed_components,
        n_per_slot=n_per_slot,
        slots=text_slots,
        platform=platform,
    )

    for slot, variants in generated_text.items():
        logger.info("text pipeline: slot=%s generated=%d variants", slot, len(variants))

    components = assemble_dynamic_ad(
        seed_components=seed_components,
        generated_text=generated_text,
        image_urls=image_urls,
    )

    generated_ad_id = save_generated_ad(
        components=components,
        source_ad_id=source_ad_id,
        db_path=db_path,
    )
    logger.info("text pipeline: stored generated_ad_id=%d (%d slots)", generated_ad_id, len(components))
    return generated_ad_id


__all__ = [
    "run_text_pipeline",
    "get_generated_ad",
    "get_generated_ad_meta",
]

"""
Ad text combination embedding pipeline for AdStac.kr.

For a dynamic ad with N headlines × M primary_texts × K descriptions,
embeds every combination as a single text vector and stores the results.

Uses the same Azure AI Inference endpoint as the existing embeddings module.
All combinations are embedded concurrently, gated by a semaphore to avoid
hitting rate limits.

Public API:
  embed_all_combinations(source_id, components, ...)  → int (count stored)
  get_embeddings_for_source(source_id, ...)           → list[dict]
  count_embeddings_for_source(source_id, ...)         → int
  combination_count(components, ...)                  → int  (dry-run, no I/O)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from ad_combination_embeddings.combinations import (
    TEXT_SLOTS,
    build_combinations,
    combination_count,
    combination_key,
)
from ad_combination_embeddings.storage import (
    DB_PATH,
    count_embeddings_for_source,
    get_embeddings_for_source,
    save_embeddings_batch,
)
from embeddings.embedder import embed_text

logger = logging.getLogger(__name__)

_DEFAULT_CONCURRENCY = 10   # max parallel embedding calls


async def embed_all_combinations(
    source_id: str,
    components: list[dict],
    slots: tuple[str, ...] | list[str] = TEXT_SLOTS,
    max_concurrency: int = _DEFAULT_CONCURRENCY,
    db_path: Path = DB_PATH,
) -> int:
    """
    Embed every Cartesian text combination and store the results.

    source_id      — caller-provided identifier (e.g. an ad_id or generated_ad_id).
                     Used as the retrieval key; no metadata attached here.
    components     — list of {slot, slot_index, value} dicts (dynamic or static ad).
    slots          — which text slots to include in combinations.
    max_concurrency — max parallel Azure embedding calls (default 10).

    Returns the number of embeddings successfully stored.
    """
    combos = build_combinations(components, slots)
    if not combos:
        logger.warning("embed_all_combinations: no combinations found for source_id=%s", source_id)
        return 0

    model_name = os.getenv("AZURE_TEXT_MODEL", "embed-v-4-0")
    logger.info(
        "embed_all_combinations: source_id=%s combinations=%d slots=%s",
        source_id, len(combos), list(slots),
    )

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _embed_one(combo: dict) -> tuple[str, object, str | None] | None:
        key = combination_key(combo)
        async with semaphore:
            vec = await embed_text(key)   # embed the JSON key string directly
        if vec is None:
            logger.warning("embed_all_combinations: embedding failed for combo %s", key)
            return None
        return (key, vec, model_name)

    tasks = [_embed_one(c) for c in combos]
    results = await asyncio.gather(*tasks)

    rows = [r for r in results if r is not None]
    if rows:
        save_embeddings_batch(source_id, rows, db_path)

    stored = len(rows)
    logger.info("embed_all_combinations: stored %d/%d embeddings for source_id=%s", stored, len(combos), source_id)
    return stored


__all__ = [
    "embed_all_combinations",
    "get_embeddings_for_source",
    "count_embeddings_for_source",
    "combination_count",
]

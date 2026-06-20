"""
warm_start/scorer.py

Score a candidate combo for the warm-start mini BO.
Returns a float in [0, 1] where higher = better ad.

Calls the real Qwen2-VL Modal endpoint via score_variant(), which returns
lower=better (internal convention) — we flip it here so the GP always sees
higher=better.
"""

from __future__ import annotations


async def score_candidate(candidate: dict) -> float:
    """
    Score a candidate dict (must have combination.title/body/image_url).
    Returns 0–1, higher = better.
    """
    combo = candidate.get("combination", {})
    title = combo.get("title", "")
    body = combo.get("body", combo.get("primary_text", ""))
    image_url = combo.get("image_url", "")
    if not image_url:
        return 0.5
    return await _real_score(image_url, title, body)


async def _real_score(image_url: str, headline: str, short_text: str) -> float:
    from ad_generation.scorer import score_variant
    result = await score_variant(image_url, headline, short_text)
    # score_variant returns lower=better (internal convention); flip for BO
    return round(1.0 - result.score, 4)

"""
Step 5 — Score a generated ad image using the fine-tuned Qwen2-VL model on Modal.
"""

from __future__ import annotations

import base64
import json
import os
import re

import httpx
from pydantic import BaseModel


MODAL_ENDPOINT = os.getenv(
    "MODAL_SCORING_ENDPOINT",
    "https://markshipman4273--bad-ads-qwen2vl-badadsmodel-web.modal.run/predict",
)


class ScoreResult(BaseModel):
    score: float          # 0–1, higher = worse
    severity: str         # "low" | "medium" | "high"
    labels: list[str]     # short issue descriptors


def _extract_score(raw_output: str) -> float:
    """Extract the numeric score from the model's raw decoded output."""
    # Try to find a JSON object containing "score" anywhere in the output
    for match in re.finditer(r'\{[^}]*"score"\s*:\s*([0-9.]+)[^}]*\}', raw_output):
        try:
            return float(match.group(1))
        except ValueError:
            continue
    # Fallback: find any bare number after "score":
    m = re.search(r'"score"\s*:\s*([0-9.]+)', raw_output)
    if m:
        return float(m.group(1))
    raise ValueError(f"No score found in model output: {raw_output!r}")


def _modal_to_internal(modal_score: float) -> float:
    """Convert Modal 1–7 (higher=better) to internal 0–1 (higher=worse)."""
    clamped = max(1.0, min(7.0, modal_score))
    return (7.0 - clamped) / 6.0


def _severity(score: float) -> str:
    if score > 0.67:
        return "high"
    if score > 0.33:
        return "medium"
    return "low"


def _image_bytes_from_url(image_url: str) -> bytes:
    """Decode a data URL or fetch an http URL to raw bytes."""
    if image_url.startswith("data:"):
        # data:<mime>;base64,<data>
        _, encoded = image_url.split(",", 1)
        return base64.b64decode(encoded)
    response = httpx.get(image_url, timeout=30)
    response.raise_for_status()
    return response.content


async def score_variant(
    image_url: str,
    headline: str,
    short_text: str,
) -> ScoreResult:
    """
    Score an ad creative image via the Modal-hosted fine-tuned Qwen2-VL model.
    image_url may be a base64 data URL or an http(s) URL.
    Returns a ScoreResult (lower score = better ad).
    """
    image_bytes = _image_bytes_from_url(image_url)
    prompt = f"Headline: {headline}\nShort text: {short_text}"

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            MODAL_ENDPOINT,
            files={"file": ("ad.png", image_bytes, "image/png")},
            data={"prompt": prompt},
        )
        response.raise_for_status()

    raw_output = response.json().get("raw_output", "")
    modal_score = _extract_score(raw_output)
    internal_score = _modal_to_internal(modal_score)

    return ScoreResult(
        score=internal_score,
        severity=_severity(internal_score),
        labels=[],
    )

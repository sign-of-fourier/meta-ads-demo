"""
Step 2 — Submit an img2img generation job to deAPI (FLUX model).
Returns a deAPI request_id; does not wait for the result.
"""

from __future__ import annotations

import os
import random

import requests

DEAPI_IMG2IMG_URL = "https://api.deapi.ai/api/v1/client/img2img"
FLUX_MODEL_ID = "Flux_2_Klein_4B_BF16"

_FLUX_SIZES: dict[str, tuple[int, int]] = {
    "1:1":  (1024, 1024),
    "4:5":  (896,  1120),
    "3:4":  (960,  1280),
    "9:16": (864,  1536),
    "16:9": (1536, 864),
    "2:3":  (960,  1440),
    "3:2":  (1440, 960),
}


def _deapi_key() -> str:
    return os.environ["DEAPI_API_KEY"]


def _download_bytes(url: str) -> bytes:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content


def submit_generation(
    seed_image_url: str,
    prompt: str,
    aspect_ratio: str = "1:1",
    seed: int | None = None,
    steps: int = 4,
    strength: float = 0.8,
) -> str:
    """
    Upload the seed image to deAPI and submit an img2img job.
    Returns the deAPI request_id string (job is async — poll for results).
    """
    if seed is None:
        seed = random.randint(1, 10_000)

    width, height = _FLUX_SIZES.get(aspect_ratio, (1024, 1024))
    img_bytes = _download_bytes(seed_image_url)

    resp = requests.post(
        DEAPI_IMG2IMG_URL,
        headers={
            "Authorization": f"Bearer {_deapi_key()}",
            "Accept": "application/json",
        },
        files={"image": ("source.png", img_bytes, "image/png")},
        data={
            "model": FLUX_MODEL_ID,
            "prompt": prompt,
            "seed": str(seed),
            "steps": str(steps),
            "strength": str(strength),
            "width": str(width),
            "height": str(height),
        },
        timeout=60,
    )
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise RuntimeError(
            f"deAPI submit failed ({e.response.status_code}): {e.response.text}"
        ) from e

    data = resp.json()
    request_id = data.get("data", {}).get("request_id")
    if not request_id:
        raise RuntimeError(f"deAPI response missing request_id: {data}")
    return request_id

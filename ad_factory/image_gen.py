"""
Image handling for ad_factory.

placeholder(concept)           → deterministic picsum URL, no API needed
generate_with_deapi(concept)   → img2img via deAPI FLUX (needs DEAPI_API_KEY)

Both return a URL string suitable for use as an ad image_url.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path


def placeholder(concept: str) -> str:
    """Return a deterministic picsum URL seeded by the concept's MD5 digest."""
    seed = hashlib.md5(concept.lower().encode()).hexdigest()[:10]
    return f"https://picsum.photos/seed/{seed}/600/315"


def generate_with_deapi(concept: str, save_dir: Path | None = None) -> str:
    """
    Generate an ad image by running deAPI img2img with a picsum reference
    image and the concept as the visual prompt.

    Falls back to placeholder() on any error so the caller always gets a URL.
    save_dir — local directory where the downloaded PNG should be saved.
               Defaults to backend/generated_images/.
    """
    # Add backend/ to sys.path so we can import the generation helpers
    backend_dir = Path(__file__).parent.parent / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    try:
        from ad_generation.generator import submit_generation
        from ad_generation.poller import poll_until_done, extract_result_url, save_image_locally

        if save_dir is None:
            save_dir = backend_dir / "generated_images"

        ref_url = placeholder(concept)
        visual_prompt = (
            f"Professional advertising photo: {concept}. "
            "Clean, high-quality product shot, bright lighting, suitable for social media."
        )

        print(f"  Submitting deAPI job (reference: {ref_url}) …")
        request_id = submit_generation(
            seed_image_url=ref_url,
            prompt=visual_prompt,
            aspect_ratio="16:9",
            steps=4,
            strength=0.75,
        )

        print(f"  Polling deAPI job {request_id} …")
        payload = asyncio.run(poll_until_done(request_id, max_wait=120))
        result_url = extract_result_url(payload)
        if not result_url:
            raise RuntimeError("deAPI returned no result_url")

        filename = save_image_locally(result_url, images_dir=save_dir)
        serve_base = os.getenv("IMAGES_SERVE_BASE_URL", "http://localhost:8000/images")
        local_url = f"{serve_base}/{filename}"
        print(f"  Image saved → {local_url}")
        return local_url

    except Exception as exc:
        print(f"  Image generation failed ({exc}), using placeholder.")
        return placeholder(concept)

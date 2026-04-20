"""
ad_text_generation — generate new ad copy variants from a seed ad for AdStac.kr.

Usage
-----
from ad_text_generation import run_text_pipeline, get_generated_ad
import asyncio

# Run the text pipeline (generates text, assembles with images, stores result)
generated_ad_id = asyncio.run(run_text_pipeline(
    seed_components=[
        {"slot": "headline",     "slot_index": 0, "value": "Wool Socks"},
        {"slot": "headline",     "slot_index": 1, "value": "Warm Feet"},
        {"slot": "primary_text", "slot_index": 0, "value": "Hand made in Switzerland."},
        {"slot": "image",        "slot_index": 0, "value": "https://example.com/img.png"},
    ],
    n_per_slot=5,
    image_urls=["https://example.com/gen1.png", "https://example.com/gen2.png"],
    source_ad_id="original_ad_123",
))

# Retrieve the assembled ad
slots = get_generated_ad(generated_ad_id)
# → [{"slot": "headline", "slot_index": 0, "value": "...", "source": "seed"}, ...]
"""

from ad_text_generation.pipeline import get_generated_ad, get_generated_ad_meta, run_text_pipeline

__all__ = [
    "run_text_pipeline",
    "get_generated_ad",
    "get_generated_ad_meta",
]

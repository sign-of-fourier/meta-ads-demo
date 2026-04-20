"""
Assemble a complete generated ad in the normalized component format.

Component format (matches ad_creative_structures):
  {"slot": str, "slot_index": int, "value": str, "source": str}

source values:
  "seed"            — carried from the seed ad unchanged
  "generated_text"  — new variant from the text generator
  "generated_image" — URL from the image generation pipeline
"""

from __future__ import annotations

TEXT_SLOTS = ("headline", "primary_text", "description")
IMAGE_SLOT = "image"


def assemble_dynamic_ad(
    seed_components: list[dict],
    generated_text: dict[str, list[str]],
    image_urls: list[str] | None = None,
) -> list[dict]:
    """
    Merge seed components, generated text variants, and image URLs into a
    single component list representing a dynamic ad.

    Slot index assignment:
      - Seed text values keep their original slot_index values.
      - Generated text values continue from max(seed slot_index) + 1.
      - Image slots: if image_urls is provided, they replace all seed image slots.
        If image_urls is None, seed image slots are preserved.

    Returns a list of component dicts with an added "source" key.
    """
    assembled: list[dict] = []

    # --- Text slots: seed first, then generated ---
    seed_by_slot: dict[str, list[dict]] = {}
    for comp in seed_components:
        slot = comp.get("slot", "")
        if slot in TEXT_SLOTS:
            seed_by_slot.setdefault(slot, []).append(comp)

    all_text_slots = set(TEXT_SLOTS) | set(generated_text.keys())
    for slot in all_text_slots:
        seed_rows = sorted(seed_by_slot.get(slot, []), key=lambda c: c.get("slot_index", 0))
        next_index = (max((c.get("slot_index", 0) for c in seed_rows), default=-1) + 1) if seed_rows else 0

        for comp in seed_rows:
            assembled.append({
                "slot": slot,
                "slot_index": comp.get("slot_index", 0),
                "value": comp.get("value", ""),
                "source": "seed",
            })

        for value in generated_text.get(slot, []):
            if value:
                assembled.append({
                    "slot": slot,
                    "slot_index": next_index,
                    "value": value,
                    "source": "generated_text",
                })
                next_index += 1

    # --- Image slot ---
    if image_urls is not None:
        for i, url in enumerate(image_urls):
            assembled.append({
                "slot": IMAGE_SLOT,
                "slot_index": i,
                "value": url,
                "source": "generated_image",
            })
    else:
        # Preserve seed image slots
        seed_images = sorted(
            [c for c in seed_components if c.get("slot") == IMAGE_SLOT],
            key=lambda c: c.get("slot_index", 0),
        )
        for comp in seed_images:
            assembled.append({
                "slot": IMAGE_SLOT,
                "slot_index": comp.get("slot_index", 0),
                "value": comp.get("value", ""),
                "source": "seed",
            })

    return assembled


def slot_counts(components: list[dict]) -> dict[str, int]:
    """Return how many values exist per slot. Useful for assertions in tests."""
    counts: dict[str, int] = {}
    for comp in components:
        slot = comp.get("slot", "")
        counts[slot] = counts.get(slot, 0) + 1
    return counts

"""
Step 1: extract structured text fields and image URL from ad component rows.

Input:  list of {"slot": str, "slot_index": int, "value": str | None}
Output: {"headline": ..., "primary_text": ..., "description": ..., "image_url": ...}

Only first-slot values are kept for text; all image slots are collected.
Pure function — no I/O, no dependencies.
"""

from __future__ import annotations


def extract_fields(components: list[dict]) -> dict:
    """Return ad fields as a flat dict ready for embedding."""
    text_slots = ("headline", "primary_text", "description")
    fields: dict[str, str] = {}

    # Index by slot_index so slot 0 always wins for text
    for comp in sorted(components, key=lambda c: c.get("slot_index", 0)):
        slot = comp.get("slot", "")
        value = comp.get("value") or ""
        if not value:
            continue

        if slot in text_slots and slot not in fields:
            fields[slot] = value
        elif slot == "image":
            is_url = value.startswith("http")
            existing_is_url = fields.get("image_url", "").startswith("http")
            if "image_url" not in fields or (is_url and not existing_is_url):
                fields["image_url"] = value

    return fields


def text_as_json(fields: dict) -> str:
    """Serialise non-image fields to a compact JSON string for text embedding."""
    import json
    text_fields = {k: v for k, v in fields.items() if k != "image_url"}
    return json.dumps(text_fields, ensure_ascii=False, separators=(",", ":"))

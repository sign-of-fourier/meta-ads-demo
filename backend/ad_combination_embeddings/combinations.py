"""
Pure combinatorics — no I/O, no dependencies outside stdlib.

Takes a list of component dicts (the normalized ad format) and returns
every Cartesian combination of text slot values as a list of dicts,
ready to be serialised and embedded.
"""

from __future__ import annotations

import json
from itertools import product

TEXT_SLOTS = ("headline", "primary_text", "description")


def build_combinations(
    components: list[dict],
    slots: tuple[str, ...] | list[str] = TEXT_SLOTS,
) -> list[dict]:
    """
    Return every Cartesian combination of text slot values.

    Input:  list of {slot, slot_index, value} dicts (dynamic or static ad)
    Output: list of dicts, one per combination, e.g.:
            [
              {"headline": "Wool Socks", "primary_text": "Hand made in Switzerland."},
              {"headline": "Wool Socks", "primary_text": "Premium wool since 1952."},
              {"headline": "Warm Feet",  "primary_text": "Hand made in Switzerland."},
              ...
            ]

    Slots with no values are excluded from combinations rather than causing
    an empty cross-product.  Order within each slot follows slot_index.
    """
    # Collect values per slot in slot_index order, deduplicated while preserving order
    slot_values: dict[str, list[str]] = {}
    for slot in slots:
        seen: set[str] = set()
        ordered: list[str] = []
        for comp in sorted(
            (c for c in components if c.get("slot") == slot),
            key=lambda c: c.get("slot_index", 0),
        ):
            v = (comp.get("value") or "").strip()
            if v and v not in seen:
                seen.add(v)
                ordered.append(v)
        if ordered:
            slot_values[slot] = ordered

    if not slot_values:
        return []

    # Maintain slot order consistent with the `slots` argument
    active = [(s, slot_values[s]) for s in slots if s in slot_values]
    slot_names = [s for s, _ in active]
    value_lists = [v for _, v in active]

    return [dict(zip(slot_names, combo)) for combo in product(*value_lists)]


def combination_key(combo: dict) -> str:
    """
    Deterministic JSON string used as the unique key for a combination.
    Keys are sorted so the same logical combination always produces the same key.
    """
    return json.dumps(combo, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def combination_count(components: list[dict], slots: tuple[str, ...] | list[str] = TEXT_SLOTS) -> int:
    """Return how many combinations a component list will produce, without building them."""
    total = 1
    found_any = False
    for slot in slots:
        values = {
            (c.get("value") or "").strip()
            for c in components
            if c.get("slot") == slot and (c.get("value") or "").strip()
        }
        if values:
            total *= len(values)
            found_any = True
    return total if found_any else 0

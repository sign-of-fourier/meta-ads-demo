"""
Generate N new text variants for a single ad slot using Azure OpenAI.

Input:  slot name + list of existing values from the seed ad
Output: list of new variant strings (length == n_variants)
"""

from __future__ import annotations

import asyncio
import json
import os

from openai import AzureOpenAI

from ad_text_generation.prompts import GENERATE_SLOT_VARIANTS, SLOT_HINTS

TEXT_SLOTS = ("headline", "primary_text", "description")


def _make_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    )


async def generate_slot_variants(
    slot: str,
    existing_values: list[str],
    n_variants: int = 5,
) -> list[str]:
    """
    Generate n_variants new copy strings for one slot.

    existing_values — all current values for this slot from the seed ad
                      (may be one value for a static ad or several for dynamic).
    Returns a list of exactly n_variants strings, or fewer if the model
    underdelivers (caller should handle gracefully).
    """
    client = _make_client()
    deployment = os.getenv("AZURE_TEXT_GEN_DEPLOYMENT", "gpt-4o")

    values_list = "\n".join(f"  - {v}" for v in existing_values if v)
    prompt = GENERATE_SLOT_VARIANTS.format(
        slot=slot,
        slot_hint=SLOT_HINTS.get(slot, ""),
        existing_values_list=values_list or "  (none provided)",
        n_variants=n_variants,
    )

    def _call() -> str:
        completion = client.chat.completions.create(
            model=deployment,
            temperature=0.8,   # some creativity, but not too wild
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"Generate {n_variants} variants for the {slot} slot."},
            ],
            max_tokens=1000,
        )
        return completion.choices[0].message.content

    raw = await asyncio.get_event_loop().run_in_executor(None, _call)
    data = json.loads(raw)
    variants = data.get("variants", [])
    return [str(v) for v in variants if v]


async def generate_all_slots(
    seed_components: list[dict],
    n_per_slot: int = 5,
    slots: list[str] | None = None,
) -> dict[str, list[str]]:
    """
    Generate text variants for all (or selected) text slots in parallel.

    seed_components — list of {slot, slot_index, value} dicts
    slots           — which slots to generate; defaults to all TEXT_SLOTS present in seed
    Returns dict mapping slot → list of new variant strings.
    """
    target_slots = slots or list(TEXT_SLOTS)

    # Collect existing values per slot from seed
    existing: dict[str, list[str]] = {s: [] for s in target_slots}
    for comp in seed_components:
        s = comp.get("slot", "")
        v = comp.get("value") or ""
        if s in existing and v:
            existing[s].append(v)

    # Generate all slots in parallel; skip slots with no seed values only if
    # caller explicitly passed a restricted slot list — otherwise still generate
    tasks = {
        slot: generate_slot_variants(slot, vals, n_per_slot)
        for slot, vals in existing.items()
        if slot in target_slots
    }

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    return {
        slot: (result if not isinstance(result, Exception) else [])
        for slot, result in zip(tasks.keys(), results)
    }

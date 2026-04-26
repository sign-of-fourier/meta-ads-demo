"""
Prompt library for the ad text generation pipeline.

Edit these strings to tune model behaviour without touching pipeline code.
GENERATE_SLOT_VARIANTS uses str.format_map; substitution keys are:
  {slot}, {slot_hint}, {existing_values_list}, {n_variants}
"""

# Slot-specific guidance injected into the prompt
SLOT_HINTS: dict[str, str] = {
    "headline":     "Headlines are short and attention-grabbing, typically under 40 characters.",
    "primary_text": "Primary text is the main body copy, typically 1–3 short sentences.",
    "description":  "Descriptions support the headline, typically under 30 characters.",
    "cta":          "Call-to-action button text, typically 2–4 action words like 'Shop Now', 'Learn More', 'Get Started'. Match the intent of the ad.",
}

# ---------------------------------------------------------------------------
# Main text generation prompt
# Used by: generator.py
# ---------------------------------------------------------------------------

GENERATE_SLOT_VARIANTS = """\
You are writing ad copy variants for a digital advertising creative.

Slot: {slot}
{slot_hint}

Current values for this slot (from the seed ad):
{existing_values_list}

Generate exactly {n_variants} new variants for this slot.

Rules:
- Match the tone, voice, and approximate length of the current values
- Each new variant must be meaningfully different from the others and from the \
current values above
- No explanations, numbering prefixes, or extra text outside the JSON
- Return valid JSON only: {{"variants": ["variant 1", "variant 2", ...]}}
"""

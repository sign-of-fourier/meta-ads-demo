"""
Step 1 — Analyze a seed ad image with GPT-4o and return 10 suggested edits.
"""

from __future__ import annotations

import os

from openai import AzureOpenAI
from pydantic import BaseModel

from ad_generation.prompts import SUGGEST_EDITS


class CreativeAnalysis(BaseModel):
    suggested_changes: list[str]


def _make_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    )


async def analyze_image(
    image_url: str,
    headline: str,
    short_text: str,
) -> list[str]:
    """
    Call GPT-4o with the seed image and ad copy.
    Returns a list of exactly 10 suggested edit strings.
    """
    client = _make_client()
    deployment = os.getenv("AZURE_ANALYSIS_DEPLOYMENT", "gpt-4o")
    prompt = SUGGEST_EDITS.format(headline=headline, short_text=short_text)

    completion = client.beta.chat.completions.parse(
        model=deployment,
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this ad creative and return the structured creative analysis."},
                    {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                ],
            },
        ],
        response_format=CreativeAnalysis,
        max_tokens=2000,
    )

    parsed: CreativeAnalysis = completion.choices[0].message.parsed
    return parsed.suggested_changes

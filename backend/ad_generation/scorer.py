"""
Step 5 — Score a generated ad image using the fine-tuned Azure OpenAI model.
The image must be accessible via a public URL (served by our own /images route).
"""

from __future__ import annotations

import json
import os

from openai import AzureOpenAI
from pydantic import BaseModel

from ad_generation.prompts import SCORE_CREATIVE


class ScoreResult(BaseModel):
    score: float          # 0–1, higher = worse
    severity: str         # "low" | "medium" | "high"
    labels: list[str]     # short issue descriptors


def _make_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    )


async def score_variant(
    image_url: str,
    headline: str,
    short_text: str,
) -> ScoreResult:
    """
    Score an ad creative image via the fine-tuned model.
    image_url must be publicly accessible (e.g. served from our /images route).
    Returns a ScoreResult (lower score = better ad).
    """
    import asyncio

    client = _make_client()
    deployment = os.getenv("AZURE_SCORING_DEPLOYMENT", "gpt-4-04-14")

    user_text = f"Headline: {headline}\nShort text: {short_text}"

    def _call() -> str:
        completion = client.chat.completions.create(
            model=deployment,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SCORE_CREATIVE},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": image_url, "detail": "low"}},
                    ],
                },
            ],
            max_tokens=300,
        )
        return completion.choices[0].message.content

    raw = await asyncio.get_event_loop().run_in_executor(None, _call)
    data = json.loads(raw)
    return ScoreResult(
        score=float(data.get("score", 1.0)),
        severity=data.get("severity", "high"),
        labels=data.get("labels", []),
    )

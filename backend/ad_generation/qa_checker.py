"""
Step 6 — QA artifact detection for generated images.

Calls GPT-4o to check for severe, obvious defects (extra limbs, distorted
products, large gibberish text, etc.). Minor imperfections are intentionally
ignored — the model is prompted to be lenient.

Returns a QAResult indicating whether the image passed and, if not, at most
two short correction strings ready to be fed back to the FLUX model.
"""

from __future__ import annotations

import json
import os

from openai import AzureOpenAI
from pydantic import BaseModel

from ad_generation.prompts import CHECK_ARTIFACTS


class QAResult(BaseModel):
    passed: bool
    corrections: list[str]   # empty when passed=True; max 2 items otherwise


def _make_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    )


async def check_for_artifacts(image_url: str) -> QAResult:
    """
    Inspect a generated image for severe defects.

    image_url must be publicly reachable (e.g. served from our /images route).
    Returns QAResult(passed=True) when no major defects are found.
    """
    import asyncio

    client = _make_client()
    deployment = os.getenv("AZURE_ANALYSIS_DEPLOYMENT", "gpt-4.1-nano")

    def _call() -> str:
        completion = client.chat.completions.create(
            model=deployment,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": CHECK_ARTIFACTS},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_url, "detail": "high"},
                        }
                    ],
                },
            ],
            max_tokens=300,
        )
        return completion.choices[0].message.content

    raw = await asyncio.get_event_loop().run_in_executor(None, _call)
    data = json.loads(raw)
    return QAResult(
        passed=bool(data.get("passed", True)),
        corrections=data.get("corrections", [])[:2],  # hard cap at 2
    )

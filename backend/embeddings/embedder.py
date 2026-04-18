"""
Step 2: embed text and image via Azure AI Inference, then concatenate.

Environment variables:
    AZURE_INFERENCE_KEY   – required
    AZURE_ENDPOINT        – defaults to the resource used in gpr_pipeline.py
    AZURE_IMAGE_MODEL     – defaults to embed-v-4-0
    AZURE_TEXT_MODEL      – defaults to embed-v-4-0 (same multimodal model)

Both clients are async (azure.ai.inference.aio).
All functions return None on failure so the caller can decide what to do.
"""

from __future__ import annotations

import base64
import logging
import os

import httpx
import numpy as np

logger = logging.getLogger(__name__)

AZURE_ENDPOINT = os.getenv(
    "AZURE_ENDPOINT",
    "https://markpshipman-2243-resource.services.ai.azure.com/models",
)
AZURE_IMAGE_MODEL = os.getenv("AZURE_IMAGE_MODEL", "embed-v-4-0")
AZURE_TEXT_MODEL = os.getenv("AZURE_TEXT_MODEL", "embed-v-4-0")


def _key() -> str:
    key = os.getenv("AZURE_INFERENCE_KEY", "")
    if not key:
        raise RuntimeError("AZURE_INFERENCE_KEY is not set")
    return key


async def embed_text(text: str) -> np.ndarray | None:
    """Embed a text string; returns float32 ndarray or None on failure."""
    if not text:
        return None
    try:
        from azure.ai.inference.aio import EmbeddingsClient
        from azure.core.credentials import AzureKeyCredential

        async with EmbeddingsClient(
            endpoint=AZURE_ENDPOINT,
            credential=AzureKeyCredential(_key()),
        ) as client:
            response = await client.embed(input=[text], model=AZURE_TEXT_MODEL)
        return np.array(response.data[0].embedding, dtype=np.float32)
    except Exception:
        logger.warning("text embedding failed", exc_info=True)
        return None


async def embed_image_url(image_url: str) -> np.ndarray | None:
    """Download image from URL and embed; returns float32 ndarray or None on failure."""
    if not image_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.get(image_url)
            r.raise_for_status()

        content_type = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        data_uri = f"data:{content_type};base64," + base64.standard_b64encode(r.content).decode()

        from azure.ai.inference.aio import ImageEmbeddingsClient
        from azure.ai.inference.models import EmbeddingInput
        from azure.core.credentials import AzureKeyCredential

        async with ImageEmbeddingsClient(
            endpoint=AZURE_ENDPOINT,
            credential=AzureKeyCredential(_key()),
        ) as client:
            response = await client.embed(
                input=[EmbeddingInput(image=data_uri)],
                model=AZURE_IMAGE_MODEL,
            )
        return np.array(response.data[0].embedding, dtype=np.float32)
    except Exception:
        logger.warning("image embedding failed for %s", image_url, exc_info=True)
        return None


def concat_embeddings(
    text_vec: np.ndarray | None,
    image_vec: np.ndarray | None,
) -> np.ndarray | None:
    """Concatenate text and image vectors. Returns None if both are None."""
    parts = [v for v in (text_vec, image_vec) if v is not None]
    if not parts:
        return None
    return np.concatenate(parts)

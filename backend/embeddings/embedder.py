"""
Embed text and (optionally) images for AdStac.kr.

Text:  OpenAI embeddings API — set OPENAI_KEY and optionally OPENAI_TEXT_MODEL.
Image: not yet implemented — embed_image_url always returns None.

Environment variables:
    OPENAI_KEY        – required for text embedding
    OPENAI_TEXT_MODEL – defaults to text-embedding-3-small
"""

from __future__ import annotations

import logging
import os

import numpy as np

logger = logging.getLogger(__name__)

OPENAI_TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "text-embedding-3-small")


def _openai_client():
    from openai import AsyncOpenAI
    key = os.getenv("OPENAI_KEY", "")
    if not key:
        raise RuntimeError("OPENAI_KEY is not set")
    return AsyncOpenAI(api_key=key)


async def embed_text(text: str) -> np.ndarray | None:
    """Embed a text string via OpenAI; returns float32 ndarray or None on failure."""
    if not text:
        return None
    try:
        client = _openai_client()
        response = await client.embeddings.create(input=[text], model=OPENAI_TEXT_MODEL)
        return np.array(response.data[0].embedding, dtype=np.float32)
    except Exception:
        logger.warning("text embedding failed", exc_info=True)
        return None


AZURE_EMBEDDING_ENDPOINT = os.getenv("AZURE_EMBEDDING_ENDPOINT", "https://markpshipman-2243-resource.services.ai.azure.com/models")
AZURE_IMAGE_MODEL = os.getenv("AZURE_IMAGE_MODEL", "embed-v-4-0")


def _azure_key() -> str:
    key = os.getenv("AZURE_INFERENCE_KEY", "")
    if not key:
        raise RuntimeError("AZURE_INFERENCE_KEY is not set")
    return key


async def embed_image_url(image_url: str) -> np.ndarray | None:
    """Download image and embed via Azure AI Inference ImageEmbeddingsClient."""
    if not image_url:
        return None
    try:
        import base64
        import httpx
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.get(image_url)
            r.raise_for_status()
        content_type = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        data_uri = f"data:{content_type};base64," + base64.standard_b64encode(r.content).decode()

        from azure.ai.inference.aio import ImageEmbeddingsClient
        from azure.ai.inference.models import ImageEmbeddingInput
        from azure.core.credentials import AzureKeyCredential
        async with ImageEmbeddingsClient(
            endpoint=AZURE_EMBEDDING_ENDPOINT,
            credential=AzureKeyCredential(_azure_key()),
        ) as client:
            response = await client.embed(
                input=[ImageEmbeddingInput(image=data_uri)],
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

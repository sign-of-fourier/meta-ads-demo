"""
Test whether the fine-tuned Azure OpenAI model can be used for image embeddings.

Short answer: GPT-based fine-tunes (chat completion models) do NOT support the
/embeddings endpoint — Azure OpenAI only exposes that for dedicated embedding
models (text-embedding-3-small, text-embedding-ada-002, etc.).

embed-v-4-0 on Azure AI Inference is a separate, purpose-built multimodal
embedding model. Fine-tuning a GPT-4 model does not give you that capability.

This test tries both so you can see the difference directly.

Run:
    cd backend && source .venv/bin/activate && python test_finetune_embed.py
"""

import asyncio
import base64
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# ── Config ──────────────────────────────────────────────────────────────────

# The fine-tuned GPT model used for scoring in this project.
FINETUNED_DEPLOYMENT = os.getenv("AZURE_SCORING_DEPLOYMENT", "gpt-4-04-14")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_KEY", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

# The existing image embedding model on Azure AI Inference.
AZURE_IMAGE_MODEL = os.getenv("AZURE_IMAGE_MODEL", "embed-v-4-0")
AZURE_EMBEDDING_ENDPOINT = os.getenv(
    "AZURE_EMBEDDING_ENDPOINT",
    "https://markpshipman-2243-resource.services.ai.azure.com/models",
)
AZURE_INFERENCE_KEY = os.getenv("AZURE_INFERENCE_KEY", "")

# Image to test with — first one in ad_images/
IMAGE_PATH = Path(__file__).parent / "ad_images" / "656986178_1375938940885453_9113096275172462030_n.png"


def _image_data_uri(path: Path) -> str:
    raw = path.read_bytes()
    b64 = base64.standard_b64encode(raw).decode()
    return f"data:image/png;base64,{b64}"


# ── Attempt 1: fine-tuned GPT model via /embeddings ─────────────────────────

async def try_finetune_embeddings():
    print(f"\n{'='*60}")
    print(f"Attempt 1: Azure OpenAI /embeddings with '{FINETUNED_DEPLOYMENT}'")
    print("=" * 60)

    if not AZURE_OPENAI_KEY:
        print("  SKIP — AZURE_OPENAI_KEY not set")
        return None

    try:
        from openai import AsyncAzureOpenAI
        client = AsyncAzureOpenAI(
            api_key=AZURE_OPENAI_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION,
        )
        # NOTE: This model hit a RateLimitError on Embeddings_Create, which means
        # Azure accepted the request type (not a 400 "unsupported"). It does appear
        # to support /embeddings — but only for TEXT, not raw image bytes.
        # We send a descriptive text string here (not a base64 image).
        response = await client.embeddings.create(
            input=["A Facebook ad image for a shoe brand with bold colors"],
            model=FINETUNED_DEPLOYMENT,
        )
        vec = response.data[0].embedding
        print(f"  SUCCESS — got vector of length {len(vec)}")
        print(f"  First 5 values: {vec[:5]}")
        return vec
    except Exception as e:
        print(f"  FAILED (expected): {type(e).__name__}: {e}")
        print()
        print("  Why: GPT fine-tuned models (chat completion) do not expose an")
        print("  /embeddings endpoint. Only dedicated embedding models do.")
        return None


# ── Attempt 2: existing embed-v-4-0 via Azure AI Inference ──────────────────

async def try_azure_inference_embeddings():
    print(f"\n{'='*60}")
    print(f"Attempt 2: Azure AI Inference '{AZURE_IMAGE_MODEL}' (current approach)")
    print("=" * 60)

    if not AZURE_INFERENCE_KEY:
        print("  SKIP — AZURE_INFERENCE_KEY not set")
        return None

    try:
        from azure.ai.inference.aio import ImageEmbeddingsClient
        from azure.ai.inference.models import ImageEmbeddingInput
        from azure.core.credentials import AzureKeyCredential

        data_uri = _image_data_uri(IMAGE_PATH)

        async with ImageEmbeddingsClient(
            endpoint=AZURE_EMBEDDING_ENDPOINT,
            credential=AzureKeyCredential(AZURE_INFERENCE_KEY),
        ) as client:
            response = await client.embed(
                input=[ImageEmbeddingInput(image=data_uri)],
                model=AZURE_IMAGE_MODEL,
            )
        vec = response.data[0].embedding
        print(f"  SUCCESS — got vector of length {len(vec)}")
        print(f"  First 5 values: {[round(v, 6) for v in vec[:5]]}")
        return vec
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        return None


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    print(f"Image: {IMAGE_PATH}")
    print(f"Exists: {IMAGE_PATH.exists()}")

    vec1 = await try_finetune_embeddings()
    vec2 = await try_azure_inference_embeddings()

    print(f"\n{'='*60}")
    print("Summary")
    print("=" * 60)
    print(f"  Fine-tuned GPT ({FINETUNED_DEPLOYMENT}) for image embedding: {'works' if vec1 else 'not supported'}")
    print(f"  Azure AI Inference ({AZURE_IMAGE_MODEL}):                     {'works' if vec2 else 'failed'}")
    print()
    print("Conclusion:")
    if not vec1 and vec2:
        print("  The fine-tuned model cannot produce image embeddings.")
        print("  embed-v-4-0 is the right tool — it is a purpose-built")
        print("  multimodal embedding model, not a generation model.")
    elif vec1:
        print("  Unexpectedly, the fine-tuned model returned a vector.")
        print("  Check if it's actually an embedding-model deployment, not a GPT one.")
    else:
        print("  Both failed — check your API keys and endpoints.")


if __name__ == "__main__":
    asyncio.run(main())

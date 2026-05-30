"""
Generate ad copy from a plain-English concept using Azure OpenAI.

Two entry points:
  generate_meta_ad_text(concept, final_url)  → dict with headline/primary_text/description/cta_type
  generate_google_rsa_text(concept, final_url) → dict with headlines/descriptions lists
"""

from __future__ import annotations

import json
import os

from openai import AzureOpenAI

_META_SYSTEM = """\
You are a professional digital advertising copywriter.
Generate a complete static Meta (Facebook/Instagram) ad for the concept below.

Concept: {concept}
Destination URL: {final_url}

Return a JSON object with exactly these fields:
  headline     — string, max 40 chars, punchy and attention-grabbing
  primary_text — string, 1-2 sentences of compelling body copy
  description  — string, max 30 chars, short supporting tagline
  cta_type     — one of: SHOP_NOW, LEARN_MORE, SIGN_UP, GET_OFFER, SUBSCRIBE, BOOK_NOW

Respond with valid JSON only — no markdown, no extra keys.\
"""

_GOOGLE_RSA_SYSTEM = """\
You are a professional Google Ads copywriter.
Generate a Responsive Search Ad (RSA) for the concept below.

Concept: {concept}
Destination URL: {final_url}

Return a JSON object with:
  headlines    — array of 8-15 strings, each STRICTLY under 30 characters (count carefully)
  descriptions — array of 3-4 strings, each STRICTLY under 90 characters

Rules:
- Every headline must be ≤ 30 characters including spaces and punctuation.
- Every description must be ≤ 90 characters.
- Vary angles across the list: unique features, benefits, CTAs, urgency signals.
- Do not repeat the same phrase in multiple headlines.

Respond with valid JSON only — no markdown, no extra keys.\
"""


def _client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.environ["AZURE_OPENAI_KEY"],
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
    )


def _deployment() -> str:
    return os.getenv("AZURE_TEXT_GEN_DEPLOYMENT", "gpt-4.1-nano")


def generate_meta_ad_text(concept: str, final_url: str) -> dict:
    """Return {headline, primary_text, description, cta_type}."""
    prompt = _META_SYSTEM.format(concept=concept, final_url=final_url)
    resp = _client().chat.completions.create(
        model=_deployment(),
        temperature=0.8,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Generate the ad copy."},
        ],
        max_tokens=400,
    )
    data = json.loads(resp.choices[0].message.content)
    # Enforce char limits defensively
    return {
        "headline":     str(data.get("headline", concept[:40]))[:40],
        "primary_text": str(data.get("primary_text", concept)),
        "description":  str(data.get("description", ""))[:30],
        "cta_type":     str(data.get("cta_type", "LEARN_MORE")),
    }


def generate_google_rsa_text(concept: str, final_url: str) -> dict:
    """Return {headlines: [...], descriptions: [...]}."""
    prompt = _GOOGLE_RSA_SYSTEM.format(concept=concept, final_url=final_url)
    resp = _client().chat.completions.create(
        model=_deployment(),
        temperature=0.8,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Generate the RSA copy."},
        ],
        max_tokens=800,
    )
    data = json.loads(resp.choices[0].message.content)
    headlines = [str(h)[:30] for h in data.get("headlines", [concept[:30]])]
    descriptions = [str(d)[:90] for d in data.get("descriptions", [concept[:90]])]
    # Enforce RSA slot minimum counts
    if len(headlines) < 3:
        headlines += [concept[:30]] * (3 - len(headlines))
    if len(descriptions) < 2:
        descriptions += [concept[:90]] * (2 - len(descriptions))
    return {"headlines": headlines[:15], "descriptions": descriptions[:4]}

"""
Step 3 — Async poll deAPI until a generation job completes.
Step 4 — Download the result image and save it locally for serving.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path

import requests

DEAPI_STATUS_URL = "https://api.deapi.ai/api/v1/client/request-status"

_DEFAULT_IMAGES_DIR = Path(__file__).parent.parent / "generated_images"


def _images_dir() -> Path:
    override = os.getenv("GENERATED_IMAGES_DIR")
    return Path(override) if override else _DEFAULT_IMAGES_DIR

_DONE_STATUSES = {"done", "COMPLETED", "completed", "success", "SUCCEEDED"}
_FAIL_STATUSES = {"FAILED", "failed", "ERROR", "error"}


def _deapi_key() -> str:
    return os.environ["DEAPI_API_KEY"]


async def poll_until_done(
    request_id: str,
    max_wait: float = 120.0,
    poll_interval: float = 3.0,
) -> dict:
    """
    Async poll until the deAPI job finishes. Returns the full status payload.
    Raises TimeoutError or RuntimeError on failure.
    """
    headers = {
        "Authorization": f"Bearer {_deapi_key()}",
        "Accept": "application/json",
    }
    deadline = time.monotonic() + max_wait

    while time.monotonic() < deadline:
        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: requests.get(
                f"{DEAPI_STATUS_URL}/{request_id}",
                headers=headers,
                timeout=30,
            ),
        )
        resp.raise_for_status()
        data = resp.json()

        status = data.get("data", {}).get("status") or data.get("status")
        if status in _DONE_STATUSES:
            return data
        if status in _FAIL_STATUSES:
            raise RuntimeError(f"deAPI generation failed for {request_id}: {data}")

        await asyncio.sleep(poll_interval)

    raise TimeoutError(f"Timed out after {max_wait}s waiting for deAPI request {request_id}")


def extract_result_url(status_payload: dict) -> str | None:
    """Pull the primary PNG result URL out of a completed status payload."""
    return status_payload.get("data", {}).get("result_url")


def save_image_locally(url: str, images_dir: Path | None = None) -> str:
    """
    Download image from url, save under images_dir with a stable filename.
    Returns the filename (basename only) — the caller constructs the serve URL.
    Filename is derived from a hash of the URL so it's idempotent.
    images_dir defaults to GENERATED_IMAGES_DIR env var or backend/generated_images/.
    """
    if images_dir is None:
        images_dir = _images_dir()
    images_dir.mkdir(parents=True, exist_ok=True)
    filename = hashlib.md5(url.encode()).hexdigest()[:8] + ".png"
    dest = images_dir / filename
    if not dest.exists():
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return filename

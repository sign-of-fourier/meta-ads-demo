"""
ad_generation — async pipeline for AI-driven ad image generation and scoring.

Usage
-----
from ad_generation import create_job, run_generation_job, get_job_status, get_job_variants
import asyncio

# Create a job record
job_id = create_job(
    user_id=1,
    campaign_id="123",
    adset_id="456",
    seed_image_url="https://example.com/seed.png",
    headline="Wool Socks",
    short_text="Hand made in Switzerland.",
)

# Run the pipeline (fire-and-forget from an async context)
asyncio.create_task(run_generation_job(job_id))

# Poll status
status = get_job_status(job_id)        # dict with 'status', 'suggestions', etc.
variants = get_job_variants(job_id)    # list of dicts with scores and local filenames
"""

from ad_generation.pipeline import (
    create_job,
    get_active_variants,
    get_job_status,
    get_job_variants,
    run_generation_job,
)

__all__ = [
    "create_job",
    "run_generation_job",
    "get_job_status",
    "get_job_variants",
    "get_active_variants",
]

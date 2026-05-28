"""
bo_pipeline — GPR-based Bayesian Optimisation for ad combination selection.

For a given seed ad, selects up to 2 text+image combinations to test next:
one via Expected Improvement, one via a fantasy (batch BO) step.

Usage
-----
from bo_pipeline import run_bo, save_bo_run, get_latest_bo_run

picks = run_bo(
    seed_ad_id="act_123_ad_456",
    text_source_id="gen_ad_789",
    user_id=1,
)
# picks: list of up to 2 dicts — combination_key, combination, selection_type, ...

save_bo_run(seed_ad_id, text_source_id, picks)
"""

from bo_pipeline.cross_platform import run_cross_platform_bo
from bo_pipeline.pipeline import run_bo
from bo_pipeline.storage import get_latest_bo_run, save_bo_run

__all__ = ["run_bo", "save_bo_run", "get_latest_bo_run", "run_cross_platform_bo"]

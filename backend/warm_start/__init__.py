"""
warm_start — mini BO loop that seeds scored_observations before real CTR arrives.

Oracle: Qwen2-VL (via MODAL_SCORING_ENDPOINT).  Scores are stored with
        metric='qwen_warm' and are superseded automatically by real CTR
        observations once WARM_START_MIN_CTR_OBS is reached.

Entry point: run_warm_start(seed_ad_id, text_source_id, user_id, db_path)
"""

from warm_start.mini_bo import WARM_START_MIN_CTR_OBS, run_warm_start

__all__ = ["run_warm_start", "WARM_START_MIN_CTR_OBS"]

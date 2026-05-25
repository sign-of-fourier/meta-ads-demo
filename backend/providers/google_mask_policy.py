# providers/google_mask_policy.py
import os


def _flag(name: str, default: bool) -> bool:
    val = os.getenv(name, "").lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


class GoogleMaskPolicy:
    """
    Reads Google masking configuration from environment variables.

    GOOGLE_MASK_MODE=off|selective|full
      off       — no masking; pass through to live provider
      selective — only masks whose individual flags are True apply
      full      — all masks default to True unless individually disabled

    Individual flags (override defaults set by GOOGLE_MASK_MODE):
      GOOGLE_MASK_STATUS=true|false
      GOOGLE_MASK_BUDGETS=true|false
      GOOGLE_MASK_METRICS=true|false
      GOOGLE_MASK_PAUSE_RESUME=true|false

    GOOGLE_METRIC_PROFILE=healthy|stable|weak  (default healthy)
    """

    def __init__(self) -> None:
        mode = os.getenv("GOOGLE_MASK_MODE", "off").lower()
        full = mode == "full"

        self.mask_status = _flag("GOOGLE_MASK_STATUS", full)
        self.mask_budgets = _flag("GOOGLE_MASK_BUDGETS", full)
        self.mask_metrics = _flag("GOOGLE_MASK_METRICS", full)
        self.mask_pause_resume = _flag("GOOGLE_MASK_PAUSE_RESUME", full)

        self.enabled = mode in ("selective", "full") or any([
            self.mask_status,
            self.mask_budgets,
            self.mask_metrics,
            self.mask_pause_resume,
        ])

        profile = os.getenv("GOOGLE_METRIC_PROFILE", "healthy").lower()
        self.metric_profile: str = profile if profile in ("healthy", "stable", "weak") else "healthy"

    def __repr__(self) -> str:
        return (
            f"GoogleMaskPolicy(enabled={self.enabled}, status={self.mask_status}, "
            f"budgets={self.mask_budgets}, metrics={self.mask_metrics}, "
            f"pause_resume={self.mask_pause_resume}, profile={self.metric_profile})"
        )

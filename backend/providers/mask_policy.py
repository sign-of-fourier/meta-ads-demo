# providers/mask_policy.py
import os


def _flag(name: str, default: bool) -> bool:
    val = os.getenv(name, "").lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


class MaskPolicy:
    """
    Reads masking configuration from environment variables.

    MASK_MODE=off|selective|full
      off       — no masking; pass through to live provider
      selective — only masks whose individual flags are True apply
      full      — all masks default to True unless individually disabled

    Individual flags (override defaults set by MASK_MODE):
      MASK_STATUS=true|false
      MASK_BUDGETS=true|false
      MASK_METRICS=true|false
      MASK_PAUSE_RESUME=true|false
      MASK_AD_STATUSES=true|false
      REAL_ASSET_CREATION=true|false   (default always True)

    METRIC_PROFILE=healthy|stable|weak  (default healthy)
    """

    def __init__(self) -> None:
        mode = os.getenv("MASK_MODE", "off").lower()
        full = mode == "full"

        self.mask_status = _flag("MASK_STATUS", full)
        self.mask_budgets = _flag("MASK_BUDGETS", full)
        self.mask_metrics = _flag("MASK_METRICS", full)
        self.mask_pause_resume = _flag("MASK_PAUSE_RESUME", full)
        self.mask_ad_statuses = _flag("MASK_AD_STATUSES", full)
        # Reserved for future use; not yet consulted by any route.
        self.real_asset_creation = _flag("REAL_ASSET_CREATION", True)

        # Masking activates when MASK_MODE is selective/full OR any individual
        # flag is explicitly set to true (so MASK_STATUS=true works without
        # requiring MASK_MODE to be set).
        self.enabled = mode in ("selective", "full") or any([
            self.mask_status,
            self.mask_budgets,
            self.mask_metrics,
            self.mask_pause_resume,
            self.mask_ad_statuses,
        ])

        profile = os.getenv("METRIC_PROFILE", "healthy").lower()
        self.metric_profile: str = profile if profile in ("healthy", "stable", "weak") else "healthy"

    def __repr__(self) -> str:
        return (
            f"MaskPolicy(enabled={self.enabled}, status={self.mask_status}, "
            f"budgets={self.mask_budgets}, metrics={self.mask_metrics}, "
            f"pause_resume={self.mask_pause_resume}, ad_statuses={self.mask_ad_statuses}, "
            f"profile={self.metric_profile})"
        )

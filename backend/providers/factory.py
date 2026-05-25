# providers/factory.py
import os

from .meta_provider import PlatformProvider
from .meta_live import LiveMetaProvider
from .meta_demo import DemoMetaProvider
from .mask_policy import MaskPolicy
from .meta_masking import MaskingMetaProvider

APP_MODE = os.getenv("APP_MODE", "live").lower()


def get_platform_provider(platform: str = "meta") -> PlatformProvider:
    if platform != "meta":
        raise NotImplementedError(f"Platform '{platform}' is not yet supported")

    if APP_MODE == "demo":
        return DemoMetaProvider()

    live = LiveMetaProvider()
    policy = MaskPolicy()
    if policy.enabled:
        return MaskingMetaProvider(live, policy)
    return live


def get_google_provider() -> PlatformProvider:
    google_app_mode = os.getenv("GOOGLE_APP_MODE", "").lower()
    if os.getenv("APP_MODE", "live").lower() == "demo" or google_app_mode == "demo":
        from .google_demo import GoogleDemoProvider
        return GoogleDemoProvider()
    from .google_provider import GooglePlatformProvider
    live = GooglePlatformProvider()
    from .google_mask_policy import GoogleMaskPolicy
    policy = GoogleMaskPolicy()
    if policy.enabled:
        from .google_masking import GoogleMaskingProvider
        return GoogleMaskingProvider(live, policy)
    return live


# Backward-compatible alias
def get_meta_provider() -> PlatformProvider:
    return get_platform_provider(platform="meta")

# providers/factory.py
import os

from .meta_provider import MetaProvider
from .meta_live import LiveMetaProvider
from .meta_demo import DemoMetaProvider
from .mask_policy import MaskPolicy
from .meta_masking import MaskingMetaProvider

APP_MODE = os.getenv("APP_MODE", "live").lower()


def get_meta_provider() -> MetaProvider:
    if APP_MODE == "demo":
        return DemoMetaProvider()

    live = LiveMetaProvider()
    policy = MaskPolicy()
    if policy.enabled:
        return MaskingMetaProvider(live, policy)
    return live


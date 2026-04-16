# providers/factory.py
import os

from .meta_provider import MetaProvider
from .meta_live import LiveMetaProvider
from .meta_demo import DemoMetaProvider

APP_MODE = os.getenv("APP_MODE", "live").lower()

def get_meta_provider() -> MetaProvider:
    if APP_MODE == "demo":
        return DemoMetaProvider()
    return LiveMetaProvider()


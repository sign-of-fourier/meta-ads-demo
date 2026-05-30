"""
ad_factory — standalone ad creation tool.

Generates a complete static ad (Meta or Google) from a concept description,
writes it into the fake_ad_server fixture files, and makes it immediately
visible to the running fake server without a restart.

Usage:
    python -m ad_factory examples/meta_config.json
    python ad_factory/create_ad.py examples/google_config.json
"""

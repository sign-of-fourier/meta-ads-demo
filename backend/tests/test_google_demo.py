"""
Tests for Chunk 10 — Google demo/masking layer.

Coverage:
- GoogleDemoProvider returns expected campaigns and metrics
- GoogleDemoProvider fetch_campaign_structure returns adsets + ads
- GoogleDemoProvider fetch_ads returns all ads
- GoogleDemoProvider pause/resume are no-ops
- GoogleMaskPolicy: off by default
- GoogleMaskPolicy: full mode enables all masks
- GoogleMaskPolicy: individual flag overrides
- GoogleMaskingProvider: masks status
- GoogleMaskingProvider: masks budgets (deterministic)
- GoogleMaskingProvider: masks metrics for low-delivery campaigns
- GoogleMaskingProvider: preserves real metrics above threshold
- GoogleMaskingProvider: pause/resume no-op when masked
- factory.get_google_provider: returns demo provider when APP_MODE=demo
- factory.get_google_provider: returns demo provider when GOOGLE_APP_MODE=demo
- factory.get_google_provider: returns masking provider when policy enabled
- factory.get_google_provider: returns live provider when no mode set
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── GoogleDemoProvider ─────────────────────────────────────────────────────────

class TestGoogleDemoProvider:
    @pytest.fixture()
    def provider(self):
        from providers.google_demo import GoogleDemoProvider
        return GoogleDemoProvider()

    def test_platform_name(self, provider):
        assert provider.platform_name == "google"

    def test_fetch_campaigns_returns_three(self, provider):
        campaigns, metrics, errors = asyncio.run(
            provider.fetch_campaigns_and_insights(None, "", "")
        )
        assert len(campaigns) == 3
        assert errors == 0

    def test_fetch_campaigns_metrics_shape(self, provider):
        campaigns, metrics, _ = asyncio.run(
            provider.fetch_campaigns_and_insights(None, "", "")
        )
        for camp in campaigns:
            cid = camp["id"]
            assert cid in metrics
            m = metrics[cid]
            assert "impressions" in m
            assert "clicks" in m
            assert "spend" in m
            assert "ctr" in m
            assert "cpm" in m
            assert "cpc" in m

    def test_fetch_campaigns_all_active(self, provider):
        campaigns, _, _ = asyncio.run(
            provider.fetch_campaigns_and_insights(None, "", "")
        )
        for c in campaigns:
            assert c["status"] == "ACTIVE"

    def test_fetch_structure_rsa(self, provider):
        adsets, ads = asyncio.run(
            provider.fetch_campaign_structure(None, "", "", "demo_g_camp_rsa")
        )
        assert len(ads) == 1
        assert ads[0]["ad_type"] == "RESPONSIVE_SEARCH_AD"

    def test_fetch_structure_unknown_returns_empty(self, provider):
        adsets, ads = asyncio.run(
            provider.fetch_campaign_structure(None, "", "", "nonexistent_id")
        )
        assert adsets == []
        assert ads == []

    def test_fetch_ads_returns_all(self, provider):
        ads = asyncio.run(provider.fetch_ads(None, "", ""))
        assert len(ads) == 3

    def test_pause_is_noop(self, provider):
        asyncio.run(provider.pause_campaign(None, "", "demo_g_camp_rsa"))

    def test_resume_is_noop(self, provider):
        asyncio.run(provider.resume_campaign(None, "", "demo_g_camp_rsa"))

    def test_normalize_creative_delegates(self, provider):
        ad = {
            "id": "demo_g_ad_rsa",
            "ad_type": "RESPONSIVE_SEARCH_AD",
            "responsive_search_ad": {
                "headlines": [{"text": "H1"}, {"text": "H2"}],
                "descriptions": [{"text": "D1"}],
            },
        }
        creative_type, components = provider.normalize_creative(ad)
        assert creative_type == "rsa"
        assert any(c["slot"] == "headline" for c in components)


# ── GoogleMaskPolicy ───────────────────────────────────────────────────────────

class TestGoogleMaskPolicy:
    def test_default_off(self, monkeypatch):
        for v in ("GOOGLE_MASK_MODE", "GOOGLE_MASK_STATUS", "GOOGLE_MASK_BUDGETS",
                  "GOOGLE_MASK_METRICS", "GOOGLE_MASK_PAUSE_RESUME"):
            monkeypatch.delenv(v, raising=False)
        from providers.google_mask_policy import GoogleMaskPolicy
        p = GoogleMaskPolicy()
        assert not p.enabled
        assert not p.mask_status
        assert not p.mask_budgets
        assert not p.mask_metrics
        assert not p.mask_pause_resume

    def test_full_mode_enables_all(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_MASK_MODE", "full")
        for v in ("GOOGLE_MASK_STATUS", "GOOGLE_MASK_BUDGETS",
                  "GOOGLE_MASK_METRICS", "GOOGLE_MASK_PAUSE_RESUME"):
            monkeypatch.delenv(v, raising=False)
        from providers.google_mask_policy import GoogleMaskPolicy
        p = GoogleMaskPolicy()
        assert p.enabled
        assert p.mask_status
        assert p.mask_budgets
        assert p.mask_metrics
        assert p.mask_pause_resume

    def test_individual_flag_enables_without_mode(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_MASK_MODE", raising=False)
        monkeypatch.setenv("GOOGLE_MASK_METRICS", "true")
        for v in ("GOOGLE_MASK_STATUS", "GOOGLE_MASK_BUDGETS", "GOOGLE_MASK_PAUSE_RESUME"):
            monkeypatch.delenv(v, raising=False)
        from providers.google_mask_policy import GoogleMaskPolicy
        p = GoogleMaskPolicy()
        assert p.enabled
        assert p.mask_metrics
        assert not p.mask_status

    def test_metric_profile_default(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_METRIC_PROFILE", raising=False)
        from providers.google_mask_policy import GoogleMaskPolicy
        p = GoogleMaskPolicy()
        assert p.metric_profile == "healthy"

    def test_metric_profile_weak(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_METRIC_PROFILE", "weak")
        from providers.google_mask_policy import GoogleMaskPolicy
        p = GoogleMaskPolicy()
        assert p.metric_profile == "weak"

    def test_invalid_metric_profile_falls_back(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_METRIC_PROFILE", "bogus")
        from providers.google_mask_policy import GoogleMaskPolicy
        p = GoogleMaskPolicy()
        assert p.metric_profile == "healthy"


# ── GoogleMaskingProvider ──────────────────────────────────────────────────────

class TestGoogleMaskingProvider:
    def _make_provider(self, live_campaigns, live_metrics, **policy_kwargs):
        from providers.google_mask_policy import GoogleMaskPolicy
        from providers.google_masking import GoogleMaskingProvider

        mock_live = MagicMock()
        mock_live.platform_name = "google"
        mock_live.fetch_campaigns_and_insights = AsyncMock(
            return_value=(live_campaigns, live_metrics, 0)
        )
        mock_live.pause_campaign = AsyncMock()
        mock_live.resume_campaign = AsyncMock()

        policy = MagicMock(spec=GoogleMaskPolicy)
        for k, v in policy_kwargs.items():
            setattr(policy, k, v)

        return GoogleMaskingProvider(mock_live, policy), mock_live

    def test_mask_status_forces_active(self):
        camps = [{"id": "c1", "status": "PAUSED", "daily_budget": 5000}]
        provider, _ = self._make_provider(
            camps, {"c1": {"impressions": 10, "clicks": 1, "spend": 0.5}},
            mask_status=True, mask_budgets=False, mask_metrics=False, mask_pause_resume=False,
            metric_profile="healthy",
        )
        result_camps, _, _ = asyncio.run(provider.fetch_campaigns_and_insights(None, "", ""))
        assert result_camps[0]["status"] == "ACTIVE"

    def test_mask_budgets_replaces_budget(self):
        camps = [{"id": "budget_test_id_1", "status": "ACTIVE", "daily_budget": 999}]
        provider, _ = self._make_provider(
            camps, {},
            mask_status=False, mask_budgets=True, mask_metrics=False, mask_pause_resume=False,
            metric_profile="healthy",
        )
        result_camps, _, _ = asyncio.run(provider.fetch_campaigns_and_insights(None, "", ""))
        assert result_camps[0]["daily_budget"] != 999

    def test_mask_metrics_replaces_low_delivery(self):
        camps = [{"id": "low_camp", "status": "ACTIVE", "daily_budget": 5000}]
        low_metrics = {"low_camp": {"impressions": 10, "clicks": 1, "spend": 0.5}}
        provider, _ = self._make_provider(
            camps, low_metrics,
            mask_status=False, mask_budgets=False, mask_metrics=True, mask_pause_resume=False,
            metric_profile="healthy",
        )
        _, result_metrics, _ = asyncio.run(provider.fetch_campaigns_and_insights(None, "", ""))
        assert result_metrics["low_camp"]["impressions"] > 100

    def test_mask_metrics_preserves_healthy(self):
        camps = [{"id": "healthy_camp", "status": "ACTIVE", "daily_budget": 5000}]
        real_metrics = {"healthy_camp": {"impressions": 50_000, "clicks": 900, "spend": 360.0}}
        provider, _ = self._make_provider(
            camps, real_metrics,
            mask_status=False, mask_budgets=False, mask_metrics=True, mask_pause_resume=False,
            metric_profile="healthy",
        )
        _, result_metrics, _ = asyncio.run(provider.fetch_campaigns_and_insights(None, "", ""))
        assert result_metrics["healthy_camp"]["impressions"] == 50_000

    def test_pause_noop_when_masked(self):
        from providers.google_mask_policy import GoogleMaskPolicy
        from providers.google_masking import GoogleMaskingProvider

        mock_live = MagicMock()
        mock_live.pause_campaign = AsyncMock()
        policy = MagicMock(spec=GoogleMaskPolicy)
        policy.mask_pause_resume = True

        p = GoogleMaskingProvider(mock_live, policy)
        asyncio.run(p.pause_campaign(None, "", "c1"))
        mock_live.pause_campaign.assert_not_called()

    def test_pause_delegates_when_unmasked(self):
        from providers.google_mask_policy import GoogleMaskPolicy
        from providers.google_masking import GoogleMaskingProvider

        mock_live = MagicMock()
        mock_live.pause_campaign = AsyncMock()
        policy = MagicMock(spec=GoogleMaskPolicy)
        policy.mask_pause_resume = False

        p = GoogleMaskingProvider(mock_live, policy)
        asyncio.run(p.pause_campaign(None, "tok", "c1"))
        mock_live.pause_campaign.assert_called_once()


# ── factory routing ────────────────────────────────────────────────────────────

class TestGoogleFactory:
    def test_demo_via_app_mode(self, monkeypatch):
        monkeypatch.setenv("APP_MODE", "demo")
        monkeypatch.delenv("GOOGLE_APP_MODE", raising=False)
        monkeypatch.delenv("GOOGLE_MASK_MODE", raising=False)
        import importlib
        import providers.factory as fac
        importlib.reload(fac)
        provider = fac.get_google_provider()
        from providers.google_demo import GoogleDemoProvider
        assert isinstance(provider, GoogleDemoProvider)

    def test_demo_via_google_app_mode(self, monkeypatch):
        monkeypatch.delenv("APP_MODE", raising=False)
        monkeypatch.setenv("GOOGLE_APP_MODE", "demo")
        monkeypatch.delenv("GOOGLE_MASK_MODE", raising=False)
        import importlib
        import providers.factory as fac
        importlib.reload(fac)
        provider = fac.get_google_provider()
        from providers.google_demo import GoogleDemoProvider
        assert isinstance(provider, GoogleDemoProvider)

    def test_masking_provider_when_policy_enabled(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_APP_MODE", "live")
        monkeypatch.setenv("APP_MODE", "live")
        monkeypatch.setenv("GOOGLE_MASK_MODE", "full")
        import importlib
        import providers.factory as fac
        importlib.reload(fac)
        provider = fac.get_google_provider()
        from providers.google_masking import GoogleMaskingProvider
        assert isinstance(provider, GoogleMaskingProvider)

    def test_live_provider_by_default(self, monkeypatch):
        monkeypatch.delenv("APP_MODE", raising=False)
        monkeypatch.delenv("GOOGLE_APP_MODE", raising=False)
        for v in ("GOOGLE_MASK_MODE", "GOOGLE_MASK_STATUS", "GOOGLE_MASK_BUDGETS",
                  "GOOGLE_MASK_METRICS", "GOOGLE_MASK_PAUSE_RESUME"):
            monkeypatch.delenv(v, raising=False)
        import importlib
        import providers.factory as fac
        importlib.reload(fac)
        provider = fac.get_google_provider()
        from providers.google_provider import GooglePlatformProvider
        assert isinstance(provider, GooglePlatformProvider)

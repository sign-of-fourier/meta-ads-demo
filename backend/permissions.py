"""Tier taxonomy and write-access rules for pushing/launching ads on a connected platform.

Six tiers exist. `trial`/`basic`/`premium`/`enterprise` are eventually backed by a real
Stripe subscription object (Stripe supports no-card trial subscriptions, so `trial`
counts too); `free` and `beta` never have a Stripe object — `free` is the base
zero-state, `beta` is the default every new signup gets today (Stripe billing isn't
wired up yet — see TECHNICAL_DEBT.md T11), hand-extendable/closable through the admin
screen. This module doesn't care about tier provenance, only the resulting tier
string, so it stays correct whether a tier was set by a Stripe webhook or an admin call.

Only `premium`/`enterprise` grant write access for now — `basic` is read-only too,
alongside free/trial/beta, until there's an actual paid write-tier offering.
"""

from __future__ import annotations

# Tiers that do NOT authorize writing/launching ads on a connected ad platform.
READ_ONLY_TIERS = frozenset({"free", "trial", "beta", "basic"})

# Tiers that DO authorize push/activate/pause on a connected ad platform.
WRITE_TIERS = frozenset({"premium", "enterprise"})

KNOWN_TIERS = READ_ONLY_TIERS | WRITE_TIERS


def tier_can_write(tier: str) -> bool:
    return tier in WRITE_TIERS


def meta_oauth_scopes(tier: str) -> str:
    """Scope requested at Meta OAuth connect time. Read-only tiers never request
    ads_management, so the consent screen itself reflects what the account can do —
    not just an in-app button being hidden."""
    if tier_can_write(tier):
        return "ads_read,ads_management,business_management"
    return "ads_read,business_management"

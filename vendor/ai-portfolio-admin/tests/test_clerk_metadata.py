from __future__ import annotations

from ai_portfolio_admin.clerk_metadata import (
    ClerkMetadataKeys,
    metadata_with_active_state,
    resolve_active,
    resolve_credit_floor_usd,
    resolve_role,
)


def test_metadata_helpers_resolve_role_active_and_credit_floor() -> None:
    metadata = {"role": "admin", "active": True, "credit_floor_usd": "-2.50"}

    assert resolve_role(metadata) == "admin"
    assert resolve_active(metadata) is True
    assert resolve_credit_floor_usd(metadata) == -2.5


def test_metadata_with_active_state_sets_default_credit_floor_once() -> None:
    updated = metadata_with_active_state({}, active=True, keys=ClerkMetadataKeys(default_credit_floor_usd=-5.0))

    assert updated["active"] is True
    assert updated["credit_floor_usd"] == -5.0
    assert metadata_with_active_state({"credit_floor_usd": -1.25}, active=True)["credit_floor_usd"] == -1.25

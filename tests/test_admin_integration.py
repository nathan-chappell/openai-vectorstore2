from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.admin import build_auth_service, payment_integration_status
from backend.app.core.config import AppSettings
from backend.app.services.auth import AuthService


@pytest.mark.asyncio
async def test_default_admin_integration_uses_local_auth_service(
    configured_settings: AppSettings,
) -> None:
    settings = configured_settings.model_copy(update={"admin_integration_provider": "default"})
    auth = build_auth_service(settings)
    try:
        status = payment_integration_status(settings)
    finally:
        await auth.close()

    assert isinstance(auth, AuthService)
    assert status.provider == "default"
    assert status.checkout_enabled is False
    assert status.reason is not None
    assert "unavailable" in status.reason


def test_shared_admin_integration_missing_module_errors(configured_settings: AppSettings) -> None:
    settings = configured_settings.model_copy(
        update={
            "admin_integration_provider": "ai_portfolio_admin",
            "admin_shared_module": "missing_ai_portfolio_admin.openai_vectorstore2",
        }
    )

    with pytest.raises(RuntimeError, match="private shared admin module"):
        build_auth_service(settings)


@pytest.mark.asyncio
async def test_shared_admin_integration_can_load_private_package_adapter(
    configured_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_src = Path(__file__).resolve().parents[2] / "ai-portfolio-admin" / "src"
    monkeypatch.syspath_prepend(str(shared_src))
    settings = configured_settings.model_copy(
        update={
            "admin_integration_provider": "ai_portfolio_admin",
            "admin_shared_module": "ai_portfolio_admin.openai_vectorstore2",
        }
    )

    auth = build_auth_service(settings)
    try:
        status = payment_integration_status(settings)
    finally:
        await auth.close()

    assert isinstance(auth, AuthService)
    assert status.provider == "ai_portfolio_admin"
    assert status.checkout_enabled is False

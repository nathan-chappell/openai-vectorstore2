from __future__ import annotations

from ai_portfolio_admin.contracts import PaymentIntegrationStatus

from openai_vectorstore2_backend.app.core.config import AppSettings
from openai_vectorstore2_backend.app.services.auth import AuthService


def build_auth_service(settings: AppSettings) -> AuthService:
    return AuthService(settings)


def payment_integration_status(settings: AppSettings) -> PaymentIntegrationStatus:
    del settings
    return PaymentIntegrationStatus(
        provider="ai_portfolio_admin",
        checkout_enabled=False,
        reason="Shared admin submodule is installed; payment checkout is not configured yet.",
    )

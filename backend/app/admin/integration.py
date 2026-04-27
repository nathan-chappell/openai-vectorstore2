from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Callable, cast

from backend.app.core.config import AppSettings
from backend.app.services.auth import AuthService


type AuthServiceFactory = Callable[[AppSettings], object]


@dataclass(frozen=True, slots=True)
class PaymentIntegrationStatus:
    provider: str
    checkout_enabled: bool
    reason: str | None = None


def build_auth_service(settings: AppSettings) -> AuthService:
    """Build the auth/admin implementation for the current deployment.

    The public app defaults to its local Clerk/local-dev implementation. The
    private shared package may expose a compatible ``build_auth_service`` factory
    at ``settings.admin_shared_module``.
    """

    if settings.admin_integration_provider == "default":
        return AuthService(settings)

    factory = _shared_factory(settings, "build_auth_service")
    return cast(AuthService, factory(settings))


def payment_integration_status(settings: AppSettings) -> PaymentIntegrationStatus:
    if settings.admin_integration_provider == "default":
        return PaymentIntegrationStatus(
            provider="default",
            checkout_enabled=False,
            reason="Payment checkout is unavailable in the default public implementation.",
        )
    factory = _shared_factory(settings, "payment_integration_status")
    return cast(PaymentIntegrationStatus, factory(settings))


def _shared_factory(settings: AppSettings, name: str) -> AuthServiceFactory:
    try:
        module = import_module(settings.admin_shared_module)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"ADMIN_INTEGRATION_PROVIDER={settings.admin_integration_provider!r} requires "
            f"the private shared admin module {settings.admin_shared_module!r}."
        ) from exc
    factory = getattr(module, name, None)
    if not callable(factory):
        raise RuntimeError(f"Shared admin module {settings.admin_shared_module!r} does not expose callable {name!r}.")
    return cast(AuthServiceFactory, factory)

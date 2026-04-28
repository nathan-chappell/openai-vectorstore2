from __future__ import annotations

import json
from typing import cast

import httpx
from pydantic import SecretStr, ValidationError
import pytest

from backend.app.core.config import AppSettings
from backend.app.services.auth import AuthService, ClerkUserPayload


@pytest.mark.asyncio
async def test_local_dev_bearer_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("APP_SIGNING_SECRET", "test-secret")
    monkeypatch.delenv("ALLOW_LOCAL_DEV_AUTH", raising=False)
    settings = AppSettings()

    assert settings.allow_local_dev_auth is False

    user = await AuthService(settings).authenticate_bearer("local-dev")

    assert user is None


@pytest.mark.asyncio
async def test_local_dev_bearer_requires_explicit_opt_in(configured_settings: AppSettings) -> None:
    settings = configured_settings.model_copy(update={"allow_local_dev_auth": True})
    auth = AuthService(settings)

    user = await auth.authenticate_bearer("local-dev")

    assert user is not None
    assert user.clerk_user_id == "local-dev"
    assert user.role == "admin"


def test_auth_service_reads_clerk_public_metadata(configured_settings: AppSettings) -> None:
    settings = configured_settings.model_copy()
    auth = AuthService(settings)
    payload = cast(ClerkUserPayload, {
        "id": "user_public",
        "primary_email_address_id": "email_1",
        "email_addresses": [{"id": "email_1", "email_address": "admin@example.com"}],
        "public_metadata": {"active": True, "role": "admin", "credit_floor_usd": "-5.25"},
        "private_metadata": {"active": False, "role": "user", "credit_floor_usd": "0"},
    })

    record = auth._user_record_from_payload(payload, clerk_user_id="user_public")  # pyright: ignore[reportPrivateUsage]

    assert record.active is True
    assert record.role == "admin"
    assert record.credit_floor_usd == -5.25


def test_local_dev_auth_cannot_be_enabled_for_non_local_base_url(configured_settings: AppSettings) -> None:
    with pytest.raises(ValidationError, match="ALLOW_LOCAL_DEV_AUTH"):
        AppSettings.model_validate(
            {
                **configured_settings.model_dump(),
                "allow_local_dev_auth": True,
                "app_base_url": "https://vectorstore.example.com",
            }
        )


@pytest.mark.asyncio
async def test_set_user_active_state_writes_clerk_public_metadata(configured_settings: AppSettings) -> None:
    settings = configured_settings.model_copy(update={"clerk_secret_key": SecretStr("sk_clerk_test")})
    seen_patch_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/users/user_public":
            return httpx.Response(
                200,
                json={
                    "id": "user_public",
                    "primary_email_address_id": "email_1",
                    "email_addresses": [{"id": "email_1", "email_address": "user@example.com"}],
                    "public_metadata": {"role": "user"},
                    "private_metadata": {"active": False, "role": "admin"},
                },
            )
        if request.method == "PATCH" and request.url.path == "/v1/users/user_public":
            payload = json.loads(request.content.decode("utf-8"))
            assert isinstance(payload, dict)
            seen_patch_payloads.append(payload)
            return httpx.Response(
                200,
                json={
                    "id": "user_public",
                    "primary_email_address_id": "email_1",
                    "email_addresses": [{"id": "email_1", "email_address": "user@example.com"}],
                    **payload,
                },
            )
        return httpx.Response(404)

    auth = AuthService(settings)
    await auth.close()
    auth._http_client = httpx.AsyncClient(  # pyright: ignore[reportPrivateUsage]
        base_url="https://api.clerk.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        record = await auth.set_user_active_state(clerk_user_id="user_public", active=True)
    finally:
        await auth.close()

    assert record.active is True
    assert record.role == "user"
    assert record.credit_floor_usd == -1.0
    assert seen_patch_payloads == [
        {"public_metadata": {"role": "user", "active": True, "credit_floor_usd": -1.0}}
    ]

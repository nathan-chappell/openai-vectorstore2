from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Mapping

import httpx
from clerk_backend_api.sdk import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions
from pydantic import BaseModel

from backend.app.core.config import AppSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClerkRequest:
    headers: Mapping[str, str]


class UserRecord(BaseModel):
    clerk_user_id: str
    primary_email: str | None = None
    display_name: str
    active: bool = False
    role: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    clerk_user_id: str
    email: str | None
    display_name: str
    active: bool
    role: str | None
    bearer_token: str


class AuthService:
    """Clerk-backed auth with a local-dev escape hatch for fast iteration."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._http_client: httpx.AsyncClient | None = None
        self._clerk_sdk: Clerk | None = None
        if settings.clerk_secret_key is not None:
            secret = settings.clerk_secret_key.get_secret_value()
            self._http_client = httpx.AsyncClient(
                base_url="https://api.clerk.com",
                headers={
                    "Authorization": f"Bearer {secret}",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
            self._clerk_sdk = Clerk(bearer_auth=secret)

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()

    async def authenticate_bearer(self, token: str | None) -> AuthenticatedUser | None:
        if token == "local-dev" and self._settings.allow_local_dev_auth:
            return AuthenticatedUser(
                clerk_user_id="local-dev",
                email="local-dev@example.com",
                display_name="Local Developer",
                active=True,
                role="admin",
                bearer_token="local-dev",
            )
        if not token or self._http_client is None or self._clerk_sdk is None or self._settings.clerk_secret_key is None:
            return None
        clerk_user_id = await self._verify_session_token(token)
        if clerk_user_id is None:
            clerk_user_id = await self._verify_oauth_token(token)
        if clerk_user_id is None:
            return None
        record = await self.get_user_record(clerk_user_id)
        return AuthenticatedUser(
            clerk_user_id=record.clerk_user_id,
            email=record.primary_email,
            display_name=record.display_name,
            active=record.active,
            role=record.role,
            bearer_token=token,
        )

    async def get_user_record(self, clerk_user_id: str) -> UserRecord:
        if clerk_user_id == "local-dev" and self._settings.allow_local_dev_auth:
            return UserRecord(
                clerk_user_id="local-dev",
                primary_email="local-dev@example.com",
                display_name="Local Developer",
                active=True,
                role="admin",
            )
        if self._http_client is None:
            raise RuntimeError("CLERK_SECRET_KEY is required for non-local users.")
        response = await self._http_client.get(f"/v1/users/{clerk_user_id}")
        response.raise_for_status()
        payload = response.json()
        private_metadata = payload.get("private_metadata") or {}
        raw_role = private_metadata.get(self._settings.clerk_role_metadata_key)
        role = raw_role.strip() if isinstance(raw_role, str) and raw_role.strip() else None
        return UserRecord(
            clerk_user_id=clerk_user_id,
            primary_email=_extract_primary_email(payload),
            display_name=_extract_display_name(payload, clerk_user_id),
            active=bool(private_metadata.get(self._settings.clerk_active_metadata_key)),
            role=role,
        )

    async def _verify_session_token(self, token: str) -> str | None:
        if self._clerk_sdk is None or self._settings.clerk_secret_key is None:
            return None
        try:
            state = await self._clerk_sdk.authenticate_request_async(
                ClerkRequest(headers={"Authorization": f"Bearer {token}"}),
                AuthenticateRequestOptions(
                    secret_key=self._settings.clerk_secret_key.get_secret_value(),
                    authorized_parties=self._settings.clerk_authorized_parties or None,
                    clock_skew_in_ms=self._settings.clerk_clock_skew_ms,
                ),
            )
        except Exception as error:
            logger.debug("clerk_session_token_rejected reason=%s", error)
            return None
        if not state.is_signed_in or state.payload is None:
            return None
        subject = state.payload.get("sub")
        return subject if isinstance(subject, str) and subject.strip() else None

    async def _verify_oauth_token(self, token: str) -> str | None:
        if self._http_client is None:
            return None
        response = await self._http_client.post("/oauth_applications/access_tokens/verify", json={"access_token": token})
        if response.status_code in {400, 401, 404}:
            return None
        response.raise_for_status()
        payload = response.json()
        if payload.get("active") is False or payload.get("revoked") is True or payload.get("expired") is True:
            return None
        subject = payload.get("subject")
        return subject if isinstance(subject, str) and subject.strip() else None


def _extract_primary_email(payload: dict[str, Any]) -> str | None:
    primary_email_id = payload.get("primary_email_address_id")
    email_addresses = payload.get("email_addresses") or []
    for email in email_addresses:
        if isinstance(email, dict) and email.get("id") == primary_email_id and isinstance(email.get("email_address"), str):
            return email["email_address"]
    for email in email_addresses:
        if isinstance(email, dict) and isinstance(email.get("email_address"), str):
            return email["email_address"]
    return None


def _extract_display_name(payload: dict[str, Any], fallback: str) -> str:
    first_name = payload.get("first_name")
    last_name = payload.get("last_name")
    full_name = " ".join(part.strip() for part in [first_name, last_name] if isinstance(part, str) and part.strip())
    if full_name:
        return full_name
    username = payload.get("username")
    if isinstance(username, str) and username.strip():
        return username.strip()
    return _extract_primary_email(payload) or fallback

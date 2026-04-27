from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from typing import Mapping, TypedDict

import httpx
from clerk_backend_api.sdk import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions
from pydantic import BaseModel

from backend.app.core.config import AppSettings

logger = logging.getLogger(__name__)


class ClerkEmailPayload(TypedDict, total=False):
    id: str
    email_address: str


class ClerkUserPayload(TypedDict, total=False):
    id: str
    primary_email_address_id: str
    email_addresses: list[ClerkEmailPayload]
    public_metadata: dict[str, object]
    first_name: str
    last_name: str
    username: str
    image_url: str
    created_at: int
    last_sign_in_at: int


@dataclass(frozen=True, slots=True)
class ClerkRequest:
    headers: Mapping[str, str]


class UserRecord(BaseModel):
    clerk_user_id: str
    primary_email: str | None = None
    display_name: str
    active: bool = False
    role: str | None = None
    credit_floor_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class AdminUserRecord:
    clerk_user_id: str
    primary_email: str | None
    display_name: str
    image_url: str | None
    active: bool
    role: str | None
    credit_floor_usd: float
    created_at_ms: int | None
    last_sign_in_at_ms: int | None


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    clerk_user_id: str
    email: str | None
    display_name: str
    active: bool
    role: str | None
    credit_floor_usd: float
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
                credit_floor_usd=self._settings.billing_default_credit_floor_usd,
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
            credit_floor_usd=record.credit_floor_usd,
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
                credit_floor_usd=self._settings.billing_default_credit_floor_usd,
            )
        if self._http_client is None:
            raise RuntimeError("CLERK_SECRET_KEY is required for non-local users.")
        response = await self._http_client.get(f"/v1/users/{clerk_user_id}")
        response.raise_for_status()
        return self._user_record_from_payload(_clerk_user_payload(response.json()), clerk_user_id=clerk_user_id)

    async def list_user_records(self, *, limit: int, offset: int, query: str | None = None) -> list[AdminUserRecord]:
        if self._settings.allow_local_dev_auth and (self._http_client is None or query in {None, "", "local-dev"}):
            return [
                AdminUserRecord(
                    clerk_user_id="local-dev",
                    primary_email="local-dev@example.com",
                    display_name="Local Developer",
                    image_url=None,
                    active=True,
                    role="admin",
                    credit_floor_usd=self._settings.billing_default_credit_floor_usd,
                    created_at_ms=None,
                    last_sign_in_at_ms=None,
                )
            ][offset : offset + limit]
        if self._http_client is None:
            raise RuntimeError("CLERK_SECRET_KEY is required for Clerk admin operations.")
        params: dict[str, str | int] = {"limit": limit, "offset": offset, "order_by": "-created_at"}
        normalized_query = query.strip() if isinstance(query, str) else ""
        if normalized_query:
            params["query"] = normalized_query
        response = await self._http_client.get("/v1/users", params=params)
        response.raise_for_status()
        return [self._admin_user_record_from_payload(item) for item in _clerk_user_payloads(response.json())]

    async def set_user_active_state(self, *, clerk_user_id: str, active: bool) -> UserRecord:
        if clerk_user_id == "local-dev" and self._settings.allow_local_dev_auth:
            return await self.get_user_record(clerk_user_id)
        if self._http_client is None:
            raise RuntimeError("CLERK_SECRET_KEY is required for Clerk admin operations.")
        current = await self._http_client.get(f"/v1/users/{clerk_user_id}")
        current.raise_for_status()
        payload = _clerk_user_payload(current.json())
        public_metadata = _public_metadata_from_payload(payload)
        public_metadata[self._settings.clerk_active_metadata_key] = active
        if (
            active
            and self._coerce_credit_floor(public_metadata.get(self._settings.clerk_credit_floor_metadata_key)) is None
        ):
            public_metadata[self._settings.clerk_credit_floor_metadata_key] = (
                self._settings.billing_default_credit_floor_usd
            )
        updated = await self._http_client.patch(
            f"/v1/users/{clerk_user_id}",
            json={"public_metadata": public_metadata},
        )
        updated.raise_for_status()
        return self._user_record_from_payload(_clerk_user_payload(updated.json()), clerk_user_id=clerk_user_id)

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
        response = await self._http_client.post(
            "/oauth_applications/access_tokens/verify", json={"access_token": token}
        )
        if response.status_code in {400, 401, 404}:
            return None
        response.raise_for_status()
        payload = response.json()
        if payload.get("active") is False or payload.get("revoked") is True or payload.get("expired") is True:
            return None
        subject = payload.get("subject")
        return subject if isinstance(subject, str) and subject.strip() else None

    def _admin_user_record_from_payload(self, payload: ClerkUserPayload) -> AdminUserRecord:
        clerk_user_id = str(payload.get("id") or "").strip()
        public_metadata = _public_metadata_from_payload(payload)
        return AdminUserRecord(
            clerk_user_id=clerk_user_id,
            primary_email=_extract_primary_email(payload),
            display_name=_extract_display_name(payload, clerk_user_id),
            image_url=payload.get("image_url"),
            active=_metadata_bool(public_metadata, self._settings.clerk_active_metadata_key),
            role=_metadata_str(public_metadata, self._settings.clerk_role_metadata_key),
            credit_floor_usd=self._resolve_credit_floor(public_metadata),
            created_at_ms=_int_or_none(payload.get("created_at")),
            last_sign_in_at_ms=_int_or_none(payload.get("last_sign_in_at")),
        )

    def _user_record_from_payload(self, payload: ClerkUserPayload, *, clerk_user_id: str) -> UserRecord:
        public_metadata = _public_metadata_from_payload(payload)
        return UserRecord(
            clerk_user_id=clerk_user_id,
            primary_email=_extract_primary_email(payload),
            display_name=_extract_display_name(payload, clerk_user_id),
            active=_metadata_bool(public_metadata, self._settings.clerk_active_metadata_key),
            role=_metadata_str(public_metadata, self._settings.clerk_role_metadata_key),
            credit_floor_usd=self._resolve_credit_floor(public_metadata),
        )

    def _resolve_credit_floor(self, metadata: Mapping[str, object]) -> float:
        resolved = self._coerce_credit_floor(metadata.get(self._settings.clerk_credit_floor_metadata_key))
        return resolved if resolved is not None else self._settings.billing_default_credit_floor_usd

    @staticmethod
    def _coerce_credit_floor(raw_value: object) -> float | None:
        if isinstance(raw_value, bool):
            return None
        if isinstance(raw_value, (int, float)):
            value = float(raw_value)
        elif isinstance(raw_value, str):
            normalized_value = raw_value.strip()
            if not normalized_value:
                return None
            try:
                value = float(normalized_value)
            except ValueError:
                return None
        else:
            return None
        if not math.isfinite(value):
            return None
        return round(value, 8)


def _clerk_user_payload(raw_payload: object) -> ClerkUserPayload:
    if not isinstance(raw_payload, dict):
        return {}

    payload: ClerkUserPayload = {}
    for key in (
        "id",
        "primary_email_address_id",
        "first_name",
        "last_name",
        "username",
        "image_url",
    ):
        raw_value = raw_payload.get(key)
        if isinstance(raw_value, str):
            payload[key] = raw_value

    for key in ("created_at", "last_sign_in_at"):
        raw_value = _int_or_none(raw_payload.get(key))
        if raw_value is not None:
            payload[key] = raw_value

    raw_metadata = raw_payload.get("public_metadata")
    if isinstance(raw_metadata, dict):
        payload["public_metadata"] = {key: value for key, value in raw_metadata.items() if isinstance(key, str)}

    raw_email_addresses = raw_payload.get("email_addresses")
    if isinstance(raw_email_addresses, list):
        email_addresses: list[ClerkEmailPayload] = []
        for raw_email in raw_email_addresses:
            if not isinstance(raw_email, dict):
                continue
            email: ClerkEmailPayload = {}
            raw_id = raw_email.get("id")
            raw_address = raw_email.get("email_address")
            if isinstance(raw_id, str):
                email["id"] = raw_id
            if isinstance(raw_address, str):
                email["email_address"] = raw_address
            if email:
                email_addresses.append(email)
        payload["email_addresses"] = email_addresses

    return payload


def _clerk_user_payloads(raw_payload: object) -> list[ClerkUserPayload]:
    raw_items = raw_payload.get("data") if isinstance(raw_payload, dict) else raw_payload
    if not isinstance(raw_items, list):
        return []
    return [_clerk_user_payload(item) for item in raw_items]


def _public_metadata_from_payload(payload: ClerkUserPayload) -> dict[str, object]:
    return dict(payload.get("public_metadata", {}))


def _metadata_str(metadata: Mapping[str, object], key: str) -> str | None:
    raw_value = metadata.get(key)
    return raw_value.strip() if isinstance(raw_value, str) and raw_value.strip() else None


def _metadata_bool(metadata: Mapping[str, object], key: str) -> bool:
    return bool(metadata.get(key))


def _extract_primary_email(payload: ClerkUserPayload) -> str | None:
    primary_email_id = payload.get("primary_email_address_id")
    email_addresses = payload.get("email_addresses", [])
    for email in email_addresses:
        email_address = email.get("email_address")
        if email.get("id") == primary_email_id and isinstance(email_address, str):
            return email_address
    for email in email_addresses:
        email_address = email.get("email_address")
        if isinstance(email_address, str):
            return email_address
    return None


def _extract_display_name(payload: ClerkUserPayload, fallback: str) -> str:
    first_name = payload.get("first_name")
    last_name = payload.get("last_name")
    full_name = " ".join(part.strip() for part in [first_name, last_name] if isinstance(part, str) and part.strip())
    if full_name:
        return full_name
    username = payload.get("username")
    if isinstance(username, str) and username.strip():
        return username.strip()
    return _extract_primary_email(payload) or fallback


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None

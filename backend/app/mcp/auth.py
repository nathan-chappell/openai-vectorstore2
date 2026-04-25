from __future__ import annotations

from typing import Literal

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token

from backend.app.core.config import AppSettings
from backend.app.services import AuthService


class VectorstoreAccessToken(AccessToken):
    subject: str
    token_type: Literal["local_dev", "clerk"]


class VectorstoreTokenVerifier(TokenVerifier):
    """FastMCP token verifier that delegates bearer validation to the app auth service."""

    def __init__(self, *, settings: AppSettings, auth: AuthService) -> None:
        super().__init__(
            base_url=settings.normalized_app_base_url,
            resource_base_url=f"{settings.normalized_app_base_url}/mcp",
            required_scopes=settings.mcp_required_scopes,
        )
        self._settings = settings
        self._auth = auth

    async def verify_token(self, token: str) -> VectorstoreAccessToken | None:
        authenticated = await self._auth.authenticate_bearer(token)
        if authenticated is None:
            return None
        token_type: Literal["local_dev", "clerk"] = "local_dev" if authenticated.clerk_user_id == "local-dev" else "clerk"
        return VectorstoreAccessToken(
            token=token,
            client_id=token_type,
            scopes=list(self._settings.mcp_required_scopes),
            subject=authenticated.clerk_user_id,
            token_type=token_type,
            claims={
                "sub": authenticated.clerk_user_id,
                "email": authenticated.email,
                "role": authenticated.role,
                "active": authenticated.active,
            },
        )


def current_mcp_clerk_user_id() -> str:
    token = get_access_token()
    if isinstance(token, VectorstoreAccessToken):
        return token.subject
    if token is not None:
        subject = token.claims.get("sub")
        if isinstance(subject, str) and subject.strip():
            return subject.strip()
    return "local-dev"

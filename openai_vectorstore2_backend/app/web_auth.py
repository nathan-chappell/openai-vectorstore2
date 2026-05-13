from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from openai_vectorstore2_backend.app.services import AuthenticatedUser, CreditRequiredError

_http_bearer = HTTPBearer(auto_error=False)


async def require_authenticated_web_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_http_bearer),
) -> AuthenticatedUser:
    token = credentials.credentials.strip() if credentials is not None else None
    user = await request.app.state.services.auth.authenticate_bearer(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid bearer token.")
    return user


async def require_active_web_user(
    user: AuthenticatedUser = Depends(require_authenticated_web_user),
) -> AuthenticatedUser:
    if not user.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is signed in but is still pending manual activation.",
        )
    return user


async def require_admin_web_user(
    user: AuthenticatedUser = Depends(require_active_web_user),
) -> AuthenticatedUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return user


async def require_billable_web_user(
    request: Request,
    user: AuthenticatedUser = Depends(require_active_web_user),
) -> AuthenticatedUser:
    try:
        await request.app.state.services.billing.assert_can_start_billable_operation(
            clerk_user_id=user.clerk_user_id,
            role=user.role,
            credit_floor_usd=user.credit_floor_usd,
            operation_kind=f"{request.method} {request.url.path}",
        )
    except CreditRequiredError as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc
    return user

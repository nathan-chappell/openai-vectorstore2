from __future__ import annotations

import httpx
import pytest

from openai_vectorstore2_backend import create_fastapi_app
from openai_vectorstore2_backend.app.core.config import AppSettings
from openai_vectorstore2_backend.app.db.availability import (
    database_unavailable_body,
    is_temporary_database_error,
)
from openai_vectorstore2_backend.app.db.session import DatabaseManager


def test_database_unavailable_body_is_actionable_and_retryable() -> None:
    assert database_unavailable_body("admin@example.com") == {
        "detail": (
            "Database service is temporarily offline. "
            "Please try again later or contact an administrator at admin@example.com."
        ),
        "code": "database_temporarily_offline",
        "retryable": True,
        "administrator_email": "admin@example.com",
    }


@pytest.mark.parametrize(
    "message",
    [
        "connection refused",
        "could not connect to server",
        "server closed the connection unexpectedly",
        "temporary failure in name resolution",
    ],
)
def test_detects_temporary_database_connection_errors(message: str) -> None:
    assert is_temporary_database_error(RuntimeError(message))


@pytest.mark.asyncio
async def test_app_starts_degraded_when_database_is_temporarily_offline(
    configured_settings: AppSettings,
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_openai

    async def unavailable_database(_manager: DatabaseManager) -> None:
        raise RuntimeError("could not connect to server")

    monkeypatch.setattr(DatabaseManager, "ensure_ready", unavailable_database)
    settings = configured_settings.model_copy(
        update={"database_recovery_retry_seconds": 0.01}
    )
    app = create_fastapi_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            health = await client.get("/health")
            ready = await client.get("/ready")
            api_response = await client.get("/api/auth/me")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json()["code"] == "database_temporarily_offline"
    assert api_response.status_code == 503
    assert api_response.json()["administrator_email"] == "nathan.s.chappell@gmail.com"

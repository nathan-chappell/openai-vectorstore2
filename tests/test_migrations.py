from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
import pytest

from backend.app.core.config import AppSettings
from backend.app.db.session import DatabaseManager, postgres_connect_args
from backend.app.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_postgres_schema_search_path_keeps_public_visible() -> None:
    assert postgres_connect_args("openai_vectorstore2", async_driver=True) == {
        "server_settings": {"search_path": "openai_vectorstore2,public"}
    }
    assert postgres_connect_args("openai_vectorstore2", async_driver=False) == {
        "options": "-csearch_path=openai_vectorstore2,public"
    }


def test_postgres_public_schema_search_path_is_not_duplicated() -> None:
    assert postgres_connect_args("public", async_driver=True) == {"server_settings": {"search_path": "public"}}
    assert postgres_connect_args("public", async_driver=False) == {"options": "-csearch_path=public"}


def test_alembic_postgres_version_table_uses_configured_schema() -> None:
    migration_env = (PROJECT_ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "version_table_schema=_version_table_schema(postgres_schema)" in migration_env


def test_alembic_ini_uses_env_resolving_placeholder() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    assert config.get_main_option("sqlalchemy.url") == "driver://user:pass@localhost/dbname"


def test_alembic_head_matches_orm_tables_and_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-check.db"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}", future=True)
    try:
        inspector = inspect(engine)
        actual_tables = set(inspector.get_table_names()) - {"alembic_version"}
        expected_tables = set(Base.metadata.tables)
        assert actual_tables == expected_tables

        for table_name, table in Base.metadata.tables.items():
            actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
            expected_columns = set(table.columns.keys())
            assert actual_columns == expected_columns, table_name
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_database_manager_can_bootstrap_with_alembic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "app.db"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("DATABASE_SCHEMA_MODE", "migrations")
    monkeypatch.setenv("DATABASE_POSTGRES_SCHEMA", "openai_vectorstore2")

    manager = DatabaseManager(AppSettings())
    try:
        await manager.ensure_ready()
    finally:
        await manager.close()

    engine = create_engine(f"sqlite:///{database_path}", future=True)
    try:
        inspector = inspect(engine)
        assert "alembic_version" in inspector.get_table_names()
        assert set(Base.metadata.tables).issubset(set(inspector.get_table_names()))
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_database_manager_retries_database_starting_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database_path = tmp_path / "cold-start.db"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("DATABASE_SCHEMA_MODE", "migrations")
    monkeypatch.setenv("DATABASE_STARTUP_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("DATABASE_STARTUP_RETRY_DELAY_SECONDS", "0")

    manager = DatabaseManager(AppSettings())
    attempts = 0

    def flaky_schema_check() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("the database system is starting up")

    monkeypatch.setattr(manager, "_ensure_postgres_schema", flaky_schema_check)
    monkeypatch.setattr(manager, "_upgrade_to_head", lambda: None)

    try:
        with caplog.at_level(logging.WARNING, logger="backend.app.db.session"):
            await manager.ensure_ready()
    finally:
        await manager.close()

    assert attempts == 2
    assert "database_starting_up_retry" in caplog.text


@pytest.mark.asyncio
async def test_database_manager_does_not_retry_unrelated_database_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "bad-credentials.db"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("DATABASE_SCHEMA_MODE", "migrations")
    monkeypatch.setenv("DATABASE_STARTUP_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("DATABASE_STARTUP_RETRY_DELAY_SECONDS", "0")

    manager = DatabaseManager(AppSettings())
    attempts = 0

    def failed_schema_check() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("password authentication failed")

    monkeypatch.setattr(manager, "_ensure_postgres_schema", failed_schema_check)

    try:
        with pytest.raises(RuntimeError, match="password authentication failed"):
            await manager.ensure_ready()
    finally:
        await manager.close()

    assert attempts == 1

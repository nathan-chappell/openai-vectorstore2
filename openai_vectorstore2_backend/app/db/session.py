from __future__ import annotations

import asyncio
from importlib import resources
import logging
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema

from openai_vectorstore2_backend.app.core.config import PROJECT_ROOT, AppSettings
from openai_vectorstore2_backend.app.db.availability import is_temporary_database_error
from openai_vectorstore2_backend.app.models import Base

logger = logging.getLogger(__name__)

_INITIALIZED_DATABASES: set[str] = set()
_INITIALIZATION_LOCKS: dict[str, asyncio.Lock] = {}


def ensure_database_directory(database_url: str) -> None:
    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() != "sqlite":
        return
    database_name = parsed_url.database
    if database_name is None or database_name in {"", ":memory:"}:
        return
    database_path = Path(database_name)
    if not database_path.is_absolute():
        database_path = (Path.cwd() / database_path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)


def postgres_connect_args(schema_name: str | None, *, async_driver: bool) -> dict[str, Any]:
    if schema_name is None:
        return {}
    search_path = schema_name if schema_name == "public" else f"{schema_name},public"
    if async_driver:
        return {"server_settings": {"search_path": search_path}}
    return {"options": f"-csearch_path={search_path}"}


class AsyncSessionAdapter:
    """Async-shaped wrapper for synchronous SQLite sessions in Python 3.14 local dev."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSessionAdapter:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        if exc_type is not None:
            self._session.rollback()
        self._session.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    async def scalar(self, statement: Any) -> Any:
        return self._session.scalar(statement)

    async def execute(self, statement: Any) -> Any:
        return self._session.execute(statement)

    async def get(self, entity: Any, ident: Any) -> Any:
        return self._session.get(entity, ident)

    async def commit(self) -> None:
        self._session.commit()

    async def flush(self) -> None:
        self._session.flush()

    async def refresh(self, instance: object) -> None:
        self._session.refresh(instance)

    async def delete(self, instance: object) -> None:
        self._session.delete(instance)


class DatabaseManager:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        parsed_url = make_url(settings.normalized_database_url)
        self._use_sync_sqlite = parsed_url.get_backend_name() == "sqlite"

        self._sync_engine: Engine | None = None
        self._sync_session_factory: sessionmaker[Session] | None = None
        self._async_engine: AsyncEngine | None = None
        self._async_session_factory: async_sessionmaker[AsyncSession] | None = None

        if self._use_sync_sqlite:
            self._sync_engine = create_engine(
                settings.sync_database_url,
                future=True,
                pool_pre_ping=True,
                connect_args={"timeout": 30},
            )
            self._sync_session_factory = sessionmaker(self._sync_engine, class_=Session, expire_on_commit=False)
        else:
            parsed_sync_url = make_url(settings.sync_database_url)
            connect_args = (
                postgres_connect_args(settings.database_postgres_schema, async_driver=True)
                if parsed_sync_url.get_backend_name() == "postgresql"
                else {}
            )
            self._async_engine = create_async_engine(
                settings.normalized_database_url,
                future=True,
                pool_pre_ping=True,
                connect_args=connect_args,
            )
            self._async_session_factory = async_sessionmaker(
                self._async_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

    async def ensure_ready(self) -> None:
        database_url = self._settings.normalized_database_url
        database_key = f"{self._settings.normalized_database_url}#{self._settings.database_postgres_schema or ''}"
        if database_key in _INITIALIZED_DATABASES:
            return
        lock = _INITIALIZATION_LOCKS.setdefault(database_key, asyncio.Lock())
        async with lock:
            if database_key in _INITIALIZED_DATABASES:
                return
            ensure_database_directory(database_url)
            attempts = max(1, self._settings.database_startup_retry_attempts)
            retry_delay_seconds = max(0.0, self._settings.database_startup_retry_delay_seconds)
            for attempt in range(1, attempts + 1):
                try:
                    await asyncio.to_thread(self._ensure_postgres_schema)
                    if self._settings.database_schema_mode == "migrations":
                        await asyncio.to_thread(self._upgrade_to_head)
                    elif self._use_sync_sqlite:
                        if self._sync_engine is None:
                            raise RuntimeError("Synchronous SQLite engine is not configured.")
                        with self._sync_engine.begin() as connection:
                            Base.metadata.create_all(connection)
                            self._validate_schema_matches_metadata(connection)
                    else:
                        if self._async_engine is None:
                            raise RuntimeError("Async engine is not configured.")
                        async with self._async_engine.begin() as connection:
                            await connection.run_sync(Base.metadata.create_all)
                            await connection.run_sync(self._validate_schema_matches_metadata)
                except Exception as exc:
                    if attempt >= attempts or not is_temporary_database_error(exc):
                        raise
                    logger.warning(
                        "database_starting_up_retry database=%s attempt=%s attempts=%s delay_seconds=%.1f",
                        _database_log_label(database_url),
                        attempt,
                        attempts,
                        retry_delay_seconds,
                    )
                    await asyncio.sleep(retry_delay_seconds)
                    continue
                _INITIALIZED_DATABASES.add(database_key)
                return

    async def ping(self) -> None:
        if self._use_sync_sqlite:
            sync_engine = self._sync_engine
            if sync_engine is None:
                raise RuntimeError("Synchronous SQLite engine is not configured.")

            def _ping_sync_engine() -> None:
                with sync_engine.connect() as connection:
                    connection.execute(text("SELECT 1"))

            await asyncio.to_thread(_ping_sync_engine)
            return

        if self._async_engine is None:
            raise RuntimeError("Async engine is not configured.")
        async with self._async_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    def _ensure_postgres_schema(self) -> None:
        schema_name = self._settings.database_postgres_schema
        if schema_name is None:
            return
        parsed_url = make_url(self._settings.sync_database_url)
        if parsed_url.get_backend_name() != "postgresql":
            return
        engine = create_engine(self._settings.sync_database_url, future=True, pool_pre_ping=True)
        try:
            with engine.begin() as connection:
                connection.execute(CreateSchema(schema_name, if_not_exists=True))
        finally:
            engine.dispose()

    def _upgrade_to_head(self) -> None:
        migrations_dir: Path | None = None
        for candidate_dir in (PROJECT_ROOT / "openai_vectorstore2_migrations", Path.cwd() / "openai_vectorstore2_migrations"):
            if candidate_dir.exists():
                migrations_dir = candidate_dir
                break
        if migrations_dir is None:
            migrations_dir = Path(str(resources.files("openai_vectorstore2_migrations"))).resolve()

        alembic_ini: Path | None = None
        for candidate_file in (PROJECT_ROOT / "alembic.ini", Path.cwd() / "alembic.ini"):
            if candidate_file.exists():
                alembic_ini = candidate_file
                break

        config = Config(str(alembic_ini)) if alembic_ini is not None else Config()
        config.set_main_option("script_location", str(migrations_dir))
        config.set_main_option("sqlalchemy.url", self._settings.sync_database_url)
        if (
            self._settings.database_postgres_schema is not None
            and make_url(self._settings.sync_database_url).get_backend_name() == "postgresql"
        ):
            config.set_main_option("postgres_schema", self._settings.database_postgres_schema)
        command.upgrade(config, "head")

    def _validate_schema_matches_metadata(self, connection: Connection) -> None:
        inspector = inspect(connection)
        actual_tables = set(inspector.get_table_names()) - {"alembic_version"}
        expected_tables = set(Base.metadata.tables)
        missing_tables = sorted(expected_tables - actual_tables)
        missing_columns: list[str] = []

        for table_name, table in Base.metadata.tables.items():
            if table_name not in actual_tables:
                continue
            actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
            missing = sorted(set(table.columns.keys()) - actual_columns)
            if missing:
                missing_columns.append(f"{table_name}({', '.join(missing)})")

        if not missing_tables and not missing_columns:
            return

        raise RuntimeError(
            "Database schema is missing ORM tables or columns after create_all. "
            "Existing tables are not altered by create_all; set DATABASE_SCHEMA_MODE=migrations "
            "and run Alembic, or reset the SQLite DB. "
            f"missing_tables={missing_tables} missing_columns={missing_columns}"
        )

    def session(self) -> AsyncSession | AsyncSessionAdapter:
        if self._use_sync_sqlite:
            if self._sync_session_factory is None:
                raise RuntimeError("Synchronous SQLite session factory is not configured.")
            return AsyncSessionAdapter(self._sync_session_factory())
        if self._async_session_factory is None:
            raise RuntimeError("Async session factory is not configured.")
        return self._async_session_factory()

    async def close(self) -> None:
        if self._sync_engine is not None:
            self._sync_engine.dispose()
        if self._async_engine is not None:
            await self._async_engine.dispose()

def _database_log_label(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)

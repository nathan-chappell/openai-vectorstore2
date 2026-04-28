from __future__ import annotations

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, make_url, pool
from sqlalchemy.schema import CreateSchema

from backend.app.core.config import AppSettings
from backend.app.db.session import postgres_connect_args
from backend.app.models import Base

config = context.config

if config.config_file_name is not None and not logging.getLogger().handlers:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url and configured_url != "driver://user:pass@localhost/dbname":
        return configured_url
    return AppSettings().sync_database_url


def _postgres_schema() -> str | None:
    if make_url(_database_url()).get_backend_name() != "postgresql":
        return None
    configured_schema = config.get_main_option("postgres_schema")
    if configured_schema and configured_schema.strip():
        return configured_schema.strip()
    return AppSettings().database_postgres_schema


def _version_table_schema(postgres_schema: str | None) -> str | None:
    return postgres_schema


def run_migrations_offline() -> None:
    postgres_schema = _postgres_schema()
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=_version_table_schema(postgres_schema),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    postgres_schema = _postgres_schema()
    if postgres_schema is not None:
        schema_engine = engine_from_config(
            configuration,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        try:
            with schema_engine.begin() as connection:
                connection.execute(CreateSchema(postgres_schema, if_not_exists=True))
        finally:
            schema_engine.dispose()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=postgres_connect_args(postgres_schema, async_driver=False),
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=_version_table_schema(postgres_schema),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

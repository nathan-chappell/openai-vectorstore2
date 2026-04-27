# Migrations

Alembic is the migration baseline for schema changes.

## Modes

`DATABASE_SCHEMA_MODE` controls how `DatabaseManager.ensure_ready()` prepares the schema.

- `migrations`: default mode. Alembic runs `upgrade head` using the configured database URL.
- `create_all`: creates missing tables from current ORM metadata for empty throwaway databases. It does not alter existing tables, so the app validates the resulting schema and fails with a clear drift error if columns are missing.

Use `migrations` for normal local development and deployed environments. Keep `create_all` only for short-lived scratch databases.

## Shared PostgreSQL Services

Do not run PlodAI and OpenAI Vectorstore2 in the same PostgreSQL schema. They
have separate Alembic histories and some same-named app tables with different
columns. Prefer separate databases. If using one physical PostgreSQL service,
set `DATABASE_POSTGRES_SCHEMA=openai_vectorstore2` for this app so migrations
and app queries run in an isolated schema.

## Commands

Upgrade the configured database:

```bash
./.venv/bin/alembic upgrade head
```

Create a new migration after changing ORM models:

```bash
./.venv/bin/alembic revision --autogenerate -m "describe schema change"
```

Then inspect the generated file before committing. Alembic should describe the durable schema change; it should not import live ORM models to create tables dynamically.

## Drift Check

Run:

```bash
./.venv/bin/pytest tests/test_migrations.py
```

The test upgrades a temporary SQLite database to Alembic head and compares migrated tables/columns against `Base.metadata`. If it fails after a model change, add or repair the migration rather than weakening the test.

## Current Baseline

The initial migration is `migrations/versions/20260425_0001_initial_schema.py`. It covers:

- app users and libraries
- legacy tags and source tag links, later collapsed into `source_file.tag_slug`
- source files with one representative tag slug and optional semantic chunks
- generated assets
- app tasks
- ChatKit threads, entries, and attachments

# Migrations

Alembic is the migration baseline for schema changes.

## Modes

`DATABASE_SCHEMA_MODE` controls how `DatabaseManager.ensure_ready()` prepares the schema.

- `create_all`: default local-dev mode. SQLAlchemy creates missing tables from current ORM metadata.
- `migrations`: production-like mode. Alembic runs `upgrade head` using the configured database URL.

Keep `create_all` for fast local iteration unless you are specifically validating migration behavior. Use `migrations` for deployed environments once production schema ownership is needed.

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
- tags and source tag links
- source files and semantic chunks
- generated assets
- app tasks
- ChatKit threads, entries, and attachments

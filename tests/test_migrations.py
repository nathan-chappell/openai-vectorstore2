from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from backend.app.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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

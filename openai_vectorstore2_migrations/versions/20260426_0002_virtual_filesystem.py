"""add virtual filesystem entries

Revision ID: 20260426_0002
Revises: 20260425_0001
Create Date: 2026-04-26 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "20260426_0002"
down_revision: str | None = "20260425_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_file") as batch_op:
        batch_op.drop_constraint("uq_source_file_display_title", type_="unique")
        batch_op.add_column(sa.Column("openai_original_file_purpose", sa.String(length=32), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE source_file
            SET openai_original_file_purpose = CASE
                WHEN source_kind = 'image' THEN 'vision'
                ELSE 'assistants'
            END
            WHERE openai_original_file_id IS NOT NULL
            """
        )
    )

    op.create_table(
        "filesystem_entry",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("library_id", sa.String(length=32), nullable=False),
        sa.Column("parent_id", sa.String(length=32), nullable=True),
        sa.Column("source_file_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("normalized_path", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["user_library.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["filesystem_entry.id"]),
        sa.ForeignKeyConstraint(["source_file_id"], ["source_file.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("library_id", "normalized_path", name="uq_filesystem_entry_library_path"),
        sa.UniqueConstraint("source_file_id"),
    )
    op.create_index(op.f("ix_filesystem_entry_library_id"), "filesystem_entry", ["library_id"], unique=False)
    op.create_index(op.f("ix_filesystem_entry_parent_id"), "filesystem_entry", ["parent_id"], unique=False)
    op.create_index("ix_filesystem_entry_source_file_id", "filesystem_entry", ["source_file_id"], unique=False)
    op.create_index(
        "ix_filesystem_entry_library_parent_kind_name",
        "filesystem_entry",
        ["library_id", "parent_id", "kind", "normalized_name"],
        unique=False,
    )

    _backfill_filesystem_entries()


def downgrade() -> None:
    op.drop_index("ix_filesystem_entry_library_parent_kind_name", table_name="filesystem_entry")
    op.drop_index("ix_filesystem_entry_source_file_id", table_name="filesystem_entry")
    op.drop_index(op.f("ix_filesystem_entry_parent_id"), table_name="filesystem_entry")
    op.drop_index(op.f("ix_filesystem_entry_library_id"), table_name="filesystem_entry")
    op.drop_table("filesystem_entry")

    with op.batch_alter_table("source_file") as batch_op:
        batch_op.drop_column("openai_original_file_purpose")
        batch_op.create_unique_constraint("uq_source_file_display_title", ["library_id", "display_title"])


def _backfill_filesystem_entries() -> None:
    connection = op.get_bind()
    now = datetime.now(UTC)
    library_rows = connection.execute(sa.text("SELECT id FROM user_library")).mappings().all()
    for library_row in library_rows:
        library_id = str(library_row["id"])
        root_id = uuid4().hex
        connection.execute(
            sa.text(
                """
                INSERT INTO filesystem_entry
                    (id, library_id, parent_id, source_file_id, kind, name, normalized_name,
                     path, normalized_path, created_at, updated_at)
                VALUES
                    (:id, :library_id, NULL, NULL, 'folder', '', '', '/', '/', :created_at, :updated_at)
                """
            ),
            {"id": root_id, "library_id": library_id, "created_at": now, "updated_at": now},
        )
        seen_names: set[str] = {""}
        source_rows = (
            connection.execute(
                sa.text(
                    """
                    SELECT id, original_filename, display_title, created_at, updated_at
                    FROM source_file
                    WHERE library_id = :library_id
                    ORDER BY created_at ASC, id ASC
                    """
                ),
                {"library_id": library_id},
            )
            .mappings()
            .all()
        )
        for source_row in source_rows:
            raw_name = str(source_row["original_filename"] or source_row["display_title"] or "Untitled source")
            name = _dedupe_name(_clean_entry_name(raw_name), seen_names)
            seen_names.add(_normalize_entry_name(name))
            path = f"/{name}"
            created_at = source_row["created_at"] or now
            updated_at = source_row["updated_at"] or created_at
            connection.execute(
                sa.text(
                    """
                    INSERT INTO filesystem_entry
                        (id, library_id, parent_id, source_file_id, kind, name, normalized_name,
                         path, normalized_path, created_at, updated_at)
                    VALUES
                        (:id, :library_id, :parent_id, :source_file_id, 'file', :name, :normalized_name,
                         :path, :normalized_path, :created_at, :updated_at)
                    """
                ),
                {
                    "id": uuid4().hex,
                    "library_id": library_id,
                    "parent_id": root_id,
                    "source_file_id": source_row["id"],
                    "name": name,
                    "normalized_name": _normalize_entry_name(name),
                    "path": path,
                    "normalized_path": _normalize_entry_path(path),
                    "created_at": created_at,
                    "updated_at": updated_at,
                },
            )


def _clean_entry_name(value: str) -> str:
    cleaned = value.replace("\\", "/").split("/")[-1].strip()
    if cleaned in {"", ".", ".."}:
        return "Untitled source"
    return cleaned[:255]


def _normalize_entry_name(value: str) -> str:
    return _clean_entry_name(value).casefold()


def _normalize_entry_path(value: str) -> str:
    normalized = "/" + "/".join(part for part in value.replace("\\", "/").split("/") if part)
    return normalized.casefold() if normalized != "" else "/"


def _dedupe_name(base_name: str, seen_names: set[str]) -> str:
    candidate = base_name
    suffix = 2
    while _normalize_entry_name(candidate) in seen_names:
        if "." in base_name and not base_name.startswith("."):
            stem, extension = base_name.rsplit(".", 1)
            candidate = f"{stem} ({suffix}).{extension}"
        else:
            candidate = f"{base_name} ({suffix})"
        suffix += 1
    return candidate

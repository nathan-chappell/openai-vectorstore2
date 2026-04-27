"""collapse source tags to a single source_file tag slug

Revision ID: 20260427_0008
Revises: 20260427_0007
Create Date: 2026-04-27 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0008"
down_revision: str | None = "20260427_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("source_file", sa.Column("tag_slug", sa.String(length=96), nullable=True))
    op.execute(
        """
        UPDATE source_file
        SET tag_slug = (
            SELECT tag.slug
            FROM source_tag_link
            JOIN tag ON tag.id = source_tag_link.tag_id
            WHERE source_tag_link.source_file_id = source_file.id
            ORDER BY tag.name COLLATE NOCASE ASC, tag.slug ASC
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1
            FROM source_tag_link
            WHERE source_tag_link.source_file_id = source_file.id
        )
        """
    )
    op.create_index("ix_source_file_library_tag_slug", "source_file", ["library_id", "tag_slug"], unique=False)
    op.drop_table("source_tag_link")
    op.drop_index(op.f("ix_tag_library_id"), table_name="tag")
    op.drop_table("tag")


def downgrade() -> None:
    op.create_table(
        "tag",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("library_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("slug", sa.String(length=96), nullable=False),
        sa.Column("color", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["user_library.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("library_id", "name", name="uq_tag_library_name"),
        sa.UniqueConstraint("library_id", "slug", name="uq_tag_library_slug"),
    )
    op.create_index(op.f("ix_tag_library_id"), "tag", ["library_id"], unique=False)
    op.create_table(
        "source_tag_link",
        sa.Column("source_file_id", sa.String(length=32), nullable=False),
        sa.Column("tag_id", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["source_file_id"], ["source_file.id"]),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"]),
        sa.PrimaryKeyConstraint("source_file_id", "tag_id"),
    )
    op.drop_index("ix_source_file_library_tag_slug", table_name="source_file")
    op.drop_column("source_file", "tag_slug")

"""add selectable public libraries

Revision ID: 20260428_0010
Revises: 20260427_0009
Create Date: 2026-04-28 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260428_0010"
down_revision: str | None = "20260427_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_library") as batch_op:
        batch_op.drop_constraint("uq_user_library_user_id", type_="unique")
        batch_op.add_column(sa.Column("visibility", sa.String(length=24), nullable=False, server_default="private"))
        batch_op.add_column(sa.Column("slug", sa.String(length=96), nullable=True))

    op.create_index(op.f("ix_user_library_slug"), "user_library", ["slug"], unique=True)
    op.create_index(
        "ix_user_library_visibility_updated_at",
        "user_library",
        ["visibility", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_library_visibility_updated_at", table_name="user_library")
    op.drop_index(op.f("ix_user_library_slug"), table_name="user_library")

    with op.batch_alter_table("user_library") as batch_op:
        batch_op.drop_column("slug")
        batch_op.drop_column("visibility")
        batch_op.create_unique_constraint("uq_user_library_user_id", ["user_id"])

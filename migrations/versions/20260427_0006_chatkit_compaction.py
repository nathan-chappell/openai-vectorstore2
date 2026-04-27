"""add chatkit compaction visibility

Revision ID: 20260427_0006
Revises: 20260427_0005
Create Date: 2026-04-27 15:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0006"
down_revision: str | None = "20260427_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_chat_entry",
        sa.Column("visibility", sa.String(length=24), nullable=False, server_default="active"),
    )
    op.add_column("app_chat_entry", sa.Column("compaction_group_id", sa.String(length=64), nullable=True))
    op.add_column("app_chat_entry", sa.Column("compacted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_app_chat_entry_thread_visibility_sequence",
        "app_chat_entry",
        ["thread_id", "visibility", "sequence"],
        unique=False,
    )
    op.create_index(
        op.f("ix_app_chat_entry_compaction_group_id"),
        "app_chat_entry",
        ["compaction_group_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_chat_entry_compaction_group_id"), table_name="app_chat_entry")
    op.drop_index("ix_app_chat_entry_thread_visibility_sequence", table_name="app_chat_entry")
    op.drop_column("app_chat_entry", "compacted_at")
    op.drop_column("app_chat_entry", "compaction_group_id")
    op.drop_column("app_chat_entry", "visibility")

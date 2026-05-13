"""add source-level vector index fields

Revision ID: 20260426_0004
Revises: 20260426_0003
Create Date: 2026-04-26 22:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260426_0004"
down_revision: str | None = "20260426_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_file") as batch_op:
        batch_op.add_column(sa.Column("openai_vector_file_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("vector_attributes_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch_op.create_unique_constraint("uq_source_file_openai_vector_file_id", ["openai_vector_file_id"])

    with op.batch_alter_table("source_file") as batch_op:
        batch_op.alter_column("vector_attributes_json", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("source_file") as batch_op:
        batch_op.drop_constraint("uq_source_file_openai_vector_file_id", type_="unique")
        batch_op.drop_column("vector_attributes_json")
        batch_op.drop_column("openai_vector_file_id")

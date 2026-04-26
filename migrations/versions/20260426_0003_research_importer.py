"""add research importer records

Revision ID: 20260426_0003
Revises: 20260426_0002
Create Date: 2026-04-26 20:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260426_0003"
down_revision: str | None = "20260426_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_file") as batch_op:
        batch_op.add_column(sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))

    with op.batch_alter_table("source_file") as batch_op:
        batch_op.alter_column("metadata_json", server_default=None)

    op.create_table(
        "research_import_candidate",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("library_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("parent_candidate_id", sa.String(length=32), nullable=True),
        sa.Column("parent_source_file_id", sa.String(length=32), nullable=True),
        sa.Column("linked_source_file_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("normalized_url", sa.String(length=2048), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["user_library.id"]),
        sa.ForeignKeyConstraint(["linked_source_file_id"], ["source_file.id"]),
        sa.ForeignKeyConstraint(["parent_candidate_id"], ["research_import_candidate.id"]),
        sa.ForeignKeyConstraint(["parent_source_file_id"], ["source_file.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["app_task.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_research_import_candidate_library_id"), "research_import_candidate", ["library_id"], unique=False)
    op.create_index(op.f("ix_research_import_candidate_user_id"), "research_import_candidate", ["user_id"], unique=False)
    op.create_index(op.f("ix_research_import_candidate_task_id"), "research_import_candidate", ["task_id"], unique=False)
    op.create_index(
        op.f("ix_research_import_candidate_parent_candidate_id"),
        "research_import_candidate",
        ["parent_candidate_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_research_import_candidate_parent_source_file_id"),
        "research_import_candidate",
        ["parent_source_file_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_research_import_candidate_linked_source_file_id"),
        "research_import_candidate",
        ["linked_source_file_id"],
        unique=False,
    )
    op.create_index(
        "ix_research_candidate_library_status_created",
        "research_import_candidate",
        ["library_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_research_candidate_task_depth",
        "research_import_candidate",
        ["task_id", "depth"],
        unique=False,
    )
    op.create_index(
        "ix_research_candidate_normalized_url",
        "research_import_candidate",
        ["library_id", "normalized_url"],
        unique=False,
    )
    op.create_index(
        "ix_research_candidate_content_hash",
        "research_import_candidate",
        ["library_id", "content_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_research_candidate_content_hash", table_name="research_import_candidate")
    op.drop_index("ix_research_candidate_normalized_url", table_name="research_import_candidate")
    op.drop_index("ix_research_candidate_task_depth", table_name="research_import_candidate")
    op.drop_index("ix_research_candidate_library_status_created", table_name="research_import_candidate")
    op.drop_index(op.f("ix_research_import_candidate_linked_source_file_id"), table_name="research_import_candidate")
    op.drop_index(op.f("ix_research_import_candidate_parent_source_file_id"), table_name="research_import_candidate")
    op.drop_index(op.f("ix_research_import_candidate_parent_candidate_id"), table_name="research_import_candidate")
    op.drop_index(op.f("ix_research_import_candidate_task_id"), table_name="research_import_candidate")
    op.drop_index(op.f("ix_research_import_candidate_user_id"), table_name="research_import_candidate")
    op.drop_index(op.f("ix_research_import_candidate_library_id"), table_name="research_import_candidate")
    op.drop_table("research_import_candidate")
    with op.batch_alter_table("source_file") as batch_op:
        batch_op.drop_column("metadata_json")

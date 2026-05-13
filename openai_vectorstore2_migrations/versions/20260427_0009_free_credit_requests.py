"""add free credit requests

Revision ID: 20260427_0009
Revises: 20260427_0008
Create Date: 2026-04-27 22:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0009"
down_revision: str | None = "20260427_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "free_credit_requests",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("clerk_user_id", sa.String(length=128), nullable=False),
        sa.Column("requested_amount_usd", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=48), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("linkedin_profile_url", sa.String(length=2048), nullable=True),
        sa.Column("relationship_note", sa.Text(), nullable=True),
        sa.Column("intended_use", sa.Text(), nullable=True),
        sa.Column("evidence_verified", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("decided_amount_usd", sa.Float(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("reviewer_clerk_user_id", sa.String(length=128), nullable=True),
        sa.Column("credit_grant_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_free_credit_requests_clerk_user_id"), "free_credit_requests", ["clerk_user_id"], unique=False)
    op.create_index(op.f("ix_free_credit_requests_reviewer_clerk_user_id"), "free_credit_requests", ["reviewer_clerk_user_id"], unique=False)
    op.create_index(op.f("ix_free_credit_requests_credit_grant_id"), "free_credit_requests", ["credit_grant_id"], unique=False)
    op.create_index("ix_free_credit_requests_user_created_at", "free_credit_requests", ["clerk_user_id", "created_at"], unique=False)
    op.create_index("ix_free_credit_requests_status_created_at", "free_credit_requests", ["status", "created_at"], unique=False)
    op.create_index("ix_free_credit_requests_idempotency_key", "free_credit_requests", ["idempotency_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_free_credit_requests_idempotency_key", table_name="free_credit_requests")
    op.drop_index("ix_free_credit_requests_status_created_at", table_name="free_credit_requests")
    op.drop_index("ix_free_credit_requests_user_created_at", table_name="free_credit_requests")
    op.drop_index(op.f("ix_free_credit_requests_credit_grant_id"), table_name="free_credit_requests")
    op.drop_index(op.f("ix_free_credit_requests_reviewer_clerk_user_id"), table_name="free_credit_requests")
    op.drop_index(op.f("ix_free_credit_requests_clerk_user_id"), table_name="free_credit_requests")
    op.drop_table("free_credit_requests")

"""add payment attempts

Revision ID: 20260427_0007
Revises: 20260427_0006
Create Date: 2026-04-27 17:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0007"
down_revision: str | None = "20260427_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("clerk_user_id", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("expected_amount_usd", sa.Float(), nullable=False),
        sa.Column("expected_currency", sa.String(length=3), nullable=False),
        sa.Column("reference_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("temporary_access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_reference", sa.String(length=255), nullable=True),
        sa.Column("credit_grant_id", sa.String(length=32), nullable=True),
        sa.Column("receipt_filename", sa.String(length=255), nullable=True),
        sa.Column("receipt_media_type", sa.String(length=128), nullable=True),
        sa.Column("receipt_text_excerpt", sa.Text(), nullable=True),
        sa.Column("review_json", sa.JSON(), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_code", name="uq_payment_attempts_reference_code"),
    )
    op.create_index(op.f("ix_payment_attempts_clerk_user_id"), "payment_attempts", ["clerk_user_id"], unique=False)
    op.create_index(op.f("ix_payment_attempts_provider_reference"), "payment_attempts", ["provider_reference"], unique=False)
    op.create_index(op.f("ix_payment_attempts_credit_grant_id"), "payment_attempts", ["credit_grant_id"], unique=False)
    op.create_index("ix_payment_attempts_user_created_at", "payment_attempts", ["clerk_user_id", "created_at"], unique=False)
    op.create_index("ix_payment_attempts_status_created_at", "payment_attempts", ["status", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_payment_attempts_status_created_at", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_user_created_at", table_name="payment_attempts")
    op.drop_index(op.f("ix_payment_attempts_credit_grant_id"), table_name="payment_attempts")
    op.drop_index(op.f("ix_payment_attempts_provider_reference"), table_name="payment_attempts")
    op.drop_index(op.f("ix_payment_attempts_clerk_user_id"), table_name="payment_attempts")
    op.drop_table("payment_attempts")

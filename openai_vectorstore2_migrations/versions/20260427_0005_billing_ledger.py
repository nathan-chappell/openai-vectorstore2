"""add billing ledger records

Revision ID: 20260427_0005
Revises: 20260426_0004
Create Date: 2026-04-27 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260427_0005"
down_revision: str | None = "20260426_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_credit_balances",
        sa.Column("clerk_user_id", sa.String(length=128), nullable=False),
        sa.Column("current_credit_usd", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("clerk_user_id"),
    )

    op.create_table(
        "credit_grants",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("clerk_user_id", sa.String(length=128), nullable=False),
        sa.Column("admin_clerk_user_id", sa.String(length=128), nullable=True),
        sa.Column("credit_amount_usd", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("payment_provider", sa.String(length=32), nullable=True),
        sa.Column("payment_reference", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_credit_grants_clerk_user_id"), "credit_grants", ["clerk_user_id"], unique=False)
    op.create_index(op.f("ix_credit_grants_admin_clerk_user_id"), "credit_grants", ["admin_clerk_user_id"], unique=False)
    op.create_index(op.f("ix_credit_grants_payment_reference"), "credit_grants", ["payment_reference"], unique=False)
    op.create_index("ix_credit_grants_user_created_at", "credit_grants", ["clerk_user_id", "created_at"], unique=False)

    op.create_table(
        "cost_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=True),
        sa.Column("clerk_user_id", sa.String(length=128), nullable=False),
        sa.Column("operation_kind", sa.String(length=80), nullable=False),
        sa.Column("origin_surface", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("source_file_id", sa.String(length=32), nullable=True),
        sa.Column("openai_response_id", sa.String(length=128), nullable=True),
        sa.Column("openai_conversation_id", sa.String(length=128), nullable=True),
        sa.Column("openai_request_id", sa.String(length=128), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("pricing_version", sa.String(length=80), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("raw_usage_json", sa.JSON(), nullable=False),
        sa.Column("openai_cost_usd", sa.Float(), nullable=False),
        sa.Column("platform_multiplier", sa.Float(), nullable=False),
        sa.Column("platform_cost_usd", sa.Float(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_cost_events_event_key"),
    )
    op.create_index(op.f("ix_cost_events_clerk_user_id"), "cost_events", ["clerk_user_id"], unique=False)
    op.create_index(op.f("ix_cost_events_thread_id"), "cost_events", ["thread_id"], unique=False)
    op.create_index(op.f("ix_cost_events_task_id"), "cost_events", ["task_id"], unique=False)
    op.create_index(op.f("ix_cost_events_source_file_id"), "cost_events", ["source_file_id"], unique=False)
    op.create_index(op.f("ix_cost_events_openai_response_id"), "cost_events", ["openai_response_id"], unique=False)
    op.create_index(
        op.f("ix_cost_events_openai_conversation_id"),
        "cost_events",
        ["openai_conversation_id"],
        unique=False,
    )
    op.create_index("ix_cost_events_user_created_at", "cost_events", ["clerk_user_id", "created_at"], unique=False)
    op.create_index("ix_cost_events_thread_created_at", "cost_events", ["thread_id", "created_at"], unique=False)
    op.create_index("ix_cost_events_task_created_at", "cost_events", ["task_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cost_events_task_created_at", table_name="cost_events")
    op.drop_index("ix_cost_events_thread_created_at", table_name="cost_events")
    op.drop_index("ix_cost_events_user_created_at", table_name="cost_events")
    op.drop_index(op.f("ix_cost_events_openai_conversation_id"), table_name="cost_events")
    op.drop_index(op.f("ix_cost_events_openai_response_id"), table_name="cost_events")
    op.drop_index(op.f("ix_cost_events_source_file_id"), table_name="cost_events")
    op.drop_index(op.f("ix_cost_events_task_id"), table_name="cost_events")
    op.drop_index(op.f("ix_cost_events_thread_id"), table_name="cost_events")
    op.drop_index(op.f("ix_cost_events_clerk_user_id"), table_name="cost_events")
    op.drop_table("cost_events")

    op.drop_index("ix_credit_grants_user_created_at", table_name="credit_grants")
    op.drop_index(op.f("ix_credit_grants_payment_reference"), table_name="credit_grants")
    op.drop_index(op.f("ix_credit_grants_admin_clerk_user_id"), table_name="credit_grants")
    op.drop_index(op.f("ix_credit_grants_clerk_user_id"), table_name="credit_grants")
    op.drop_table("credit_grants")

    op.drop_table("user_credit_balances")

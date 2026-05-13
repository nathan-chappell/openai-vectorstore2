"""align shared billing column names

Revision ID: 20260428_0011
Revises: 20260428_0010
Create Date: 2026-04-28 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260428_0011"
down_revision: str | None = "20260428_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    inspector = inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    if index_name in _indexes(table_name):
        op.drop_index(index_name, table_name=table_name)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if index_name in _indexes(table_name):
        return
    available_columns = _columns(table_name)
    if any(column not in available_columns for column in columns):
        return
    op.create_index(index_name, table_name, columns, unique=unique)


def _rename_column_if_needed(
    table_name: str,
    old_name: str,
    new_name: str,
    column_type: sa.TypeEngine,
    *,
    nullable: bool,
) -> None:
    columns = _columns(table_name)
    if old_name not in columns or new_name in columns:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.alter_column(old_name, new_column_name=new_name, existing_type=column_type, existing_nullable=nullable)


def _alter_column_type(table_name: str, column_name: str, old_type: sa.TypeEngine, new_type: sa.TypeEngine) -> None:
    if column_name not in _columns(table_name):
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.alter_column(column_name, existing_type=old_type, type_=new_type)


def upgrade() -> None:
    _drop_index_if_exists("ix_credit_grants_clerk_user_id", "credit_grants")
    _drop_index_if_exists("ix_credit_grants_admin_clerk_user_id", "credit_grants")
    _drop_index_if_exists("ix_credit_grants_user_created_at", "credit_grants")
    _rename_column_if_needed("user_credit_balances", "clerk_user_id", "user_id", sa.String(length=128), nullable=False)
    _rename_column_if_needed("credit_grants", "clerk_user_id", "user_id", sa.String(length=128), nullable=False)
    _rename_column_if_needed(
        "credit_grants",
        "admin_clerk_user_id",
        "admin_user_id",
        sa.String(length=128),
        nullable=True,
    )
    _create_index_if_missing(op.f("ix_credit_grants_user_id"), "credit_grants", ["user_id"])
    _create_index_if_missing(op.f("ix_credit_grants_admin_user_id"), "credit_grants", ["admin_user_id"])
    _create_index_if_missing("ix_credit_grants_user_created_at", "credit_grants", ["user_id", "created_at"])
    _alter_column_type("credit_grants", "id", sa.String(length=32), sa.Text())

    _drop_index_if_exists("ix_payment_attempts_clerk_user_id", "payment_attempts")
    _drop_index_if_exists("ix_payment_attempts_user_created_at", "payment_attempts")
    _rename_column_if_needed("payment_attempts", "clerk_user_id", "user_id", sa.String(length=128), nullable=False)
    _create_index_if_missing(op.f("ix_payment_attempts_user_id"), "payment_attempts", ["user_id"])
    _create_index_if_missing("ix_payment_attempts_user_created_at", "payment_attempts", ["user_id", "created_at"])
    _alter_column_type("payment_attempts", "id", sa.String(length=32), sa.Text())
    _alter_column_type("payment_attempts", "credit_grant_id", sa.String(length=32), sa.Text())

    _drop_index_if_exists("ix_free_credit_requests_clerk_user_id", "free_credit_requests")
    _drop_index_if_exists("ix_free_credit_requests_reviewer_clerk_user_id", "free_credit_requests")
    _drop_index_if_exists("ix_free_credit_requests_user_created_at", "free_credit_requests")
    _rename_column_if_needed("free_credit_requests", "clerk_user_id", "user_id", sa.String(length=128), nullable=False)
    _rename_column_if_needed(
        "free_credit_requests",
        "reviewer_clerk_user_id",
        "reviewer_user_id",
        sa.String(length=128),
        nullable=True,
    )
    _create_index_if_missing(op.f("ix_free_credit_requests_user_id"), "free_credit_requests", ["user_id"])
    _create_index_if_missing(
        op.f("ix_free_credit_requests_reviewer_user_id"),
        "free_credit_requests",
        ["reviewer_user_id"],
    )
    _create_index_if_missing("ix_free_credit_requests_user_created_at", "free_credit_requests", ["user_id", "created_at"])
    _alter_column_type("free_credit_requests", "id", sa.String(length=32), sa.Text())
    _alter_column_type("free_credit_requests", "credit_grant_id", sa.String(length=32), sa.Text())


def downgrade() -> None:
    _drop_index_if_exists("ix_free_credit_requests_user_id", "free_credit_requests")
    _drop_index_if_exists("ix_free_credit_requests_reviewer_user_id", "free_credit_requests")
    _drop_index_if_exists("ix_free_credit_requests_user_created_at", "free_credit_requests")
    _rename_column_if_needed("free_credit_requests", "user_id", "clerk_user_id", sa.Text(), nullable=False)
    _rename_column_if_needed("free_credit_requests", "reviewer_user_id", "reviewer_clerk_user_id", sa.Text(), nullable=True)
    _create_index_if_missing(op.f("ix_free_credit_requests_clerk_user_id"), "free_credit_requests", ["clerk_user_id"])
    _create_index_if_missing(
        op.f("ix_free_credit_requests_reviewer_clerk_user_id"),
        "free_credit_requests",
        ["reviewer_clerk_user_id"],
    )
    _create_index_if_missing(
        "ix_free_credit_requests_user_created_at",
        "free_credit_requests",
        ["clerk_user_id", "created_at"],
    )
    _alter_column_type("free_credit_requests", "id", sa.Text(), sa.String(length=32))
    _alter_column_type("free_credit_requests", "credit_grant_id", sa.Text(), sa.String(length=32))

    _drop_index_if_exists("ix_payment_attempts_user_id", "payment_attempts")
    _drop_index_if_exists("ix_payment_attempts_user_created_at", "payment_attempts")
    _rename_column_if_needed("payment_attempts", "user_id", "clerk_user_id", sa.Text(), nullable=False)
    _create_index_if_missing(op.f("ix_payment_attempts_clerk_user_id"), "payment_attempts", ["clerk_user_id"])
    _create_index_if_missing("ix_payment_attempts_user_created_at", "payment_attempts", ["clerk_user_id", "created_at"])
    _alter_column_type("payment_attempts", "id", sa.Text(), sa.String(length=32))
    _alter_column_type("payment_attempts", "credit_grant_id", sa.Text(), sa.String(length=32))

    _drop_index_if_exists("ix_credit_grants_user_id", "credit_grants")
    _drop_index_if_exists("ix_credit_grants_admin_user_id", "credit_grants")
    _drop_index_if_exists("ix_credit_grants_user_created_at", "credit_grants")
    _rename_column_if_needed("credit_grants", "user_id", "clerk_user_id", sa.Text(), nullable=False)
    _rename_column_if_needed("credit_grants", "admin_user_id", "admin_clerk_user_id", sa.Text(), nullable=True)
    _rename_column_if_needed("user_credit_balances", "user_id", "clerk_user_id", sa.Text(), nullable=False)
    _create_index_if_missing(op.f("ix_credit_grants_clerk_user_id"), "credit_grants", ["clerk_user_id"])
    _create_index_if_missing(op.f("ix_credit_grants_admin_clerk_user_id"), "credit_grants", ["admin_clerk_user_id"])
    _create_index_if_missing("ix_credit_grants_user_created_at", "credit_grants", ["clerk_user_id", "created_at"])
    _alter_column_type("credit_grants", "id", sa.Text(), sa.String(length=32))

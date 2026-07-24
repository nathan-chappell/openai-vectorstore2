from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, Text, func
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column


def new_text_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class DataclassUserCreditBalanceMixin(MappedAsDataclass):
    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    current_credit_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), init=False, default_factory=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        init=False,
        default_factory=utcnow,
        onupdate=utcnow,
    )


class DataclassCreditGrantMixin(MappedAsDataclass):
    id: Mapped[str] = mapped_column(Text, primary_key=True, default_factory=new_text_id, kw_only=True)
    user_id: Mapped[str] = mapped_column(Text, index=True)
    admin_user_id: Mapped[str | None] = mapped_column(Text, index=True, default=None, kw_only=True)
    credit_amount_usd: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(Text, default="admin_manual", kw_only=True)
    note: Mapped[str | None] = mapped_column(Text, default=None, kw_only=True)
    payment_provider: Mapped[str | None] = mapped_column(Text, default=None, kw_only=True)
    payment_reference: Mapped[str | None] = mapped_column(Text, index=True, default=None, kw_only=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), init=False, default_factory=utcnow)


class DataclassPaymentAttemptMixin(MappedAsDataclass):
    id: Mapped[str] = mapped_column(Text, primary_key=True, default_factory=new_text_id, kw_only=True)
    user_id: Mapped[str] = mapped_column(Text, index=True)
    expected_amount_usd: Mapped[float] = mapped_column(Float)
    reference_code: Mapped[str] = mapped_column(Text, unique=True)
    expected_currency: Mapped[str] = mapped_column(Text, default="USD", kw_only=True)
    provider: Mapped[str] = mapped_column(Text, default="paypal", kw_only=True)
    status: Mapped[str] = mapped_column(Text, default="pending_payment", index=True, kw_only=True)
    temporary_access_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        kw_only=True,
    )
    provider_reference: Mapped[str | None] = mapped_column(Text, index=True, default=None, kw_only=True)
    credit_grant_id: Mapped[str | None] = mapped_column(Text, index=True, default=None, kw_only=True)
    receipt_filename: Mapped[str | None] = mapped_column(Text, default=None, kw_only=True)
    receipt_media_type: Mapped[str | None] = mapped_column(Text, default=None, kw_only=True)
    receipt_text_excerpt: Mapped[str | None] = mapped_column(Text, default=None, kw_only=True)
    review_json: Mapped[dict[str, object]] = mapped_column(JSON, default_factory=dict, kw_only=True)
    decision_note: Mapped[str | None] = mapped_column(Text, default=None, kw_only=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), init=False, default_factory=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        init=False,
        default_factory=utcnow,
        onupdate=utcnow,
    )

    @property
    def review_payload(self) -> dict[str, object]:
        return dict(self.review_json or {})

    @review_payload.setter
    def review_payload(self, value: dict[str, object]) -> None:
        self.review_json = dict(value)


class DataclassFreeCreditRequestMixin(MappedAsDataclass):
    id: Mapped[str] = mapped_column(Text, primary_key=True, default_factory=new_text_id, kw_only=True)
    user_id: Mapped[str] = mapped_column(Text, index=True)
    requested_amount_usd: Mapped[float | None] = mapped_column(Float, default=None, kw_only=True)
    source: Mapped[str] = mapped_column(Text, default="general", index=True, kw_only=True)
    reason: Mapped[str] = mapped_column(Text)
    linkedin_profile_url: Mapped[str | None] = mapped_column(Text, default=None, kw_only=True)
    relationship_note: Mapped[str | None] = mapped_column(Text, default=None, kw_only=True)
    intended_use: Mapped[str | None] = mapped_column(Text, default=None, kw_only=True)
    evidence_verified: Mapped[bool] = mapped_column(default=False, kw_only=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, index=True, default=None, kw_only=True)
    status: Mapped[str] = mapped_column(Text, default="pending", index=True, kw_only=True)
    decided_amount_usd: Mapped[float | None] = mapped_column(Float, default=None, kw_only=True)
    decision_note: Mapped[str | None] = mapped_column(Text, default=None, kw_only=True)
    reviewer_user_id: Mapped[str | None] = mapped_column(Text, index=True, default=None, kw_only=True)
    credit_grant_id: Mapped[str | None] = mapped_column(Text, index=True, default=None, kw_only=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), init=False, default_factory=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        init=False,
        default_factory=utcnow,
        onupdate=utcnow,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None, kw_only=True)


class UserCreditBalanceMixin:
    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    current_credit_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CreditGrantMixin:
    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_text_id)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    admin_user_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    credit_amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="admin_manual")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PaymentAttemptMixin:
    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_text_id)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="paypal")
    expected_amount_usd: Mapped[float] = mapped_column(Float, nullable=False)
    expected_currency: Mapped[str] = mapped_column(Text, nullable=False, default="USD")
    reference_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending_payment", index=True)
    temporary_access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    credit_grant_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    receipt_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_media_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_text_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    @property
    def review_payload(self) -> dict[str, object]:
        return dict(self.review_json or {})

    @review_payload.setter
    def review_payload(self, value: dict[str, object]) -> None:
        self.review_json = dict(value)


class FreeCreditRequestMixin:
    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_text_id)
    user_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    requested_amount_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="general", index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    linkedin_profile_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    relationship_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    intended_use: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_verified: Mapped[bool] = mapped_column(nullable=False, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending", index=True)
    decided_amount_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_user_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    credit_grant_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

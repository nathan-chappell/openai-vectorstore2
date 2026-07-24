from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

UserRole = Literal["admin", "user"]
CreditGrantSource = Literal[
    "admin_manual",
    "free_credit_request",
    "paypal_receipt",
    "paypal_reversal",
    "paypal_checkout",
    "stripe_checkout",
    "system",
]
FreeCreditSource = Literal["general", "linkedin_connection", "beta_tester", "manual_admin"]
FreeCreditRequestStatus = Literal["pending", "approved", "rejected", "manual_review_required", "expired"]
PaymentAttemptStatus = Literal[
    "pending_payment",
    "temporarily_approved",
    "confirmed_paid",
    "rejected_payment",
    "expired_temporary_access",
    "manual_review_required",
]
PaymentProvider = Literal["paypal", "stripe", "manual", "none"]


class SharedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserIdentity(SharedModel):
    user_id: str = Field(min_length=1, max_length=128)
    email: str | None = None
    display_name: str | None = None
    image_url: str | None = None
    role: UserRole = "user"
    is_active: bool = False
    credit_floor_usd: float = 0.0


class AdminUserSummary(UserIdentity):
    current_credit_usd: float = 0.0
    created_at_ms: int | None = None
    last_sign_in_at_ms: int | None = None


class ManualCreditGrantRequest(SharedModel):
    user_id: str = Field(min_length=1, max_length=128)
    credit_amount_usd: float = Field(gt=0)
    note: str = Field(min_length=1, max_length=500)
    source: CreditGrantSource = "admin_manual"
    request_id: str | None = None
    payment_reference: str | None = None
    idempotency_key: str | None = None

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str) -> str:
        return value.strip()


class CreditGrantRecord(SharedModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    admin_user_id: str | None = None
    credit_amount_usd: float
    source: CreditGrantSource
    note: str | None = None
    request_id: str | None = None
    payment_provider: PaymentProvider | None = None
    payment_reference: str | None = None
    resulting_balance_usd: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FreeCreditRequestCreate(SharedModel):
    user_id: str = Field(min_length=1, max_length=128)
    requested_amount_usd: float | None = Field(default=None, gt=0)
    source: FreeCreditSource = "general"
    reason: str = Field(min_length=1, max_length=1000)
    linkedin_profile_url: str | None = Field(default=None, max_length=2048)
    relationship_note: str | None = Field(default=None, max_length=1000)
    intended_use: str | None = Field(default=None, max_length=1000)
    evidence_verified: bool = False
    idempotency_key: str | None = None

    @field_validator("reason", "relationship_note", "intended_use")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class FreeCreditRequestRecord(FreeCreditRequestCreate):
    id: str = Field(default_factory=lambda: str(uuid4()))
    status: FreeCreditRequestStatus = "pending"
    decided_amount_usd: float | None = None
    decision_note: str | None = None
    reviewer_user_id: str | None = None
    credit_grant_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None


class FreeCreditDecision(SharedModel):
    status: FreeCreditRequestStatus
    credit_amount_usd: float | None = None
    source: FreeCreditSource
    reason: str
    idempotency_key: str | None = None
    requires_admin_review: bool = False


class PaymentIntegrationStatus(SharedModel):
    provider: PaymentProvider | str
    checkout_enabled: bool
    reason: str | None = None


class PayPalReceiptReviewResult(SharedModel):
    amount_usd: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    payment_date: str | None = None
    transaction_id: str | None = None
    payer_email: str | None = None
    recipient_email: str | None = None
    reference_code: str | None = None
    appears_paypal_receipt: bool = False
    mismatch_flags: list[str] = Field(default_factory=list)
    tampering_flags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PaymentAttemptRecord(SharedModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    provider: PaymentProvider = "paypal"
    expected_amount_usd: float = Field(gt=0)
    expected_currency: str = Field(default="USD", min_length=3, max_length=3)
    reference_code: str
    status: PaymentAttemptStatus = "pending_payment"
    temporary_access_expires_at: datetime | None = None
    provider_reference: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

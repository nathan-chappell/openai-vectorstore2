from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass

from ai_portfolio_admin.orm import (
    CreditGrantMixin,
    DataclassCreditGrantMixin,
    DataclassFreeCreditRequestMixin,
    DataclassPaymentAttemptMixin,
    DataclassUserCreditBalanceMixin,
    FreeCreditRequestMixin,
    PaymentAttemptMixin,
    UserCreditBalanceMixin,
)


class DataclassBase(MappedAsDataclass, DeclarativeBase):
    pass


class RegularBase(DeclarativeBase):
    pass


class DataclassBalance(DataclassUserCreditBalanceMixin, DataclassBase):
    __tablename__ = "dataclass_user_credit_balances"


class DataclassGrant(DataclassCreditGrantMixin, DataclassBase):
    __tablename__ = "dataclass_credit_grants"


class DataclassAttempt(DataclassPaymentAttemptMixin, DataclassBase):
    __tablename__ = "dataclass_payment_attempts"


class DataclassFreeRequest(DataclassFreeCreditRequestMixin, DataclassBase):
    __tablename__ = "dataclass_free_credit_requests"


class RegularBalance(UserCreditBalanceMixin, RegularBase):
    __tablename__ = "regular_user_credit_balances"


class RegularGrant(CreditGrantMixin, RegularBase):
    __tablename__ = "regular_credit_grants"


class RegularAttempt(PaymentAttemptMixin, RegularBase):
    __tablename__ = "regular_payment_attempts"


class RegularFreeRequest(FreeCreditRequestMixin, RegularBase):
    __tablename__ = "regular_free_credit_requests"


def test_dataclass_mixins_build_expected_columns() -> None:
    assert set(DataclassBalance.__table__.columns.keys()) == {
        "user_id",
        "current_credit_usd",
        "created_at",
        "updated_at",
    }
    assert "admin_user_id" in DataclassGrant.__table__.columns
    assert "user_id" in DataclassAttempt.__table__.columns
    assert "reviewer_user_id" in DataclassFreeRequest.__table__.columns

    attempt = DataclassAttempt(
        user_id="user_1",
        expected_amount_usd=5,
        reference_code="REF",
    )
    attempt.review_payload = {"ok": True}
    assert attempt.id
    assert attempt.review_payload == {"ok": True}


def test_regular_mixins_build_expected_columns() -> None:
    assert set(RegularBalance.__table__.columns.keys()) == {
        "user_id",
        "current_credit_usd",
        "created_at",
        "updated_at",
    }
    assert "admin_user_id" in RegularGrant.__table__.columns
    assert "user_id" in RegularAttempt.__table__.columns
    assert "reviewer_user_id" in RegularFreeRequest.__table__.columns

    attempt = RegularAttempt(
        user_id="user_1",
        expected_amount_usd=5,
        reference_code="REF",
    )
    attempt.review_payload = {"ok": True}
    assert attempt.review_payload == {"ok": True}

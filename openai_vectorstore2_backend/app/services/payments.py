from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Literal, cast

from sqlalchemy import select

from ai_portfolio_admin.contracts import PaymentAttemptRecord
from ai_portfolio_admin.payments import PayPalReceiptWorkflow

from openai_vectorstore2_backend.app.core.config import AppSettings
from openai_vectorstore2_backend.app.db.session import DatabaseManager
from openai_vectorstore2_backend.app.models import PaymentAttempt
from openai_vectorstore2_backend.app.schemas import PaymentAttemptStatus, PaymentAttemptSummary
from openai_vectorstore2_backend.app.services.auth import AuthService
from openai_vectorstore2_backend.app.services.billing import BillingService

logger = logging.getLogger(__name__)


class PaymentService:
    """PayPal receipt-first payment flow for temporary prepaid credits."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        database: DatabaseManager,
        auth: AuthService,
        billing: BillingService,
    ) -> None:
        self._settings = settings
        self._database = database
        self._auth = auth
        self._billing = billing

    async def create_paypal_attempt(self, *, clerk_user_id: str, expected_amount_usd: float) -> PaymentAttemptSummary:
        workflow = self._paypal_workflow()
        amount = workflow.normalize_payment_amount(expected_amount_usd)
        await self._database.ensure_ready()
        async with self._database.session() as session:
            attempt = PaymentAttempt(
                user_id=clerk_user_id,
                provider="paypal",
                expected_amount_usd=amount,
                expected_currency="USD",
                reference_code=workflow.new_reference_code(),
                status="pending_payment",
                review_json={},
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(attempt)
            await session.commit()
            await session.refresh(attempt)
            logger.info(
                "payment_attempt_created id=%s clerk_user_id=%s provider=paypal amount_usd=%.2f",
                attempt.id,
                clerk_user_id,
                amount,
            )
            return _payment_attempt_summary(attempt)

    async def list_user_attempts(self, *, clerk_user_id: str) -> list[PaymentAttemptSummary]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(PaymentAttempt)
                        .where(PaymentAttempt.user_id == clerk_user_id)
                        .order_by(PaymentAttempt.created_at.desc())
                        .limit(20)
                    )
                )
                .scalars()
                .all()
            )
            return [_payment_attempt_summary(row) for row in rows]

    async def list_admin_attempts(
        self,
        *,
        status: PaymentAttemptStatus | None,
        limit: int = 50,
    ) -> list[PaymentAttemptSummary]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            statement = select(PaymentAttempt).order_by(PaymentAttempt.created_at.desc()).limit(limit)
            if status is not None:
                statement = statement.where(PaymentAttempt.status == status)
            rows = (await session.execute(statement)).scalars().all()
            return [_payment_attempt_summary(row) for row in rows]

    async def review_receipt_upload(
        self,
        *,
        clerk_user_id: str,
        attempt_id: str,
        filename: str,
        media_type: str | None,
        payload: bytes,
    ) -> PaymentAttemptSummary:
        self._require_paypal_recipient()
        await self._database.ensure_ready()
        async with self._database.session() as session:
            attempt = await session.get(PaymentAttempt, attempt_id)
            if attempt is None or attempt.user_id != clerk_user_id:
                raise FileNotFoundError("Payment attempt was not found.")
            if attempt.status in {"confirmed_paid", "rejected_payment"}:
                raise ValueError("This payment attempt is already closed.")
            outcome = self._paypal_workflow().review_receipt(
                _payment_attempt_record(attempt),
                payload=payload,
                media_type=media_type,
            )
            decision_status: PaymentAttemptStatus = outcome.status
            decision_reason = outcome.decision_reason
            review_payload = dict(outcome.review_payload)
            if outcome.provider_reference:
                duplicate = await session.scalar(
                    select(PaymentAttempt).where(
                        PaymentAttempt.provider_reference == outcome.provider_reference,
                        PaymentAttempt.id != attempt.id,
                    )
                )
                if duplicate is not None:
                    decision_status = "manual_review_required"
                    decision_reason = "Receipt transaction ID was already used on another payment attempt."
                    review_payload["decision_reason"] = decision_reason
            attempt.provider_reference = outcome.provider_reference
            attempt.receipt_filename = filename[:255]
            attempt.receipt_media_type = (media_type or "application/octet-stream")[:128]
            attempt.receipt_text_excerpt = outcome.receipt_text_excerpt
            attempt.review_payload = review_payload
            attempt.status = decision_status
            attempt.decision_note = decision_reason
            attempt.updated_at = _utcnow()
            await session.commit()
            await session.refresh(attempt)

        if attempt.status == "temporarily_approved" and attempt.credit_grant_id is None:
            await self._grant_attempt_credit(
                attempt_id=attempt.id,
                clerk_user_id=clerk_user_id,
                amount_usd=float(attempt.expected_amount_usd),
                note=f"Temporary PayPal receipt approval: {attempt.reference_code}",
                provider_reference=attempt.provider_reference,
            )
            async with self._database.session() as session:
                refreshed = await session.get(PaymentAttempt, attempt.id)
                if refreshed is None:
                    raise FileNotFoundError("Payment attempt was not found after approval.")
                return _payment_attempt_summary(refreshed)
        logger.info(
            "payment_receipt_reviewed id=%s clerk_user_id=%s status=%s reason=%s",
            attempt.id,
            clerk_user_id,
            attempt.status,
            attempt.decision_note,
        )
        return _payment_attempt_summary(attempt)

    async def decide_admin_attempt(
        self,
        *,
        attempt_id: str,
        admin_clerk_user_id: str,
        status: Literal["confirmed_paid", "rejected_payment", "manual_review_required"],
        decision_note: str,
        credit_amount_usd: float | None,
        provider_reference: str | None,
    ) -> PaymentAttemptSummary:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            attempt = await session.get(PaymentAttempt, attempt_id)
            if attempt is None:
                raise FileNotFoundError("Payment attempt was not found.")
            attempt.status = status
            attempt.decision_note = decision_note.strip()
            if provider_reference and provider_reference.strip():
                attempt.provider_reference = provider_reference.strip()
            if status == "confirmed_paid":
                attempt.temporary_access_expires_at = None
            attempt.updated_at = _utcnow()
            await session.commit()
            await session.refresh(attempt)

        if status == "confirmed_paid" and attempt.credit_grant_id is None:
            await self._grant_attempt_credit(
                attempt_id=attempt.id,
                clerk_user_id=attempt.user_id,
                amount_usd=credit_amount_usd or float(attempt.expected_amount_usd),
                note=f"Admin-confirmed PayPal payment: {attempt.reference_code}. {decision_note.strip()}",
                provider_reference=attempt.provider_reference,
                admin_clerk_user_id=admin_clerk_user_id,
                temporary=False,
            )
            async with self._database.session() as session:
                refreshed = await session.get(PaymentAttempt, attempt.id)
                if refreshed is None:
                    raise FileNotFoundError("Payment attempt was not found after confirmation.")
                return _payment_attempt_summary(refreshed)
        if status == "rejected_payment" and attempt.credit_grant_id is not None:
            await self._revoke_attempt_credit(
                attempt_id=attempt.id,
                clerk_user_id=attempt.user_id,
                amount_usd=float(attempt.expected_amount_usd),
                note=f"Revoked PayPal receipt credit: {attempt.reference_code}. {decision_note.strip()}",
                admin_clerk_user_id=admin_clerk_user_id,
                provider_reference=attempt.provider_reference,
            )
            async with self._database.session() as session:
                refreshed = await session.get(PaymentAttempt, attempt.id)
                if refreshed is None:
                    raise FileNotFoundError("Payment attempt was not found after rejection.")
                return _payment_attempt_summary(refreshed)
        return _payment_attempt_summary(attempt)

    async def _grant_attempt_credit(
        self,
        *,
        attempt_id: str,
        clerk_user_id: str,
        amount_usd: float,
        note: str,
        provider_reference: str | None,
        admin_clerk_user_id: str | None = None,
        temporary: bool = True,
    ) -> None:
        target = await self._auth.get_user_record(clerk_user_id)
        _, grant = await self._billing.grant_credit(
            clerk_user_id=clerk_user_id,
            credit_amount_usd=amount_usd,
            admin_clerk_user_id=admin_clerk_user_id,
            note=note,
            source="paypal_receipt",
            payment_provider="paypal",
            payment_reference=provider_reference or attempt_id,
            credit_floor_usd=target.credit_floor_usd,
            role=target.role,
        )
        async with self._database.session() as session:
            attempt = await session.get(PaymentAttempt, attempt_id)
            if attempt is None:
                raise FileNotFoundError("Payment attempt was not found after credit grant.")
            attempt.credit_grant_id = grant.id
            attempt.temporary_access_expires_at = None
            attempt.updated_at = _utcnow()
            await session.commit()
        logger.info(
            "payment_credit_granted attempt_id=%s clerk_user_id=%s amount_usd=%.2f grant_id=%s temporary=%s",
            attempt_id,
            clerk_user_id,
            amount_usd,
            grant.id,
            temporary,
        )

    async def _revoke_attempt_credit(
        self,
        *,
        attempt_id: str,
        clerk_user_id: str,
        amount_usd: float,
        note: str,
        admin_clerk_user_id: str,
        provider_reference: str | None,
    ) -> None:
        target = await self._auth.get_user_record(clerk_user_id)
        _, reversal = await self._billing.adjust_credit(
            clerk_user_id=clerk_user_id,
            credit_amount_usd=-abs(amount_usd),
            admin_clerk_user_id=admin_clerk_user_id,
            note=note,
            source="paypal_reversal",
            payment_provider="paypal",
            payment_reference=provider_reference or attempt_id,
            credit_floor_usd=target.credit_floor_usd,
            role=target.role,
        )
        logger.info(
            "payment_credit_revoked attempt_id=%s clerk_user_id=%s amount_usd=%.2f reversal_grant_id=%s",
            attempt_id,
            clerk_user_id,
            amount_usd,
            reversal.id,
        )

    def _require_paypal_recipient(self) -> str:
        recipient = (self._settings.paypal_recipient_email or "").strip()
        if not recipient:
            raise RuntimeError("PAYPAL_RECIPIENT_EMAIL is required for receipt-based PayPal credit.")
        return recipient

    def _paypal_workflow(self) -> PayPalReceiptWorkflow:
        return PayPalReceiptWorkflow(
            recipient_email=self._require_paypal_recipient(),
            reference_prefix="OVS2",
            min_payment_usd=self._settings.paypal_min_payment_usd,
            max_payment_usd=self._settings.paypal_max_payment_usd,
        )


def _payment_attempt_summary(attempt: PaymentAttempt) -> PaymentAttemptSummary:
    review = attempt.review_payload
    reason = review.get("decision_reason")
    return PaymentAttemptSummary(
        id=attempt.id,
        clerk_user_id=attempt.user_id,
        provider=attempt.provider,
        expected_amount_usd=round(float(attempt.expected_amount_usd), 2),
        expected_currency=attempt.expected_currency,
        reference_code=attempt.reference_code,
        status=cast(PaymentAttemptStatus, attempt.status),
        temporary_access_expires_at=attempt.temporary_access_expires_at,
        provider_reference=attempt.provider_reference,
        credit_grant_id=attempt.credit_grant_id,
        receipt_filename=attempt.receipt_filename,
        review_reason=reason if isinstance(reason, str) else attempt.decision_note,
        decision_note=attempt.decision_note,
        created_at=attempt.created_at,
        updated_at=attempt.updated_at,
    )


def _payment_attempt_record(attempt: PaymentAttempt) -> PaymentAttemptRecord:
    return PaymentAttemptRecord(
        id=attempt.id,
        user_id=attempt.user_id,
        provider="paypal",
        expected_amount_usd=round(float(attempt.expected_amount_usd), 2),
        expected_currency=attempt.expected_currency,
        reference_code=attempt.reference_code,
        status=cast(PaymentAttemptStatus, attempt.status),
        temporary_access_expires_at=attempt.temporary_access_expires_at,
        provider_reference=attempt.provider_reference,
        created_at=attempt.created_at,
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)

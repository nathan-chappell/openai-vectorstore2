from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
import logging
import re
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy import select

from ai_portfolio_admin.contracts import PayPalReceiptReviewResult
from ai_portfolio_admin.payments import PayPalReceiptPolicy, evaluate_paypal_receipt_review

from backend.app.core.config import AppSettings
from backend.app.db.session import DatabaseManager
from backend.app.models import PaymentAttempt
from backend.app.schemas import PaymentAttemptStatus, PaymentAttemptSummary
from backend.app.services.auth import AuthService
from backend.app.services.billing import BillingService

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_TRANSACTION_PATTERN = re.compile(
    r"(?:transaction|txn|payment)\s*(?:id|number|#)?\s*[:#]?\s*([A-Z0-9-]{8,})",
    re.IGNORECASE,
)
_USD_PATTERNS = (
    re.compile(r"(?:US\s*)?\$\s*([0-9][0-9,]*(?:\.[0-9]{2})?)", re.IGNORECASE),
    re.compile(r"\bUSD\s*([0-9][0-9,]*(?:\.[0-9]{2})?)", re.IGNORECASE),
    re.compile(r"([0-9][0-9,]*(?:\.[0-9]{2})?)\s*USD\b", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class ReceiptExtraction:
    text: str
    review: PayPalReceiptReviewResult
    reason: str


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
        self._require_paypal_recipient()
        amount = round(float(expected_amount_usd), 2)
        if amount < self._settings.paypal_min_payment_usd or amount > self._settings.paypal_max_payment_usd:
            raise ValueError(
                f"Payment amount must be between ${self._settings.paypal_min_payment_usd:.2f} "
                f"and ${self._settings.paypal_max_payment_usd:.2f}."
            )
        await self._database.ensure_ready()
        async with self._database.session() as session:
            attempt = PaymentAttempt(
                clerk_user_id=clerk_user_id,
                provider="paypal",
                expected_amount_usd=amount,
                expected_currency="USD",
                reference_code=f"OVS2-{uuid4().hex[:10].upper()}",
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
                        .where(PaymentAttempt.clerk_user_id == clerk_user_id)
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
            if attempt is None or attempt.clerk_user_id != clerk_user_id:
                raise FileNotFoundError("Payment attempt was not found.")
            if attempt.status in {"confirmed_paid", "rejected_payment"}:
                raise ValueError("This payment attempt is already closed.")
            expected_recipient = self._require_paypal_recipient()
            extraction = _extract_receipt(
                payload,
                media_type=media_type,
                reference_code=attempt.reference_code,
                recipient_email=expected_recipient,
            )
            policy = PayPalReceiptPolicy(
                expected_amount_usd=float(attempt.expected_amount_usd),
                expected_currency=attempt.expected_currency,
                recipient_email=expected_recipient,
                reference_code=attempt.reference_code,
            )
            decision = evaluate_paypal_receipt_review(extraction.review, policy)
            decision_status: PaymentAttemptStatus = decision.status
            decision_reason = decision.reason
            if decision.provider_reference:
                duplicate = await session.scalar(
                    select(PaymentAttempt).where(
                        PaymentAttempt.provider_reference == decision.provider_reference,
                        PaymentAttempt.id != attempt.id,
                    )
                )
                if duplicate is not None:
                    decision_status = "manual_review_required"
                    decision_reason = "Receipt transaction ID was already used on another payment attempt."
            attempt.provider_reference = decision.provider_reference
            attempt.receipt_filename = filename[:255]
            attempt.receipt_media_type = (media_type or "application/octet-stream")[:128]
            attempt.receipt_text_excerpt = extraction.text[:4000] or None
            attempt.review_payload = {
                **extraction.review.model_dump(mode="json"),
                "decision_reason": decision_reason,
                "extraction_reason": extraction.reason,
                "temporary_access_level": decision.temporary_access_level,
            }
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
                clerk_user_id=attempt.clerk_user_id,
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
                clerk_user_id=attempt.clerk_user_id,
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


def _extract_receipt(
    payload: bytes,
    *,
    media_type: str | None,
    reference_code: str,
    recipient_email: str,
) -> ReceiptExtraction:
    text = _extract_text(payload, media_type=media_type)
    lowered = text.casefold()
    emails = _EMAIL_PATTERN.findall(text)
    amount = _extract_usd_amount(text)
    transaction_match = _TRANSACTION_PATTERN.search(text)
    transaction_id = transaction_match.group(1) if transaction_match else None
    matched_recipient = next((email for email in emails if email.casefold() == recipient_email.casefold()), None)
    appears_paypal = "paypal" in lowered
    reference_found = reference_code if reference_code in text else None
    confidence = 0.0
    if appears_paypal:
        confidence += 0.35
    if amount is not None:
        confidence += 0.2
    if emails:
        confidence += 0.15
    if reference_found is not None:
        confidence += 0.2
    if transaction_id:
        confidence += 0.1
    reason = "Readable receipt evidence parsed." if text.strip() else "Receipt text could not be read."
    review = PayPalReceiptReviewResult(
        amount_usd=amount,
        currency="USD" if amount is not None or "$" in text or "usd" in lowered else None,
        transaction_id=transaction_id,
        payer_email=emails[0] if emails else None,
        recipient_email=matched_recipient or (emails[-1] if emails else None),
        reference_code=reference_found,
        appears_paypal_receipt=appears_paypal,
        mismatch_flags=[],
        tampering_flags=[],
        confidence=min(confidence, 1.0),
    )
    return ReceiptExtraction(text=text, review=review, reason=reason)


def _extract_text(payload: bytes, *, media_type: str | None) -> str:
    if (media_type or "").casefold() == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(payload))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as error:  # noqa: BLE001 - a receipt fallback should degrade to manual review.
            logger.info("paypal_receipt_pdf_text_unavailable error=%s", error.__class__.__name__)
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _extract_usd_amount(text: str) -> float | None:
    matches: list[float] = []
    for pattern in _USD_PATTERNS:
        for match in pattern.findall(text):
            try:
                matches.append(float(match.replace(",", "")))
            except ValueError:
                continue
    return max(matches) if matches else None


def _payment_attempt_summary(attempt: PaymentAttempt) -> PaymentAttemptSummary:
    review = attempt.review_payload
    reason = review.get("decision_reason")
    return PaymentAttemptSummary(
        id=attempt.id,
        clerk_user_id=attempt.clerk_user_id,
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


def _utcnow() -> datetime:
    return datetime.now(UTC)

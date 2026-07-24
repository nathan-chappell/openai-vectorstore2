from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from uuid import uuid4

from pydantic import Field

from .contracts import PaymentAttemptRecord, PaymentAttemptStatus, PayPalReceiptReviewResult, SharedModel

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


class PayPalReceiptPolicy(SharedModel):
    expected_amount_usd: float = Field(gt=0)
    expected_currency: str = Field(default="USD", min_length=3, max_length=3)
    recipient_email: str
    reference_code: str | None = None
    minimum_confidence_for_temporary_access: float = Field(default=0.82, ge=0, le=1)


class PayPalReceiptDecision(SharedModel):
    status: PaymentAttemptStatus
    reason: str
    temporary_access_level: int = Field(default=0, ge=0, le=3)
    provider_reference: str | None = None


class PayPalReceiptExtraction(SharedModel):
    text: str
    review: PayPalReceiptReviewResult
    reason: str


class PayPalReceiptReviewOutcome(SharedModel):
    status: PaymentAttemptStatus
    decision_reason: str
    provider_reference: str | None = None
    receipt_text_excerpt: str | None = None
    review_payload: dict[str, object]
    temporary_access_level: int = Field(default=0, ge=0, le=3)
    should_grant_temporary_credit: bool = False


@dataclass(frozen=True, slots=True)
class PayPalReceiptWorkflow:
    recipient_email: str
    reference_prefix: str
    min_payment_usd: float = 5.0
    max_payment_usd: float = 250.0
    receipt_excerpt_chars: int = 4000

    def require_recipient_email(self) -> str:
        recipient = self.recipient_email.strip()
        if not recipient:
            raise RuntimeError("PayPal recipient email is required for receipt-based credit.")
        return recipient

    def normalize_payment_amount(self, amount_usd: float) -> float:
        amount = round(float(amount_usd), 2)
        if amount < self.min_payment_usd or amount > self.max_payment_usd:
            raise ValueError(
                f"Payment amount must be between ${self.min_payment_usd:.2f} "
                f"and ${self.max_payment_usd:.2f}."
            )
        return amount

    def new_reference_code(self) -> str:
        prefix = self.reference_prefix.strip().upper() or "PAY"
        return f"{prefix}-{uuid4().hex[:10].upper()}"

    def review_receipt(
        self,
        attempt: PaymentAttemptRecord,
        *,
        payload: bytes,
        media_type: str | None,
        duplicate_provider_reference_exists: Callable[[str], bool] | None = None,
    ) -> PayPalReceiptReviewOutcome:
        recipient = self.require_recipient_email()
        extraction = extract_paypal_receipt(
            payload,
            media_type=media_type,
            reference_code=attempt.reference_code,
            recipient_email=recipient,
        )
        policy = PayPalReceiptPolicy(
            expected_amount_usd=attempt.expected_amount_usd,
            expected_currency=attempt.expected_currency,
            recipient_email=recipient,
            reference_code=attempt.reference_code,
        )
        decision = evaluate_paypal_receipt_review(extraction.review, policy)
        status = decision.status
        reason = decision.reason
        if (
            decision.provider_reference
            and duplicate_provider_reference_exists is not None
            and duplicate_provider_reference_exists(decision.provider_reference)
        ):
            status = "manual_review_required"
            reason = "Receipt transaction ID was already used on another payment attempt."
        review_payload: dict[str, object] = {
            **extraction.review.model_dump(mode="json"),
            "decision_reason": reason,
            "extraction_reason": extraction.reason,
            "temporary_access_level": decision.temporary_access_level,
        }
        text_excerpt = extraction.text[: self.receipt_excerpt_chars] or None
        return PayPalReceiptReviewOutcome(
            status=status,
            decision_reason=reason,
            provider_reference=decision.provider_reference,
            receipt_text_excerpt=text_excerpt,
            review_payload=review_payload,
            temporary_access_level=decision.temporary_access_level,
            should_grant_temporary_credit=status == "temporarily_approved",
        )


def evaluate_paypal_receipt_review(
    review: PayPalReceiptReviewResult,
    policy: PayPalReceiptPolicy,
) -> PayPalReceiptDecision:
    if not review.appears_paypal_receipt:
        return PayPalReceiptDecision(
            status="rejected_payment",
            reason="Uploaded evidence does not look like a PayPal receipt.",
        )
    if review.tampering_flags:
        return PayPalReceiptDecision(status="manual_review_required", reason="Receipt has possible tampering flags.")
    if review.mismatch_flags:
        return PayPalReceiptDecision(status="manual_review_required", reason="Receipt has payment mismatch flags.")
    if review.amount_usd is None or round(review.amount_usd, 2) != round(policy.expected_amount_usd, 2):
        return PayPalReceiptDecision(status="rejected_payment", reason="Receipt amount does not match expected amount.")
    if (review.currency or "").upper() != policy.expected_currency.upper():
        return PayPalReceiptDecision(
            status="rejected_payment",
            reason="Receipt currency does not match expected currency.",
        )
    if (review.recipient_email or "").casefold() != policy.recipient_email.casefold():
        return PayPalReceiptDecision(
            status="manual_review_required",
            reason="Receipt recipient does not match expected recipient.",
        )
    if policy.reference_code and review.reference_code != policy.reference_code:
        return PayPalReceiptDecision(
            status="manual_review_required",
            reason="Receipt reference code is missing or mismatched.",
        )
    if review.confidence < policy.minimum_confidence_for_temporary_access:
        return PayPalReceiptDecision(
            status="manual_review_required",
            reason="Receipt confidence is below temporary approval threshold.",
        )

    temporary_access_level = 1 if review.transaction_id else 0
    return PayPalReceiptDecision(
        status="temporarily_approved",
        reason="Receipt matches temporary access policy.",
        temporary_access_level=temporary_access_level,
        provider_reference=review.transaction_id,
    )


def extract_paypal_receipt(
    payload: bytes,
    *,
    media_type: str | None,
    reference_code: str,
    recipient_email: str,
) -> PayPalReceiptExtraction:
    text = extract_receipt_text(payload, media_type=media_type)
    lowered = text.casefold()
    emails = _EMAIL_PATTERN.findall(text)
    amount = extract_usd_amount(text)
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
    return PayPalReceiptExtraction(text=text, review=review, reason=reason)


def extract_receipt_text(payload: bytes, *, media_type: str | None) -> str:
    if (media_type or "").casefold() == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(payload))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as error:
            logger.info("paypal_receipt_pdf_text_unavailable error=%s", error.__class__.__name__)
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def extract_usd_amount(text: str) -> float | None:
    matches: list[float] = []
    for pattern in _USD_PATTERNS:
        for match in pattern.findall(text):
            try:
                matches.append(float(match.replace(",", "")))
            except ValueError:
                continue
    return max(matches) if matches else None

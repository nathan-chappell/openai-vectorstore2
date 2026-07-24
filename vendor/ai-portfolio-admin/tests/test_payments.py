from __future__ import annotations

from ai_portfolio_admin.contracts import PaymentAttemptRecord, PayPalReceiptReviewResult
from ai_portfolio_admin.payments import (
    PayPalReceiptPolicy,
    PayPalReceiptWorkflow,
    evaluate_paypal_receipt_review,
    extract_usd_amount,
)


def test_paypal_receipt_review_temporarily_approves_matching_receipt() -> None:
    decision = evaluate_paypal_receipt_review(
        PayPalReceiptReviewResult(
            amount_usd=10.0,
            currency="USD",
            transaction_id="PAYID-123",
            recipient_email="owner@example.com",
            reference_code="ABC123",
            appears_paypal_receipt=True,
            confidence=0.95,
        ),
        PayPalReceiptPolicy(expected_amount_usd=10.0, recipient_email="owner@example.com", reference_code="ABC123"),
    )

    assert decision.status == "temporarily_approved"
    assert decision.temporary_access_level == 1


def test_paypal_receipt_review_rejects_wrong_amount() -> None:
    decision = evaluate_paypal_receipt_review(
        PayPalReceiptReviewResult(
            amount_usd=1.0,
            currency="USD",
            recipient_email="owner@example.com",
            appears_paypal_receipt=True,
            confidence=0.95,
        ),
        PayPalReceiptPolicy(expected_amount_usd=10.0, recipient_email="owner@example.com"),
    )

    assert decision.status == "rejected_payment"


def test_paypal_receipt_workflow_extracts_and_approves_text_receipt() -> None:
    workflow = PayPalReceiptWorkflow(
        recipient_email="owner@example.com",
        reference_prefix="TEST",
        min_payment_usd=5.0,
        max_payment_usd=250.0,
    )
    attempt = PaymentAttemptRecord(
        user_id="user_123",
        expected_amount_usd=12.5,
        expected_currency="USD",
        reference_code="TEST-ABC123",
    )

    outcome = workflow.review_receipt(
        attempt,
        payload=(
            b"PayPal payment receipt\n"
            b"Transaction ID: PAYID-123456789\n"
            b"Paid to owner@example.com\n"
            b"Reference TEST-ABC123\n"
            b"Total USD 12.50"
        ),
        media_type="text/plain",
    )

    assert outcome.status == "temporarily_approved"
    assert outcome.provider_reference == "PAYID-123456789"
    assert outcome.should_grant_temporary_credit is True
    assert outcome.review_payload["decision_reason"] == "Receipt matches temporary access policy."


def test_paypal_receipt_workflow_flags_duplicate_transaction_id() -> None:
    workflow = PayPalReceiptWorkflow(recipient_email="owner@example.com", reference_prefix="TEST")
    attempt = PaymentAttemptRecord(
        user_id="user_123",
        expected_amount_usd=10.0,
        expected_currency="USD",
        reference_code="TEST-ABC123",
    )

    outcome = workflow.review_receipt(
        attempt,
        payload=(
            b"PayPal receipt\nTransaction ID: PAYID-DUPLICATE\n"
            b"To: owner@example.com\nReference TEST-ABC123\n$10.00"
        ),
        media_type="text/plain",
        duplicate_provider_reference_exists=lambda value: value == "PAYID-DUPLICATE",
    )

    assert outcome.status == "manual_review_required"
    assert outcome.should_grant_temporary_credit is False


def test_extract_usd_amount_uses_largest_usd_value() -> None:
    assert extract_usd_amount("fee $0.50 total USD 12.50") == 12.5

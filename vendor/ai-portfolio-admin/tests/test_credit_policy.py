from __future__ import annotations

import pytest

from ai_portfolio_admin.contracts import CreditGrantRecord, FreeCreditRequestCreate, ManualCreditGrantRequest
from ai_portfolio_admin.credit_policy import FreeCreditPolicy, evaluate_free_credit_request
from ai_portfolio_admin.services import AdminCreditWorkflow


def test_linkedin_connection_policy_approves_verified_five_dollar_credit() -> None:
    request = FreeCreditRequestCreate(
        user_id="user_1",
        source="linkedin_connection",
        reason="We are connected on LinkedIn.",
        linkedin_profile_url="https://www.linkedin.com/in/example",
        evidence_verified=True,
    )

    decision = evaluate_free_credit_request(request, FreeCreditPolicy.early_access_defaults())

    assert decision.status == "approved"
    assert decision.credit_amount_usd == 5.0


def test_linkedin_connection_policy_requires_verified_evidence() -> None:
    request = FreeCreditRequestCreate(
        user_id="user_1",
        source="linkedin_connection",
        reason="I think we are connected.",
        linkedin_profile_url="https://www.linkedin.com/in/example",
    )

    decision = evaluate_free_credit_request(request, FreeCreditPolicy.early_access_defaults())

    assert decision.status == "manual_review_required"
    assert decision.requires_admin_review is True


class InMemoryLedger:
    def __init__(self) -> None:
        self.grants: list[CreditGrantRecord] = []

    async def grant_credit(self, request: ManualCreditGrantRequest, *, admin_user_id: str | None) -> CreditGrantRecord:
        grant = CreditGrantRecord(
            user_id=request.user_id,
            admin_user_id=admin_user_id,
            credit_amount_usd=request.credit_amount_usd,
            source=request.source,
            note=request.note,
            resulting_balance_usd=request.credit_amount_usd,
        )
        self.grants.append(grant)
        return grant

    async def count_approved_free_credit_requests(self, *, user_id: str, source: str) -> int:
        return len(
            [
                grant
                for grant in self.grants
                if grant.user_id == user_id and grant.source == "free_credit_request"
            ]
        )


@pytest.mark.asyncio
async def test_admin_credit_workflow_records_auto_approved_free_credit() -> None:
    ledger = InMemoryLedger()
    workflow = AdminCreditWorkflow(ledger=ledger, free_credit_policy=FreeCreditPolicy.early_access_defaults())
    request = FreeCreditRequestCreate(
        user_id="user_1",
        source="linkedin_connection",
        reason="Connected on LinkedIn.",
        evidence_verified=True,
        idempotency_key="free:user_1:linkedin",
    )

    grant = await workflow.request_free_credit(request, admin_user_id="system")

    assert grant is not None
    assert grant.credit_amount_usd == 5.0
    assert grant.source == "free_credit_request"

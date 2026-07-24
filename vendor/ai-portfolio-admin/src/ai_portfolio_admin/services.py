from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .contracts import CreditGrantRecord, FreeCreditRequestCreate, ManualCreditGrantRequest
from .credit_policy import FreeCreditPolicy, evaluate_free_credit_request


class CreditLedger(Protocol):
    async def grant_credit(self, request: ManualCreditGrantRequest, *, admin_user_id: str | None) -> CreditGrantRecord:
        """Persist a credit grant and return the recorded grant with resulting balance when available."""
        ...

    async def count_approved_free_credit_requests(self, *, user_id: str, source: str) -> int:
        """Return previous approved request count for once-per-user policy decisions."""
        ...


@dataclass(frozen=True, slots=True)
class AdminCreditWorkflow:
    ledger: CreditLedger
    free_credit_policy: FreeCreditPolicy

    async def request_free_credit(
        self,
        request: FreeCreditRequestCreate,
        *,
        admin_user_id: str | None = None,
    ) -> CreditGrantRecord | None:
        prior_count = await self.ledger.count_approved_free_credit_requests(
            user_id=request.user_id,
            source=request.source,
        )
        decision = evaluate_free_credit_request(
            request,
            self.free_credit_policy,
            prior_approved_request_count=prior_count,
        )
        if decision.status != "approved" or decision.credit_amount_usd is None:
            return None
        grant_request = ManualCreditGrantRequest(
            user_id=request.user_id,
            credit_amount_usd=decision.credit_amount_usd,
            note=decision.reason,
            source="free_credit_request",
            idempotency_key=decision.idempotency_key,
        )
        return await self.ledger.grant_credit(grant_request, admin_user_id=admin_user_id)

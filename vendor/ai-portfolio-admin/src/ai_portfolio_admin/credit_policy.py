from __future__ import annotations

from pydantic import Field

from .contracts import FreeCreditDecision, FreeCreditRequestCreate, FreeCreditSource, SharedModel


class FreeCreditRule(SharedModel):
    source: FreeCreditSource
    credit_amount_usd: float = Field(gt=0)
    max_amount_usd: float = Field(gt=0)
    requires_verified_evidence: bool = True
    once_per_user: bool = True
    decision_reason: str


def _empty_rules() -> list[FreeCreditRule]:
    return []


class FreeCreditPolicy(SharedModel):
    rules: list[FreeCreditRule] = Field(default_factory=_empty_rules)
    default_manual_review_reason: str = "Free credit request requires admin review."

    @classmethod
    def early_access_defaults(cls) -> FreeCreditPolicy:
        return cls(
            rules=[
                FreeCreditRule(
                    source="linkedin_connection",
                    credit_amount_usd=5.0,
                    max_amount_usd=5.0,
                    requires_verified_evidence=True,
                    once_per_user=True,
                    decision_reason="Verified LinkedIn connection early-access credit.",
                ),
                FreeCreditRule(
                    source="beta_tester",
                    credit_amount_usd=5.0,
                    max_amount_usd=10.0,
                    requires_verified_evidence=False,
                    once_per_user=True,
                    decision_reason="Beta tester early-access credit.",
                ),
            ]
        )


def evaluate_free_credit_request(
    request: FreeCreditRequestCreate,
    policy: FreeCreditPolicy,
    *,
    prior_approved_request_count: int = 0,
) -> FreeCreditDecision:
    rule = next((candidate for candidate in policy.rules if candidate.source == request.source), None)
    if rule is None:
        return FreeCreditDecision(
            status="manual_review_required",
            source=request.source,
            reason=policy.default_manual_review_reason,
            requires_admin_review=True,
            idempotency_key=request.idempotency_key,
        )
    if rule.once_per_user and prior_approved_request_count > 0:
        return FreeCreditDecision(
            status="rejected",
            source=request.source,
            reason="This free-credit rule can only be used once per account.",
            idempotency_key=request.idempotency_key,
        )
    if rule.requires_verified_evidence and not request.evidence_verified:
        return FreeCreditDecision(
            status="manual_review_required",
            source=request.source,
            reason="Evidence must be verified before automatic credit is granted.",
            requires_admin_review=True,
            idempotency_key=request.idempotency_key,
        )

    requested_amount = request.requested_amount_usd or rule.credit_amount_usd
    amount = min(requested_amount, rule.credit_amount_usd, rule.max_amount_usd)
    return FreeCreditDecision(
        status="approved",
        credit_amount_usd=round(float(amount), 8),
        source=request.source,
        reason=rule.decision_reason,
        idempotency_key=request.idempotency_key,
    )

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Literal, cast

from sqlalchemy import func, select

from ai_portfolio_admin.contracts import FreeCreditRequestCreate as SharedFreeCreditRequestCreate
from ai_portfolio_admin.credit_policy import FreeCreditPolicy, evaluate_free_credit_request

from openai_vectorstore2_backend.app.core.config import AppSettings
from openai_vectorstore2_backend.app.db.session import DatabaseManager
from openai_vectorstore2_backend.app.models import FreeCreditRequest
from openai_vectorstore2_backend.app.schemas import (
    FreeCreditRequestCreate,
    FreeCreditSource,
    FreeCreditRequestStatus,
    FreeCreditRequestSummary,
)
from openai_vectorstore2_backend.app.services.auth import AuthService
from openai_vectorstore2_backend.app.services.billing import BillingService

logger = logging.getLogger(__name__)

ACTIVE_FREE_CREDIT_STATUSES = {"pending", "manual_review_required"}


class FreeCreditService:
    """Host-owned persistence for shared admin free-credit request contracts."""

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
        self._policy = FreeCreditPolicy.early_access_defaults()

    async def create_request(
        self,
        *,
        clerk_user_id: str,
        payload: FreeCreditRequestCreate,
    ) -> FreeCreditRequestSummary:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            if payload.idempotency_key:
                existing = await session.scalar(
                    select(FreeCreditRequest).where(
                        FreeCreditRequest.user_id == clerk_user_id,
                        FreeCreditRequest.idempotency_key == payload.idempotency_key,
                    )
                )
                if existing is not None:
                    return _free_credit_summary(existing)

            active_request = await session.scalar(
                select(FreeCreditRequest).where(
                    FreeCreditRequest.user_id == clerk_user_id,
                    FreeCreditRequest.status.in_(ACTIVE_FREE_CREDIT_STATUSES),
                )
            )
            if active_request is not None:
                raise ValueError("You already have a free-credit request awaiting review.")

            prior_approved = await self._approved_request_count(clerk_user_id=clerk_user_id, source=payload.source)
            shared_request = SharedFreeCreditRequestCreate(
                user_id=clerk_user_id,
                requested_amount_usd=payload.requested_amount_usd,
                source=payload.source,
                reason=payload.reason,
                linkedin_profile_url=payload.linkedin_profile_url,
                relationship_note=payload.relationship_note,
                intended_use=payload.intended_use,
                evidence_verified=False,
                idempotency_key=payload.idempotency_key,
            )
            policy_decision = evaluate_free_credit_request(
                shared_request,
                self._policy,
                prior_approved_request_count=prior_approved,
            )
            status: FreeCreditRequestStatus = "pending" if policy_decision.requires_admin_review else policy_decision.status
            now = _utcnow()
            request = FreeCreditRequest(
                user_id=clerk_user_id,
                requested_amount_usd=payload.requested_amount_usd,
                source=payload.source,
                reason=payload.reason.strip(),
                linkedin_profile_url=_normalized_text(payload.linkedin_profile_url),
                relationship_note=_normalized_text(payload.relationship_note),
                intended_use=_normalized_text(payload.intended_use),
                evidence_verified=False,
                idempotency_key=_normalized_text(payload.idempotency_key),
                status=status,
                decision_note=policy_decision.reason,
                created_at=now,
                updated_at=now,
                decided_at=now if status in {"approved", "rejected"} else None,
            )
            session.add(request)
            await session.commit()
            await session.refresh(request)

        if status == "approved" and policy_decision.credit_amount_usd is not None:
            return await self._grant_approved_request(
                request_id=request.id,
                admin_clerk_user_id="system",
                amount_usd=policy_decision.credit_amount_usd,
                decision_note=policy_decision.reason,
            )

        logger.info(
            "free_credit_request_created request_id=%s clerk_user_id=%s status=%s source=%s",
            request.id,
            clerk_user_id,
            status,
            payload.source,
        )
        return _free_credit_summary(request)

    async def list_user_requests(self, *, clerk_user_id: str, limit: int = 20) -> list[FreeCreditRequestSummary]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(FreeCreditRequest)
                        .where(FreeCreditRequest.user_id == clerk_user_id)
                        .order_by(FreeCreditRequest.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [_free_credit_summary(row) for row in rows]

    async def list_admin_requests(
        self,
        *,
        status: FreeCreditRequestStatus | None,
        limit: int = 50,
    ) -> list[FreeCreditRequestSummary]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            statement = select(FreeCreditRequest).order_by(FreeCreditRequest.created_at.desc()).limit(limit)
            if status is not None:
                statement = statement.where(FreeCreditRequest.status == status)
            rows = (await session.execute(statement)).scalars().all()
            return [_free_credit_summary(row) for row in rows]

    async def decide_admin_request(
        self,
        *,
        request_id: str,
        admin_clerk_user_id: str,
        status: Literal["approved", "rejected", "manual_review_required"],
        decision_note: str,
        credit_amount_usd: float | None,
    ) -> FreeCreditRequestSummary:
        if status == "approved":
            request = await self._request_by_id(request_id)
            amount = round(float(credit_amount_usd or request.requested_amount_usd or 5.0), 8)
            return await self._grant_approved_request(
                request_id=request_id,
                admin_clerk_user_id=admin_clerk_user_id,
                amount_usd=amount,
                decision_note=decision_note,
            )

        await self._database.ensure_ready()
        async with self._database.session() as session:
            request = await session.get(FreeCreditRequest, request_id)
            if request is None:
                raise FileNotFoundError("Free-credit request was not found.")
            request.status = status
            request.decision_note = decision_note.strip()
            request.reviewer_user_id = admin_clerk_user_id
            request.decided_amount_usd = None
            request.decided_at = _utcnow() if status == "rejected" else None
            request.updated_at = _utcnow()
            await session.commit()
            await session.refresh(request)
            logger.info(
                "free_credit_request_decided request_id=%s clerk_user_id=%s status=%s admin=%s",
                request.id,
                request.user_id,
                request.status,
                admin_clerk_user_id,
            )
            return _free_credit_summary(request)

    async def _request_by_id(self, request_id: str) -> FreeCreditRequest:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            request = await session.get(FreeCreditRequest, request_id)
            if request is None:
                raise FileNotFoundError("Free-credit request was not found.")
            return request

    async def _approved_request_count(self, *, clerk_user_id: str, source: str) -> int:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            return int(
                await session.scalar(
                    select(func.count(FreeCreditRequest.id)).where(
                        FreeCreditRequest.user_id == clerk_user_id,
                        FreeCreditRequest.source == source,
                        FreeCreditRequest.status == "approved",
                    )
                )
                or 0
            )

    async def _grant_approved_request(
        self,
        *,
        request_id: str,
        admin_clerk_user_id: str,
        amount_usd: float,
        decision_note: str,
    ) -> FreeCreditRequestSummary:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            request = await session.get(FreeCreditRequest, request_id)
            if request is None:
                raise FileNotFoundError("Free-credit request was not found.")
            if request.credit_grant_id is not None:
                request.status = "approved"
                request.decision_note = decision_note.strip()
                request.reviewer_user_id = admin_clerk_user_id
                request.decided_at = request.decided_at or _utcnow()
                request.updated_at = _utcnow()
                await session.commit()
                await session.refresh(request)
                return _free_credit_summary(request)
            target_user_id = request.user_id
            requested_source = request.source

        target = await self._auth.get_user_record(target_user_id)
        _, grant = await self._billing.grant_credit(
            clerk_user_id=target_user_id,
            credit_amount_usd=amount_usd,
            admin_clerk_user_id=admin_clerk_user_id,
            note=decision_note,
            source="free_credit_request",
            payment_provider=None,
            payment_reference=request_id,
            credit_floor_usd=target.credit_floor_usd,
            role=target.role,
        )

        async with self._database.session() as session:
            request = await session.get(FreeCreditRequest, request_id)
            if request is None:
                raise FileNotFoundError("Free-credit request was not found after credit grant.")
            request.status = "approved"
            request.decided_amount_usd = round(float(amount_usd), 8)
            request.decision_note = decision_note.strip()
            request.reviewer_user_id = admin_clerk_user_id
            request.credit_grant_id = grant.id
            request.decided_at = _utcnow()
            request.updated_at = _utcnow()
            await session.commit()
            await session.refresh(request)
            logger.info(
                "free_credit_request_approved request_id=%s clerk_user_id=%s source=%s amount_usd=%.8f grant_id=%s",
                request.id,
                request.user_id,
                requested_source,
                amount_usd,
                grant.id,
            )
            return _free_credit_summary(request)


def _free_credit_summary(request: FreeCreditRequest) -> FreeCreditRequestSummary:
    return FreeCreditRequestSummary(
        id=request.id,
        clerk_user_id=request.user_id,
        requested_amount_usd=round(float(request.requested_amount_usd), 8)
        if request.requested_amount_usd is not None
        else None,
        source=cast(FreeCreditSource, request.source),
        reason=request.reason,
        linkedin_profile_url=request.linkedin_profile_url,
        relationship_note=request.relationship_note,
        intended_use=request.intended_use,
        evidence_verified=request.evidence_verified,
        idempotency_key=request.idempotency_key,
        status=cast(FreeCreditRequestStatus, request.status),
        decided_amount_usd=round(float(request.decided_amount_usd), 8)
        if request.decided_amount_usd is not None
        else None,
        decision_note=request.decision_note,
        reviewer_clerk_user_id=request.reviewer_user_id,
        credit_grant_id=request.credit_grant_id,
        created_at=request.created_at,
        updated_at=request.updated_at,
        decided_at=request.decided_at,
    )


def _normalized_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _utcnow() -> datetime:
    return datetime.now(UTC)

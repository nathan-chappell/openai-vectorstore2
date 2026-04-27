from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any, Mapping

from sqlalchemy import select

from backend.app.core.config import AppSettings
from backend.app.db.session import DatabaseManager
from backend.app.models import CostEvent, CreditGrant, UserCreditBalance
from backend.app.schemas import CostEventSummary, CreditBalanceSummary, CreditGrantSummary, OpenAIUsagePayload

logger = logging.getLogger(__name__)

PRICING_VERSION = "openai_api_pricing_2026-04-27"


@dataclass(frozen=True, slots=True)
class ModelPricing:
    input_per_million: float
    cached_input_per_million: float
    output_per_million: float


@dataclass(frozen=True, slots=True)
class UsageCost:
    pricing_version: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    raw_usage: OpenAIUsagePayload
    openai_cost_usd: float
    platform_multiplier: float
    platform_cost_usd: float
    note: str | None = None


class CreditRequiredError(PermissionError):
    """Raised before starting a billable operation when a user has exhausted credit."""


class UnknownModelPricingError(RuntimeError):
    """Raised when a billable model has no configured pricing and the policy is block."""


MODEL_PRICING_USD_PER_MILLION: dict[str, ModelPricing] = {
    "gpt-5.5": ModelPricing(input_per_million=5.00, cached_input_per_million=0.50, output_per_million=30.00),
    "gpt-5.4": ModelPricing(input_per_million=2.50, cached_input_per_million=0.25, output_per_million=15.00),
    "gpt-5.4-mini": ModelPricing(input_per_million=0.75, cached_input_per_million=0.075, output_per_million=4.50),
    "gpt-5.2": ModelPricing(input_per_million=1.75, cached_input_per_million=0.175, output_per_million=14.00),
    "gpt-5.1": ModelPricing(input_per_million=1.25, cached_input_per_million=0.125, output_per_million=10.00),
    "gpt-5": ModelPricing(input_per_million=1.25, cached_input_per_million=0.125, output_per_million=10.00),
    "gpt-4.1": ModelPricing(input_per_million=2.00, cached_input_per_million=0.50, output_per_million=8.00),
    "gpt-4.1-mini": ModelPricing(input_per_million=0.40, cached_input_per_million=0.10, output_per_million=1.60),
    "gpt-4.1-nano": ModelPricing(input_per_million=0.10, cached_input_per_million=0.025, output_per_million=0.40),
    "gpt-4o": ModelPricing(input_per_million=2.50, cached_input_per_million=1.25, output_per_million=10.00),
    "gpt-4o-mini": ModelPricing(input_per_million=0.15, cached_input_per_million=0.075, output_per_million=0.60),
}


class BillingService:
    """Credit ledger and pricing boundary shared by REST, ChatKit, MCP, and tests."""

    def __init__(self, *, settings: AppSettings, database: DatabaseManager) -> None:
        self._settings = settings
        self._database = database

    async def get_status(
        self,
        *,
        clerk_user_id: str,
        credit_floor_usd: float,
        role: str | None = None,
    ) -> CreditBalanceSummary:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            balance = await self._get_or_create_balance(session, clerk_user_id=clerk_user_id)
            await session.commit()
            return self._balance_summary(
                balance,
                credit_floor_usd=credit_floor_usd,
                role=role,
            )

    async def list_balance_amounts(self, clerk_user_ids: list[str]) -> dict[str, float]:
        if not clerk_user_ids:
            return {}
        await self._database.ensure_ready()
        async with self._database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(UserCreditBalance).where(UserCreditBalance.clerk_user_id.in_(clerk_user_ids))
                    )
                )
                .scalars()
                .all()
            )
            return {row.clerk_user_id: round(float(row.current_credit_usd), 8) for row in rows}

    async def grant_credit(
        self,
        *,
        clerk_user_id: str,
        credit_amount_usd: float,
        admin_clerk_user_id: str | None,
        note: str | None = None,
        source: str = "manual",
        payment_provider: str | None = None,
        payment_reference: str | None = None,
        credit_floor_usd: float,
        role: str | None = None,
    ) -> tuple[CreditBalanceSummary, CreditGrantSummary]:
        amount = round(float(credit_amount_usd), 8)
        if amount <= 0:
            raise ValueError("Credit amount must be positive.")
        await self._database.ensure_ready()
        async with self._database.session() as session:
            balance = await self._get_or_create_balance(session, clerk_user_id=clerk_user_id)
            grant = CreditGrant(
                clerk_user_id=clerk_user_id,
                admin_clerk_user_id=admin_clerk_user_id,
                credit_amount_usd=amount,
                source=source,
                note=_normalized_note(note),
                payment_provider=_normalized_note(payment_provider),
                payment_reference=_normalized_note(payment_reference),
                created_at=_utcnow(),
            )
            session.add(grant)
            balance.current_credit_usd = round(float(balance.current_credit_usd) + amount, 8)
            balance.updated_at = _utcnow()
            await session.commit()
            await session.refresh(balance)
            await session.refresh(grant)
            logger.info(
                "credit_granted clerk_user_id=%s admin_clerk_user_id=%s amount_usd=%.8f balance_usd=%.8f source=%s",
                clerk_user_id,
                admin_clerk_user_id,
                amount,
                balance.current_credit_usd,
                source,
            )
            return (
                self._balance_summary(balance, credit_floor_usd=credit_floor_usd, role=role),
                _grant_summary(grant),
            )

    async def assert_can_start_billable_operation(
        self,
        *,
        clerk_user_id: str,
        role: str | None,
        credit_floor_usd: float,
        operation_kind: str,
    ) -> None:
        if not self._settings.billing_enabled or role == "admin":
            return
        status = await self.get_status(
            clerk_user_id=clerk_user_id,
            credit_floor_usd=credit_floor_usd,
            role=role,
        )
        if status.current_credit_usd <= credit_floor_usd:
            logger.info(
                "credit_required clerk_user_id=%s operation=%s balance_usd=%.8f floor_usd=%.8f",
                clerk_user_id,
                operation_kind,
                status.current_credit_usd,
                credit_floor_usd,
            )
            raise CreditRequiredError("Credit limit reached. Add credit to continue using the app.")

    def calculate_usage_cost(
        self,
        *,
        model: str | None,
        usage: object,
    ) -> UsageCost:
        raw_usage = usage_to_mapping(usage)
        input_tokens = _int_from_mapping(raw_usage, "input_tokens")
        cached_input_tokens = _int_from_nested_mapping(raw_usage, "input_tokens_details", "cached_tokens")
        output_tokens = _int_from_mapping(raw_usage, "output_tokens")
        pricing = MODEL_PRICING_USD_PER_MILLION.get((model or "").strip())
        if pricing is None:
            note = f"No configured pricing for model {model!r}."
            if self._settings.billing_unknown_model_policy == "block":
                raise UnknownModelPricingError(note)
            logger.warning(
                "billing_unknown_model_pricing model=%s input_tokens=%s output_tokens=%s policy=zero",
                model,
                input_tokens,
                output_tokens,
            )
            return UsageCost(
                pricing_version=PRICING_VERSION,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                raw_usage=raw_usage,
                openai_cost_usd=0.0,
                platform_multiplier=self._settings.billing_platform_markup_multiplier,
                platform_cost_usd=0.0,
                note=note,
            )
        non_cached_input_tokens = max(input_tokens - cached_input_tokens, 0)
        openai_cost_usd = (
            (non_cached_input_tokens * pricing.input_per_million) / 1_000_000
            + (cached_input_tokens * pricing.cached_input_per_million) / 1_000_000
            + (output_tokens * pricing.output_per_million) / 1_000_000
        )
        platform_cost_usd = openai_cost_usd * self._settings.billing_platform_markup_multiplier
        return UsageCost(
            pricing_version=PRICING_VERSION,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            raw_usage=raw_usage,
            openai_cost_usd=round(openai_cost_usd, 8),
            platform_multiplier=self._settings.billing_platform_markup_multiplier,
            platform_cost_usd=round(platform_cost_usd, 8),
        )

    async def record_usage_cost(
        self,
        *,
        clerk_user_id: str,
        operation_kind: str,
        origin_surface: str,
        model: str | None,
        usage: object,
        event_key: str | None = None,
        thread_id: str | None = None,
        task_id: str | None = None,
        source_file_id: str | None = None,
        openai_response_id: str | None = None,
        openai_conversation_id: str | None = None,
        openai_request_id: str | None = None,
        note: str | None = None,
    ) -> CostEventSummary:
        cost = self.calculate_usage_cost(model=model, usage=usage)
        merged_note = _join_notes(note, cost.note)
        return await self.record_cost_event(
            clerk_user_id=clerk_user_id,
            operation_kind=operation_kind,
            origin_surface=origin_surface,
            event_key=event_key,
            thread_id=thread_id,
            task_id=task_id,
            source_file_id=source_file_id,
            openai_response_id=openai_response_id,
            openai_conversation_id=openai_conversation_id,
            openai_request_id=openai_request_id,
            model=model,
            pricing_version=cost.pricing_version,
            input_tokens=cost.input_tokens,
            cached_input_tokens=cost.cached_input_tokens,
            output_tokens=cost.output_tokens,
            raw_usage=cost.raw_usage,
            openai_cost_usd=cost.openai_cost_usd,
            platform_multiplier=cost.platform_multiplier,
            platform_cost_usd=cost.platform_cost_usd,
            note=merged_note,
        )

    async def record_cost_event(
        self,
        *,
        clerk_user_id: str,
        operation_kind: str,
        origin_surface: str,
        pricing_version: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        raw_usage: OpenAIUsagePayload,
        openai_cost_usd: float,
        platform_multiplier: float,
        platform_cost_usd: float,
        event_key: str | None = None,
        thread_id: str | None = None,
        task_id: str | None = None,
        source_file_id: str | None = None,
        openai_response_id: str | None = None,
        openai_conversation_id: str | None = None,
        openai_request_id: str | None = None,
        model: str | None = None,
        note: str | None = None,
    ) -> CostEventSummary:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            if event_key is not None:
                existing = await session.scalar(select(CostEvent).where(CostEvent.event_key == event_key))
                if existing is not None:
                    return _cost_event_summary(existing)
            balance = await self._get_or_create_balance(session, clerk_user_id=clerk_user_id)
            event = CostEvent(
                event_key=event_key,
                clerk_user_id=clerk_user_id,
                operation_kind=operation_kind,
                origin_surface=origin_surface,
                thread_id=thread_id,
                task_id=task_id,
                source_file_id=source_file_id,
                openai_response_id=openai_response_id,
                openai_conversation_id=openai_conversation_id,
                openai_request_id=openai_request_id,
                model=model,
                pricing_version=pricing_version,
                input_tokens=max(0, int(input_tokens)),
                cached_input_tokens=max(0, int(cached_input_tokens)),
                output_tokens=max(0, int(output_tokens)),
                raw_usage_json={},
                openai_cost_usd=round(float(openai_cost_usd), 8),
                platform_multiplier=round(float(platform_multiplier), 8),
                platform_cost_usd=round(float(platform_cost_usd), 8),
                note=_normalized_note(note),
                created_at=_utcnow(),
            )
            event.raw_usage = raw_usage
            session.add(event)
            if event.platform_cost_usd > 0:
                balance.current_credit_usd = round(float(balance.current_credit_usd) - event.platform_cost_usd, 8)
                balance.updated_at = _utcnow()
            await session.commit()
            await session.refresh(event)
            logger.info(
                "cost_event_recorded clerk_user_id=%s operation=%s surface=%s event_key=%s "
                "response=%s conversation=%s openai_cost_usd=%.8f platform_cost_usd=%.8f",
                clerk_user_id,
                operation_kind,
                origin_surface,
                event_key,
                openai_response_id,
                openai_conversation_id,
                event.openai_cost_usd,
                event.platform_cost_usd,
            )
            return _cost_event_summary(event)

    async def _get_or_create_balance(self, session: Any, *, clerk_user_id: str) -> UserCreditBalance:
        balance = await session.get(UserCreditBalance, clerk_user_id)
        if balance is None:
            balance = UserCreditBalance(
                clerk_user_id=clerk_user_id,
                current_credit_usd=0.0,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(balance)
            await session.flush()
        return balance

    def _balance_summary(
        self,
        balance: UserCreditBalance,
        *,
        credit_floor_usd: float,
        role: str | None,
    ) -> CreditBalanceSummary:
        current_credit_usd = round(float(balance.current_credit_usd), 8)
        billable = (not self._settings.billing_enabled) or role == "admin" or current_credit_usd > credit_floor_usd
        return CreditBalanceSummary(
            clerk_user_id=balance.clerk_user_id,
            current_credit_usd=current_credit_usd,
            credit_floor_usd=round(float(credit_floor_usd), 8),
            billable=billable,
            billing_enabled=self._settings.billing_enabled,
        )


def usage_to_mapping(usage: object) -> OpenAIUsagePayload:
    if isinstance(usage, Mapping):
        output: OpenAIUsagePayload = {}
        for key in ("requests", "input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                output[key] = int(value)
        input_details = usage.get("input_tokens_details")
        if isinstance(input_details, Mapping):
            cached_tokens = input_details.get("cached_tokens")
            if isinstance(cached_tokens, (int, float)) and not isinstance(cached_tokens, bool):
                output["input_tokens_details"] = {"cached_tokens": int(cached_tokens)}
        output_details = usage.get("output_tokens_details")
        if isinstance(output_details, Mapping):
            reasoning_tokens = output_details.get("reasoning_tokens")
            if isinstance(reasoning_tokens, (int, float)) and not isinstance(reasoning_tokens, bool):
                output["output_tokens_details"] = {"reasoning_tokens": int(reasoning_tokens)}
        return output
    output: OpenAIUsagePayload = {}
    for key in ("requests", "input_tokens", "output_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            output[key] = int(value)
    input_details = getattr(usage, "input_tokens_details", None)
    cached_tokens = getattr(input_details, "cached_tokens", None)
    if isinstance(cached_tokens, (int, float)) and not isinstance(cached_tokens, bool):
        output["input_tokens_details"] = {"cached_tokens": int(cached_tokens)}
    output_details = getattr(usage, "output_tokens_details", None)
    reasoning_tokens = getattr(output_details, "reasoning_tokens", None)
    if isinstance(reasoning_tokens, (int, float)) and not isinstance(reasoning_tokens, bool):
        output["output_tokens_details"] = {"reasoning_tokens": int(reasoning_tokens)}
    return output


def _grant_summary(grant: CreditGrant) -> CreditGrantSummary:
    return CreditGrantSummary(
        id=grant.id,
        clerk_user_id=grant.clerk_user_id,
        admin_clerk_user_id=grant.admin_clerk_user_id,
        credit_amount_usd=round(float(grant.credit_amount_usd), 8),
        source=grant.source,
        note=grant.note,
        payment_provider=grant.payment_provider,
        payment_reference=grant.payment_reference,
        created_at=grant.created_at,
    )


def _cost_event_summary(event: CostEvent) -> CostEventSummary:
    return CostEventSummary(
        id=event.id,
        event_key=event.event_key,
        clerk_user_id=event.clerk_user_id,
        operation_kind=event.operation_kind,
        origin_surface=event.origin_surface,
        thread_id=event.thread_id,
        task_id=event.task_id,
        source_file_id=event.source_file_id,
        openai_response_id=event.openai_response_id,
        openai_conversation_id=event.openai_conversation_id,
        model=event.model,
        pricing_version=event.pricing_version,
        input_tokens=event.input_tokens,
        cached_input_tokens=event.cached_input_tokens,
        output_tokens=event.output_tokens,
        openai_cost_usd=round(float(event.openai_cost_usd), 8),
        platform_multiplier=round(float(event.platform_multiplier), 8),
        platform_cost_usd=round(float(event.platform_cost_usd), 8),
        note=event.note,
        created_at=event.created_at,
    )


def _int_from_mapping(value: Mapping[str, object], key: str) -> int:
    raw_value = value.get(key)
    if isinstance(raw_value, bool):
        return 0
    if isinstance(raw_value, (int, float)):
        return max(0, int(raw_value))
    return 0


def _int_from_nested_mapping(value: Mapping[str, object], parent_key: str, key: str) -> int:
    nested = value.get(parent_key)
    if not isinstance(nested, Mapping):
        return 0
    return _int_from_mapping(nested, key)


def _normalized_note(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    return normalized_value or None


def _join_notes(first: str | None, second: str | None) -> str | None:
    notes = [note for note in (_normalized_note(first), _normalized_note(second)) if note]
    return " ".join(notes) if notes else None


def _utcnow() -> datetime:
    return datetime.now(UTC)

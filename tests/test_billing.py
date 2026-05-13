from __future__ import annotations

import pytest

from openai_vectorstore2_backend.app.core.config import AppSettings
from openai_vectorstore2_backend.app.db.session import DatabaseManager
from openai_vectorstore2_backend.app.services.billing import BillingService, CreditRequiredError, pricing_key_for_model


@pytest.mark.asyncio
async def test_billing_service_calculates_and_debits_idempotent_usage(
    configured_settings: AppSettings,
) -> None:
    database = DatabaseManager(configured_settings)
    billing = BillingService(settings=configured_settings, database=database)
    try:
        await billing.grant_credit(
            clerk_user_id="user_test",
            credit_amount_usd=1.0,
            admin_clerk_user_id="admin_test",
            note="test grant",
            credit_floor_usd=-1.0,
            role="user",
        )
        usage = {
            "requests": 1,
            "input_tokens": 1_000,
            "input_tokens_details": {"cached_tokens": 100},
            "output_tokens": 2_000,
            "total_tokens": 3_000,
        }

        first = await billing.record_usage_cost(
            clerk_user_id="user_test",
            operation_kind="chatkit_turn",
            origin_surface="chatkit",
            model="gpt-5.4-mini",
            usage=usage,
            event_key="chatkit:test:resp_1",
            thread_id="chat_test",
            openai_response_id="resp_1",
        )
        second = await billing.record_usage_cost(
            clerk_user_id="user_test",
            operation_kind="chatkit_turn",
            origin_surface="chatkit",
            model="gpt-5.4-mini",
            usage=usage,
            event_key="chatkit:test:resp_1",
            thread_id="chat_test",
            openai_response_id="resp_1",
        )
        status = await billing.get_status(clerk_user_id="user_test", credit_floor_usd=-1.0, role="user")
    finally:
        await database.close()

    assert first.id == second.id
    assert first.input_tokens == 1_000
    assert first.cached_input_tokens == 100
    assert first.output_tokens == 2_000
    assert first.openai_cost_usd == 0.0096825
    assert first.platform_cost_usd == 0.01258725
    assert status.current_credit_usd == 0.98741275


@pytest.mark.asyncio
async def test_billing_service_blocks_non_admins_at_credit_floor(
    configured_settings: AppSettings,
) -> None:
    database = DatabaseManager(configured_settings)
    billing = BillingService(settings=configured_settings, database=database)
    try:
        with pytest.raises(CreditRequiredError):
            await billing.assert_can_start_billable_operation(
                clerk_user_id="user_low",
                role="user",
                credit_floor_usd=0.0,
                operation_kind="qa",
            )
        await billing.assert_can_start_billable_operation(
            clerk_user_id="admin_low",
            role="admin",
            credit_floor_usd=0.0,
            operation_kind="qa",
        )
    finally:
        await database.close()


def test_billing_model_pricing_uses_longest_known_family_prefix() -> None:
    assert pricing_key_for_model("gpt-5.5-2026-04-23") == "gpt-5.5"
    assert pricing_key_for_model("gpt-5.4-mini-2026-04-23") == "gpt-5.4-mini"
    assert pricing_key_for_model("unknown-model-2026-04-23") is None


def test_billing_service_calculates_gpt_55_dated_model_pricing(
    configured_settings: AppSettings,
) -> None:
    database = DatabaseManager(configured_settings)
    billing = BillingService(settings=configured_settings, database=database)
    usage = {
        "requests": 1,
        "input_tokens": 1_000,
        "input_tokens_details": {"cached_tokens": 100},
        "output_tokens": 2_000,
        "total_tokens": 3_000,
    }

    cost = billing.calculate_usage_cost(model="gpt-5.5-2026-04-23", usage=usage)

    assert cost.openai_cost_usd == 0.06455
    assert cost.platform_cost_usd == 0.083915
    assert cost.note == "Priced as gpt-5.5."

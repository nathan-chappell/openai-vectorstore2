from __future__ import annotations

import pytest

from backend.app.integrations.chat_completions import (
    ChatCompletionsCompactionSummary,
    ChatCompletionMessagePayload,
    chat_completions_model_context,
    compaction_summary_message,
    estimate_chat_completion_message_tokens,
    known_chat_completions_context_window,
    render_compaction_summary,
    should_compact_chat_completion_messages,
)


def test_known_chat_completions_context_window_uses_configured_override() -> None:
    assert (
        known_chat_completions_context_window(
            "private-model",
            configured_context_window_tokens=65_536,
        )
        == 65_536
    )


def test_known_chat_completions_context_window_rejects_unknown_without_override() -> None:
    with pytest.raises(ValueError, match="CHAT_COMPLETIONS_CONTEXT_WINDOW_TOKENS"):
        known_chat_completions_context_window(
            "private-model",
            configured_context_window_tokens=None,
        )


def test_chat_completions_model_context_uses_75_25_default_shape() -> None:
    context = chat_completions_model_context(
        "oss-small",
        configured_context_window_tokens=None,
        output_token_reserve=4_096,
        compaction_remaining_ratio=0.25,
        compaction_compress_ratio=0.50,
    )

    assert context.context_window_tokens == 131_072
    assert context.output_token_reserve == 4_096
    assert context.history_token_budget == 126_976
    assert context.compaction_trigger_tokens == 95_232
    assert context.compaction_target_tokens == 47_616


def test_chat_completion_token_estimate_and_compaction_trigger() -> None:
    small_messages: list[ChatCompletionMessagePayload] = [
        {"role": "system", "content": "Use tools carefully."},
        {"role": "user", "content": "Summarize the selected source."},
    ]
    large_messages: list[ChatCompletionMessagePayload] = [
        {"role": "user", "content": "x" * 2_000},
        {"role": "assistant", "content": "y" * 2_000},
    ]
    context = chat_completions_model_context(
        "tiny-test-model",
        configured_context_window_tokens=2_048,
        output_token_reserve=512,
        compaction_remaining_ratio=0.25,
        compaction_compress_ratio=0.50,
    )

    assert estimate_chat_completion_message_tokens(small_messages) < context.compaction_trigger_tokens
    assert not should_compact_chat_completion_messages(small_messages, context=context)
    assert should_compact_chat_completion_messages(large_messages, context=context)


def test_compaction_summary_message_uses_stable_sections() -> None:
    summary = ChatCompletionsCompactionSummary(
        data="- source_a: Alpha paper, ready",
        conversation="The user is comparing retrieval strategies.",
        remarks="Keep citations as chatkit source links.",
    )

    rendered = render_compaction_summary(summary)
    message = compaction_summary_message(summary)

    assert "## Data" in rendered
    assert "## Conversation" in rendered
    assert "## Remarks" in rendered
    assert message["role"] == "system"
    assert message["content"] == rendered

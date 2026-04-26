from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from chatkit.types import (
    AssistantMessageContent,
    AssistantMessageItem,
    ClientToolCallItem,
    InferenceOptions,
    UserMessageItem,
    UserMessageTextContent,
)

from backend.app.chatkit.server import (
    chatkit_model_settings_for_model,
    chatkit_metadata_with_openai_state,
    chatkit_openai_state,
    chatkit_progress_update_event,
    chatkit_request_log_summary,
    pending_chatkit_thread_items,
    selected_scope,
)
from backend.app.chatkit.store import VectorstoreChatContext

NOW = datetime(2026, 4, 27, tzinfo=UTC)


def test_selected_scope_falls_back_when_model_passes_openai_file_ids() -> None:
    context = VectorstoreChatContext(
        clerk_user_id="local-dev",
        user_email="local-dev@example.com",
        display_name="Local Developer",
        bearer_token="local-dev",
        selected_source_ids=["source_a", "source_b"],
        thread_origin="web",
    )

    assert selected_scope(context, ["file-openai-a", "file-openai-b"]) == ["source_a", "source_b"]


def test_selected_scope_preserves_explicit_app_source_ids() -> None:
    context = VectorstoreChatContext(
        clerk_user_id="local-dev",
        user_email="local-dev@example.com",
        display_name="Local Developer",
        bearer_token="local-dev",
        selected_source_ids=["source_a"],
        thread_origin="web",
    )

    assert selected_scope(context, ["source_b", "file-openai-a"]) == ["source_b"]


def test_chatkit_progress_update_event_maps_research_icons() -> None:
    assert chatkit_progress_update_event("library", "Build").icon == "book-open"
    assert chatkit_progress_update_event("folder", "Folder").icon == "lucide:folder"
    assert chatkit_progress_update_event("download", "Download").icon == "lucide:download"
    assert chatkit_progress_update_event("copy-check", "Duplicate").icon == "lucide:copy-check"
    assert chatkit_progress_update_event("alert-circle", "Failed").icon == "info"
    assert chatkit_progress_update_event("search", "Search").icon == "lucide:search"


def test_chatkit_request_log_summary_extracts_op_and_thread() -> None:
    summary = chatkit_request_log_summary(
        b'{"type":"threads.add_user_message","params":{"thread_id":"chat_123","input":{"content":"hi"}}}'
    )

    assert summary.op == "threads.add_user_message"
    assert summary.thread_id == "chat_123"


def test_chatkit_request_log_summary_tolerates_invalid_payloads() -> None:
    summary = chatkit_request_log_summary(b"not-json")

    assert summary.op == "unknown"
    assert summary.thread_id is None


def test_chatkit_openai_state_round_trips_metadata() -> None:
    metadata = chatkit_metadata_with_openai_state(
        {"existing": "value"},
        conversation_id="conv_123",
        previous_response_id="resp_456",
    )

    state = chatkit_openai_state(metadata)

    assert metadata["existing"] == "value"
    assert state.conversation_id == "conv_123"
    assert state.previous_response_id == "resp_456"


def test_chatkit_model_settings_enable_server_side_compaction() -> None:
    settings = chatkit_model_settings_for_model("gpt-5.4-mini", compact_threshold=80_000)

    assert settings.extra_body == {"context_management": {"compact_threshold": 80_000}}
    assert settings.reasoning is not None
    assert settings.reasoning.effort == "low"
    assert settings.reasoning.summary == "auto"


def test_chatkit_model_settings_can_disable_compaction() -> None:
    settings = chatkit_model_settings_for_model("gpt-4.1", compact_threshold=None)

    assert settings.extra_body is None
    assert settings.reasoning is None


def test_pending_chatkit_items_replays_history_before_conversation_exists() -> None:
    items = [_user_message("user_1"), _assistant_message("assistant_1"), _user_message("user_2")]

    pending = pending_chatkit_thread_items(items, has_openai_conversation=False)

    assert [item.id for item in pending] == ["user_1", "assistant_1", "user_2"]


def test_pending_chatkit_items_uses_only_items_after_last_assistant_with_conversation() -> None:
    items = [_user_message("user_1"), _assistant_message("assistant_1"), _user_message("user_2")]

    pending = pending_chatkit_thread_items(items, has_openai_conversation=True)

    assert [item.id for item in pending] == ["user_2"]


def test_pending_chatkit_items_keeps_completed_client_tool_output_with_conversation() -> None:
    completed_tool = _client_tool_call("tool_1", status="completed")
    pending_tool = _client_tool_call("tool_2", status="pending")

    assert pending_chatkit_thread_items(
        [_user_message("user_1"), _assistant_message("assistant_1"), completed_tool],
        has_openai_conversation=True,
    ) == [completed_tool]
    assert pending_chatkit_thread_items(
        [_user_message("user_1"), _assistant_message("assistant_1"), pending_tool],
        has_openai_conversation=True,
    ) == []


def _user_message(item_id: str) -> UserMessageItem:
    return UserMessageItem(
        id=item_id,
        thread_id="thread_1",
        created_at=NOW,
        content=[UserMessageTextContent(text=item_id)],
        inference_options=InferenceOptions(),
    )


def _assistant_message(item_id: str) -> AssistantMessageItem:
    return AssistantMessageItem(
        id=item_id,
        thread_id="thread_1",
        created_at=NOW,
        content=[AssistantMessageContent(text=item_id)],
    )


def _client_tool_call(item_id: str, *, status: Literal["pending", "completed"]) -> ClientToolCallItem:
    return ClientToolCallItem(
        id=item_id,
        thread_id="thread_1",
        created_at=NOW,
        status=status,
        call_id=f"call_{item_id}",
        name="set_file_selection",
        arguments={"source_ids": ["source_a"]},
        output={"ok": True} if status == "completed" else None,
    )

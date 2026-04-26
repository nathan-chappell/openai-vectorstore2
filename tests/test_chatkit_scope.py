from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from chatkit.types import (
    AssistantMessageContent,
    AssistantMessageItem,
    ClientToolCallItem,
    InferenceOptions,
    ThreadMetadata,
    UserMessageItem,
    UserMessageTextContent,
)

from backend.app.chatkit.server import (
    apply_agent_thread_title,
    clean_thread_title,
    chatkit_source_deeplink,
    chatkit_model_settings_for_model,
    chatkit_metadata_with_openai_state,
    chatkit_openai_state,
    chatkit_progress_update_event,
    chatkit_request_log_summary,
    compact_chatkit_hit_payload,
    compact_chatkit_source_payload,
    compact_chatkit_tag,
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

    assert settings.extra_body == {
        "context_management": [{"type": "compaction", "compact_threshold": 80_000}]
    }
    assert settings.reasoning is not None
    assert settings.reasoning.effort == "low"
    assert settings.reasoning.summary == "auto"


def test_chatkit_model_settings_can_disable_compaction() -> None:
    settings = chatkit_model_settings_for_model("gpt-4.1", compact_threshold=None)

    assert settings.extra_body is None
    assert settings.reasoning is None


def test_clean_thread_title_collapses_whitespace_and_bounds_length() -> None:
    title = clean_thread_title("  Research\nlibrary    context overflow investigation.   ")

    assert title == "Research library context overflow investigation"
    assert len(clean_thread_title("x" * 100)) == 72


def test_apply_agent_thread_title_updates_title_and_metadata() -> None:
    thread = ThreadMetadata(
        id="chat_title_test",
        created_at=NOW,
        metadata={"existing": "value"},
    )

    title = apply_agent_thread_title(thread, " Selected file context fixes ")

    assert title == "Selected file context fixes"
    assert thread.title == "Selected file context fixes"
    assert thread.metadata["existing"] == "value"
    assert thread.metadata["agent_thread_title"] == "Selected file context fixes"
    assert isinstance(thread.metadata["agent_thread_title_updated_at"], str)


def test_chatkit_source_deeplink_points_to_source() -> None:
    assert chatkit_source_deeplink("source_123") == "chatkit-link://source?source_id=source_123"
    assert chatkit_source_deeplink("source_123", locator={"type": "page_range", "start_page": 4, "end_page": 5}) == (
        "chatkit-link://source?source_id=source_123&locator=pp.+4-5"
    )


def test_compact_chatkit_tag_keeps_slug_and_optional_name() -> None:
    assert compact_chatkit_tag({"id": "tag_1", "name": "Multi Head Attention", "slug": "multi-head-attention"}) == {
        "slug": "multi-head-attention",
        "name": "Multi Head Attention",
    }


def test_compact_chatkit_source_payload_keeps_citation_fields_only() -> None:
    compact = compact_chatkit_source_payload(
        {
            "id": "source_1",
            "display_title": "Attention Is All You Need",
            "original_filename": "1706.03762.pdf",
            "source_kind": "pdf",
            "status": "ready",
            "virtual_path": "/Research/attention/Attention Is All You Need.pdf",
            "summary": "Transformer paper summary.",
            "openai_vector_file_id": "file_ignored",
            "tags": [{"id": "tag_1", "name": "Transformer", "slug": "transformer"}],
        }
    )

    assert compact == {
        "id": "source_1",
        "type": "pdf",
        "name": "Attention Is All You Need",
        "path": "/Research/attention/Attention Is All You Need.pdf",
        "status": "ready",
        "summary": "Transformer paper summary.",
        "tags": ["transformer"],
        "citation_link": "chatkit-link://source?source_id=source_1",
    }


def test_compact_chatkit_hit_payload_adds_source_link_and_locator_label() -> None:
    compact = compact_chatkit_hit_payload(
        {
            "chunk_id": "chunk_1",
            "source_file_id": "source_1",
            "source_title": "Attention Is All You Need",
            "score": 0.87,
            "title": "Multi-head attention",
            "summary": "Describes scaled dot-product attention.",
            "text": "x" * 1500,
            "tags": ["attention"],
            "locator": {"type": "page_range", "start_page": 3, "end_page": 3},
            "attributes": {"debug": "ignored"},
        }
    )

    assert compact["id"] == "source_1"
    assert compact["locator"] == "p. 3"
    assert compact["citation_link"] == "chatkit-link://source?source_id=source_1&locator=p.+3"
    assert compact["tags"] == ["attention"]
    assert isinstance(compact["text"], str)
    assert len(compact["text"]) == 1200
    assert "attributes" not in compact


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

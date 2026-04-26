from __future__ import annotations

from backend.app.chatkit.server import chatkit_progress_update_event, chatkit_request_log_summary, selected_scope
from backend.app.chatkit.store import VectorstoreChatContext


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

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from time import perf_counter
from typing import Any, cast
from urllib.parse import urlencode

from agents import Agent, Runner, StopAtTools, function_tool
from agents.model_settings import ModelSettings
from agents.tool import Tool
from agents.tool_context import ToolContext
from chatkit.agents import AgentContext as ChatKitAgentContext
from chatkit.agents import ClientToolCall
from chatkit.agents import ThreadItemConverter, stream_agent_response
from chatkit.server import ChatKitServer
from chatkit.types import (
    Attachment,
    ChatKitReq,
    ProgressUpdateEvent,
    ThreadItem,
    ThreadMetadata,
    ThreadStreamEvent,
    UserMessageItem,
)
from openai.types.responses.response_input_item_param import Message, ResponseInputItemParam
from openai.types.responses import ResponseInputContentParam, ResponseInputTextParam
from openai.types.shared import Reasoning
from pydantic import TypeAdapter

from backend.app.chatkit.store import VectorstoreChatContext, VectorstoreChatStore, thread_metadata_with_scope
from backend.app.core.config import AppSettings
from backend.app.core.openai_observability import openai_platform_log_url, openai_platform_log_urls
from backend.app.integrations.openai_gateway import OpenAIGateway
from backend.app.schemas import (
    BranchSearchRequest,
    FreeformRequest,
    ImageGenerationRequest,
    QaRequest,
    ResearchCandidateIngestRequest,
    ResearchCandidateStatusUpdateRequest,
    ResearchImportCreateRequest,
    ResearchLibraryBuildRequest,
    SearchRequest,
    TaskKind,
    VoiceGenerationRequest,
)
from backend.app.services import ActionService, ResearchImportService, SourceService

logger = logging.getLogger("chatkit.server")

MODEL_ALIASES = {
    "default": "gpt-5.4-mini",
    "lightweight": "gpt-5.4-mini",
    "balanced": "gpt-5.4-mini",
    "powerful": "gpt-5.5",
}
MAX_AGENT_TURNS = 20
CHATKIT_SELECTED_FILE_INPUT_LIMIT = 2
CHATKIT_SELECTED_FILE_SINGLE_MAX_BYTES = 250_000
CHATKIT_SELECTED_FILE_TOTAL_MAX_BYTES = 350_000
CHATKIT_TEXT_SNIPPET_MAX_CHARS = 1_200
CHATKIT_DETAIL_CHUNK_LIMIT = 8
THREAD_TITLE_MAX_CHARS = 72
STOP_AT_TOOL_NAMES = [
    "set_file_selection",
    "reveal_file",
    "set_file_search",
    "delete_source",
    "delete_filesystem_entries",
]

ChatKitToolContext = ToolContext[ChatKitAgentContext[VectorstoreChatContext]]

CHATKIT_PROGRESS_ICON_ALIASES = {
    "alert-circle": "info",
    "copy-check": "lucide:copy-check",
    "download": "lucide:download",
    "folder": "lucide:folder",
    "library": "book-open",
}


@dataclass(frozen=True, slots=True)
class ChatKitRequestLogSummary:
    op: str
    thread_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatKitOpenAIState:
    conversation_id: str | None = None
    previous_response_id: str | None = None


def chatkit_request_log_summary(raw_request: bytes | str) -> ChatKitRequestLogSummary:
    try:
        parsed = json.loads(raw_request)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ChatKitRequestLogSummary(op="unknown")
    if not isinstance(parsed, dict):
        return ChatKitRequestLogSummary(op="unknown")
    raw_op = parsed.get("type")
    op = raw_op if isinstance(raw_op, str) and raw_op else "unknown"
    params = parsed.get("params")
    if not isinstance(params, dict):
        return ChatKitRequestLogSummary(op=op)
    thread_id = params.get("thread_id")
    return ChatKitRequestLogSummary(op=op, thread_id=thread_id if isinstance(thread_id, str) and thread_id else None)


def chatkit_progress_update_event(icon: str, text: str) -> ProgressUpdateEvent:
    normalized_icon = icon.strip()
    if not normalized_icon:
        return ProgressUpdateEvent(text=text)
    if normalized_icon.startswith(("lucide:", "vendor:")):
        return ProgressUpdateEvent(icon=normalized_icon, text=text)
    aliased_icon = CHATKIT_PROGRESS_ICON_ALIASES.get(normalized_icon)
    if aliased_icon is not None:
        return ProgressUpdateEvent(icon=aliased_icon, text=text)
    return ProgressUpdateEvent(icon=f"lucide:{normalized_icon}", text=text)


async def stream_chatkit_progress(ctx: ChatKitToolContext, icon: str, text: str) -> None:
    await ctx.context.stream(chatkit_progress_update_event(icon, text))


def chatkit_openai_state(metadata: Mapping[str, Any] | None) -> ChatKitOpenAIState:
    metadata_dict = _metadata_dict(metadata)
    return ChatKitOpenAIState(
        conversation_id=_string_or_none(metadata_dict.get("openai_conversation_id")),
        previous_response_id=_string_or_none(metadata_dict.get("openai_previous_response_id")),
    )


def chatkit_metadata_with_openai_state(
    metadata: Mapping[str, Any] | None,
    *,
    conversation_id: str,
    previous_response_id: str | None,
) -> dict[str, object]:
    output = _metadata_dict(metadata)
    output["openai_conversation_id"] = conversation_id
    if previous_response_id is not None:
        output["openai_previous_response_id"] = previous_response_id
    return output


def pending_chatkit_thread_items(
    chronological_items: Sequence[ThreadItem],
    *,
    has_openai_conversation: bool,
) -> list[ThreadItem]:
    items = list(chronological_items)
    if not has_openai_conversation:
        return items

    boundary_index = -1
    for index, item in enumerate(items):
        if item.type in {"assistant_message", "client_tool_call"}:
            boundary_index = index
    if boundary_index < 0:
        return items

    boundary_item = items[boundary_index]
    if boundary_index == len(items) - 1:
        if boundary_item.type == "client_tool_call" and boundary_item.status == "completed":
            return [boundary_item]
        return []
    return items[boundary_index + 1 :]


class VectorstoreChatKitServer(ChatKitServer[VectorstoreChatContext]):
    """ChatKit surface that talks to app services directly instead of looping through MCP."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        store: VectorstoreChatStore,
        sources: SourceService,
        research: ResearchImportService,
        actions: ActionService,
        openai: OpenAIGateway,
    ) -> None:
        super().__init__(store=store, attachment_store=store)
        self._settings = settings
        self._store = store
        self._sources = sources
        self._research = research
        self._actions = actions
        self._openai = openai
        self._converter = VectorstoreThreadItemConverter()

    async def build_request_context(
        self,
        raw_request: bytes | str,
        *,
        clerk_user_id: str,
        user_email: str | None,
        display_name: str,
        bearer_token: str,
        request_app: Any,
    ) -> VectorstoreChatContext:
        del request_app
        parsed_request = TypeAdapter(ChatKitReq).validate_json(raw_request)
        metadata = _metadata_dict(parsed_request.metadata)
        return VectorstoreChatContext(
            clerk_user_id=clerk_user_id,
            user_email=user_email,
            display_name=display_name,
            bearer_token=bearer_token,
            selected_source_ids=_string_list(metadata.get("selected_source_ids")),
            thread_origin=_string_or_none(metadata.get("origin")),
        )

    def request_log_summary(self, raw_request: bytes | str) -> ChatKitRequestLogSummary:
        return chatkit_request_log_summary(raw_request)

    def build_user_context(
        self,
        *,
        clerk_user_id: str,
        user_email: str | None,
        display_name: str,
        bearer_token: str,
    ) -> VectorstoreChatContext:
        return VectorstoreChatContext(
            clerk_user_id=clerk_user_id,
            user_email=user_email,
            display_name=display_name,
            bearer_token=bearer_token,
            selected_source_ids=[],
            thread_origin=None,
        )

    async def create_uploaded_attachment(
        self,
        *,
        filename: str,
        declared_media_type: str | None,
        payload: bytes,
        tag_ids: list[str],
        user_guidance: str | None,
        thread_id: str | None,
        context: VectorstoreChatContext,
    ) -> Attachment:
        attachment = await self._store.create_source_attachment(
            filename=filename,
            declared_media_type=declared_media_type,
            payload=payload,
            tag_ids=tag_ids,
            user_guidance=user_guidance,
            thread_id=thread_id,
            context=context,
        )
        metadata = _metadata_dict(attachment.metadata)
        logger.info(
            "chatkit_attachment_uploaded attachment_id=%s source_id=%s task_id=%s thread_id=%s bytes=%s",
            attachment.id,
            metadata.get("source_id"),
            metadata.get("task_id"),
            thread_id,
            len(payload),
        )
        return attachment

    def respond(
        self,
        thread: ThreadMetadata,
        input_user_message: UserMessageItem | None,
        context: VectorstoreChatContext,
    ) -> AsyncIterator[ThreadStreamEvent]:
        return self._respond(thread=thread, input_user_message=input_user_message, context=context)

    async def _respond(
        self,
        *,
        thread: ThreadMetadata,
        input_user_message: UserMessageItem | None,
        context: VectorstoreChatContext,
    ) -> AsyncIterator[ThreadStreamEvent]:
        started_at = perf_counter()
        requested_model = self._resolve_requested_model(input_user_message=input_user_message)
        if thread.title is None and input_user_message is not None:
            thread.title = _title_from_user_message(input_user_message)
        thread.metadata = thread_metadata_with_scope(thread.metadata, context)
        openai_state = chatkit_openai_state(thread.metadata)
        had_openai_conversation = openai_state.conversation_id is not None
        conversation_id = openai_state.conversation_id
        if conversation_id is None:
            conversation_id = await self._create_openai_conversation(thread=thread, context=context)
            thread.metadata = chatkit_metadata_with_openai_state(
                thread.metadata,
                conversation_id=conversation_id,
                previous_response_id=openai_state.previous_response_id,
            )
        await self.store.save_thread(thread, context)

        agent_input = await self._agent_input_for_turn(
            thread=thread,
            input_user_message=input_user_message,
            context=context,
            has_openai_conversation=had_openai_conversation,
        )
        selected_source_context = await self._selected_source_context_items(
            context=context,
            attach_files=input_user_message is not None,
        )
        if selected_source_context:
            agent_input = selected_source_context + agent_input

        logger.info(
            "chat turn started thread=%s model=%s conversation=%s conversation_log_url=%s "
            "previous_response=%s previous_response_log_url=%s selected_sources=%s input_items=%s "
            "history_mode=%s compact_threshold=%s",
            thread.id,
            requested_model,
            conversation_id,
            openai_platform_log_url(conversation_id),
            openai_state.previous_response_id,
            openai_platform_log_url(openai_state.previous_response_id),
            len(context.selected_source_ids),
            len(agent_input),
            "pending" if had_openai_conversation else "bootstrap",
            self._settings.openai_context_compact_threshold,
        )
        agent_context = ChatKitAgentContext[VectorstoreChatContext](
            thread=thread,
            store=self.store,
            request_context=context,
        )
        agent = Agent[ChatKitAgentContext[VectorstoreChatContext]](
            name="indexed_file_vectorstore_agent",
            model=requested_model,
            model_settings=chatkit_model_settings_for_model(
                requested_model,
                compact_threshold=self._settings.openai_context_compact_threshold,
            ),
            tools=self._build_tools(),
            instructions=self._agent_instructions,
            tool_use_behavior=StopAtTools(stop_at_tool_names=STOP_AT_TOOL_NAMES),
        )
        result = Runner.run_streamed(
            agent,
            agent_input,
            context=agent_context,
            max_turns=MAX_AGENT_TURNS,
            conversation_id=conversation_id,
        )
        try:
            async for event in stream_agent_response(agent_context, result):
                yield event
        except Exception:
            partial_response_ids = [response.response_id for response in result.raw_responses if response.response_id]
            logger.error(
                "chat turn failed thread=%s model=%s conversation=%s conversation_log_url=%s "
                "previous_response=%s previous_response_log_url=%s response=%s responses=%s "
                "openai_log_url=%s openai_log_urls=%s (%.1fms)",
                thread.id,
                requested_model,
                conversation_id,
                openai_platform_log_url(conversation_id),
                openai_state.previous_response_id,
                openai_platform_log_url(openai_state.previous_response_id),
                result.last_response_id,
                ",".join(partial_response_ids) or None,
                openai_platform_log_url(result.last_response_id),
                ",".join(openai_platform_log_urls(partial_response_ids)) or None,
                (perf_counter() - started_at) * 1000,
            )
            raise

        response_ids = [response.response_id for response in result.raw_responses if response.response_id]
        for response_index, raw_response in enumerate(result.raw_responses, start=1):
            if raw_response.response_id is None:
                continue
            logger.info(
                "chat openai response thread=%s model=%s #%s response=%s openai_log_url=%s request=%s",
                thread.id,
                requested_model,
                response_index,
                raw_response.response_id,
                openai_platform_log_url(raw_response.response_id),
                raw_response.request_id,
            )

        result_conversation_id = (
            cast(str | None, getattr(result, "conversation_id", None))
            or cast(str | None, getattr(result, "_conversation_id", None))
            or conversation_id
        )
        thread.metadata = chatkit_metadata_with_openai_state(
            thread.metadata,
            conversation_id=result_conversation_id,
            previous_response_id=result.last_response_id,
        )
        await self.store.save_thread(thread, context)
        logger.info(
            "chat turn completed thread=%s model=%s response=%s responses=%s openai_log_url=%s "
            "openai_log_urls=%s conversation=%s conversation_log_url=%s (%.1fms)",
            thread.id,
            requested_model,
            result.last_response_id,
            ",".join(response_ids) or None,
            openai_platform_log_url(result.last_response_id),
            ",".join(openai_platform_log_urls(response_ids)) or None,
            result_conversation_id,
            openai_platform_log_url(result_conversation_id),
            (perf_counter() - started_at) * 1000,
        )

    async def _create_openai_conversation(
        self,
        *,
        thread: ThreadMetadata,
        context: VectorstoreChatContext,
    ) -> str:
        metadata = {
            "app": self._settings.app_name,
            "surface": "chatkit",
            "thread_id": thread.id,
            "user_id": context.clerk_user_id,
        }
        if context.thread_origin:
            metadata["origin"] = context.thread_origin[:512]
        if isinstance(thread.title, str) and thread.title.strip():
            metadata["thread_title"] = thread.title.strip()[:512]
        conversation_id = await self._openai.create_conversation(metadata=metadata)
        logger.info(
            "chat conversation created thread=%s conversation=%s conversation_log_url=%s",
            thread.id,
            conversation_id,
            openai_platform_log_url(conversation_id),
        )
        return conversation_id

    async def _agent_input_for_turn(
        self,
        *,
        thread: ThreadMetadata,
        input_user_message: UserMessageItem | None,
        context: VectorstoreChatContext,
        has_openai_conversation: bool,
    ) -> list[ResponseInputItemParam]:
        history = await self.store.load_thread_items(
            thread.id,
            after=None,
            limit=80,
            order="asc",
            context=context,
        )
        items = list(history.data)
        if input_user_message is not None:
            items = [item for item in items if item.id != input_user_message.id]
            items.append(input_user_message)
        items = pending_chatkit_thread_items(items, has_openai_conversation=has_openai_conversation)
        return await self._converter.to_agent_input(items)

    async def _selected_source_context_items(
        self,
        *,
        context: VectorstoreChatContext,
        attach_files: bool,
    ) -> list[ResponseInputItemParam]:
        if not context.selected_source_ids:
            return []
        if not attach_files:
            return []
        file_inputs = await self._sources.ensure_source_file_inputs(
            clerk_user_id=context.clerk_user_id,
            source_ids=context.selected_source_ids,
            limit=CHATKIT_SELECTED_FILE_INPUT_LIMIT,
            max_file_bytes=CHATKIT_SELECTED_FILE_SINGLE_MAX_BYTES,
            max_total_bytes=CHATKIT_SELECTED_FILE_TOTAL_MAX_BYTES,
        )
        selected_count = len(list(dict.fromkeys(context.selected_source_ids)))
        source_lines = [
            f"- {item.virtual_path}: app source_id={item.source_id}; media_type={item.media_type}; "
            f"bytes={item.byte_size}; attached OpenAI file_id={item.file_id}"
            for item in file_inputs
        ]
        if source_lines:
            source_text = "\n".join(source_lines)
        else:
            source_text = "- No selected files were small enough for direct attachment on this turn."
        remaining_count = max(0, selected_count - len(file_inputs))
        remaining_text = (
            f" {remaining_count} selected file{'' if remaining_count == 1 else 's'} "
            "remain available through retrieval tools instead of direct attachment."
            if remaining_count
            else ""
        )
        content: list[dict[str, object]] = [
            {
                "type": "input_text",
                "text": (
                    f"The user selected {selected_count} file{'' if selected_count == 1 else 's'} in the app explorer. "
                    "Treat the selection as the primary retrieval scope unless the user asks to widen it. "
                    "Use search_chunks or answer_from_library to search the full selected scope; those tools automatically use "
                    "the current selection when selected_source_ids is omitted. "
                    "When calling app tools, pass app source_id values as selected_source_ids; do not pass OpenAI file_id values. "
                    f"Attached {len(file_inputs)} small selected file{'' if len(file_inputs) == 1 else 's'} for direct reading."
                    f"{remaining_text}\n{source_text}"
                ),
            }
        ]
        content.extend(
            {
                "type": "input_file",
                "file_id": item.file_id,
            }
            for item in file_inputs
        )
        return [
            cast(
                ResponseInputItemParam,
                Message(
                    role="user",
                    type="message",
                    content=cast(Any, content),
                ),
            )
        ]

    def tool_names(self) -> set[str]:
        return {tool.name for tool in self._build_tools()}

    def _build_tools(self) -> list[Tool]:
        @function_tool(name_override="name_thread")
        async def name_thread_tool(ctx: ChatKitToolContext, title: str) -> dict[str, object]:
            """Set a concise user-facing title for the current ChatKit thread."""
            request_context = ctx.context.request_context
            thread = ctx.context.thread
            previous_title = thread.title
            next_title = apply_agent_thread_title(thread, title)
            await self.store.save_thread(thread, request_context)
            logger.info(
                "chat_thread_named thread=%s title=%s changed=%s",
                thread.id,
                next_title,
                previous_title != next_title,
            )
            return {
                "thread_id": thread.id,
                "title": next_title,
                "changed": previous_title != next_title,
            }

        @function_tool(name_override="list_sources")
        async def list_sources_tool(
            ctx: ChatKitToolContext,
            query: str | None = None,
            tag_ids: list[str] | None = None,
            tag_match_mode: str = "all",
            page_size: int = 20,
        ) -> dict[str, object]:
            """List sources in the user's indexed file library."""
            request_context = ctx.context.request_context
            response = await self._sources.list_sources(
                clerk_user_id=request_context.clerk_user_id,
                query=query,
                tag_ids=tag_ids or [],
                tag_match_mode="any" if tag_match_mode == "any" else "all",
                page=1,
                page_size=max(1, min(page_size, 50)),
            )
            return compact_chatkit_file_list_payload(response.model_dump(mode="json"))

        @function_tool(name_override="list_filesystem")
        async def list_filesystem_tool(ctx: ChatKitToolContext, folder_id: str | None = None) -> dict[str, object]:
            """List the children of a virtual filesystem folder."""
            request_context = ctx.context.request_context
            response = await self._sources.list_filesystem(
                clerk_user_id=request_context.clerk_user_id,
                folder_id=folder_id,
            )
            return compact_chatkit_filesystem_list_payload(response.model_dump(mode="json"))

        @function_tool(name_override="find_files")
        async def find_files_tool(
            ctx: ChatKitToolContext,
            query: str | None = None,
            tag_ids: list[str] | None = None,
            tag_match_mode: str = "all",
            page_size: int = 20,
        ) -> dict[str, object]:
            """Find virtual files and folders by path, filename, tags, and vector-store retrieval."""
            request_context = ctx.context.request_context
            response = await self._sources.search_filesystem(
                clerk_user_id=request_context.clerk_user_id,
                query=query,
                tag_ids=tag_ids or [],
                tag_match_mode="any" if tag_match_mode == "any" else "all",
                page=1,
                page_size=max(1, min(page_size, 50)),
            )
            return compact_chatkit_filesystem_search_payload(response.model_dump(mode="json"))

        @function_tool(name_override="create_folder")
        async def create_folder_tool(
            ctx: ChatKitToolContext,
            name: str,
            parent_id: str | None = None,
        ) -> dict[str, object]:
            """Create a folder in the virtual filesystem."""
            request_context = ctx.context.request_context
            response = await self._sources.create_folder(
                clerk_user_id=request_context.clerk_user_id,
                parent_id=parent_id,
                name=name,
            )
            return compact_chatkit_filesystem_entry(response.model_dump(mode="json"))

        @function_tool(name_override="update_filesystem_entry")
        async def update_filesystem_entry_tool(
            ctx: ChatKitToolContext,
            entry_id: str,
            name: str | None = None,
            parent_id: str | None = None,
        ) -> dict[str, object]:
            """Rename or move a virtual file or folder."""
            request_context = ctx.context.request_context
            response = await self._sources.update_filesystem_entry(
                clerk_user_id=request_context.clerk_user_id,
                entry_id=entry_id,
                name=name,
                parent_id=parent_id,
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
            )
            return compact_chatkit_filesystem_entry(response.model_dump(mode="json"))

        @function_tool(name_override="delete_filesystem_entries")
        async def delete_filesystem_entries_tool(
            ctx: ChatKitToolContext,
            entry_ids: list[str],
            confirm: bool = False,
        ) -> dict[str, object]:
            """Permanently delete virtual files or folders only after explicit user confirmation."""
            if not confirm:
                return {
                    "confirmation_required": True,
                    "entry_ids": entry_ids,
                    "message": "Ask the user to confirm permanent deletion, then call delete_filesystem_entries again with confirm=true.",
                }
            request_context = ctx.context.request_context
            response = await self._sources.delete_filesystem_entries(
                clerk_user_id=request_context.clerk_user_id,
                entry_ids=entry_ids,
                confirm=confirm,
            )
            return response.model_dump(mode="json")

        @function_tool(name_override="set_file_selection")
        async def set_file_selection_tool(
            ctx: ChatKitToolContext,
            source_ids: list[str],
            mode: str = "replace",
        ) -> dict[str, object]:
            """Ask the client explorer to replace, add to, or remove from the selected files for chat."""
            normalized_mode = mode if mode in {"replace", "add", "remove"} else "replace"
            ctx.context.client_tool_call = ClientToolCall(
                name="set_file_selection",
                arguments={"source_ids": source_ids[:10], "mode": normalized_mode},
            )
            return {"client_tool": "set_file_selection", "source_ids": source_ids[:10], "mode": normalized_mode}

        @function_tool(name_override="reveal_file")
        async def reveal_file_tool(
            ctx: ChatKitToolContext,
            source_id: str | None = None,
            entry_id: str | None = None,
        ) -> dict[str, object]:
            """Ask the client explorer to navigate to and focus a file or folder."""
            ctx.context.client_tool_call = ClientToolCall(
                name="reveal_file",
                arguments={"source_id": source_id, "entry_id": entry_id},
            )
            return {"client_tool": "reveal_file", "source_id": source_id, "entry_id": entry_id}

        @function_tool(name_override="set_file_search")
        async def set_file_search_tool(
            ctx: ChatKitToolContext,
            query: str | None = None,
            tag_ids: list[str] | None = None,
        ) -> dict[str, object]:
            """Ask the client explorer to update its query and tag filters."""
            ctx.context.client_tool_call = ClientToolCall(
                name="set_file_search",
                arguments={"query": query, "tag_ids": tag_ids or []},
            )
            return {"client_tool": "set_file_search", "query": query, "tag_ids": tag_ids or []}

        @function_tool(name_override="list_tags")
        async def list_tags_tool(ctx: ChatKitToolContext) -> dict[str, object]:
            """List available auto and manual tag slugs for filtering retrieval."""
            request_context = ctx.context.request_context
            tags = await self._sources.list_tags(clerk_user_id=request_context.clerk_user_id)
            return {"tags": [compact_chatkit_tag(tag.model_dump(mode="json")) for tag in tags]}

        @function_tool(name_override="create_tag")
        async def create_tag_tool(ctx: ChatKitToolContext, name: str, color: str | None = None) -> dict[str, object]:
            """Create a manual tag for organizing files and filtering retrieval."""
            request_context = ctx.context.request_context
            response = await self._sources.create_tag(
                clerk_user_id=request_context.clerk_user_id,
                name=name,
                color=color,
            )
            return compact_chatkit_tag_mutation_payload(response.model_dump(mode="json"))

        @function_tool(name_override="update_tag")
        async def update_tag_tool(
            ctx: ChatKitToolContext,
            tag_id: str,
            name: str | None = None,
            color: str | None = None,
        ) -> dict[str, object]:
            """Rename or recolor a tag, queuing reindex tasks when filter slugs change."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "settings-slider", "Updating tag metadata and queuing affected reindex tasks.")
            response = await self._sources.update_tag(
                clerk_user_id=request_context.clerk_user_id,
                tag_id=tag_id,
                name=name,
                color=color,
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Tag updated. Queued {len(response.tasks)} reindex task{'' if len(response.tasks) == 1 else 's'}.",
            )
            return compact_chatkit_tag_mutation_payload(response.model_dump(mode="json"))

        @function_tool(name_override="delete_tag")
        async def delete_tag_tool(ctx: ChatKitToolContext, tag_id: str, confirm: bool = False) -> dict[str, object]:
            """Delete a tag only after explicit user confirmation."""
            if not confirm:
                return {
                    "confirmation_required": True,
                    "tag_id": tag_id,
                    "message": "Ask the user to confirm tag deletion, then call delete_tag again with confirm=true.",
                }
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "settings-slider", "Deleting tag and queuing affected reindex tasks.")
            response = await self._sources.delete_tag(
                clerk_user_id=request_context.clerk_user_id,
                tag_id=tag_id,
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Tag deleted. Queued {len(response.tasks)} reindex task{'' if len(response.tasks) == 1 else 's'}.",
            )
            return compact_chatkit_tag_mutation_payload(response.model_dump(mode="json"))

        @function_tool(name_override="get_source_detail")
        async def get_source_detail_tool(ctx: ChatKitToolContext, source_id: str) -> dict[str, object]:
            """Load one source with its stored metadata and optional semantic split records."""
            request_context = ctx.context.request_context
            detail = await self._sources.get_source(
                clerk_user_id=request_context.clerk_user_id,
                source_id=source_id,
            )
            return compact_chatkit_source_detail_payload(detail.model_dump(mode="json"))

        @function_tool(name_override="ingest_text_source")
        async def ingest_text_source_tool(
            ctx: ChatKitToolContext,
            filename: str,
            text: str,
            tag_ids: list[str] | None = None,
            user_guidance: str | None = None,
        ) -> dict[str, object]:
            """Create a text source and queue source-level vector indexing."""
            if not text.strip():
                raise ValueError("Text source content is required.")
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "document", "Queuing text source ingestion.")
            response = await self._sources.ingest_source(
                clerk_user_id=request_context.clerk_user_id,
                filename=filename.strip() or "note.txt",
                declared_media_type="text/plain",
                payload=text.encode("utf-8"),
                tag_ids=tag_ids or [],
                user_guidance=user_guidance,
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
            )
            task_label = (
                f"task {response.task.id[:8]} ({response.task.status})" if response.task else "no task returned"
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Source {response.source.id[:8]} queued for ingestion as {task_label}.",
            )
            return compact_chatkit_ingest_payload(response.model_dump(mode="json"))

        @function_tool(name_override="search_chunks")
        async def search_chunks_tool(
            ctx: ChatKitToolContext,
            query: str,
            selected_source_ids: list[str] | None = None,
            tag_ids: list[str] | None = None,
            virtual_paths: list[str] | None = None,
            tag_match_mode: str = "all",
            max_results: int = 8,
        ) -> dict[str, object]:
            """Search OpenAI vector-store indexed source files, filtered by selected files, tags, or paths."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "search", f"Searching indexed files for '{query[:80]}'.")
            response = await self._sources.search(
                clerk_user_id=request_context.clerk_user_id,
                request=SearchRequest(
                    query=query,
                    selected_source_ids=selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    virtual_paths=virtual_paths or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    max_results=max(1, min(max_results, 16)),
                ),
            )
            source_count = len({hit.source_file_id for hit in response.hits})
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Found {len(response.hits)} file match{'' if len(response.hits) == 1 else 'es'} across {source_count} source{'' if source_count == 1 else 's'}.",
            )
            return compact_chatkit_search_payload(response.model_dump(mode="json"))

        @function_tool(name_override="branch_search")
        async def branch_search_tool(
            ctx: ChatKitToolContext,
            query: str,
            selected_source_ids: list[str] | None = None,
            tag_ids: list[str] | None = None,
            virtual_paths: list[str] | None = None,
            tag_match_mode: str = "all",
            descend: int = 2,
            max_width: int = 3,
        ) -> dict[str, object]:
            """Run layered source-file vector search, using hits from each layer to branch outward."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "compass", "Branching through indexed source-file neighborhoods.")
            response = await self._sources.branch_search(
                clerk_user_id=request_context.clerk_user_id,
                request=BranchSearchRequest(
                    query=query,
                    selected_source_ids=selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    virtual_paths=virtual_paths or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    descend=max(0, min(descend, 4)),
                    max_width=max(1, min(max_width, 8)),
                ),
            )
            hit_count = sum(len(level.hits) for level in response.levels)
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Explored {len(response.levels)} level{'' if len(response.levels) == 1 else 's'} with {hit_count} hit{'' if hit_count == 1 else 's'}.",
            )
            return compact_chatkit_branch_search_payload(response.model_dump(mode="json"))

        @function_tool(name_override="preview_semantic_split")
        async def preview_semantic_split_tool(
            ctx: ChatKitToolContext,
            filename: str,
            text: str,
            media_type: str | None = "text/plain",
            user_guidance: str | None = None,
        ) -> dict[str, object]:
            """Preview semantic split records and auto-tags for text without creating a source or publishing vectors."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "batch", "Previewing semantic split without publishing chunks.")
            response = await self._sources.preview_semantic_split(
                clerk_user_id=request_context.clerk_user_id,
                filename=filename,
                declared_media_type=media_type,
                payload=text.encode("utf-8"),
                user_guidance=user_guidance,
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                (
                    f"Preview ready: {len(response.split.chunks)} chunk{'' if len(response.split.chunks) == 1 else 's'}, "
                    f"{len(response.split.tags)} tag{'' if len(response.split.tags) == 1 else 's'}, strategy {response.ingest_strategy}."
                ),
            )
            return compact_chatkit_split_preview_payload(response.model_dump(mode="json"))

        @function_tool(name_override="start_research_import")
        async def start_research_import_tool(
            ctx: ChatKitToolContext,
            seed_type: str = "topic",
            text: str | None = None,
            url: str | None = None,
            title: str | None = None,
            folder_name: str | None = None,
            ingest_seed: bool = True,
            discover_references: bool = True,
            max_depth: int = 2,
            max_candidates_per_source: int = 8,
            max_pending_candidates: int = 40,
        ) -> dict[str, object]:
            """Start a lower-level research import from a topic, paper title, pasted text, or public URL."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "search", "Starting research import and collecting candidates.")
            response = await self._research.create_import(
                clerk_user_id=request_context.clerk_user_id,
                payload=ResearchImportCreateRequest(
                    seed_type=cast(
                        Any,
                        seed_type
                        if seed_type in {"topic", "paper", "text", "url", "pdf_url", "arxiv_url", "linkedin_export"}
                        else "topic",
                    ),
                    text=text,
                    url=url,
                    title=title,
                    folder_name=folder_name,
                    ingest_seed=ingest_seed,
                    discover_references=discover_references,
                    max_depth=max(0, min(max_depth, 4)),
                    max_candidates_per_source=max(0, min(max_candidates_per_source, 20)),
                    max_pending_candidates=max(0, min(max_pending_candidates, 200)),
                ),
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
                progress_callback=lambda icon, text: stream_chatkit_progress(ctx, icon, text),
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Research import complete with {len(response.candidates)} candidate{'' if len(response.candidates) == 1 else 's'}.",
            )
            return compact_chatkit_research_import_payload(response.model_dump(mode="json"))

        @function_tool(name_override="build_research_library")
        async def build_research_library_tool(
            ctx: ChatKitToolContext,
            query: str,
            seed_type: str = "topic",
            title: str | None = None,
            folder_name: str | None = None,
            auto_ingest: bool = True,
            discover_references: bool = True,
            max_depth: int = 2,
            max_sources: int = 12,
            max_candidates_per_source: int = 8,
            max_pending_candidates: int = 50,
        ) -> dict[str, object]:
            """Create a foldered research library from a topic, paper title, or public URL."""
            request_context = ctx.context.request_context
            normalized_seed_type = (
                seed_type
                if seed_type in {"topic", "paper", "text", "url", "pdf_url", "arxiv_url", "linkedin_export"}
                else "topic"
            )
            await stream_chatkit_progress(ctx, "library", "Building a foldered research library from the seed.")
            response = await self._research.build_library(
                clerk_user_id=request_context.clerk_user_id,
                payload=ResearchLibraryBuildRequest(
                    seed_type=cast(Any, normalized_seed_type),
                    query=query,
                    title=title,
                    folder_name=folder_name,
                    auto_ingest=auto_ingest,
                    discover_references=discover_references,
                    max_depth=max(0, min(max_depth, 4)),
                    max_sources=max(1, min(max_sources, 50)),
                    max_candidates_per_source=max(0, min(max_candidates_per_source, 20)),
                    max_pending_candidates=max(0, min(max_pending_candidates, 200)),
                ),
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
                progress_callback=lambda icon, text: stream_chatkit_progress(ctx, icon, text),
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                (
                    f"Research library build queued {len(response.ingested)} source"
                    f"{'' if len(response.ingested) == 1 else 's'} for indexing and skipped {response.duplicate_count} duplicate"
                    f"{'' if response.duplicate_count == 1 else 's'}."
                ),
            )
            payload = response.model_dump(mode="json")
            compact_payload = compact_chatkit_research_build_payload(payload)
            ctx.context.client_tool_call = ClientToolCall(
                name="show_research_builder",
                arguments={
                    "query": query,
                    "seed_type": normalized_seed_type,
                    "max_depth": max(0, min(max_depth, 4)),
                    "max_sources": max(1, min(max_sources, 50)),
                    "auto_ingest": auto_ingest,
                    "result": payload,
                },
            )
            return compact_payload

        @function_tool(name_override="list_research_candidates")
        async def list_research_candidates_tool(
            ctx: ChatKitToolContext,
            task_id: str | None = None,
            status: str | None = None,
            page_size: int = 20,
        ) -> dict[str, object]:
            """List research import candidates by task or status."""
            request_context = ctx.context.request_context
            normalized_status = (
                status
                if status in {"pending", "approved", "rejected", "ingesting", "ingested", "failed", "duplicate"}
                else None
            )
            response = await self._research.list_candidates(
                clerk_user_id=request_context.clerk_user_id,
                task_id=task_id,
                status=cast(Any, normalized_status),
                page=1,
                page_size=max(1, min(page_size, 50)),
            )
            payload = response.model_dump(mode="json")
            ctx.context.client_tool_call = ClientToolCall(
                name="show_research_builder",
                arguments={"task_id": task_id, "candidates": payload["candidates"]},
            )
            return compact_chatkit_research_candidate_list_payload(payload)

        @function_tool(name_override="update_research_candidate_status")
        async def update_research_candidate_status_tool(
            ctx: ChatKitToolContext,
            candidate_ids: list[str],
            status: str,
        ) -> dict[str, object]:
            """Approve, reject, or return lower-level research import candidates to pending review."""
            request_context = ctx.context.request_context
            response = await self._research.update_candidate_status(
                clerk_user_id=request_context.clerk_user_id,
                candidate_ids=candidate_ids,
                status=ResearchCandidateStatusUpdateRequest(
                    candidate_ids=candidate_ids, status=cast(Any, status)
                ).status,
            )
            payload = response.model_dump(mode="json")
            task_id = response.candidates[0].task_id if response.candidates else None
            ctx.context.client_tool_call = ClientToolCall(
                name="show_research_builder",
                arguments={"task_id": task_id, "candidates": payload["candidates"]},
            )
            return {"candidates": [compact_chatkit_research_candidate(item) for item in payload["candidates"]]}

        @function_tool(name_override="ingest_research_candidates")
        async def ingest_research_candidates_tool(
            ctx: ChatKitToolContext,
            candidate_ids: list[str] | None = None,
            task_id: str | None = None,
            tag_ids: list[str] | None = None,
            folder_id: str | None = None,
        ) -> dict[str, object]:
            """Ingest approved lower-level research candidates through the app's normal source ingestion path."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "document", "Queuing approved lower-level research candidates for ingestion.")
            response = await self._research.ingest_approved_candidates(
                clerk_user_id=request_context.clerk_user_id,
                payload=ResearchCandidateIngestRequest(
                    candidate_ids=candidate_ids,
                    task_id=task_id,
                    tag_ids=tag_ids,
                    folder_id=folder_id,
                ),
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
                progress_callback=lambda icon, text: stream_chatkit_progress(ctx, icon, text),
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Queued {len(response.ingested)} lower-level candidate{'' if len(response.ingested) == 1 else 's'} for ingestion.",
            )
            payload = response.model_dump(mode="json")
            resolved_task_id = task_id
            if resolved_task_id is None and response.candidates:
                resolved_task_id = response.candidates[0].task_id
            ctx.context.client_tool_call = ClientToolCall(
                name="show_research_builder",
                arguments={
                    "task_id": resolved_task_id,
                    "folder_id": folder_id,
                    "candidates": payload["candidates"],
                    "ingested": payload["ingested"],
                },
            )
            return compact_chatkit_research_ingest_payload(payload)

        @function_tool(name_override="answer_research_library")
        async def answer_research_library_tool(
            ctx: ChatKitToolContext,
            question: str,
            task_id: str | None = None,
            source_ids: list[str] | None = None,
            tag_ids: list[str] | None = None,
            max_results: int = 8,
        ) -> dict[str, object]:
            """Answer a question against sources ingested by a research library build or explicit source IDs."""
            request_context = ctx.context.request_context
            selected_source_ids = list(dict.fromkeys(source_ids or []))
            linked_source_total = 0
            linked_source_pending = 0
            linked_source_failed = 0
            if task_id:
                poll_seconds = max(2.0, min(5.0, self._settings.openai_poll_interval_ms / 1000))
                wait_deadline = perf_counter() + 90.0
                last_reported_counts: tuple[int, int, int] | None = None
                while True:
                    linked_scope = await self._research.linked_source_scope_for_task(
                        clerk_user_id=request_context.clerk_user_id,
                        task_id=task_id,
                    )
                    linked_source_total = linked_scope.total_count
                    linked_source_pending = linked_scope.pending_count
                    linked_source_failed = linked_scope.failed_count
                    selected_source_ids = list(dict.fromkeys([*selected_source_ids, *linked_scope.ready_source_ids]))
                    counts = (linked_scope.ready_count, linked_source_pending, linked_source_failed)
                    if linked_source_total > 0 and linked_source_pending > 0 and counts != last_reported_counts:
                        await stream_chatkit_progress(
                            ctx,
                            "reload",
                            (
                                f"Waiting for research files to finish indexing "
                                f"({linked_scope.ready_count}/{linked_source_total} ready)."
                            ),
                        )
                        last_reported_counts = counts
                    if linked_source_total == 0 or linked_source_pending == 0 or perf_counter() >= wait_deadline:
                        break
                    await asyncio.sleep(min(poll_seconds, max(0.0, wait_deadline - perf_counter())))
                if linked_source_total > 0 and not selected_source_ids:
                    answer = (
                        "The research library files could not be indexed, so I cannot answer from them yet."
                        if linked_source_failed == linked_source_total
                        else "The research library files are still indexing, so I cannot answer from them yet."
                    )
                    return {
                        "kind": "qa",
                        "answer": answer,
                        "hits": [],
                        "source_status": {
                            "total": linked_source_total,
                            "ready": len(selected_source_ids),
                            "pending": linked_source_pending,
                            "failed": linked_source_failed,
                        },
                    }
                if linked_source_total == 0 and not selected_source_ids:
                    return {
                        "kind": "qa",
                        "answer": "That research task does not have any ingested files to search yet.",
                        "hits": [],
                        "source_status": {
                            "total": 0,
                            "ready": 0,
                            "pending": 0,
                            "failed": 0,
                        },
                    }
                if linked_source_total > 0 and linked_source_pending > 0:
                    await stream_chatkit_progress(
                        ctx,
                        "info",
                        (
                            f"Answering with {len(selected_source_ids)} ready research file"
                            f"{'' if len(selected_source_ids) == 1 else 's'}; "
                            f"{linked_source_pending} still indexing."
                        ),
                    )
                elif linked_source_total > 0:
                    await stream_chatkit_progress(
                        ctx,
                        "check-circle",
                        (
                            f"All {len(selected_source_ids)} searchable research file"
                            f"{'' if len(selected_source_ids) == 1 else 's'} are ready."
                        ),
                    )
            if not selected_source_ids and task_id is None:
                selected_source_ids = selected_scope(request_context, None)
            await stream_chatkit_progress(
                ctx,
                "search",
                f"Searching {len(selected_source_ids) or 'all'} ready file{'' if len(selected_source_ids) == 1 else 's'} for an answer.",
            )
            response = await self._actions.qa(
                clerk_user_id=request_context.clerk_user_id,
                payload=QaRequest(
                    prompt=question,
                    selected_source_ids=selected_source_ids,
                    tag_ids=tag_ids or [],
                    max_results=max(1, min(max_results, 16)),
                    origin_thread_id=ctx.context.thread.id,
                ),
                origin_surface="chatkit",
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Research answer ready with {len(response.hits)} cited match{'' if len(response.hits) == 1 else 'es'}.",
            )
            return compact_chatkit_action_payload(response.model_dump(mode="json"))

        @function_tool(name_override="resplit_source")
        async def resplit_source_tool(
            ctx: ChatKitToolContext,
            source_id: str,
            tag_ids: list[str] | None = None,
            user_guidance: str | None = None,
        ) -> dict[str, object]:
            """Queue a re-split that replaces one source's optional split records using its stored payload."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "reload", "Queuing a safe re-split for the selected source.")
            response = await self._sources.resplit_source(
                clerk_user_id=request_context.clerk_user_id,
                source_id=source_id,
                tag_ids=tag_ids,
                user_guidance=user_guidance,
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
            )
            task_label = (
                f"task {response.task.id[:8]} ({response.task.status})" if response.task else "no task returned"
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Re-split queued for source {response.source.id[:8]} as {task_label}.",
            )
            return compact_chatkit_ingest_payload(response.model_dump(mode="json"))

        @function_tool(name_override="update_source_tags")
        async def update_source_tags_tool(
            ctx: ChatKitToolContext,
            source_id: str,
            tag_ids: list[str],
        ) -> dict[str, object]:
            """Replace a source's tag assignments and queue vector-store reindexing."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "settings-slider", "Queuing tag reindex for the selected source.")
            response = await self._sources.update_source_tags(
                clerk_user_id=request_context.clerk_user_id,
                source_id=source_id,
                tag_ids=tag_ids,
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
            )
            task_label = (
                f"task {response.task.id[:8]} ({response.task.status})" if response.task else "no task returned"
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Tag reindex queued for source {response.source.id[:8]} as {task_label}.",
            )
            return compact_chatkit_ingest_payload(response.model_dump(mode="json"))

        @function_tool(name_override="delete_source")
        async def delete_source_tool(
            ctx: ChatKitToolContext,
            source_id: str,
            confirm: bool = False,
        ) -> dict[str, object]:
            """Delete one source only after explicit user confirmation."""
            if not confirm:
                return {
                    "confirmation_required": True,
                    "source_id": source_id,
                    "message": "Ask the user to confirm deletion, then call delete_source again with confirm=true.",
                }
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "reload", "Deleting source and cleaning up stored files.")
            deleted_id = await self._sources.delete_source(
                clerk_user_id=request_context.clerk_user_id,
                source_id=source_id,
            )
            await stream_chatkit_progress(ctx, "check-circle", f"Deleted source {deleted_id[:8]}.")
            return {"deleted_source_id": deleted_id}

        @function_tool(name_override="list_tasks")
        async def list_tasks_tool(
            ctx: ChatKitToolContext,
            kind: str | None = None,
            limit: int = 20,
        ) -> dict[str, object]:
            """List recent app tasks for the current user."""
            request_context = ctx.context.request_context
            valid_task_kinds = {
                "ingest",
                "resplit",
                "reindex",
                "research_import",
                "qa",
                "freeform",
                "branch_search",
                "image_gen",
                "voice_gen",
            }
            task_kind: TaskKind | None = cast(TaskKind, kind) if kind in valid_task_kinds else None
            response = await self._actions.list_tasks(
                clerk_user_id=request_context.clerk_user_id,
                kind=task_kind,
                limit=max(1, min(limit, 50)),
            )
            return {"tasks": [compact_chatkit_task_payload(item) for item in response.model_dump(mode="json")["tasks"]]}

        @function_tool(name_override="get_task")
        async def get_task_tool(ctx: ChatKitToolContext, task_id: str) -> dict[str, object]:
            """Load task status, inputs, state, result, and error information."""
            request_context = ctx.context.request_context
            response = await self._actions.get_task(
                clerk_user_id=request_context.clerk_user_id,
                task_id=task_id,
            )
            return compact_chatkit_task_payload(response.model_dump(mode="json"))

        @function_tool(name_override="answer_from_library")
        async def answer_from_library_tool(
            ctx: ChatKitToolContext,
            prompt: str,
            selected_source_ids: list[str] | None = None,
            tag_ids: list[str] | None = None,
            tag_match_mode: str = "all",
            max_results: int = 8,
        ) -> dict[str, object]:
            """Run a QA action that retrieves chunks and answers from the evidence."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "search", "Retrieving chunks and drafting a grounded answer.")
            response = await self._actions.qa(
                clerk_user_id=request_context.clerk_user_id,
                payload=QaRequest(
                    prompt=prompt,
                    selected_source_ids=selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    max_results=max(1, min(max_results, 16)),
                    origin_thread_id=ctx.context.thread.id,
                ),
                origin_surface="chatkit",
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Grounded answer complete as task {response.task_id[:8]} with {len(response.hits)} citation{'' if len(response.hits) == 1 else 's'}.",
            )
            return compact_chatkit_action_payload(response.model_dump(mode="json"))

        @function_tool(name_override="freeform_from_library")
        async def freeform_from_library_tool(
            ctx: ChatKitToolContext,
            prompt: str,
            mode: str = "grounded",
            selected_source_ids: list[str] | None = None,
            tag_ids: list[str] | None = None,
            tag_match_mode: str = "all",
            max_results: int = 8,
        ) -> dict[str, object]:
            """Run a free-form writing action with optional grounded/creative retrieval context."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "write", "Drafting from the library context.")
            response = await self._actions.freeform(
                clerk_user_id=request_context.clerk_user_id,
                payload=FreeformRequest(
                    prompt=prompt,
                    mode="creative" if mode == "creative" else "grounded",
                    selected_source_ids=selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    max_results=max(1, min(max_results, 16)),
                    origin_thread_id=ctx.context.thread.id,
                ),
                origin_surface="chatkit",
            )
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Draft complete as task {response.task_id[:8]} with {len(response.hits)} retrieved chunk{'' if len(response.hits) == 1 else 's'}.",
            )
            return compact_chatkit_action_payload(response.model_dump(mode="json"))

        @function_tool(name_override="generate_image_from_library")
        async def generate_image_from_library_tool(
            ctx: ChatKitToolContext,
            prompt: str,
            size: str = "1024x1024",
            selected_source_ids: list[str] | None = None,
            tag_ids: list[str] | None = None,
            tag_match_mode: str = "all",
        ) -> dict[str, object]:
            """Generate an image, optionally grounded in retrieved indexed file matches."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "images", "Generating an image asset from retrieved context.")
            response = await self._actions.image(
                clerk_user_id=request_context.clerk_user_id,
                payload=ImageGenerationRequest(
                    prompt=prompt,
                    size=size,
                    selected_source_ids=selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    origin_thread_id=ctx.context.thread.id,
                ),
                origin_surface="chatkit",
            )
            asset_text = f" and asset {response.asset.id[:8]}" if response.asset else ""
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Image generation complete as task {response.task_id[:8]}{asset_text}.",
            )
            return compact_chatkit_action_payload(response.model_dump(mode="json"))

        @function_tool(name_override="generate_voice_from_library")
        async def generate_voice_from_library_tool(
            ctx: ChatKitToolContext,
            prompt: str,
            source_text: str | None = None,
            voice: str | None = None,
            response_format: str = "mp3",
            selected_source_ids: list[str] | None = None,
            tag_ids: list[str] | None = None,
            tag_match_mode: str = "all",
        ) -> dict[str, object]:
            """Generate a narrated audio asset from provided text or a prompt."""
            request_context = ctx.context.request_context
            await stream_chatkit_progress(ctx, "play", "Generating a voice asset.")
            response = await self._actions.voice(
                clerk_user_id=request_context.clerk_user_id,
                payload=VoiceGenerationRequest(
                    prompt=prompt,
                    source_text=source_text,
                    voice=voice,
                    response_format=cast(Any, response_format if response_format in {"mp3", "wav", "opus"} else "mp3"),
                    selected_source_ids=selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    origin_thread_id=ctx.context.thread.id,
                ),
                origin_surface="chatkit",
            )
            asset_text = f" and asset {response.asset.id[:8]}" if response.asset else ""
            await stream_chatkit_progress(
                ctx,
                "check-circle",
                f"Voice generation complete as task {response.task_id[:8]}{asset_text}.",
            )
            return compact_chatkit_action_payload(response.model_dump(mode="json"))

        return [
            name_thread_tool,
            list_sources_tool,
            list_filesystem_tool,
            find_files_tool,
            create_folder_tool,
            update_filesystem_entry_tool,
            delete_filesystem_entries_tool,
            set_file_selection_tool,
            reveal_file_tool,
            set_file_search_tool,
            list_tags_tool,
            create_tag_tool,
            update_tag_tool,
            delete_tag_tool,
            get_source_detail_tool,
            ingest_text_source_tool,
            search_chunks_tool,
            branch_search_tool,
            preview_semantic_split_tool,
            start_research_import_tool,
            build_research_library_tool,
            list_research_candidates_tool,
            update_research_candidate_status_tool,
            ingest_research_candidates_tool,
            answer_research_library_tool,
            resplit_source_tool,
            update_source_tags_tool,
            delete_source_tool,
            list_tasks_tool,
            get_task_tool,
            answer_from_library_tool,
            freeform_from_library_tool,
            generate_image_from_library_tool,
            generate_voice_from_library_tool,
        ]

    def _resolve_requested_model(self, *, input_user_message: UserMessageItem | None) -> str:
        requested_model = input_user_message.inference_options.model if input_user_message is not None else None
        if requested_model is None or not requested_model.strip():
            return self._settings.openai_fast_model
        return MODEL_ALIASES.get(requested_model.strip(), requested_model.strip())

    @staticmethod
    async def _agent_instructions(_context: Any, _agent: Any) -> str:
        return (
            "You are the indexed file-library assistant for an app-first OpenAI vector-store backed file explorer. "
            "Use the direct app tools to list the virtual filesystem, find files, inspect source details and tags, ingest text snippets, search indexed files, "
            "branch through related indexed file matches, preview proposed text splits without publishing them, re-split an existing source when the user asks "
            "to replace its optional split records, build foldered research libraries directly from topics or papers, start lower-level research imports when needed, answer questions over built research libraries, update a source's tags when the user explicitly asks, list task progress, answer questions, and create image or voice assets. "
            "When a conversation starts or the topic becomes clear, call name_thread early with a concise 3-8 word title. "
            "When the user asks to research a topic, gather papers, or build a library from a paper title, use build_research_library as the primary path and let the browser panel mirror progress. "
            "The app's file explorer is the primary source of file input and selection; selected files are retrieval scope first, and only small ready files may be attached to a user turn as OpenAI file inputs. "
            "Use set_file_selection, reveal_file, and set_file_search to coordinate the browser UI when the user asks you to select files, navigate to a file, or filter the explorer. "
            "Research build tools update the browser's research builder panel so the user can inspect candidate, duplicate, download, and indexing state. "
            "Tool results are intentionally compact: source/file records expose id, name, type, description, summary, tag slugs, and a citation_link when available. "
            "When citing evidence, use markdown links with the provided citation_link, for example [Source title](chatkit-link://source?source_id=...). "
            "Those links reveal the source in the file explorer when clicked. "
            "Treat tag slugs as the stable tag identifiers; tag filter arguments named tag_ids accept tag slugs as well as legacy tag IDs. "
            "Use selected_source_ids as the retrieval scope when present, and call find_files or search_chunks when the user asks to discover files beyond that selection. "
            "If a selected source is still processing, check the task with get_task or list_tasks and explain that retrieval can start after ingestion completes. "
            "Treat split previews as inspect-only; iterate by rerunning the preview with revised guidance before re-splitting. "
            "Prefer the user's selected files when present. Only delete files, folders, or sources after explicit user confirmation. "
            "Be concise, name the evidence you used, and say clearly when the library "
            "does not support a claim."
        )


def _metadata_dict(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _string_or_none(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def clean_thread_title(title: str) -> str:
    cleaned = " ".join(title.split()).strip(" -:;,.\"'")
    if not cleaned:
        raise ValueError("Thread title is required.")
    if len(cleaned) <= THREAD_TITLE_MAX_CHARS:
        return cleaned
    return cleaned[:THREAD_TITLE_MAX_CHARS].rstrip(" -:;,.\"'")


def apply_agent_thread_title(thread: ThreadMetadata, title: str) -> str:
    cleaned = clean_thread_title(title)
    metadata = _metadata_dict(thread.metadata)
    metadata["agent_thread_title"] = cleaned
    metadata["agent_thread_title_updated_at"] = datetime.now(UTC).isoformat()
    thread.title = cleaned
    thread.metadata = metadata
    return cleaned


def selected_scope(context: VectorstoreChatContext, explicit_ids: list[str] | None) -> list[str]:
    if explicit_ids:
        source_ids = [
            source_id.strip()
            for source_id in explicit_ids
            if source_id.strip() and not source_id.strip().startswith("file-")
        ]
        if source_ids:
            return source_ids
        if context.selected_source_ids:
            return list(context.selected_source_ids)
    return list(context.selected_source_ids)


def chatkit_source_deeplink(source_id: str, *, locator: object | None = None) -> str:
    params: dict[str, str] = {"source_id": source_id}
    locator_label = _locator_label(locator)
    if locator_label is not None:
        params["locator"] = locator_label
    return f"chatkit-link://source?{urlencode(params)}"


def compact_chatkit_tag(value: object) -> dict[str, str]:
    tag = _mapping_or_empty(value)
    slug = _string_or_none(tag.get("slug")) or _string_or_none(tag.get("name")) or _string_or_none(tag.get("id"))
    if slug is None:
        return {}
    output = {"slug": slug}
    name = _string_or_none(tag.get("name"))
    if name is not None and name != slug:
        output["name"] = name
    return output


def compact_chatkit_file_list_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    return _drop_none(
        {
            "sources": [compact_chatkit_source_payload(source) for source in _mapping_list(payload.get("sources"))],
            "total_count": payload.get("total_count"),
            "page": payload.get("page"),
            "page_size": payload.get("page_size"),
            "has_more": payload.get("has_more"),
        }
    )


def compact_chatkit_filesystem_list_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    return _drop_none(
        {
            "current": compact_chatkit_filesystem_entry(payload.get("current")),
            "breadcrumbs": [_breadcrumb_payload(item) for item in _mapping_list(payload.get("breadcrumbs"))],
            "entries": [compact_chatkit_filesystem_entry(entry) for entry in _mapping_list(payload.get("entries"))],
        }
    )


def compact_chatkit_filesystem_search_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    return _drop_none(
        {
            "query": payload.get("query"),
            "entries": [compact_chatkit_filesystem_entry(entry) for entry in _mapping_list(payload.get("entries"))],
            "total_count": payload.get("total_count"),
            "page": payload.get("page"),
            "page_size": payload.get("page_size"),
            "has_more": payload.get("has_more"),
        }
    )


def compact_chatkit_filesystem_entry(value: object) -> dict[str, object]:
    entry = _mapping_or_empty(value)
    source_id = _string_or_none(entry.get("source_id"))
    entry_type = _string_or_none(entry.get("source_kind")) or _string_or_none(entry.get("kind"))
    output = _drop_none(
        {
            "id": _string_or_none(entry.get("id")),
            "type": entry_type,
            "name": _string_or_none(entry.get("name")),
            "path": _string_or_none(entry.get("path")),
            "source_id": source_id,
            "status": _string_or_none(entry.get("status")),
            "description": _trim_text(entry.get("description"), limit=600),
            "summary": _trim_text(entry.get("summary"), limit=900),
            "tags": _compact_tag_slugs(entry.get("tags")) or _string_list(entry.get("suggested_tags")),
            "citation_link": chatkit_source_deeplink(source_id) if source_id is not None else None,
        }
    )
    return output


def compact_chatkit_source_payload(value: object) -> dict[str, object]:
    source = _mapping_or_empty(value)
    source_id = _string_or_none(source.get("id")) or _string_or_none(source.get("source_id"))
    name = (
        _string_or_none(source.get("display_title"))
        or _string_or_none(source.get("virtual_name"))
        or _string_or_none(source.get("original_filename"))
        or _string_or_none(source.get("name"))
    )
    source_type = _string_or_none(source.get("source_kind")) or _string_or_none(source.get("media_type"))
    return _drop_none(
        {
            "id": source_id,
            "type": source_type,
            "name": name,
            "path": _string_or_none(source.get("virtual_path")),
            "status": _string_or_none(source.get("status")),
            "description": _trim_text(source.get("description"), limit=600),
            "summary": _trim_text(source.get("summary"), limit=900),
            "tags": _compact_tag_slugs(source.get("tags")) or _string_list(source.get("suggested_tags")),
            "citation_link": chatkit_source_deeplink(source_id) if source_id is not None else None,
        }
    )


def compact_chatkit_source_detail_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    chunks = _mapping_list(payload.get("chunks"))
    output = compact_chatkit_source_payload(payload)
    output["total_chunks"] = len(chunks)
    output["chunks"] = [compact_chatkit_chunk_payload(chunk) for chunk in chunks[:CHATKIT_DETAIL_CHUNK_LIMIT]]
    return output


def compact_chatkit_chunk_payload(value: object) -> dict[str, object]:
    chunk = _mapping_or_empty(value)
    locator = chunk.get("locator")
    return _drop_none(
        {
            "id": _string_or_none(chunk.get("id")) or _string_or_none(chunk.get("chunk_id")),
            "sequence": chunk.get("sequence"),
            "title": _string_or_none(chunk.get("title")),
            "summary": _trim_text(chunk.get("summary"), limit=700),
            "text": _trim_text(chunk.get("text"), limit=CHATKIT_TEXT_SNIPPET_MAX_CHARS),
            "locator": _locator_label(locator) or locator,
            "keywords": _string_list(chunk.get("keywords")),
        }
    )


def compact_chatkit_hit_payload(value: object) -> dict[str, object]:
    hit = _mapping_or_empty(value)
    source_id = _string_or_none(hit.get("source_file_id")) or _string_or_none(hit.get("source_id"))
    locator = hit.get("locator")
    return _drop_none(
        {
            "id": source_id,
            "type": "source",
            "name": _string_or_none(hit.get("source_title")),
            "chunk_id": _string_or_none(hit.get("chunk_id")),
            "title": _string_or_none(hit.get("title")),
            "summary": _trim_text(hit.get("summary"), limit=700),
            "text": _trim_text(hit.get("text"), limit=CHATKIT_TEXT_SNIPPET_MAX_CHARS),
            "tags": _compact_tag_slugs(hit.get("tags")),
            "locator": _locator_label(locator) or locator,
            "score": hit.get("score"),
            "citation_link": chatkit_source_deeplink(source_id, locator=locator) if source_id is not None else None,
        }
    )


def compact_chatkit_search_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    return _drop_none(
        {
            "query": _string_or_none(payload.get("query")),
            "hits": [compact_chatkit_hit_payload(hit) for hit in _mapping_list(payload.get("hits"))],
        }
    )


def compact_chatkit_branch_search_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    return _drop_none(
        {
            "query": _string_or_none(payload.get("query")),
            "descend": payload.get("descend"),
            "max_width": payload.get("max_width"),
            "levels": [
                _drop_none(
                    {
                        "depth": level.get("depth"),
                        "hits": [compact_chatkit_hit_payload(hit) for hit in _mapping_list(level.get("hits"))],
                    }
                )
                for level in _mapping_list(payload.get("levels"))
            ],
        }
    )


def compact_chatkit_split_preview_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    split = _mapping_or_empty(payload.get("split"))
    return _drop_none(
        {
            "name": _string_or_none(payload.get("filename")),
            "type": _string_or_none(payload.get("source_kind")) or _string_or_none(payload.get("media_type")),
            "byte_size": payload.get("byte_size"),
            "ingest_strategy": _string_or_none(payload.get("ingest_strategy")),
            "extracted_character_count": payload.get("extracted_character_count"),
            "tags": _string_list(split.get("tags")),
            "chunks": [compact_chatkit_chunk_payload(chunk) for chunk in _mapping_list(split.get("chunks"))],
        }
    )


def compact_chatkit_ingest_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    return _drop_none(
        {
            "source": compact_chatkit_source_payload(payload.get("source")),
            "task": compact_chatkit_task_payload(payload.get("task")),
        }
    )


def compact_chatkit_tag_mutation_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    tag = payload.get("tag")
    return _drop_none(
        {
            "tag": compact_chatkit_tag(tag) if isinstance(tag, Mapping) else None,
            "tasks": [compact_chatkit_task_payload(task) for task in _mapping_list(payload.get("tasks"))],
        }
    )


def compact_chatkit_action_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    return _drop_none(
        {
            "task_id": _string_or_none(payload.get("task_id")),
            "type": _string_or_none(payload.get("kind")),
            "answer": _string_or_none(payload.get("answer")),
            "sources": [compact_chatkit_hit_payload(hit) for hit in _mapping_list(payload.get("hits"))],
            "asset": _compact_asset_payload(payload.get("asset")),
            "source_status": payload.get("source_status") if isinstance(payload.get("source_status"), Mapping) else None,
        }
    )


def compact_chatkit_task_payload(value: object) -> dict[str, object]:
    task = _mapping_or_empty(value)
    if not task:
        return {}
    result = task.get("result_json")
    return _drop_none(
        {
            "id": _string_or_none(task.get("id")),
            "type": _string_or_none(task.get("kind")),
            "status": _string_or_none(task.get("status")),
            "title": _string_or_none(task.get("title")),
            "source_id": _string_or_none(task.get("source_file_id")),
            "error_message": _trim_text(task.get("error_message"), limit=900),
            "state": task.get("state_json") if isinstance(task.get("state_json"), Mapping) else None,
            "result": _compact_task_result_payload(result),
            "created_at": _string_or_none(task.get("created_at")),
            "updated_at": _string_or_none(task.get("updated_at")),
            "completed_at": _string_or_none(task.get("completed_at")),
        }
    )


def compact_chatkit_research_import_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    return _drop_none(
        {
            "task": compact_chatkit_task_payload(payload.get("task")),
            "seed_source": compact_chatkit_source_payload(payload.get("seed_source")),
            "candidates": [compact_chatkit_research_candidate(item) for item in _mapping_list(payload.get("candidates"))],
            "duplicate_count": payload.get("duplicate_count"),
            "target_folder_id": _string_or_none(payload.get("target_folder_id")),
        }
    )


def compact_chatkit_research_build_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    return _drop_none(
        {
            "task": compact_chatkit_task_payload(payload.get("task")),
            "target_folder_id": _string_or_none(payload.get("target_folder_id")),
            "seed_source": compact_chatkit_source_payload(payload.get("seed_source")),
            "candidates": [compact_chatkit_research_candidate(item) for item in _mapping_list(payload.get("candidates"))],
            "ingested": [compact_chatkit_ingest_payload(item) for item in _mapping_list(payload.get("ingested"))],
            "duplicate_count": payload.get("duplicate_count"),
        }
    )


def compact_chatkit_research_candidate_list_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    return _drop_none(
        {
            "candidates": [compact_chatkit_research_candidate(item) for item in _mapping_list(payload.get("candidates"))],
            "total_count": payload.get("total_count"),
            "page": payload.get("page"),
            "page_size": payload.get("page_size"),
            "has_more": payload.get("has_more"),
        }
    )


def compact_chatkit_research_ingest_payload(value: object) -> dict[str, object]:
    payload = _mapping_or_empty(value)
    return _drop_none(
        {
            "ingested": [compact_chatkit_ingest_payload(item) for item in _mapping_list(payload.get("ingested"))],
            "candidates": [compact_chatkit_research_candidate(item) for item in _mapping_list(payload.get("candidates"))],
        }
    )


def compact_chatkit_research_candidate(value: object) -> dict[str, object]:
    candidate = _mapping_or_empty(value)
    source_id = _string_or_none(candidate.get("linked_source_file_id"))
    return _drop_none(
        {
            "id": _string_or_none(candidate.get("id")),
            "task_id": _string_or_none(candidate.get("task_id")),
            "status": _string_or_none(candidate.get("status")),
            "type": _string_or_none(candidate.get("source_type")),
            "name": _string_or_none(candidate.get("title")),
            "url": _string_or_none(candidate.get("url")),
            "description": _trim_text(candidate.get("description"), limit=600),
            "summary": _trim_text(candidate.get("summary"), limit=900),
            "tags": _string_list(candidate.get("suggested_tags")),
            "authors": _string_list(candidate.get("authors")),
            "published_at": _string_or_none(candidate.get("published_at")),
            "doi": _string_or_none(candidate.get("doi")),
            "arxiv_id": _string_or_none(candidate.get("arxiv_id")),
            "depth": candidate.get("depth"),
            "parent_id": _string_or_none(candidate.get("parent_candidate_id")),
            "source_id": source_id,
            "error_message": _trim_text(candidate.get("error_message"), limit=900),
            "citation_link": chatkit_source_deeplink(source_id) if source_id is not None else None,
        }
    )


def _title_from_user_message(item: UserMessageItem) -> str | None:
    text_parts = [part.text.strip() for part in item.content if getattr(part, "type", None) == "text"]
    combined = " ".join(part for part in text_parts if part).strip()
    if not combined:
        return None
    return combined if len(combined) <= 72 else combined[:69].rstrip() + "..."


def _mapping_or_empty(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [_mapping_or_empty(item) for item in value if isinstance(item, Mapping)]


def _drop_none(value: Mapping[str, object | None]) -> dict[str, object]:
    return {key: item for key, item in value.items() if item is not None}


def _trim_text(value: object, *, limit: int) -> str | None:
    text = _string_or_none(value)
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _compact_tag_slugs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        slug: str | None
        if isinstance(item, Mapping):
            mapping = _mapping_or_empty(item)
            slug = _string_or_none(mapping.get("slug")) or _string_or_none(mapping.get("name")) or _string_or_none(mapping.get("id"))
        else:
            slug = _string_or_none(item)
        if slug is not None and slug not in output:
            output.append(slug)
    return output


def _breadcrumb_payload(value: object) -> dict[str, object]:
    breadcrumb = _mapping_or_empty(value)
    return _drop_none(
        {
            "id": _string_or_none(breadcrumb.get("id")),
            "name": _string_or_none(breadcrumb.get("name")),
            "path": _string_or_none(breadcrumb.get("path")),
        }
    )


def _compact_asset_payload(value: object) -> dict[str, object] | None:
    asset = _mapping_or_empty(value)
    if not asset:
        return None
    return _drop_none(
        {
            "id": _string_or_none(asset.get("id")),
            "type": _string_or_none(asset.get("kind")),
            "name": _string_or_none(asset.get("filename")),
            "media_type": _string_or_none(asset.get("media_type")),
            "byte_size": asset.get("byte_size"),
            "download_url": _string_or_none(asset.get("download_url")),
        }
    )


def _compact_task_result_payload(value: object) -> object | None:
    result = _mapping_or_empty(value)
    if not result:
        return None
    if "answer" in result or "hits" in result:
        return compact_chatkit_action_payload(result)
    if "asset" in result:
        return _drop_none({"asset": _compact_asset_payload(result.get("asset"))})
    if "source" in result or "task" in result:
        return compact_chatkit_ingest_payload(result)
    return {
        key: item
        for key, item in result.items()
        if key in {"stage", "source_id", "task_id", "candidate_id", "openai_response_id", "openai_conversation_id"}
    } or None


def _locator_label(value: object) -> str | None:
    locator = _mapping_or_empty(value)
    locator_type = _string_or_none(locator.get("type"))
    if locator_type == "page_range":
        start_page = _int_or_none(locator.get("start_page"))
        end_page = _int_or_none(locator.get("end_page"))
        if start_page is not None:
            return f"p. {start_page}" if end_page in {None, start_page} else f"pp. {start_page}-{end_page}"
    if locator_type == "line_range":
        start_line = _int_or_none(locator.get("start_line"))
        end_line = _int_or_none(locator.get("end_line"))
        if start_line is not None:
            return f"line {start_line}" if end_line in {None, start_line} else f"lines {start_line}-{end_line}"
    if locator_type == "time_range":
        start_seconds = _float_or_none(locator.get("start_seconds"))
        end_seconds = _float_or_none(locator.get("end_seconds"))
        if start_seconds is not None and end_seconds is not None:
            return f"{start_seconds:.1f}s-{end_seconds:.1f}s"
    return "generated" if locator_type == "generated" else None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def chatkit_model_settings_for_model(model: str | None, *, compact_threshold: int | None) -> ModelSettings:
    extra_body: dict[str, object] | None = None
    if compact_threshold is not None and compact_threshold > 0:
        extra_body = {
            "context_management": [
                {
                    "type": "compaction",
                    "compact_threshold": compact_threshold,
                }
            ]
        }
    if isinstance(model, str) and model.startswith("gpt-5"):
        return ModelSettings(
            reasoning=Reasoning(effort="low", summary="auto"),
            extra_body=extra_body,
        )
    return ModelSettings(extra_body=extra_body)


class VectorstoreThreadItemConverter(ThreadItemConverter):
    async def attachment_to_message_content(self, attachment: Attachment) -> ResponseInputContentParam:
        metadata = _metadata_dict(attachment.metadata)
        source_id = _string_or_none(metadata.get("source_id"))
        task_id = _string_or_none(metadata.get("task_id"))
        source_title = _string_or_none(metadata.get("source_title")) or attachment.name
        source_status = _string_or_none(metadata.get("source_status")) or "unknown"
        task_status = _string_or_none(metadata.get("task_status")) or "unknown"

        lines = [
            "The user attached a file that has been added to the app indexed file library.",
            f"Attachment: {attachment.name} ({attachment.id}, {attachment.mime_type})",
            f"Source: {source_title} ({source_id or 'source id unavailable'}, status: {source_status})",
        ]
        if task_id is not None:
            lines.append(f"Ingest task: {task_id} (status when uploaded: {task_status})")
        if source_id is not None:
            lines.append(
                "Use this source by calling search_chunks or answer_from_library with "
                f"selected_source_ids=['{source_id}'] after ingestion is ready."
            )
        if task_id is not None and source_status != "ready":
            lines.append("If retrieval returns no chunks, call get_task for the ingest task before answering.")
        return cast(
            ResponseInputContentParam,
            ResponseInputTextParam(type="input_text", text="\n".join(lines)),
        )

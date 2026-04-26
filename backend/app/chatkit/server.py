from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import logging
from time import perf_counter
from typing import Any, cast

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
STOP_AT_TOOL_NAMES = [
    "set_file_selection",
    "reveal_file",
    "set_file_search",
    "delete_source",
    "delete_filesystem_entries",
]

ChatKitToolContext = ToolContext[ChatKitAgentContext[VectorstoreChatContext]]


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
    ) -> None:
        super().__init__(store=store, attachment_store=store)
        self._settings = settings
        self._store = store
        self._sources = sources
        self._research = research
        self._actions = actions
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
        await self.store.save_thread(thread, context)

        agent_input = await self._agent_input_for_turn(
            thread=thread,
            input_user_message=input_user_message,
            context=context,
        )
        selected_source_context = await self._selected_source_context_items(context=context)
        if selected_source_context:
            agent_input = selected_source_context + agent_input

        logger.info(
            "chat_turn_started thread_id=%s model=%s selected_sources=%s",
            thread.id,
            requested_model,
            len(context.selected_source_ids),
        )
        agent_context = ChatKitAgentContext[VectorstoreChatContext](
            thread=thread,
            store=self.store,
            request_context=context,
        )
        agent = Agent[ChatKitAgentContext[VectorstoreChatContext]](
            name="indexed_file_vectorstore_agent",
            model=requested_model,
            model_settings=_model_settings_override_for_model(requested_model) or ModelSettings(),
            tools=self._build_tools(),
            instructions=self._agent_instructions,
            tool_use_behavior=StopAtTools(stop_at_tool_names=STOP_AT_TOOL_NAMES),
        )
        result = Runner.run_streamed(
            agent,
            agent_input,
            context=agent_context,
            max_turns=MAX_AGENT_TURNS,
        )
        async for event in stream_agent_response(agent_context, result):
            yield event

        logger.info(
            "chat_turn_completed thread_id=%s model=%s response_id=%s duration_ms=%.1f",
            thread.id,
            requested_model,
            result.last_response_id,
            (perf_counter() - started_at) * 1000,
        )

    async def _agent_input_for_turn(
        self,
        *,
        thread: ThreadMetadata,
        input_user_message: UserMessageItem | None,
        context: VectorstoreChatContext,
    ) -> list[ResponseInputItemParam]:
        if input_user_message is None:
            history = await self.store.load_thread_items(
                thread.id,
                after=None,
                limit=80,
                order="asc",
                context=context,
            )
            return await self._converter.to_agent_input(history.data)
        history = await self.store.load_thread_items(
            thread.id,
            after=None,
            limit=80,
            order="asc",
            context=context,
        )
        items = [item for item in history.data if item.id != input_user_message.id]
        items.append(input_user_message)
        return await self._converter.to_agent_input(items)

    async def _selected_source_context_items(
        self,
        *,
        context: VectorstoreChatContext,
    ) -> list[ResponseInputItemParam]:
        if not context.selected_source_ids:
            return []
        file_inputs = await self._sources.ensure_source_file_inputs(
            clerk_user_id=context.clerk_user_id,
            source_ids=context.selected_source_ids,
        )
        if not file_inputs:
            return []
        source_lines = [
            f"- {item.virtual_path}: app source_id={item.source_id}; media_type={item.media_type}; attached OpenAI file_id={item.file_id}"
            for item in file_inputs
        ]
        content: list[dict[str, object]] = [
            {
                "type": "input_text",
                "text": (
                    "The user selected these files in the app explorer. They are attached as input_file content "
                    "for this turn and should be treated as the primary file scope unless the user asks to widen it. "
                    "When calling app tools, pass the app source_id values as selected_source_ids; do not pass OpenAI file_id values. "
                    f"At most {len(file_inputs)} selected files are attached.\n" + "\n".join(source_lines)
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
            return response.model_dump(mode="json")

        @function_tool(name_override="list_filesystem")
        async def list_filesystem_tool(ctx: ChatKitToolContext, folder_id: str | None = None) -> dict[str, object]:
            """List the children of a virtual filesystem folder."""
            request_context = ctx.context.request_context
            response = await self._sources.list_filesystem(
                clerk_user_id=request_context.clerk_user_id,
                folder_id=folder_id,
            )
            return response.model_dump(mode="json")

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
            return response.model_dump(mode="json")

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
            return response.model_dump(mode="json")

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
            return response.model_dump(mode="json")

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
        async def list_tags_tool(ctx: ChatKitToolContext) -> list[dict[str, object]]:
            """List available auto and manual tags for filtering retrieval."""
            request_context = ctx.context.request_context
            tags = await self._sources.list_tags(clerk_user_id=request_context.clerk_user_id)
            return [tag.model_dump(mode="json") for tag in tags]

        @function_tool(name_override="create_tag")
        async def create_tag_tool(ctx: ChatKitToolContext, name: str, color: str | None = None) -> dict[str, object]:
            """Create a manual tag for organizing files and filtering retrieval."""
            request_context = ctx.context.request_context
            response = await self._sources.create_tag(
                clerk_user_id=request_context.clerk_user_id,
                name=name,
                color=color,
            )
            return response.model_dump(mode="json")

        @function_tool(name_override="update_tag")
        async def update_tag_tool(
            ctx: ChatKitToolContext,
            tag_id: str,
            name: str | None = None,
            color: str | None = None,
        ) -> dict[str, object]:
            """Rename or recolor a tag, queuing reindex tasks when filter slugs change."""
            request_context = ctx.context.request_context
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="settings-slider", text="Updating tag metadata and queuing affected reindex tasks."
                )
            )
            response = await self._sources.update_tag(
                clerk_user_id=request_context.clerk_user_id,
                tag_id=tag_id,
                name=name,
                color=color,
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
            )
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle",
                    text=f"Tag updated. Queued {len(response.tasks)} reindex task{'' if len(response.tasks) == 1 else 's'}.",
                )
            )
            return response.model_dump(mode="json")

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
            await ctx.context.stream(
                ProgressUpdateEvent(icon="settings-slider", text="Deleting tag and queuing affected reindex tasks.")
            )
            response = await self._sources.delete_tag(
                clerk_user_id=request_context.clerk_user_id,
                tag_id=tag_id,
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
            )
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle",
                    text=f"Tag deleted. Queued {len(response.tasks)} reindex task{'' if len(response.tasks) == 1 else 's'}.",
                )
            )
            return response.model_dump(mode="json")

        @function_tool(name_override="get_source_detail")
        async def get_source_detail_tool(ctx: ChatKitToolContext, source_id: str) -> dict[str, object]:
            """Load one source with its stored metadata and optional semantic split records."""
            request_context = ctx.context.request_context
            detail = await self._sources.get_source(
                clerk_user_id=request_context.clerk_user_id,
                source_id=source_id,
            )
            return detail.model_dump(mode="json")

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
            await ctx.context.stream(ProgressUpdateEvent(icon="document", text="Queuing text source ingestion."))
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
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle", text=f"Source {response.source.id[:8]} queued for ingestion as {task_label}."
                )
            )
            return response.model_dump(mode="json")

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
            await ctx.context.stream(
                ProgressUpdateEvent(icon="search", text=f"Searching indexed files for '{query[:80]}'.")
            )
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
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle",
                    text=f"Found {len(response.hits)} file match{'' if len(response.hits) == 1 else 'es'} across {source_count} source{'' if source_count == 1 else 's'}.",
                )
            )
            return response.model_dump(mode="json")

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
            await ctx.context.stream(
                ProgressUpdateEvent(icon="compass", text="Branching through indexed source-file neighborhoods.")
            )
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
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle",
                    text=f"Explored {len(response.levels)} level{'' if len(response.levels) == 1 else 's'} with {hit_count} hit{'' if hit_count == 1 else 's'}.",
                )
            )
            return response.model_dump(mode="json")

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
            await ctx.context.stream(
                ProgressUpdateEvent(icon="batch", text="Previewing semantic split without publishing chunks.")
            )
            response = await self._sources.preview_semantic_split(
                clerk_user_id=request_context.clerk_user_id,
                filename=filename,
                declared_media_type=media_type,
                payload=text.encode("utf-8"),
                user_guidance=user_guidance,
            )
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle",
                    text=(
                        f"Preview ready: {len(response.split.chunks)} chunk{'' if len(response.split.chunks) == 1 else 's'}, "
                        f"{len(response.split.tags)} tag{'' if len(response.split.tags) == 1 else 's'}, strategy {response.ingest_strategy}."
                    ),
                )
            )
            return response.model_dump(mode="json")

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
            """Start a research import from a topic, paper title, pasted text, or public URL and queue discovered candidates for review."""
            request_context = ctx.context.request_context
            await ctx.context.stream(
                ProgressUpdateEvent(icon="search", text="Starting research import and collecting review candidates.")
            )
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
            )
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle",
                    text=f"Research import complete with {len(response.candidates)} candidate{'' if len(response.candidates) == 1 else 's'} for review.",
                )
            )
            return response.model_dump(mode="json")

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
            await ctx.context.stream(
                ProgressUpdateEvent(icon="library", text="Building a foldered research library from the seed.")
            )
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
            )
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle",
                    text=(
                        f"Research library build complete with {len(response.candidates)} candidate"
                        f"{'' if len(response.candidates) == 1 else 's'} and {len(response.ingested)} queued ingest"
                        f"{'' if len(response.ingested) == 1 else 's'}."
                    ),
                )
            )
            return response.model_dump(mode="json")

        @function_tool(name_override="list_research_candidates")
        async def list_research_candidates_tool(
            ctx: ChatKitToolContext,
            task_id: str | None = None,
            status: str | None = None,
            page_size: int = 20,
        ) -> dict[str, object]:
            """List research import candidates by task or review status."""
            request_context = ctx.context.request_context
            normalized_status = (
                status if status in {"pending", "approved", "rejected", "ingesting", "ingested", "failed"} else None
            )
            response = await self._research.list_candidates(
                clerk_user_id=request_context.clerk_user_id,
                task_id=task_id,
                status=cast(Any, normalized_status),
                page=1,
                page_size=max(1, min(page_size, 50)),
            )
            return response.model_dump(mode="json")

        @function_tool(name_override="update_research_candidate_status")
        async def update_research_candidate_status_tool(
            ctx: ChatKitToolContext,
            candidate_ids: list[str],
            status: str,
        ) -> dict[str, object]:
            """Approve, reject, or return research import candidates to pending review."""
            request_context = ctx.context.request_context
            response = await self._research.update_candidate_status(
                clerk_user_id=request_context.clerk_user_id,
                candidate_ids=candidate_ids,
                status=ResearchCandidateStatusUpdateRequest(
                    candidate_ids=candidate_ids, status=cast(Any, status)
                ).status,
            )
            return response.model_dump(mode="json")

        @function_tool(name_override="ingest_research_candidates")
        async def ingest_research_candidates_tool(
            ctx: ChatKitToolContext,
            candidate_ids: list[str] | None = None,
            task_id: str | None = None,
            tag_ids: list[str] | None = None,
            folder_id: str | None = None,
        ) -> dict[str, object]:
            """Ingest approved research candidates through the app's normal source ingestion path."""
            request_context = ctx.context.request_context
            await ctx.context.stream(
                ProgressUpdateEvent(icon="document", text="Queuing approved research candidates for ingestion.")
            )
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
            )
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle",
                    text=f"Queued {len(response.ingested)} approved candidate{'' if len(response.ingested) == 1 else 's'} for ingestion.",
                )
            )
            return response.model_dump(mode="json")

        @function_tool(name_override="resplit_source")
        async def resplit_source_tool(
            ctx: ChatKitToolContext,
            source_id: str,
            tag_ids: list[str] | None = None,
            user_guidance: str | None = None,
        ) -> dict[str, object]:
            """Queue a re-split that replaces one source's optional split records using its stored payload."""
            request_context = ctx.context.request_context
            await ctx.context.stream(
                ProgressUpdateEvent(icon="reload", text="Queuing a safe re-split for the selected source.")
            )
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
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle", text=f"Re-split queued for source {response.source.id[:8]} as {task_label}."
                )
            )
            return response.model_dump(mode="json")

        @function_tool(name_override="update_source_tags")
        async def update_source_tags_tool(
            ctx: ChatKitToolContext,
            source_id: str,
            tag_ids: list[str],
        ) -> dict[str, object]:
            """Replace a source's tag assignments and queue vector-store reindexing."""
            request_context = ctx.context.request_context
            await ctx.context.stream(
                ProgressUpdateEvent(icon="settings-slider", text="Queuing tag reindex for the selected source.")
            )
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
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle", text=f"Tag reindex queued for source {response.source.id[:8]} as {task_label}."
                )
            )
            return response.model_dump(mode="json")

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
            await ctx.context.stream(
                ProgressUpdateEvent(icon="reload", text="Deleting source and cleaning up stored files.")
            )
            deleted_id = await self._sources.delete_source(
                clerk_user_id=request_context.clerk_user_id,
                source_id=source_id,
            )
            await ctx.context.stream(ProgressUpdateEvent(icon="check-circle", text=f"Deleted source {deleted_id[:8]}."))
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
            return response.model_dump(mode="json")

        @function_tool(name_override="get_task")
        async def get_task_tool(ctx: ChatKitToolContext, task_id: str) -> dict[str, object]:
            """Load task status, inputs, state, result, and error information."""
            request_context = ctx.context.request_context
            response = await self._actions.get_task(
                clerk_user_id=request_context.clerk_user_id,
                task_id=task_id,
            )
            return response.model_dump(mode="json")

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
            await ctx.context.stream(
                ProgressUpdateEvent(icon="search", text="Retrieving chunks and drafting a grounded answer.")
            )
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
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle",
                    text=f"Grounded answer complete as task {response.task_id[:8]} with {len(response.hits)} citation{'' if len(response.hits) == 1 else 's'}.",
                )
            )
            return response.model_dump(mode="json")

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
            await ctx.context.stream(ProgressUpdateEvent(icon="write", text="Drafting from the library context."))
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
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle",
                    text=f"Draft complete as task {response.task_id[:8]} with {len(response.hits)} retrieved chunk{'' if len(response.hits) == 1 else 's'}.",
                )
            )
            return response.model_dump(mode="json")

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
            await ctx.context.stream(
                ProgressUpdateEvent(icon="images", text="Generating an image asset from retrieved context.")
            )
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
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle", text=f"Image generation complete as task {response.task_id[:8]}{asset_text}."
                )
            )
            return response.model_dump(mode="json")

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
            await ctx.context.stream(ProgressUpdateEvent(icon="play", text="Generating a voice asset."))
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
            await ctx.context.stream(
                ProgressUpdateEvent(
                    icon="check-circle", text=f"Voice generation complete as task {response.task_id[:8]}{asset_text}."
                )
            )
            return response.model_dump(mode="json")

        return [
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
            "to replace its optional split records, build foldered research libraries from topics or papers, start research imports, review/import discovered candidates, update a source's tags when the user explicitly asks, list task progress, answer questions, and create image or voice assets. "
            "The app's file explorer is the primary source of file input and selection; selected files are attached to your turn as OpenAI file inputs when ready. "
            "Use set_file_selection, reveal_file, and set_file_search to coordinate the browser UI when the user asks you to select files, navigate to a file, or filter the explorer. "
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


def _title_from_user_message(item: UserMessageItem) -> str | None:
    text_parts = [part.text.strip() for part in item.content if getattr(part, "type", None) == "text"]
    combined = " ".join(part for part in text_parts if part).strip()
    if not combined:
        return None
    return combined if len(combined) <= 72 else combined[:69].rstrip() + "..."


def _model_settings_override_for_model(model: str | None) -> ModelSettings | None:
    if not isinstance(model, str) or not model.startswith("gpt-5"):
        return None
    return ModelSettings(reasoning=Reasoning(effort="low", summary="auto"))


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

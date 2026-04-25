from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import logging
from time import perf_counter
from typing import Any, cast

from agents import Agent, Runner, function_tool
from agents.model_settings import ModelSettings
from agents.tool import Tool
from agents.tool_context import ToolContext
from chatkit.agents import AgentContext as ChatKitAgentContext
from chatkit.agents import ThreadItemConverter, stream_agent_response
from chatkit.server import ChatKitServer
from chatkit.types import ChatKitReq, ProgressUpdateEvent, ThreadMetadata, ThreadStreamEvent, UserMessageItem
from openai.types.responses.response_input_item_param import Message, ResponseInputItemParam
from openai.types.shared import Reasoning
from pydantic import TypeAdapter

from backend.app.chatkit.store import VectorstoreChatContext, VectorstoreChatStore
from backend.app.core.config import AppSettings
from backend.app.schemas import (
    BranchSearchRequest,
    FreeformRequest,
    ImageGenerationRequest,
    QaRequest,
    SearchRequest,
    VoiceGenerationRequest,
)
from backend.app.services import ActionService, SourceService

logger = logging.getLogger("chatkit.server")

MODEL_ALIASES = {
    "default": "gpt-5.4-mini",
    "lightweight": "gpt-5.4-mini",
    "balanced": "gpt-5.4-mini",
    "powerful": "gpt-5.5",
}
MAX_AGENT_TURNS = 20

ChatKitToolContext = ToolContext[ChatKitAgentContext[VectorstoreChatContext]]


class VectorstoreChatKitServer(ChatKitServer[VectorstoreChatContext]):
    """ChatKit surface that talks to app services directly instead of looping through MCP."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        store: VectorstoreChatStore,
        sources: SourceService,
        actions: ActionService,
    ) -> None:
        super().__init__(store=store)
        self._settings = settings
        self._sources = sources
        self._actions = actions
        self._converter = ThreadItemConverter()

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
            name="semantic_vectorstore_agent",
            model=requested_model,
            model_settings=_model_settings_override_for_model(requested_model) or ModelSettings(),
            tools=self._build_tools(),
            instructions=self._agent_instructions,
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
        source_lines: list[str] = []
        for source_id in context.selected_source_ids[:8]:
            try:
                detail = await self._sources.get_source(
                    clerk_user_id=context.clerk_user_id,
                    source_id=source_id,
                )
            except Exception:
                continue
            source_lines.append(
                f"- {detail.display_title} ({detail.id}, {detail.source_kind}, "
                f"{detail.chunk_count} chunks, tags: {', '.join(tag.name for tag in detail.tags) or 'none'})"
            )
        if not source_lines:
            return []
        return [
            cast(
                ResponseInputItemParam,
                Message(
                    role="user",
                    type="message",
                    content=[
                        {
                            "type": "input_text",
                            "text": (
                                "The user currently has these sources selected in the app. "
                                "Treat them as the first retrieval scope unless the user asks to widen it.\n"
                                + "\n".join(source_lines)
                            ),
                        }
                    ],
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
            """List sources in the user's semantic library."""
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

        @function_tool(name_override="list_tags")
        async def list_tags_tool(ctx: ChatKitToolContext) -> list[dict[str, object]]:
            """List available auto and manual tags for filtering retrieval."""
            request_context = ctx.context.request_context
            tags = await self._sources.list_tags(clerk_user_id=request_context.clerk_user_id)
            return [tag.model_dump(mode="json") for tag in tags]

        @function_tool(name_override="search_chunks")
        async def search_chunks_tool(
            ctx: ChatKitToolContext,
            query: str,
            selected_source_ids: list[str] | None = None,
            tag_ids: list[str] | None = None,
            tag_match_mode: str = "all",
            max_results: int = 8,
        ) -> dict[str, object]:
            """Search OpenAI vector-store chunks, then return full app-owned semantic chunks."""
            request_context = ctx.context.request_context
            await ctx.context.stream(ProgressUpdateEvent(text=f"Searching semantic chunks for '{query[:80]}'."))
            response = await self._sources.search(
                clerk_user_id=request_context.clerk_user_id,
                request=SearchRequest(
                    query=query,
                    selected_source_ids=_selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    max_results=max(1, min(max_results, 16)),
                ),
            )
            return response.model_dump(mode="json")

        @function_tool(name_override="branch_search")
        async def branch_search_tool(
            ctx: ChatKitToolContext,
            query: str,
            selected_source_ids: list[str] | None = None,
            tag_ids: list[str] | None = None,
            tag_match_mode: str = "all",
            descend: int = 2,
            max_width: int = 3,
        ) -> dict[str, object]:
            """Run layered semantic search, using hits from each layer to find adjacent chunks."""
            request_context = ctx.context.request_context
            await ctx.context.stream(ProgressUpdateEvent(text="Branching through nearby semantic neighborhoods."))
            response = await self._sources.branch_search(
                clerk_user_id=request_context.clerk_user_id,
                request=BranchSearchRequest(
                    query=query,
                    selected_source_ids=_selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    descend=max(0, min(descend, 4)),
                    max_width=max(1, min(max_width, 8)),
                ),
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
            """Preview semantic chunks and auto-tags for text without creating a source or publishing vectors."""
            request_context = ctx.context.request_context
            await ctx.context.stream(ProgressUpdateEvent(text="Previewing semantic split without publishing chunks."))
            response = await self._sources.preview_semantic_split(
                clerk_user_id=request_context.clerk_user_id,
                filename=filename,
                declared_media_type=media_type,
                payload=text.encode("utf-8"),
                user_guidance=user_guidance,
            )
            return response.model_dump(mode="json")

        @function_tool(name_override="resplit_source")
        async def resplit_source_tool(
            ctx: ChatKitToolContext,
            source_id: str,
            tag_ids: list[str] | None = None,
            user_guidance: str | None = None,
        ) -> dict[str, object]:
            """Queue a re-split that replaces one source's published chunks using its stored payload."""
            request_context = ctx.context.request_context
            await ctx.context.stream(ProgressUpdateEvent(text="Queuing a safe re-split for the selected source."))
            response = await self._sources.resplit_source(
                clerk_user_id=request_context.clerk_user_id,
                source_id=source_id,
                tag_ids=tag_ids,
                user_guidance=user_guidance,
                origin_surface="chatkit",
                origin_thread_id=ctx.context.thread.id,
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
            await ctx.context.stream(ProgressUpdateEvent(text="Retrieving chunks and drafting a grounded answer."))
            response = await self._actions.qa(
                clerk_user_id=request_context.clerk_user_id,
                payload=QaRequest(
                    prompt=prompt,
                    selected_source_ids=_selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    max_results=max(1, min(max_results, 16)),
                    origin_thread_id=ctx.context.thread.id,
                ),
                origin_surface="chatkit",
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
            response = await self._actions.freeform(
                clerk_user_id=request_context.clerk_user_id,
                payload=FreeformRequest(
                    prompt=prompt,
                    mode="creative" if mode == "creative" else "grounded",
                    selected_source_ids=_selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    max_results=max(1, min(max_results, 16)),
                    origin_thread_id=ctx.context.thread.id,
                ),
                origin_surface="chatkit",
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
            """Generate an image, optionally grounded in retrieved semantic chunks."""
            request_context = ctx.context.request_context
            await ctx.context.stream(ProgressUpdateEvent(text="Generating an image asset from retrieved context."))
            response = await self._actions.image(
                clerk_user_id=request_context.clerk_user_id,
                payload=ImageGenerationRequest(
                    prompt=prompt,
                    size=size,
                    selected_source_ids=_selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    origin_thread_id=ctx.context.thread.id,
                ),
                origin_surface="chatkit",
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
            response = await self._actions.voice(
                clerk_user_id=request_context.clerk_user_id,
                payload=VoiceGenerationRequest(
                    prompt=prompt,
                    source_text=source_text,
                    voice=voice,
                    response_format=cast(Any, response_format if response_format in {"mp3", "wav", "opus"} else "mp3"),
                    selected_source_ids=_selected_scope(request_context, selected_source_ids),
                    tag_ids=tag_ids or [],
                    tag_match_mode="any" if tag_match_mode == "any" else "all",
                    origin_thread_id=ctx.context.thread.id,
                ),
                origin_surface="chatkit",
            )
            return response.model_dump(mode="json")

        return [
            list_sources_tool,
            list_tags_tool,
            search_chunks_tool,
            branch_search_tool,
            preview_semantic_split_tool,
            resplit_source_tool,
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
            "You are the semantic library assistant for an app-first OpenAI vector-store RAG workspace. "
            "Use the direct app tools to list sources, inspect tags, search chunks, branch through related "
            "semantic chunks, preview proposed text splits without publishing them, re-split an existing source when the user asks "
            "to replace its published chunks, answer questions, and create image or voice assets. "
            "Treat split previews as inspect-only; iterate by rerunning the preview with revised guidance before re-splitting. Prefer the user's selected "
            "sources when present. Be concise, name the evidence you used, and say clearly when the library "
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


def _selected_scope(context: VectorstoreChatContext, explicit_ids: list[str] | None) -> list[str]:
    if explicit_ids:
        return [source_id for source_id in explicit_ids if source_id.strip()]
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

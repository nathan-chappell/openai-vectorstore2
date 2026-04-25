from __future__ import annotations

from base64 import b64decode
import binascii
from collections.abc import Awaitable
from contextlib import asynccontextmanager
import json
import logging
from time import perf_counter
from typing import Annotated, Any, Literal

from fastmcp import FastMCP, FastMCPApp
from fastmcp.server.context import Context
from mcp.types import ToolAnnotations
from prefab_ui import PrefabApp
from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.mcp import CallTool
from prefab_ui.components import (
    ERROR,
    RESULT,
    STATE,
    Badge,
    Button,
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
    Column,
    ForEach,
    H3 as PrefabH3,
    If,
    Muted,
    Row,
    Separator,
    Small,
    Text,
)
from pydantic import BaseModel, Field

from backend.app.bootstrap import AppServices
from backend.app.core.config import AppSettings
from backend.app.mcp.auth import VectorstoreTokenVerifier, current_mcp_clerk_user_id
from backend.app.schemas import (
    ActionResponse,
    BranchSearchRequest,
    BranchSearchResponse,
    FileListResponse,
    FreeformRequest,
    ImageGenerationRequest,
    IngestFinalizeResponse,
    LibrarySourceDetail,
    QaRequest,
    SearchRequest,
    SearchResponse,
    SplitPreviewResponse,
    SourceKind,
    TagSummary,
    TaskDetail,
    TaskKind,
    TaskListResponse,
    VoiceGenerationRequest,
)

Badge: Any = Badge
Button: Any = Button
CallTool: Any = CallTool
Card: Any = Card
CardContent: Any = CardContent
CardDescription: Any = CardDescription
CardHeader: Any = CardHeader
CardTitle: Any = CardTitle
Column: Any = Column
ForEach: Any = ForEach
h3: Any = PrefabH3
If: Any = If
Muted: Any = Muted
Row: Any = Row
Separator: Any = Separator
SetState: Any = SetState
ShowToast: Any = ShowToast
Small: Any = Small
Text: Any = Text

logger = logging.getLogger(__name__)


def create_mcp_server(settings: AppSettings, services: AppServices) -> FastMCP:
    return _build_mcp_server(
        settings=settings,
        services=services,
        auth=VectorstoreTokenVerifier(settings=settings, auth=services.auth),
    )


def create_dev_mcp_server(settings: AppSettings, services: AppServices) -> FastMCP:
    return _build_mcp_server(settings=settings, services=services, auth=None)


async def _run_logged_tool(
    *,
    tool_name: str,
    clerk_user_id: str,
    arguments: dict[str, object],
    operation: Awaitable[Any],
) -> Any:
    started_at = perf_counter()
    logger.info(
        "mcp_tool_started tool=%s clerk_user_id=%s arguments=%s",
        tool_name,
        clerk_user_id,
        _serialize_for_log(arguments),
    )
    try:
        result = await operation
    except Exception:
        logger.error(
            "mcp_tool_failed tool=%s clerk_user_id=%s arguments=%s duration_ms=%.1f",
            tool_name,
            clerk_user_id,
            _serialize_for_log(arguments),
            (perf_counter() - started_at) * 1000,
        )
        raise
    logger.info(
        "mcp_tool_completed tool=%s clerk_user_id=%s result=%s duration_ms=%.1f",
        tool_name,
        clerk_user_id,
        _serialize_for_log(_summarize_result(result)),
        (perf_counter() - started_at) * 1000,
    )
    return result


def _build_mcp_server(*, settings: AppSettings, services: AppServices, auth: Any | None) -> FastMCP:
    @asynccontextmanager
    async def server_lifespan(_: FastMCP[None]):
        await services.database.ensure_ready()
        yield None

    server = FastMCP(
        name=settings.app_name,
        instructions=(
            "You are the MCP adapter for an app-first semantic RAG workspace. The app owns ingestion, "
            "semantic chunks, storage, retrieval, and generation. Use sources for a visual library UI; "
            "use list_sources, list_tags, search_chunks, branch_search, qa, freeform, generate_image, "
            "generate_voice, list_tasks, and get_task to operate on the current user's library. "
            "Only delete a source after explicit user confirmation."
        ),
        auth=auth,
        lifespan=server_lifespan,
    )
    _register_tools(server=server, services=services)
    _register_sources_app(server=server, services=services)
    return server


def _register_tools(*, server: FastMCP, services: AppServices) -> None:
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    mutating = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)

    @server.tool(name="list_sources", description="List sources in the user's semantic library.", annotations=read_only)
    async def list_sources_tool(
        query: Annotated[str | None, Field(min_length=1)] = None,
        tag_ids: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> FileListResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="list_sources",
            clerk_user_id=clerk_user_id,
            arguments={
                "query": query,
                "tag_ids": tag_ids or [],
                "tag_match_mode": tag_match_mode,
                "page": page,
                "page_size": page_size,
            },
            operation=services.sources.list_sources(
                clerk_user_id=clerk_user_id,
                query=query,
                tag_ids=tag_ids or [],
                tag_match_mode=tag_match_mode,
                page=page,
                page_size=page_size,
            ),
        )

    @server.tool(name="list_tags", description="List available source tags.", annotations=read_only)
    async def list_tags_tool() -> list[TagSummary]:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="list_tags",
            clerk_user_id=clerk_user_id,
            arguments={},
            operation=_list_tags(services=services, clerk_user_id=clerk_user_id),
        )

    @server.tool(
        name="get_source_detail", description="Load source metadata and semantic chunks.", annotations=read_only
    )
    async def get_source_detail_tool(source_id: Annotated[str, Field(min_length=1)]) -> LibrarySourceDetail:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="get_source_detail",
            clerk_user_id=clerk_user_id,
            arguments={"source_id": source_id},
            operation=services.sources.get_source(clerk_user_id=clerk_user_id, source_id=source_id),
        )

    @server.tool(
        name="ingest_text_source",
        description="Create a text source and publish its semantic chunks to the vector store.",
        annotations=mutating,
    )
    async def ingest_text_source_tool(
        filename: Annotated[str, Field(min_length=1)] = "note.txt",
        text: Annotated[str, Field(min_length=1)] = "",
        tag_ids: list[str] | None = None,
        user_guidance: str | None = None,
    ) -> IngestFinalizeResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="ingest_text_source",
            clerk_user_id=clerk_user_id,
            arguments={"filename": filename, "chars": len(text), "tag_ids": tag_ids or []},
            operation=services.sources.ingest_source(
                clerk_user_id=clerk_user_id,
                filename=filename,
                declared_media_type="text/plain",
                payload=text.encode("utf-8"),
                tag_ids=tag_ids or [],
                user_guidance=user_guidance,
                origin_surface="mcp",
            ),
        )

    @server.tool(
        name="ingest_file_source",
        description="Create a file source from base64 payload and publish its semantic chunks to the vector store.",
        annotations=mutating,
    )
    async def ingest_file_source_tool(
        filename: Annotated[str, Field(min_length=1)],
        payload_base64: Annotated[str, Field(min_length=1)],
        media_type: str | None = None,
        tag_ids: list[str] | None = None,
        user_guidance: str | None = None,
    ) -> IngestFinalizeResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        try:
            payload = b64decode(payload_base64, validate=True)
        except binascii.Error as exc:
            raise ValueError("payload_base64 must be valid base64 data.") from exc
        return await _run_logged_tool(
            tool_name="ingest_file_source",
            clerk_user_id=clerk_user_id,
            arguments={
                "filename": filename,
                "media_type": media_type,
                "bytes": len(payload),
                "tag_ids": tag_ids or [],
            },
            operation=services.sources.ingest_source(
                clerk_user_id=clerk_user_id,
                filename=filename,
                declared_media_type=media_type,
                payload=payload,
                tag_ids=tag_ids or [],
                user_guidance=user_guidance,
                origin_surface="mcp",
            ),
        )

    @server.tool(
        name="preview_text_split",
        description="Preview semantic chunks and tags for text without creating a source or publishing vectors.",
        annotations=read_only,
    )
    async def preview_text_split_tool(
        filename: Annotated[str, Field(min_length=1)] = "note.txt",
        text: Annotated[str, Field(min_length=1)] = "",
        media_type: str | None = "text/plain",
        user_guidance: str | None = None,
    ) -> SplitPreviewResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="preview_text_split",
            clerk_user_id=clerk_user_id,
            arguments={"filename": filename, "chars": len(text), "media_type": media_type},
            operation=services.sources.preview_semantic_split(
                clerk_user_id=clerk_user_id,
                filename=filename,
                declared_media_type=media_type,
                payload=text.encode("utf-8"),
                user_guidance=user_guidance,
            ),
        )

    @server.tool(
        name="preview_file_split",
        description="Preview semantic chunks and tags for a base64 file payload without creating a source or publishing vectors.",
        annotations=read_only,
    )
    async def preview_file_split_tool(
        filename: Annotated[str, Field(min_length=1)],
        payload_base64: Annotated[str, Field(min_length=1)],
        media_type: str | None = None,
        user_guidance: str | None = None,
    ) -> SplitPreviewResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        try:
            payload = b64decode(payload_base64, validate=True)
        except binascii.Error as exc:
            raise ValueError("payload_base64 must be valid base64 data.") from exc
        return await _run_logged_tool(
            tool_name="preview_file_split",
            clerk_user_id=clerk_user_id,
            arguments={"filename": filename, "media_type": media_type, "bytes": len(payload)},
            operation=services.sources.preview_semantic_split(
                clerk_user_id=clerk_user_id,
                filename=filename,
                declared_media_type=media_type,
                payload=payload,
                user_guidance=user_guidance,
            ),
        )

    @server.tool(
        name="resplit_source",
        description="Replace one source's semantic chunks and vector-store files after explicit confirmation.",
        annotations=destructive,
    )
    async def resplit_source_tool(
        source_id: Annotated[str, Field(min_length=1)],
        tag_ids: list[str] | None = None,
        user_guidance: str | None = None,
        confirm: bool = False,
    ) -> IngestFinalizeResponse | dict[str, object]:
        clerk_user_id = current_mcp_clerk_user_id()
        if not confirm:
            return {
                "confirmation_required": True,
                "source_id": source_id,
                "message": "Ask the user to confirm replacing this source's published chunks, then call resplit_source again with confirm=true.",
            }
        return await _run_logged_tool(
            tool_name="resplit_source",
            clerk_user_id=clerk_user_id,
            arguments={"source_id": source_id, "tag_ids": tag_ids or [], "confirm": confirm},
            operation=services.sources.resplit_source(
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                tag_ids=tag_ids,
                user_guidance=user_guidance,
                origin_surface="mcp",
            ),
        )

    @server.tool(
        name="delete_source",
        description="Delete one source after explicit confirmation.",
        annotations=destructive,
    )
    async def delete_source_tool(
        source_id: Annotated[str, Field(min_length=1)],
        confirm: bool = False,
    ) -> dict[str, object]:
        clerk_user_id = current_mcp_clerk_user_id()
        if not confirm:
            return {
                "confirmation_required": True,
                "source_id": source_id,
                "message": "Ask the user to confirm deletion, then call delete_source again with confirm=true.",
            }
        deleted_id = await _run_logged_tool(
            tool_name="delete_source",
            clerk_user_id=clerk_user_id,
            arguments={"source_id": source_id, "confirm": confirm},
            operation=services.sources.delete_source(clerk_user_id=clerk_user_id, source_id=source_id),
        )
        return {"deleted_source_id": deleted_id}

    @server.tool(
        name="search_chunks",
        description="Search semantic chunks and return full app-owned chunk text.",
        annotations=read_only,
    )
    async def search_chunks_tool(
        query: Annotated[str, Field(min_length=1)],
        selected_source_ids: list[str] | None = None,
        source_kinds: list[SourceKind] | None = None,
        tag_ids: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        max_results: Annotated[int, Field(ge=1, le=24)] = 8,
    ) -> SearchResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = SearchRequest(
            query=query,
            selected_source_ids=selected_source_ids or [],
            source_kinds=source_kinds or [],
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
            max_results=max_results,
        )
        return await _run_logged_tool(
            tool_name="search_chunks",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=services.sources.search(clerk_user_id=clerk_user_id, request=payload),
        )

    @server.tool(
        name="branch_search", description="Layer semantic search outward from each layer's hits.", annotations=read_only
    )
    async def branch_search_tool(
        query: Annotated[str, Field(min_length=1)],
        selected_source_ids: list[str] | None = None,
        source_kinds: list[SourceKind] | None = None,
        tag_ids: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        descend: Annotated[int, Field(ge=0, le=4)] = 2,
        max_width: Annotated[int, Field(ge=1, le=8)] = 3,
    ) -> BranchSearchResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = BranchSearchRequest(
            query=query,
            selected_source_ids=selected_source_ids or [],
            source_kinds=source_kinds or [],
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
            descend=descend,
            max_width=max_width,
        )
        return await _run_logged_tool(
            tool_name="branch_search",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=services.sources.branch_search(clerk_user_id=clerk_user_id, request=payload),
        )

    @server.tool(name="qa", description="Answer a question using retrieved semantic chunks.", annotations=mutating)
    async def qa_tool(
        prompt: Annotated[str, Field(min_length=1)],
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        max_results: Annotated[int, Field(ge=1, le=16)] = 8,
    ) -> ActionResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = QaRequest(
            prompt=prompt,
            selected_source_ids=selected_source_ids or [],
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
            max_results=max_results,
        )
        return await _run_logged_tool(
            tool_name="qa",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=services.actions.qa(clerk_user_id=clerk_user_id, payload=payload, origin_surface="mcp"),
        )

    @server.tool(
        name="freeform", description="Generate grounded or creative text with retrieved context.", annotations=mutating
    )
    async def freeform_tool(
        prompt: Annotated[str, Field(min_length=1)],
        mode: Literal["grounded", "creative"] = "grounded",
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        max_results: Annotated[int, Field(ge=1, le=16)] = 8,
    ) -> ActionResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = FreeformRequest(
            prompt=prompt,
            mode=mode,
            selected_source_ids=selected_source_ids or [],
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
            max_results=max_results,
        )
        return await _run_logged_tool(
            tool_name="freeform",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=services.actions.freeform(clerk_user_id=clerk_user_id, payload=payload, origin_surface="mcp"),
        )

    @server.tool(
        name="generate_image",
        description="Generate an image asset, optionally grounded in chunks.",
        annotations=mutating,
    )
    async def generate_image_tool(
        prompt: Annotated[str, Field(min_length=1)],
        size: str = "1024x1024",
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
    ) -> ActionResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = ImageGenerationRequest(
            prompt=prompt,
            size=size,
            selected_source_ids=selected_source_ids or [],
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
        )
        return await _run_logged_tool(
            tool_name="generate_image",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=services.actions.image(clerk_user_id=clerk_user_id, payload=payload, origin_surface="mcp"),
        )

    @server.tool(name="generate_voice", description="Generate a voice/audio asset from text.", annotations=mutating)
    async def generate_voice_tool(
        prompt: Annotated[str, Field(min_length=1)],
        source_text: str | None = None,
        voice: str | None = None,
        response_format: Literal["mp3", "wav", "opus"] = "mp3",
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
    ) -> ActionResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = VoiceGenerationRequest(
            prompt=prompt,
            source_text=source_text,
            voice=voice,
            response_format=response_format,
            selected_source_ids=selected_source_ids or [],
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
        )
        return await _run_logged_tool(
            tool_name="generate_voice",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=services.actions.voice(clerk_user_id=clerk_user_id, payload=payload, origin_surface="mcp"),
        )

    @server.tool(name="list_tasks", description="List recent app tasks.", annotations=read_only)
    async def list_tasks_tool(
        kind: TaskKind | None = None, limit: Annotated[int, Field(ge=1, le=200)] = 50
    ) -> TaskListResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="list_tasks",
            clerk_user_id=clerk_user_id,
            arguments={"kind": kind, "limit": limit},
            operation=services.actions.list_tasks(clerk_user_id=clerk_user_id, kind=kind, limit=limit),
        )

    @server.tool(name="get_task", description="Load a task by ID.", annotations=read_only)
    async def get_task_tool(task_id: Annotated[str, Field(min_length=1)]) -> TaskDetail:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="get_task",
            clerk_user_id=clerk_user_id,
            arguments={"task_id": task_id},
            operation=services.actions.get_task(clerk_user_id=clerk_user_id, task_id=task_id),
        )


async def _list_tags(*, services: AppServices, clerk_user_id: str) -> list[TagSummary]:
    return await services.sources.list_tags(clerk_user_id=clerk_user_id)


def _register_sources_app(*, server: FastMCP, services: AppServices) -> None:
    sources_app = FastMCPApp("Semantic Sources")

    @sources_app.tool("refresh_sources")
    async def refresh_sources_tool(ctx: Context) -> dict[str, Any]:
        del ctx
        response = await services.sources.list_sources(
            clerk_user_id=current_mcp_clerk_user_id(),
            query=None,
            tag_ids=[],
            tag_match_mode="all",
            page=1,
            page_size=30,
        )
        return response.model_dump(mode="json")

    @sources_app.tool("search_sources_for_ui")
    async def search_sources_for_ui_tool(
        query: str,
        ctx: Context,
        max_results: Annotated[int, Field(ge=1, le=16)] = 8,
    ) -> dict[str, Any]:
        del ctx
        if not query.strip():
            return {"query": "", "hits": []}
        response = await services.sources.search(
            clerk_user_id=current_mcp_clerk_user_id(),
            request=SearchRequest(query=query, max_results=max_results),
        )
        return response.model_dump(mode="json")

    @sources_app.tool("load_source_for_ui")
    async def load_source_for_ui_tool(source_id: str, ctx: Context) -> dict[str, Any]:
        del ctx
        response = await services.sources.get_source(
            clerk_user_id=current_mcp_clerk_user_id(),
            source_id=source_id,
        )
        return response.model_dump(mode="json")

    @sources_app.tool("resplit_source_for_ui")
    async def resplit_source_for_ui_tool(source_id: str, ctx: Context) -> dict[str, Any]:
        del ctx
        response = await services.sources.resplit_source(
            clerk_user_id=current_mcp_clerk_user_id(),
            source_id=source_id,
            tag_ids=None,
            user_guidance=None,
            origin_surface="mcp",
        )
        return response.model_dump(mode="json")

    @sources_app.ui(
        name="sources",
        title="Semantic Sources",
        description="Browse the current user's semantic sources and inspect chunk search output.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def sources(ctx: Context) -> PrefabApp:
        initial_sources = await refresh_sources_tool(ctx)
        with Card(css_class="max-w-4xl mx-auto") as view:
            with CardHeader(), Column(gap=1):
                CardTitle("Semantic Sources")
                CardDescription("A compact MCP App view over the same app-owned semantic library used by ChatKit.")
            with CardContent(), Column(gap=4):
                with Row(gap=2, align="center"):
                    h3("Library")
                    Button(
                        "Refresh",
                        on_click=CallTool(
                            "refresh_sources",
                            on_success=SetState("sources", RESULT),
                            on_error=ShowToast(ERROR, variant="error"),
                        ),
                    )
                with ForEach(STATE.sources.sources) as source, Card(css_class="border border-slate-200"):
                    with CardContent(), Column(gap=2):
                        with Row(gap=2, align="center"):
                            Text(source.display_title)  # ty:ignore[invalid-argument-type]
                            Badge(source.source_kind, variant="secondary")  # ty:ignore[invalid-argument-type]
                            Badge(source.status, variant="outline")  # ty:ignore[invalid-argument-type]
                        Small(source.original_filename)  # ty:ignore[invalid-argument-type]
                        Muted(source.chunk_count)  # ty:ignore[invalid-argument-type]
                        with Row(gap=2, align="center"):
                            Button(
                                "Inspect",
                                on_click=CallTool(
                                    "load_source_for_ui",
                                    arguments={"source_id": source.id},  # ty:ignore[invalid-argument-type]
                                    on_success=SetState("selectedSource", RESULT),
                                    on_error=ShowToast(ERROR, variant="error"),
                                ),
                            )
                            Button(
                                "Re-split",
                                variant="secondary",
                                on_click=CallTool(
                                    "resplit_source_for_ui",
                                    arguments={"source_id": source.id},  # ty:ignore[invalid-argument-type]
                                    on_success=[
                                        ShowToast("Re-split queued", variant="success"),
                                        CallTool(
                                            "refresh_sources",
                                            on_success=SetState("sources", RESULT),
                                            on_error=ShowToast(ERROR, variant="error"),
                                        ),
                                    ],
                                    on_error=ShowToast(ERROR, variant="error"),
                                ),
                            )
                with If(STATE.selectedSource):
                    Separator()
                    with Card(css_class="border border-slate-200"):
                        with CardHeader(), Column(gap=1):
                            CardTitle(STATE.selectedSource.display_title)  # ty:ignore[invalid-argument-type]
                            CardDescription(STATE.selectedSource.original_filename)  # ty:ignore[invalid-argument-type]
                        with CardContent(), Column(gap=2):
                            with Row(gap=2, align="center"):
                                Badge(STATE.selectedSource.source_kind, variant="secondary")  # ty:ignore[invalid-argument-type]
                                Badge(STATE.selectedSource.status, variant="outline")  # ty:ignore[invalid-argument-type]
                                Muted(STATE.selectedSource.chunk_count)  # ty:ignore[invalid-argument-type]
                            with ForEach(STATE.selectedSource.chunks) as chunk:
                                Small(chunk.title)  # ty:ignore[invalid-argument-type]
                Separator()
                Muted("Use the search_chunks and branch_search tools for deeper retrieval from ChatGPT hosts.")
        return PrefabApp(
            title="Semantic Sources", view=view, state={"sources": initial_sources, "selectedSource": None}
        )

    server.add_provider(sources_app)


def _serialize_for_log(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _summarize_result(result: object) -> object:
    if isinstance(result, FileListResponse):
        return {"returned": len(result.sources), "total_count": result.total_count}
    if isinstance(result, SearchResponse):
        return {"returned": len(result.hits)}
    if isinstance(result, BranchSearchResponse):
        return {"levels": len(result.levels), "returned": sum(len(level.hits) for level in result.levels)}
    if isinstance(result, SplitPreviewResponse):
        return {
            "source_kind": result.source_kind,
            "chunks": len(result.split.chunks),
            "tags": len(result.split.tags),
            "extracted_character_count": result.extracted_character_count,
        }
    if isinstance(result, IngestFinalizeResponse):
        return {
            "source_id": result.source.id,
            "source_status": result.source.status,
            "task_id": result.task.id if result.task is not None else None,
            "task_status": result.task.status if result.task is not None else None,
        }
    if isinstance(result, ActionResponse):
        return {
            "task_id": result.task_id,
            "kind": result.kind,
            "hits": len(result.hits),
            "asset": result.asset is not None,
        }
    if isinstance(result, TaskListResponse):
        return {"returned": len(result.tasks)}
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, list):
        return {"returned": len(result)}
    return result

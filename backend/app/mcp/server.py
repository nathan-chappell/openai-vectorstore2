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
    EVENT,
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
    Form,
    H3 as PrefabH3,
    If,
    Input,
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
    FilesystemDeleteResponse,
    FilesystemEntrySummary,
    FilesystemListResponse,
    FilesystemSearchResponse,
    FreeformRequest,
    ImageGenerationRequest,
    IngestFinalizeResponse,
    LibrarySourceDetail,
    QaRequest,
    ResearchCandidateIngestRequest,
    ResearchCandidateIngestResponse,
    ResearchCandidateListResponse,
    ResearchCandidateStatus,
    ResearchCandidateStatusUpdateResponse,
    ResearchImportCreateRequest,
    ResearchImportResponse,
    ResearchLibraryBuildRequest,
    ResearchLibraryBuildResponse,
    SearchRequest,
    SearchResponse,
    SplitPreviewResponse,
    SourceKind,
    TagMutationResponse,
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
Form: Any = Form
h3: Any = PrefabH3
If: Any = If
Input: Any = Input
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
            "You are the MCP adapter for an app-first file explorer backed by OpenAI vector-store search. "
            "The app owns ingestion, indexing, storage, retrieval, and generation. Use sources for a visual library UI; "
            "use list_sources, list_filesystem, search_filesystem, list_tags, create_tag, update_tag, delete_tag, "
            "start_research_import, build_research_library, list_research_candidates, update_research_candidate_status, ingest_research_candidates, "
            "search_chunks, branch_search, qa, answer_research_library, freeform, generate_image, generate_voice, update_source_tags, "
            "list_tasks, and get_task to operate on the current user's library. "
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

    @server.tool(
        name="list_sources", description="List sources in the user's indexed file library.", annotations=read_only
    )
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

    @server.tool(
        name="list_filesystem",
        description="List the children of one virtual filesystem folder.",
        annotations=read_only,
    )
    async def list_filesystem_tool(
        folder_id: Annotated[str | None, Field(min_length=1)] = None,
    ) -> FilesystemListResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="list_filesystem",
            clerk_user_id=clerk_user_id,
            arguments={"folder_id": folder_id},
            operation=services.sources.list_filesystem(clerk_user_id=clerk_user_id, folder_id=folder_id),
        )

    @server.tool(
        name="search_filesystem",
        description="Find virtual files and folders by path, filename, tags, and vector-store retrieval.",
        annotations=read_only,
    )
    async def search_filesystem_tool(
        query: Annotated[str | None, Field(min_length=1)] = None,
        tag_ids: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> FilesystemSearchResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="search_filesystem",
            clerk_user_id=clerk_user_id,
            arguments={
                "query": query,
                "tag_ids": tag_ids or [],
                "tag_match_mode": tag_match_mode,
                "page": page,
                "page_size": page_size,
            },
            operation=services.sources.search_filesystem(
                clerk_user_id=clerk_user_id,
                query=query,
                tag_ids=tag_ids or [],
                tag_match_mode=tag_match_mode,
                page=page,
                page_size=page_size,
            ),
        )

    @server.tool(name="create_folder", description="Create a virtual filesystem folder.", annotations=mutating)
    async def create_folder_tool(
        name: Annotated[str, Field(min_length=1, max_length=255)],
        parent_id: Annotated[str | None, Field(min_length=1)] = None,
    ) -> FilesystemEntrySummary:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="create_folder",
            clerk_user_id=clerk_user_id,
            arguments={"name": name, "parent_id": parent_id},
            operation=services.sources.create_folder(
                clerk_user_id=clerk_user_id,
                parent_id=parent_id,
                name=name,
            ),
        )

    @server.tool(
        name="update_filesystem_entry",
        description="Rename or move a virtual file or folder.",
        annotations=mutating,
    )
    async def update_filesystem_entry_tool(
        entry_id: Annotated[str, Field(min_length=1)],
        name: Annotated[str | None, Field(min_length=1, max_length=255)] = None,
        parent_id: Annotated[str | None, Field(min_length=1)] = None,
    ) -> FilesystemEntrySummary:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="update_filesystem_entry",
            clerk_user_id=clerk_user_id,
            arguments={"entry_id": entry_id, "name": name, "parent_id": parent_id},
            operation=services.sources.update_filesystem_entry(
                clerk_user_id=clerk_user_id,
                entry_id=entry_id,
                name=name,
                parent_id=parent_id,
                origin_surface="mcp",
            ),
        )

    @server.tool(
        name="delete_filesystem_entries",
        description="Permanently delete virtual files or folders after explicit confirmation.",
        annotations=destructive,
    )
    async def delete_filesystem_entries_tool(
        entry_ids: list[str],
        confirm: bool = False,
    ) -> FilesystemDeleteResponse | dict[str, object]:
        clerk_user_id = current_mcp_clerk_user_id()
        if not confirm:
            return {
                "confirmation_required": True,
                "entry_ids": entry_ids,
                "message": "Ask the user to confirm permanent deletion, then call delete_filesystem_entries again with confirm=true.",
            }
        return await _run_logged_tool(
            tool_name="delete_filesystem_entries",
            clerk_user_id=clerk_user_id,
            arguments={"entry_ids": entry_ids, "confirm": confirm},
            operation=services.sources.delete_filesystem_entries(
                clerk_user_id=clerk_user_id,
                entry_ids=entry_ids,
                confirm=confirm,
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

    @server.tool(name="create_tag", description="Create a manual source tag.", annotations=mutating)
    async def create_tag_tool(
        name: Annotated[str, Field(min_length=1, max_length=80)],
        color: Annotated[str | None, Field(max_length=32)] = None,
    ) -> TagMutationResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="create_tag",
            clerk_user_id=clerk_user_id,
            arguments={"name": name, "color": color},
            operation=services.sources.create_tag(clerk_user_id=clerk_user_id, name=name, color=color),
        )

    @server.tool(
        name="update_tag",
        description="Rename or recolor a tag and queue reindexing if its filter slug changes.",
        annotations=mutating,
    )
    async def update_tag_tool(
        tag_id: Annotated[str, Field(min_length=1)],
        name: Annotated[str | None, Field(min_length=1, max_length=80)] = None,
        color: Annotated[str | None, Field(max_length=32)] = None,
    ) -> TagMutationResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="update_tag",
            clerk_user_id=clerk_user_id,
            arguments={"tag_id": tag_id, "name": name, "color": color},
            operation=services.sources.update_tag(
                clerk_user_id=clerk_user_id,
                tag_id=tag_id,
                name=name,
                color=color,
                origin_surface="mcp",
            ),
        )

    @server.tool(
        name="delete_tag",
        description="Delete a tag after explicit confirmation and queue affected source reindexing.",
        annotations=destructive,
    )
    async def delete_tag_tool(
        tag_id: Annotated[str, Field(min_length=1)],
        confirm: bool = False,
    ) -> TagMutationResponse | dict[str, object]:
        clerk_user_id = current_mcp_clerk_user_id()
        if not confirm:
            return {
                "confirmation_required": True,
                "tag_id": tag_id,
                "message": "Ask the user to confirm deleting this tag, then call delete_tag again with confirm=true.",
            }
        return await _run_logged_tool(
            tool_name="delete_tag",
            clerk_user_id=clerk_user_id,
            arguments={"tag_id": tag_id, "confirm": confirm},
            operation=services.sources.delete_tag(
                clerk_user_id=clerk_user_id,
                tag_id=tag_id,
                origin_surface="mcp",
            ),
        )

    @server.tool(
        name="get_source_detail",
        description="Load source metadata and optional semantic split records.",
        annotations=read_only,
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
        description="Create a text source and index it as a source-level OpenAI vector-store file.",
        annotations=mutating,
    )
    async def ingest_text_source_tool(
        filename: Annotated[str, Field(min_length=1)] = "note.txt",
        text: Annotated[str, Field(min_length=1)] = "",
        tag_ids: list[str] | None = None,
        user_guidance: str | None = None,
        folder_id: Annotated[str | None, Field(min_length=1)] = None,
        virtual_name: Annotated[str | None, Field(min_length=1, max_length=255)] = None,
    ) -> IngestFinalizeResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="ingest_text_source",
            clerk_user_id=clerk_user_id,
            arguments={
                "filename": filename,
                "chars": len(text),
                "tag_ids": tag_ids or [],
                "folder_id": folder_id,
                "virtual_name": virtual_name,
            },
            operation=services.sources.ingest_source(
                clerk_user_id=clerk_user_id,
                filename=filename,
                declared_media_type="text/plain",
                payload=text.encode("utf-8"),
                tag_ids=tag_ids or [],
                user_guidance=user_guidance,
                origin_surface="mcp",
                folder_id=folder_id,
                virtual_name=virtual_name,
            ),
        )

    @server.tool(
        name="ingest_file_source",
        description="Create a file source from base64 payload and index it as a source-level OpenAI vector-store file.",
        annotations=mutating,
    )
    async def ingest_file_source_tool(
        filename: Annotated[str, Field(min_length=1)],
        payload_base64: Annotated[str, Field(min_length=1)],
        media_type: str | None = None,
        tag_ids: list[str] | None = None,
        user_guidance: str | None = None,
        folder_id: Annotated[str | None, Field(min_length=1)] = None,
        virtual_name: Annotated[str | None, Field(min_length=1, max_length=255)] = None,
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
                "folder_id": folder_id,
                "virtual_name": virtual_name,
            },
            operation=services.sources.ingest_source(
                clerk_user_id=clerk_user_id,
                filename=filename,
                declared_media_type=media_type,
                payload=payload,
                tag_ids=tag_ids or [],
                user_guidance=user_guidance,
                origin_surface="mcp",
                folder_id=folder_id,
                virtual_name=virtual_name,
            ),
        )

    @server.tool(
        name="preview_text_split",
        description="Preview semantic split records and tags for text without creating a source or publishing vectors.",
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
        description="Preview semantic split records and tags for a base64 file payload without creating a source or publishing vectors.",
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
        name="start_research_import",
        description="Start a research import from a topic, paper title, pasted text, an uploaded base64 file, or a public URL.",
        annotations=mutating,
    )
    async def start_research_import_tool(
        seed_type: Literal[
            "topic", "paper", "text", "url", "pdf_url", "arxiv_url", "uploaded_file", "linkedin_export"
        ] = "topic",
        text: str | None = None,
        url: Annotated[str | None, Field(max_length=2048)] = None,
        title: Annotated[str | None, Field(max_length=512)] = None,
        filename: Annotated[str | None, Field(max_length=255)] = None,
        payload_base64: str | None = None,
        media_type: Annotated[str | None, Field(max_length=128)] = None,
        tag_ids: list[str] | None = None,
        folder_id: Annotated[str | None, Field(min_length=1)] = None,
        folder_name: Annotated[str | None, Field(max_length=255)] = None,
        ingest_seed: bool = True,
        discover_references: bool = True,
        max_depth: Annotated[int, Field(ge=0, le=4)] = 2,
        max_candidates_per_source: Annotated[int, Field(ge=0, le=20)] = 8,
        max_pending_candidates: Annotated[int, Field(ge=0, le=200)] = 40,
    ) -> ResearchImportResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = ResearchImportCreateRequest(
            seed_type=seed_type,
            text=text,
            url=url,
            title=title,
            filename=filename,
            payload_base64=payload_base64,
            media_type=media_type,
            tag_ids=tag_ids or [],
            folder_id=folder_id,
            folder_name=folder_name,
            ingest_seed=ingest_seed,
            discover_references=discover_references,
            max_depth=max_depth,
            max_candidates_per_source=max_candidates_per_source,
            max_pending_candidates=max_pending_candidates,
        )
        return await _run_logged_tool(
            tool_name="start_research_import",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json", exclude={"payload_base64"}),
            operation=services.research.create_import(
                clerk_user_id=clerk_user_id,
                payload=payload,
                origin_surface="mcp",
            ),
        )

    @server.tool(
        name="build_research_library",
        description="Create a foldered research library from a topic, paper title, or public URL and auto-ingest bounded public candidates.",
        annotations=mutating,
    )
    async def build_research_library_tool(
        query: Annotated[str, Field(min_length=1, max_length=4096)],
        seed_type: Literal["topic", "paper", "text", "url", "pdf_url", "arxiv_url", "linkedin_export"] = "topic",
        title: Annotated[str | None, Field(max_length=512)] = None,
        folder_id: Annotated[str | None, Field(min_length=1)] = None,
        folder_name: Annotated[str | None, Field(max_length=255)] = None,
        tag_ids: list[str] | None = None,
        auto_ingest: bool = True,
        discover_references: bool = True,
        max_depth: Annotated[int, Field(ge=0, le=4)] = 2,
        max_sources: Annotated[int, Field(ge=1, le=50)] = 12,
        max_candidates_per_source: Annotated[int, Field(ge=0, le=20)] = 8,
        max_pending_candidates: Annotated[int, Field(ge=0, le=200)] = 50,
    ) -> ResearchLibraryBuildResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = ResearchLibraryBuildRequest(
            seed_type=seed_type,
            query=query,
            title=title,
            folder_id=folder_id,
            folder_name=folder_name,
            tag_ids=tag_ids or [],
            auto_ingest=auto_ingest,
            discover_references=discover_references,
            max_depth=max_depth,
            max_sources=max_sources,
            max_candidates_per_source=max_candidates_per_source,
            max_pending_candidates=max_pending_candidates,
        )
        return await _run_logged_tool(
            tool_name="build_research_library",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=services.research.build_library(
                clerk_user_id=clerk_user_id,
                payload=payload,
                origin_surface="mcp",
            ),
        )

    @server.tool(
        name="list_research_candidates",
        description="List research import candidates by task or status.",
        annotations=read_only,
    )
    async def list_research_candidates_tool(
        task_id: Annotated[str | None, Field(min_length=1)] = None,
        status: ResearchCandidateStatus | None = None,
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> ResearchCandidateListResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="list_research_candidates",
            clerk_user_id=clerk_user_id,
            arguments={"task_id": task_id, "status": status, "page": page, "page_size": page_size},
            operation=services.research.list_candidates(
                clerk_user_id=clerk_user_id,
                task_id=task_id,
                status=status,
                page=page,
                page_size=page_size,
            ),
        )

    @server.tool(
        name="update_research_candidate_status",
        description="Approve, reject, or return lower-level research import candidates to pending review.",
        annotations=mutating,
    )
    async def update_research_candidate_status_tool(
        candidate_ids: list[str],
        status: Literal["approved", "rejected", "pending"],
    ) -> ResearchCandidateStatusUpdateResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="update_research_candidate_status",
            clerk_user_id=clerk_user_id,
            arguments={"candidate_ids": candidate_ids, "status": status},
            operation=services.research.update_candidate_status(
                clerk_user_id=clerk_user_id,
                candidate_ids=candidate_ids,
                status=status,
            ),
        )

    @server.tool(
        name="ingest_research_candidates",
        description="Ingest approved lower-level research candidates through the normal source ingestion path.",
        annotations=mutating,
    )
    async def ingest_research_candidates_tool(
        candidate_ids: list[str] | None = None,
        task_id: Annotated[str | None, Field(min_length=1)] = None,
        tag_ids: list[str] | None = None,
        folder_id: Annotated[str | None, Field(min_length=1)] = None,
    ) -> ResearchCandidateIngestResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = ResearchCandidateIngestRequest(
            candidate_ids=candidate_ids,
            task_id=task_id,
            tag_ids=tag_ids,
            folder_id=folder_id,
        )
        return await _run_logged_tool(
            tool_name="ingest_research_candidates",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=services.research.ingest_approved_candidates(
                clerk_user_id=clerk_user_id,
                payload=payload,
                origin_surface="mcp",
            ),
        )

    @server.tool(
        name="resplit_source",
        description="Recompute one source's semantic split records after explicit confirmation.",
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
                "message": "Ask the user to confirm replacing this source's optional split records, then call resplit_source again with confirm=true.",
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
        name="update_source_tags",
        description="Replace a source's tag assignments and queue vector-store reindexing.",
        annotations=mutating,
    )
    async def update_source_tags_tool(
        source_id: Annotated[str, Field(min_length=1)],
        tag_ids: list[str] | None = None,
    ) -> IngestFinalizeResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="update_source_tags",
            clerk_user_id=clerk_user_id,
            arguments={"source_id": source_id, "tag_ids": tag_ids or []},
            operation=services.sources.update_source_tags(
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                tag_ids=tag_ids or [],
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
        description="Search OpenAI vector-store indexed source files and return source-level matches.",
        annotations=read_only,
    )
    async def search_chunks_tool(
        query: Annotated[str, Field(min_length=1)],
        selected_source_ids: list[str] | None = None,
        source_kinds: list[SourceKind] | None = None,
        tag_ids: list[str] | None = None,
        virtual_paths: list[str] | None = None,
        tag_match_mode: Literal["all", "any"] = "all",
        max_results: Annotated[int, Field(ge=1, le=24)] = 8,
    ) -> SearchResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = SearchRequest(
            query=query,
            selected_source_ids=selected_source_ids or [],
            source_kinds=source_kinds or [],
            tag_ids=tag_ids or [],
            virtual_paths=virtual_paths or [],
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
        name="branch_search",
        description="Layer source-file vector search outward from each layer's hits.",
        annotations=read_only,
    )
    async def branch_search_tool(
        query: Annotated[str, Field(min_length=1)],
        selected_source_ids: list[str] | None = None,
        source_kinds: list[SourceKind] | None = None,
        tag_ids: list[str] | None = None,
        virtual_paths: list[str] | None = None,
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
            virtual_paths=virtual_paths or [],
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

    @server.tool(
        name="qa", description="Answer a question using retrieved source-file vector matches.", annotations=mutating
    )
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
        name="answer_research_library",
        description="Answer a question using sources ingested by a research library build task or explicit source IDs.",
        annotations=mutating,
    )
    async def answer_research_library_tool(
        question: Annotated[str, Field(min_length=1)],
        task_id: Annotated[str | None, Field(min_length=1)] = None,
        source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        max_results: Annotated[int, Field(ge=1, le=16)] = 8,
    ) -> ActionResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        selected_source_ids = list(dict.fromkeys(source_ids or []))
        if task_id:
            linked_scope = await services.research.linked_source_scope_for_task(
                clerk_user_id=clerk_user_id,
                task_id=task_id,
            )
            selected_source_ids = list(
                dict.fromkeys([*selected_source_ids, *linked_scope.ready_source_ids])
            )
            if linked_scope.total_count > 0 and not selected_source_ids:
                raise ValueError("The research library files are still indexing; try again when at least one file is ready.")
            if linked_scope.total_count == 0 and not selected_source_ids:
                raise ValueError("That research task does not have any ingested files to search yet.")
        payload = QaRequest(
            prompt=question,
            selected_source_ids=selected_source_ids,
            tag_ids=tag_ids or [],
            tag_match_mode="all",
            max_results=max_results,
        )
        return await _run_logged_tool(
            tool_name="answer_research_library",
            clerk_user_id=clerk_user_id,
            arguments={"question": question, "task_id": task_id, "source_ids": selected_source_ids, "tag_ids": tag_ids or []},
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
    sources_app = FastMCPApp("Indexed Files")

    @sources_app.tool("refresh_sources")
    async def refresh_sources_tool(
        ctx: Context,
        query: str | None = None,
        tag_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        del ctx
        response = await services.sources.list_sources(
            clerk_user_id=current_mcp_clerk_user_id(),
            query=query,
            tag_ids=tag_ids or [],
            tag_match_mode="all",
            page=1,
            page_size=30,
        )
        payload = response.model_dump(mode="json")
        payload["query"] = query or ""
        payload["tag_ids"] = tag_ids or []
        return payload

    @sources_app.tool("refresh_tags")
    async def refresh_tags_tool(ctx: Context) -> list[dict[str, Any]]:
        del ctx
        tags = await services.sources.list_tags(clerk_user_id=current_mcp_clerk_user_id())
        return [tag.model_dump(mode="json") for tag in tags]

    @sources_app.tool("refresh_tasks")
    async def refresh_tasks_tool(ctx: Context) -> dict[str, Any]:
        del ctx
        response = await services.actions.list_tasks(
            clerk_user_id=current_mcp_clerk_user_id(),
            kind=None,
            limit=12,
        )
        return response.model_dump(mode="json")

    @sources_app.tool("search_sources_for_ui")
    async def search_sources_for_ui_tool(
        query: str,
        ctx: Context,
        tag_ids: list[str] | None = None,
        max_results: Annotated[int, Field(ge=1, le=16)] = 8,
    ) -> dict[str, Any]:
        del ctx
        if not query.strip():
            return {"query": "", "hits": []}
        response = await services.sources.search(
            clerk_user_id=current_mcp_clerk_user_id(),
            request=SearchRequest(query=query, tag_ids=tag_ids or [], tag_match_mode="all", max_results=max_results),
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

    @sources_app.tool("build_research_library_for_ui")
    async def build_research_library_for_ui_tool(
        query: str,
        ctx: Context,
        seed_type: Literal["topic", "paper"] = "topic",
        max_depth: Annotated[int, Field(ge=0, le=4)] = 2,
        max_sources: Annotated[int, Field(ge=1, le=50)] = 12,
    ) -> dict[str, Any]:
        del ctx
        response = await services.research.build_library(
            clerk_user_id=current_mcp_clerk_user_id(),
            payload=ResearchLibraryBuildRequest(
                seed_type=seed_type,
                query=query,
                title=query,
                auto_ingest=True,
                discover_references=True,
                max_depth=max_depth,
                max_sources=max_sources,
                max_candidates_per_source=min(max_sources, 8),
                max_pending_candidates=max(50, max_sources * max(1, max_depth + 1)),
            ),
            origin_surface="mcp_app",
        )
        return response.model_dump(mode="json")

    @sources_app.tool("refresh_research_candidates_for_ui")
    async def refresh_research_candidates_for_ui_tool(
        ctx: Context,
        task_id: str | None = None,
        status: ResearchCandidateStatus | None = None,
    ) -> dict[str, Any]:
        del ctx
        response = await services.research.list_candidates(
            clerk_user_id=current_mcp_clerk_user_id(),
            task_id=task_id,
            status=status,
            page=1,
            page_size=50,
        )
        return response.model_dump(mode="json")

    @sources_app.tool("update_research_candidate_status_for_ui")
    async def update_research_candidate_status_for_ui_tool(
        candidate_ids: list[str],
        status: Literal["approved", "rejected", "pending"],
        ctx: Context,
    ) -> dict[str, Any]:
        del ctx
        response = await services.research.update_candidate_status(
            clerk_user_id=current_mcp_clerk_user_id(),
            candidate_ids=candidate_ids,
            status=status,
        )
        return response.model_dump(mode="json")

    @sources_app.tool("ingest_research_candidates_for_ui")
    async def ingest_research_candidates_for_ui_tool(
        ctx: Context,
        task_id: str | None = None,
        candidate_ids: list[str] | None = None,
        folder_id: str | None = None,
    ) -> dict[str, Any]:
        del ctx
        response = await services.research.ingest_approved_candidates(
            clerk_user_id=current_mcp_clerk_user_id(),
            payload=ResearchCandidateIngestRequest(
                candidate_ids=candidate_ids,
                task_id=task_id,
                folder_id=folder_id,
            ),
            origin_surface="mcp_app",
        )
        return response.model_dump(mode="json")

    @sources_app.ui(
        name="sources",
        title="Indexed Files",
        description="Browse indexed files, build research libraries, and inspect discovered candidate status.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def sources(ctx: Context) -> PrefabApp:
        initial_sources = await refresh_sources_tool(ctx)
        initial_tags = await refresh_tags_tool(ctx)
        initial_tasks = await refresh_tasks_tool(ctx)
        initial_research_candidates = await refresh_research_candidates_for_ui_tool(ctx)
        with Card(css_class="max-w-5xl mx-auto") as view:
            with CardHeader(), Column(gap=1):
                CardTitle("Indexed Files")
                CardDescription("Browse files, filter by tags, search indexed source files, and inspect app tasks.")
            with CardContent(), Column(gap=4):
                with Column(gap=2):
                    with Row(gap=2, align="center"):
                        h3("Files")
                        Button(
                            "Refresh",
                            variant="secondary",
                            on_click=[
                                CallTool(
                                    "refresh_sources",
                                    arguments={"query": STATE.sources.query, "tag_ids": STATE.selectedTagIds},
                                    on_success=SetState("sources", RESULT),
                                    on_error=ShowToast(ERROR, variant="error"),
                                ),
                                CallTool(
                                    "refresh_tasks",
                                    on_success=SetState("tasks", RESULT),
                                    on_error=ShowToast(ERROR, variant="error"),
                                ),
                            ],
                        )
                    with Form(
                        on_submit=CallTool(
                            "refresh_sources",
                            arguments={"query": EVENT.formData.file_query, "tag_ids": STATE.selectedTagIds},
                            on_success=SetState("sources", RESULT),
                            on_error=ShowToast(ERROR, variant="error"),
                        )
                    ):
                        with Row(gap=2, align="center"):
                            Input(
                                name="file_query",
                                input_type="search",
                                placeholder="Query files, filenames, kinds, status",
                                value=STATE.sources.query,
                            )
                            Button("Query", button_type="submit")
                    with Row(gap=2, align="center"):
                        Small("Tags")
                        Button(
                            "All",
                            variant="secondary",
                            size="sm",
                            on_click=[
                                SetState("selectedTagIds", []),
                                CallTool(
                                    "refresh_sources",
                                    arguments={"query": STATE.sources.query, "tag_ids": []},
                                    on_success=SetState("sources", RESULT),
                                    on_error=ShowToast(ERROR, variant="error"),
                                ),
                            ],
                        )
                        with ForEach(STATE.tags) as tag:
                            Button(
                                tag.name,  # ty:ignore[invalid-argument-type]
                                variant="outline",
                                size="sm",
                                on_click=[
                                    SetState("selectedTagIds", [tag.id]),  # ty:ignore[invalid-argument-type]
                                    CallTool(
                                        "refresh_sources",
                                        arguments={"query": STATE.sources.query, "tag_ids": [tag.id]},  # ty:ignore[invalid-argument-type]
                                        on_success=SetState("sources", RESULT),
                                        on_error=ShowToast(ERROR, variant="error"),
                                    ),
                                ],
                            )
                with ForEach(STATE.sources.sources) as source, Card(css_class="border border-slate-200"):
                    with CardContent(), Column(gap=2):
                        with Row(gap=2, align="center"):
                            Text(source.display_title)  # ty:ignore[invalid-argument-type]
                            Badge(source.source_kind, variant="secondary")  # ty:ignore[invalid-argument-type]
                            Badge(source.status, variant="outline")  # ty:ignore[invalid-argument-type]
                        Small(source.original_filename)  # ty:ignore[invalid-argument-type]
                        with Row(gap=2, align="center"):
                            Muted(source.chunk_count)  # ty:ignore[invalid-argument-type]
                            with ForEach(source.tags) as tag:
                                Badge(tag.name, variant="outline")  # ty:ignore[invalid-argument-type]
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
                Separator()
                with Column(gap=2):
                    with Row(gap=2, align="center"):
                        h3("Research Library Builder")
                        Button(
                            "Refresh candidates",
                            variant="secondary",
                            on_click=CallTool(
                                "refresh_research_candidates_for_ui",
                                on_success=SetState("researchCandidates", RESULT),
                                on_error=ShowToast(ERROR, variant="error"),
                            ),
                        )
                    with Form(
                        on_submit=CallTool(
                            "build_research_library_for_ui",
                            arguments={
                                "query": EVENT.formData.research_query,
                                "seed_type": EVENT.formData.research_seed_type,
                                "max_depth": EVENT.formData.research_max_depth,
                                "max_sources": EVENT.formData.research_max_sources,
                            },
                            on_success=[
                                SetState("researchBuild", RESULT),
                                CallTool(
                                    "refresh_research_candidates_for_ui",
                                    arguments={"task_id": RESULT.task.id},  # ty:ignore[invalid-argument-type]
                                    on_success=SetState("researchCandidates", RESULT),
                                    on_error=ShowToast(ERROR, variant="error"),
                                ),
                                ShowToast("Research library build complete", variant="success"),
                            ],
                            on_error=ShowToast(ERROR, variant="error"),
                        )
                    ):
                        with Row(gap=2, align="center"):
                            Input(
                                name="research_query",
                                input_type="search",
                                placeholder="Topic or paper title",
                            )
                            Input(
                                name="research_seed_type",
                                placeholder="topic or paper",
                                value="topic",
                            )
                            Input(
                                name="research_max_sources",
                                input_type="number",
                                value="12",
                            )
                            Input(
                                name="research_max_depth",
                                input_type="number",
                                value="2",
                            )
                            Button("Build library", button_type="submit")
                    with If(STATE.researchBuild):
                        with Card(css_class="border border-slate-200"):
                            with CardContent(), Column(gap=2):
                                with Row(gap=2, align="center"):
                                    Text(STATE.researchBuild.task.title)  # ty:ignore[invalid-argument-type]
                                    Badge(STATE.researchBuild.task.status, variant="outline")  # ty:ignore[invalid-argument-type]
                                    Badge(STATE.researchBuild.target_folder_id, variant="secondary")  # ty:ignore[invalid-argument-type]
                                Small(STATE.researchBuild.duplicate_count)  # ty:ignore[invalid-argument-type]
                    with Row(gap=2, align="center"):
                        h3("Research Candidates")
                        Badge(STATE.researchCandidates.total_count, variant="secondary")  # ty:ignore[invalid-argument-type]
                    with ForEach(STATE.researchCandidates.candidates) as candidate:
                        with Card(css_class="border border-slate-200"):
                            with CardContent(), Column(gap=2):
                                with Row(gap=2, align="center"):
                                    Text(candidate.title)  # ty:ignore[invalid-argument-type]
                                    Badge(candidate.status, variant="outline")  # ty:ignore[invalid-argument-type]
                                    Badge(candidate.source_type, variant="secondary")  # ty:ignore[invalid-argument-type]
                                Muted(candidate.summary)  # ty:ignore[invalid-argument-type]
                                Small(candidate.normalized_url)  # ty:ignore[invalid-argument-type]
                Separator()
                with Column(gap=2):
                    h3("Chunk Query")
                    with Form(
                        on_submit=CallTool(
                            "search_sources_for_ui",
                            arguments={
                                "query": EVENT.formData.chunk_query,
                                "tag_ids": STATE.selectedTagIds,
                                "max_results": 8,
                            },
                            on_success=SetState("searchResults", RESULT),
                            on_error=ShowToast(ERROR, variant="error"),
                        )
                    ):
                        with Row(gap=2, align="center"):
                            Input(
                                name="chunk_query",
                                input_type="search",
                                placeholder="Search indexed files with the selected tag scope",
                            )
                            Button("Search chunks", button_type="submit")
                    with If(STATE.searchResults):
                        with ForEach(STATE.searchResults.hits) as hit:
                            with Card(css_class="border border-slate-200"):
                                with CardContent(), Column(gap=1):
                                    Text(hit.title)  # ty:ignore[invalid-argument-type]
                                    Small(hit.source_title)  # ty:ignore[invalid-argument-type]
                                    Muted(hit.summary)  # ty:ignore[invalid-argument-type]
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
                                with Card(css_class="border border-slate-200"):
                                    with CardContent(), Column(gap=1):
                                        Small(chunk.title)  # ty:ignore[invalid-argument-type]
                                        Muted(chunk.summary)  # ty:ignore[invalid-argument-type]
                Separator()
                with Column(gap=2):
                    with Row(gap=2, align="center"):
                        h3("Recent Tasks")
                        Button(
                            "Refresh tasks",
                            variant="secondary",
                            on_click=CallTool(
                                "refresh_tasks",
                                on_success=SetState("tasks", RESULT),
                                on_error=ShowToast(ERROR, variant="error"),
                            ),
                        )
                    with ForEach(STATE.tasks.tasks) as task:
                        with Row(gap=2, align="center"):
                            Text(task.title)  # ty:ignore[invalid-argument-type]
                            Badge(task.kind, variant="secondary")  # ty:ignore[invalid-argument-type]
                            Badge(task.status, variant="outline")  # ty:ignore[invalid-argument-type]
                Separator()
                Muted("Use the search_chunks and branch_search tools for deeper retrieval from ChatGPT hosts.")
        return PrefabApp(
            title="Indexed Files",
            view=view,
            state={
                "sources": initial_sources,
                "tags": initial_tags,
                "tasks": initial_tasks,
                "selectedTagIds": [],
                "selectedSource": None,
                "searchResults": None,
                "researchBuild": None,
                "researchCandidates": initial_research_candidates,
                "researchIngest": None,
            },
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
    if isinstance(result, ResearchImportResponse):
        return {
            "task_id": result.task.id,
            "candidates": len(result.candidates),
            "duplicate_count": result.duplicate_count,
            "seed_source_id": result.seed_source.id if result.seed_source is not None else None,
            "target_folder_id": result.target_folder_id,
        }
    if isinstance(result, ResearchLibraryBuildResponse):
        return {
            "task_id": result.task.id,
            "candidates": len(result.candidates),
            "ingested": len(result.ingested),
            "duplicate_count": result.duplicate_count,
            "target_folder_id": result.target_folder_id,
        }
    if isinstance(result, ResearchCandidateListResponse):
        return {"returned": len(result.candidates), "total_count": result.total_count}
    if isinstance(result, ResearchCandidateStatusUpdateResponse):
        return {"updated": len(result.candidates)}
    if isinstance(result, ResearchCandidateIngestResponse):
        return {"ingested": len(result.ingested), "candidates": len(result.candidates)}
    if isinstance(result, TagMutationResponse):
        return {"tag_id": result.tag.id if result.tag is not None else None, "tasks": len(result.tasks)}
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

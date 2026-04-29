from __future__ import annotations

from base64 import b64decode, b64encode
import binascii
from collections.abc import Awaitable
from contextlib import asynccontextmanager
import json
import logging
from time import perf_counter
from typing import Annotated, Any, Literal, cast

from fastmcp import FastMCP, FastMCPApp
from fastmcp.server.context import Context
from fastmcp.tools import Tool, ToolResult
from mcp.types import BlobResourceContents, EmbeddedResource, TextContent, TextResourceContents, ToolAnnotations
from prefab_ui import PrefabApp
from prefab_ui.actions import SetState, ShowToast
from prefab_ui.actions.base import Action
from prefab_ui.actions.mcp import SendMessage, UpdateContext
from prefab_ui.components import (
    ERROR,
    EVENT,
    RESULT,
    STATE,
    Badge,
    Button,
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
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
    Text,
)
from pydantic import BaseModel, Field

from backend.app.bootstrap import AppServices
from backend.app.core.config import AppSettings
from backend.app.mcp.agent_facade import (
    McpFacadeInput,
    run_answer_from_library_agent,
    run_library_search_agent,
    run_manage_library_agent,
    run_research_library_agent,
)
from backend.app.mcp.auth import VectorstoreTokenVerifier, current_mcp_clerk_user_id
from backend.app.schemas import (
    ActionResponse,
    BranchSearchRequest,
    BranchSearchResponse,
    ChunkHit,
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
    ReportDocument,
    ReportMarkdownSaveRequest,
    ReportMarkdownSaveResponse,
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
from backend.app.services.reports import save_report_markdown_source

Badge: Any = Badge
Button: Any = Button
SendMessage: Any = SendMessage
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
Table: Any = Table
TableBody: Any = TableBody
TableCell: Any = TableCell
TableHead: Any = TableHead
TableHeader: Any = TableHeader
TableRow: Any = TableRow
Text: Any = Text
UpdateContext: Any = UpdateContext

logger = logging.getLogger(__name__)


class VisibleToolCall(Action):
    action: Literal["toolCall"] = "toolCall"
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)

TEXT_RESOURCE_MEDIA_TYPES = {
    "application/javascript",
    "application/json",
    "application/ld+json",
    "application/sql",
    "application/toml",
    "application/x-ndjson",
    "application/x-yaml",
    "application/xml",
    "application/yaml",
    "image/svg+xml",
}
DEFAULT_RETRIEVE_MAX_BYTES_PER_FILE = 2_000_000
DEFAULT_RETRIEVE_MAX_EXTRACTED_CHARS_PER_FILE = 120_000


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
            "You are the MCP adapter for Vector Library Search. "
            "Use open_file_search_ui to open the interactive file search UI. "
            "Use library_search, research_library, answer_from_library, and manage_library for all non-UI work."
        ),
        auth=auth,
        lifespan=server_lifespan,
    )
    _register_agent_facade_tools(server=server, services=services)
    _register_sources_app(server=server, services=services, settings=settings)
    return server


def _register_agent_facade_tools(*, server: FastMCP, services: AppServices) -> None:
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    mutating = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)

    @server.tool(
        name="library_search",
        description=(
            "Subagent facade for library search, browsing, source inspection, vector search, branch search, and file "
            "content retrieval. Set include_file_contents=true when selected source payloads are needed."
        ),
        annotations=read_only,
    )
    async def library_search_tool(
        instruction: Annotated[str, Field(min_length=1, max_length=16_000)],
        query: Annotated[str | None, Field(max_length=4096)] = None,
        source_ids: list[str] | None = None,
        folder_id: Annotated[str | None, Field(min_length=1)] = None,
        tag_ids: list[str] | None = None,
        max_results: Annotated[int, Field(ge=1, le=24)] = 8,
        include_file_contents: bool = False,
    ) -> ToolResult:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = McpFacadeInput(
            instruction=instruction,
            query=query,
            source_ids=source_ids or [],
            selected_source_ids=source_ids or [],
            folder_id=folder_id,
            tag_ids=tag_ids or [],
            max_results=max_results,
            include_file_contents=include_file_contents,
        )
        return await _run_logged_tool(
            tool_name="library_search",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=run_library_search_agent(services=services, clerk_user_id=clerk_user_id, payload=payload),
        )

    @server.tool(
        name="research_library",
        description=(
            "Subagent facade for research imports, research library builds, candidate review, candidate ingestion, "
            "and answers from research-library task sources."
        ),
        annotations=mutating,
    )
    async def research_library_tool(
        instruction: Annotated[str, Field(min_length=1, max_length=16_000)],
        query: Annotated[str | None, Field(max_length=4096)] = None,
        task_id: Annotated[str | None, Field(min_length=1)] = None,
        candidate_ids: list[str] | None = None,
        source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        folder_id: Annotated[str | None, Field(min_length=1)] = None,
        folder_name: Annotated[str | None, Field(max_length=255)] = None,
        max_sources: Annotated[int, Field(ge=1, le=50)] = 12,
        confirm: bool = False,
    ) -> ToolResult:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = McpFacadeInput(
            instruction=instruction,
            query=query,
            prompt=query,
            task_id=task_id,
            candidate_ids=candidate_ids or [],
            source_ids=source_ids or [],
            selected_source_ids=source_ids or [],
            tag_ids=tag_ids or [],
            folder_id=folder_id,
            folder_name=folder_name,
            max_sources=max_sources,
            confirm=confirm,
        )
        return await _run_logged_tool(
            tool_name="research_library",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=run_research_library_agent(services=services, clerk_user_id=clerk_user_id, payload=payload),
        )

    @server.tool(
        name="answer_from_library",
        description=(
            "Subagent facade for grounded QA, freeform writing, saving Markdown reports, and generating image/audio "
            "assets from library context."
        ),
        annotations=mutating,
    )
    async def answer_from_library_tool(
        instruction: Annotated[str, Field(min_length=1, max_length=16_000)],
        prompt: Annotated[str | None, Field(max_length=16_000)] = None,
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        mode: Literal["grounded", "creative"] = "grounded",
        max_results: Annotated[int, Field(ge=1, le=16)] = 8,
        document: dict[str, Any] | None = None,
    ) -> ToolResult:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = McpFacadeInput(
            instruction=instruction,
            prompt=prompt,
            query=prompt,
            selected_source_ids=selected_source_ids or [],
            source_ids=selected_source_ids or [],
            tag_ids=tag_ids or [],
            mode=mode,
            max_results=max_results,
            document=document,
        )
        return await _run_logged_tool(
            tool_name="answer_from_library",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=run_answer_from_library_agent(services=services, clerk_user_id=clerk_user_id, payload=payload),
        )

    @server.tool(
        name="manage_library",
        description=(
            "Subagent facade for guarded library mutations: ingest files/text, folders, tags, split/reindex, deletion, "
            "and task inspection. Destructive operations require confirm=true."
        ),
        annotations=destructive,
    )
    async def manage_library_tool(
        instruction: Annotated[str, Field(min_length=1, max_length=16_000)],
        source_ids: list[str] | None = None,
        folder_id: Annotated[str | None, Field(min_length=1)] = None,
        folder_name: Annotated[str | None, Field(max_length=255)] = None,
        tag_ids: list[str] | None = None,
        task_id: Annotated[str | None, Field(min_length=1)] = None,
        filename: Annotated[str | None, Field(max_length=255)] = None,
        text: str | None = None,
        payload_base64: str | None = None,
        media_type: Annotated[str | None, Field(max_length=128)] = None,
        confirm: bool = False,
    ) -> ToolResult:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = McpFacadeInput(
            instruction=instruction,
            source_ids=source_ids or [],
            selected_source_ids=source_ids or [],
            folder_id=folder_id,
            folder_name=folder_name,
            tag_ids=tag_ids or [],
            task_id=task_id,
            filename=filename,
            text=text,
            payload_base64=payload_base64,
            media_type=media_type,
            confirm=confirm,
        )
        return await _run_logged_tool(
            tool_name="manage_library",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json", exclude={"payload_base64"}),
            operation=run_manage_library_agent(services=services, clerk_user_id=clerk_user_id, payload=payload),
        )


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
        description=(
            "Load source metadata and optional semantic split records. The response includes a temporary download_url "
            "and a content_retrieval_tool hint; call retrieve_files, download_source, get_file, or read_file_bytes "
            "with the source_id when the original file bytes are needed."
        ),
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
        name="retrieve_files",
        description=(
            "Retrieve original file payloads by source ID through MCP embedded resources. "
            "Text-like files are returned as text resources; binary files such as PDFs are returned as base64 blobs "
            "with extracted semantic text when available. Use this instead of fetching app file URLs."
        ),
        annotations=read_only,
    )
    async def retrieve_files_tool(
        source_ids: Annotated[list[str], Field(min_length=1, max_length=5)],
        include_extracted_text: bool = True,
        max_bytes_per_file: Annotated[int, Field(ge=1, le=5_000_000)] = DEFAULT_RETRIEVE_MAX_BYTES_PER_FILE,
        max_extracted_chars_per_file: Annotated[int, Field(ge=1, le=300_000)] = (
            DEFAULT_RETRIEVE_MAX_EXTRACTED_CHARS_PER_FILE
        ),
    ) -> ToolResult:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="retrieve_files",
            clerk_user_id=clerk_user_id,
            arguments={
                "source_ids": source_ids,
                "include_extracted_text": include_extracted_text,
                "max_bytes_per_file": max_bytes_per_file,
                "max_extracted_chars_per_file": max_extracted_chars_per_file,
            },
            operation=_retrieve_files_result(
                services=services,
                clerk_user_id=clerk_user_id,
                source_ids=source_ids,
                include_extracted_text=include_extracted_text,
                max_bytes_per_file=max_bytes_per_file,
                max_extracted_chars_per_file=max_extracted_chars_per_file,
            ),
        )

    @server.tool(
        name="download_source",
        description=(
            "Retrieve one stored source file by source_id through MCP embedded resources. "
            "Use this when get_source_detail exposes a storage_key, OpenAI file ID, or source_id and the original "
            "PDF/file payload is needed."
        ),
        annotations=read_only,
    )
    async def download_source_tool(
        source_id: Annotated[str, Field(min_length=1)],
        include_extracted_text: bool = True,
        max_bytes_per_file: Annotated[int, Field(ge=1, le=5_000_000)] = DEFAULT_RETRIEVE_MAX_BYTES_PER_FILE,
        max_extracted_chars_per_file: Annotated[int, Field(ge=1, le=300_000)] = (
            DEFAULT_RETRIEVE_MAX_EXTRACTED_CHARS_PER_FILE
        ),
    ) -> ToolResult:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="download_source",
            clerk_user_id=clerk_user_id,
            arguments={
                "source_id": source_id,
                "include_extracted_text": include_extracted_text,
                "max_bytes_per_file": max_bytes_per_file,
                "max_extracted_chars_per_file": max_extracted_chars_per_file,
            },
            operation=_retrieve_files_result(
                services=services,
                clerk_user_id=clerk_user_id,
                source_ids=[source_id],
                include_extracted_text=include_extracted_text,
                max_bytes_per_file=max_bytes_per_file,
                max_extracted_chars_per_file=max_extracted_chars_per_file,
            ),
        )

    @server.tool(
        name="get_file",
        description=(
            "Read one original library file by source_id and return its contents as MCP embedded resources. "
            "Binary files such as PDFs are returned as base64 blobs."
        ),
        annotations=read_only,
    )
    async def get_file_tool(
        source_id: Annotated[str, Field(min_length=1)],
        include_extracted_text: bool = True,
        max_bytes_per_file: Annotated[int, Field(ge=1, le=5_000_000)] = DEFAULT_RETRIEVE_MAX_BYTES_PER_FILE,
        max_extracted_chars_per_file: Annotated[int, Field(ge=1, le=300_000)] = (
            DEFAULT_RETRIEVE_MAX_EXTRACTED_CHARS_PER_FILE
        ),
    ) -> ToolResult:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="get_file",
            clerk_user_id=clerk_user_id,
            arguments={
                "source_id": source_id,
                "include_extracted_text": include_extracted_text,
                "max_bytes_per_file": max_bytes_per_file,
                "max_extracted_chars_per_file": max_extracted_chars_per_file,
            },
            operation=_retrieve_files_result(
                services=services,
                clerk_user_id=clerk_user_id,
                source_ids=[source_id],
                include_extracted_text=include_extracted_text,
                max_bytes_per_file=max_bytes_per_file,
                max_extracted_chars_per_file=max_extracted_chars_per_file,
            ),
        )

    @server.tool(
        name="read_file_bytes",
        description=(
            "Return the original bytes for one stored source file by source_id through MCP embedded resources. "
            "Use this for PDFs or other non-text files when the model needs the stored file content."
        ),
        annotations=read_only,
    )
    async def read_file_bytes_tool(
        source_id: Annotated[str, Field(min_length=1)],
        include_extracted_text: bool = True,
        max_bytes_per_file: Annotated[int, Field(ge=1, le=5_000_000)] = DEFAULT_RETRIEVE_MAX_BYTES_PER_FILE,
        max_extracted_chars_per_file: Annotated[int, Field(ge=1, le=300_000)] = (
            DEFAULT_RETRIEVE_MAX_EXTRACTED_CHARS_PER_FILE
        ),
    ) -> ToolResult:
        clerk_user_id = current_mcp_clerk_user_id()
        return await _run_logged_tool(
            tool_name="read_file_bytes",
            clerk_user_id=clerk_user_id,
            arguments={
                "source_id": source_id,
                "include_extracted_text": include_extracted_text,
                "max_bytes_per_file": max_bytes_per_file,
                "max_extracted_chars_per_file": max_extracted_chars_per_file,
            },
            operation=_retrieve_files_result(
                services=services,
                clerk_user_id=clerk_user_id,
                source_ids=[source_id],
                include_extracted_text=include_extracted_text,
                max_bytes_per_file=max_bytes_per_file,
                max_extracted_chars_per_file=max_extracted_chars_per_file,
            ),
        )

    @server.tool(
        name="create_download_link",
        description=(
            "Create a temporary direct download URL for one stored source file by source_id. "
            "Prefer retrieve_files/download_source for model-readable MCP resources; use this when a URL is needed."
        ),
        annotations=read_only,
    )
    async def create_download_link_tool(source_id: Annotated[str, Field(min_length=1)]) -> dict[str, object]:
        clerk_user_id = current_mcp_clerk_user_id()
        detail = await _run_logged_tool(
            tool_name="create_download_link",
            clerk_user_id=clerk_user_id,
            arguments={"source_id": source_id},
            operation=services.sources.get_source(clerk_user_id=clerk_user_id, source_id=source_id),
        )
        return {
            "source_id": detail.id,
            "title": detail.display_title,
            "original_filename": detail.original_filename,
            "media_type": detail.media_type,
            "byte_size": detail.byte_size,
            "download_url": detail.download_url,
            "expires_in_seconds": detail.download_url_expires_in_seconds,
            "retrieval_tool": detail.content_retrieval_tool,
            "retrieval_arguments": {"source_ids": [detail.id]},
        }

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
            selected_source_ids = list(dict.fromkeys([*selected_source_ids, *linked_scope.ready_source_ids]))
            if linked_scope.total_count > 0 and not selected_source_ids:
                raise ValueError(
                    "The research library files are still indexing; try again when at least one file is ready."
                )
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
            arguments={
                "question": question,
                "task_id": task_id,
                "source_ids": selected_source_ids,
                "tag_ids": tag_ids or [],
            },
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
        name="save_report_markdown",
        description="Render a structured report to Markdown and save it as a searchable library source.",
        annotations=mutating,
    )
    async def save_report_markdown_tool(
        document: ReportDocument,
        filename: Annotated[str | None, Field(min_length=1, max_length=255)] = None,
        folder_id: Annotated[str | None, Field(min_length=1)] = None,
        tag_ids: list[str] | None = None,
        user_guidance: Annotated[str | None, Field(max_length=2048)] = None,
    ) -> ReportMarkdownSaveResponse:
        clerk_user_id = current_mcp_clerk_user_id()
        payload = ReportMarkdownSaveRequest(
            document=document,
            filename=filename,
            folder_id=folder_id,
            tag_ids=tag_ids or [],
            user_guidance=user_guidance,
        )
        return await _run_logged_tool(
            tool_name="save_report_markdown",
            clerk_user_id=clerk_user_id,
            arguments=payload.model_dump(mode="json"),
            operation=save_report_markdown_source(
                sources=services.sources,
                clerk_user_id=clerk_user_id,
                request=payload,
                origin_surface="mcp",
            ),
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


async def _retrieve_files_result(
    *,
    services: AppServices,
    clerk_user_id: str,
    source_ids: list[str],
    include_extracted_text: bool,
    max_bytes_per_file: int,
    max_extracted_chars_per_file: int,
) -> ToolResult:
    content_blocks: list[TextContent | EmbeddedResource] = []
    files: list[dict[str, object]] = []
    for source_id in list(dict.fromkeys(source_ids)):
        detail, payload = await services.sources.read_source_bytes(
            clerk_user_id=clerk_user_id,
            source_id=source_id,
        )
        returned_payload = payload[:max_bytes_per_file]
        original_truncated = len(payload) > len(returned_payload)
        resource_uri = f"vectorstore://sources/{detail.id}/original"
        content_kind: Literal["text", "blob"]
        if _is_text_resource(detail=detail):
            content_kind = "text"
            content_blocks.append(
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(
                        uri=cast(Any, resource_uri),
                        mimeType=detail.media_type,
                        text=returned_payload.decode("utf-8", errors="replace"),
                    ),
                )
            )
        else:
            content_kind = "blob"
            content_blocks.append(
                EmbeddedResource(
                    type="resource",
                    resource=BlobResourceContents(
                        uri=cast(Any, resource_uri),
                        mimeType=detail.media_type,
                        blob=b64encode(returned_payload).decode("ascii"),
                    ),
                )
            )

        extracted_text_included = False
        extracted_text_truncated = False
        if include_extracted_text and detail.chunks:
            extracted_text = _source_extracted_text(detail)
            if extracted_text:
                clipped_extracted_text = extracted_text[:max_extracted_chars_per_file]
                extracted_text_included = True
                extracted_text_truncated = len(extracted_text) > len(clipped_extracted_text)
                content_blocks.append(
                    EmbeddedResource(
                        type="resource",
                        resource=TextResourceContents(
                            uri=cast(Any, f"vectorstore://sources/{detail.id}/extracted-text"),
                            mimeType="text/markdown",
                            text=clipped_extracted_text,
                        ),
                    )
                )

        files.append(
            {
                "source_id": detail.id,
                "title": detail.display_title,
                "original_filename": detail.original_filename,
                "media_type": detail.media_type,
                "source_kind": detail.source_kind,
                "byte_size": detail.byte_size,
                "returned_bytes": len(returned_payload),
                "original_truncated": original_truncated,
                "content_kind": content_kind,
                "resource_uri": resource_uri,
                "extracted_text_included": extracted_text_included,
                "extracted_text_truncated": extracted_text_truncated,
                "chunk_count": len(detail.chunks),
            }
        )

    summary_text = (
        "Retrieved "
        f"{len(files)} file{'s' if len(files) != 1 else ''} through MCP embedded resources. "
        "Use the returned resource content blocks for the file payloads; do not fetch app storage URLs."
    )
    content_blocks.insert(0, TextContent(type="text", text=summary_text))
    return ToolResult(content=content_blocks, structured_content={"files": files})


def _is_text_resource(*, detail: LibrarySourceDetail) -> bool:
    media_type = detail.media_type.split(";", 1)[0].strip().casefold()
    return media_type.startswith("text/") or media_type in TEXT_RESOURCE_MEDIA_TYPES


def _source_extracted_text(detail: LibrarySourceDetail) -> str:
    parts: list[str] = []
    for chunk in sorted(detail.chunks, key=lambda item: item.sequence):
        title = chunk.title.strip()
        text = chunk.text.strip()
        if not title and not text:
            continue
        if title:
            parts.append(f"## {title}\n\n{text}".strip())
        else:
            parts.append(text)
    return "\n\n".join(parts)


def _register_sources_app(*, server: FastMCP, services: AppServices, settings: AppSettings) -> None:
    del settings

    sources_app = FastMCPApp("Indexed Files")

    async def run_file_search_for_ui(
        ctx: Context,
        query: str,
        selected_files: list[dict[str, Any]] | None = None,
        dismissed_source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_query = query.strip()
        kept_files = _dedupe_selected_files(selected_files or [])
        kept_ids = _selected_file_ids(kept_files)
        dismissed_ids = {str(source_id) for source_id in dismissed_source_ids or [] if str(source_id).strip()}
        if not normalized_query:
            return {
                "query": "",
                "iteration": 0,
                "results": kept_files,
                "added": [],
                "referenceContext": _file_search_reference_context(kept_files),
                "message": "Enter a search query.",
                "selected_files": kept_files,
                "selected_source_ids": kept_ids,
                "dismissed_source_ids": sorted(dismissed_ids),
            }
        await ctx.report_progress(1, 3, "Searching indexed files")
        response = await services.sources.search(
            clerk_user_id=current_mcp_clerk_user_id(),
            request=SearchRequest(query=normalized_query, max_results=24),
            origin_surface="mcp_app",
        )
        await ctx.report_progress(2, 3, "Preparing file matches")
        added = _file_search_items_from_hits(
            response.hits,
            exclude_source_ids=dismissed_ids | set(kept_ids),
            limit=5,
        )
        kept_files = _dedupe_selected_files([*kept_files, *added])
        kept_ids = _selected_file_ids(kept_files)
        await ctx.report_progress(3, 3, "File search ready")
        return {
            "query": normalized_query,
            "iteration": 1,
            "results": kept_files,
            "added": added,
            "referenceContext": _file_search_reference_context(kept_files),
            "message": _file_search_status_message(total=len(kept_files), added=len(added)),
            "selected_files": kept_files,
            "selected_source_ids": kept_ids,
            "dismissed_source_ids": sorted(dismissed_ids),
        }

    async def continue_file_search_for_ui(
        ctx: Context,
        query: str,
        current_results: list[dict[str, Any]] | None = None,
        dismissed_source_ids: list[str] | None = None,
        selected_files: list[dict[str, Any]] | None = None,
        iteration: int = 1,
    ) -> dict[str, Any]:
        normalized_query = query.strip()
        current_items = list(current_results or [])
        dismissed_ids = {str(source_id) for source_id in dismissed_source_ids or [] if str(source_id).strip()}
        kept_files = _dedupe_selected_files(selected_files or current_items)
        kept_ids = _selected_file_ids(kept_files)
        seen_ids = {
            str(item.get("source_id"))
            for item in kept_files
            if str(item.get("source_id") or "").strip()
        } | dismissed_ids
        if not normalized_query:
            return {
                "query": "",
                "iteration": iteration,
                "results": kept_files,
                "added": [],
                "referenceContext": _file_search_reference_context(kept_files),
                "message": "Enter a search query.",
                "selected_files": kept_files,
                "selected_source_ids": kept_ids,
                "dismissed_source_ids": sorted(dismissed_ids),
            }

        await ctx.report_progress(1, 4, "Building search from kept files")
        reference_context = _file_search_reference_context(kept_files or current_items)
        branch_query = "\n\n".join([normalized_query, reference_context]).strip()
        await ctx.report_progress(2, 4, "Searching for more files")
        response = await services.sources.search(
            clerk_user_id=current_mcp_clerk_user_id(),
            request=SearchRequest(query=branch_query, max_results=24),
            origin_surface="mcp_app",
        )
        added = _file_search_items_from_hits(response.hits, exclude_source_ids=seen_ids, limit=5)
        if not added:
            await ctx.report_progress(3, 4, "Retrying without kept-file context")
            response = await services.sources.search(
                clerk_user_id=current_mcp_clerk_user_id(),
                request=SearchRequest(query=normalized_query, max_results=24),
                origin_surface="mcp_app",
            )
            added = _file_search_items_from_hits(response.hits, exclude_source_ids=seen_ids, limit=5)
        kept_files = _dedupe_selected_files([*kept_files, *added])
        kept_ids = _selected_file_ids(kept_files)
        await ctx.report_progress(4, 4, "File search ready")
        return {
            "query": normalized_query,
            "iteration": iteration + 1,
            "results": kept_files,
            "added": added,
            "referenceContext": _file_search_reference_context(kept_files),
            "message": _file_search_status_message(total=len(kept_files), added=len(added)),
            "selected_files": kept_files,
            "selected_source_ids": kept_ids,
            "dismissed_source_ids": sorted(dismissed_ids),
        }

    def dismiss_file_for_ui(
        source_id: str,
        current_results: list[dict[str, Any]] | None = None,
        dismissed_source_ids: list[str] | None = None,
        selected_files: list[dict[str, Any]] | None = None,
        query: str | None = None,
        iteration: int = 0,
    ) -> dict[str, Any]:
        normalized_source_id = source_id.strip()
        dismissed_ids = [str(item) for item in dismissed_source_ids or [] if str(item).strip()]
        selected = _dedupe_selected_files(selected_files or [])
        if normalized_source_id and normalized_source_id not in dismissed_ids:
            dismissed_ids.append(normalized_source_id)
        if normalized_source_id:
            selected = [item for item in selected if item["source_id"] != normalized_source_id]
        selected_ids = _selected_file_ids(selected)
        results = [
            item
            for item in current_results or []
            if str(item.get("source_id") or "").strip() != normalized_source_id
        ]
        results = _dedupe_selected_files(results)
        search = {
            "query": query or "",
            "iteration": iteration,
            "results": results,
            "added": [],
            "referenceContext": _file_search_reference_context(results),
            "message": f"Showing {len(results)} files.",
        }
        return {
            "search": search,
            "dismissed_source_ids": dismissed_ids,
            "selected_files": selected,
            "selected_source_ids": selected_ids,
            "selected_count": len(selected_ids),
        }

    async def confirm_file_selection_for_ui(
        selected_source_ids: list[str] | None = None,
        selected_files: list[dict[str, Any]] | None = None,
        current_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        selected = _dedupe_selected_files(selected_files or [])
        wanted_ids = {str(source_id) for source_id in selected_source_ids or [] if str(source_id).strip()}
        if not selected and wanted_ids:
            selected = [
                _file_search_selected_item(item)
                for item in current_results or []
                if str(item.get("source_id") or "").strip() in wanted_ids
            ]
        if not selected:
            return {
                "selected_files": [],
                "selected_source_ids": [],
                "model_context": "No files are currently selected in the file search widget.",
                "follow_up_prompt": "No files are selected yet.",
                "message": "No files selected.",
            }
        selected_source_ids = [item["source_id"] for item in selected]
        selected_lines = [
            f"- {item['title']} (`{item['source_id']}`): {item.get('preview') or item['summary']}"
            for item in selected
        ]
        retrieval_payload = {
            "instruction": "Retrieve the full contents for the files the user selected in the file search widget.",
            "source_ids": selected_source_ids,
            "include_file_contents": True,
            "max_results": max(len(selected_source_ids), 1),
        }
        model_context = "\n".join(
            [
                "The user confirmed these selected library files in the file search widget:",
                *selected_lines,
                "",
                f"Selected source IDs: {', '.join(selected_source_ids)}",
                "Immediately call the MCP tool `library_search` with this JSON payload to retrieve the selected file contents:",
                json.dumps(retrieval_payload, sort_keys=True),
            ]
        )
        follow_up_prompt = (
            "Please retrieve the full contents for my selected library files now. "
            f"Call `library_search` with: {json.dumps(retrieval_payload, sort_keys=True)}"
        )
        return {
            "selected_files": selected,
            "selected_source_ids": selected_source_ids,
            "retrieval_tool": "library_search",
            "retrieval_payload": retrieval_payload,
            "model_context": model_context,
            "follow_up_prompt": follow_up_prompt,
            "message": f"Confirmed {len(selected)} selected file{'s' if len(selected) != 1 else ''}.",
        }

    def visible_tool_call(
        *,
        arguments: dict[str, Any],
        on_success: Any,
        on_error: Any | None = None,
    ) -> VisibleToolCall:
        action = VisibleToolCall(tool="open_file_search_ui", arguments=arguments)
        action.on_success = on_success
        if on_error is not None:
            action.on_error = on_error
        return action

    def build_file_search_app() -> PrefabApp:
        with Column(gap=4, css_class="p-4 max-w-4xl mx-auto") as view:
            with Column(gap=1):
                Text("File Search", bold=True, css_class="text-lg")
                Muted("Semantic search over the indexed library.")
            with Form(
                on_submit=visible_tool_call(
                    arguments={
                        "action": "search",
                        "query": EVENT.formData.file_query,
                        "previous_query": STATE.search.query,
                        "current_results": STATE.search.results,
                        "dismissed_source_ids": STATE.dismissedSourceIds,
                        "selected_files": STATE.selectedFiles,
                        "iteration": STATE.search.iteration,
                    },
                    on_success=[
                        SetState("search", RESULT),
                        SetState("selectedFiles", RESULT.selected_files),
                        SetState("selectedSourceIds", RESULT.selected_source_ids),
                        SetState("dismissedSourceIds", RESULT.dismissed_source_ids),
                        SetState("selection", False),
                    ],
                    on_error=ShowToast(f"{ERROR}", variant="error"),
                )
            ):
                with Column(gap=2):
                    Input(
                        name="file_query",
                        value=STATE.search.query,
                        inputType="search",
                        placeholder="Search files by meaning, topic, method, claim, or source",
                    )
                    Button("Search", size="sm", buttonType="submit")
            with If(STATE.search.results):
                with Row(gap=2, align="center", justify="between"):
                    Badge(STATE.search.message, variant="secondary")
                with Table():
                    with TableHeader():
                        with TableRow():
                            TableHead("Score", css_class="w-20")
                            TableHead("File")
                            TableHead("Match")
                            TableHead("", css_class="w-12")
                    with TableBody():
                        with ForEach(STATE.search.results) as item:
                            with TableRow():
                                with TableCell():
                                    Badge(item.relevance_score, variant="outline")
                                with TableCell():
                                    Text(item.title, bold=True, css_class="max-w-72 truncate")
                                with TableCell():
                                    Muted(item.preview, css_class="max-w-xl truncate")
                                with TableCell():
                                    Button(
                                        "X",
                                        variant="destructive",
                                        size="sm",
                                        onClick=visible_tool_call(
                                            arguments={
                                                "action": "dismiss",
                                                "source_id": item.source_id,
                                                "query": STATE.search.query,
                                                "current_results": STATE.search.results,
                                                "dismissed_source_ids": STATE.dismissedSourceIds,
                                                "selected_files": STATE.selectedFiles,
                                                "iteration": STATE.search.iteration,
                                            },
                                            on_success=[
                                                SetState("search", RESULT.search),
                                                SetState("dismissedSourceIds", RESULT.dismissed_source_ids),
                                                SetState("selectedFiles", RESULT.selected_files),
                                                SetState("selectedSourceIds", RESULT.selected_source_ids),
                                            ],
                                            on_error=ShowToast(f"{ERROR}", variant="error"),
                                        ),
                                    )
            with If("{{ selectedSourceIds.length > 0 }}"):
                Separator()
                with Row(gap=2, align="center"):
                    Text("{{ selectedSourceIds.length }} kept", bold=True)
                    Button(
                        "Use kept files",
                        size="sm",
                        onClick=visible_tool_call(
                            arguments={
                                "action": "confirm",
                                "selected_source_ids": STATE.selectedSourceIds,
                                "selected_files": STATE.selectedFiles,
                                "current_results": STATE.search.results,
                            },
                            on_success=[
                                SetState("selection", RESULT),
                                UpdateContext(content=RESULT.model_context),
                                SendMessage(f"{RESULT.follow_up_prompt}"),
                                ShowToast(RESULT.message, variant="success"),
                            ],
                            on_error=ShowToast(f"{ERROR}", variant="error"),
                        ),
                    )
            with If(STATE.selection):
                Small(STATE.selection.message)
        return PrefabApp(
            title="File Search",
            view=view,
            state={
                "search": {
                    "query": "",
                    "iteration": 0,
                    "results": [],
                    "added": [],
                    "referenceContext": "",
                    "message": "",
                },
                "selectedFiles": [],
                "selectedSourceIds": [],
                "dismissedSourceIds": [],
                "selection": None,
            },
        )

    @sources_app.ui(
        name="open_file_search_ui",
        title="Open File Search UI",
        description="Search the indexed file library five semantic matches at a time.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False),
    )
    async def open_file_search_ui(
        ctx: Context,
        action: Literal["render", "search", "continue", "dismiss", "confirm"] = "render",
        query: str | None = None,
        previous_query: str | None = None,
        source_id: str | None = None,
        current_results: list[dict[str, Any]] | None = None,
        dismissed_source_ids: list[str] | None = None,
        selected_source_ids: list[str] | None = None,
        selected_files: list[dict[str, Any]] | None = None,
        iteration: int = 1,
    ) -> PrefabApp | dict[str, Any]:
        if action == "search":
            if current_results and (query or "").strip() == (previous_query or "").strip():
                return await continue_file_search_for_ui(
                    ctx,
                    query or "",
                    current_results=current_results,
                    dismissed_source_ids=dismissed_source_ids,
                    selected_files=selected_files,
                    iteration=iteration,
                )
            return await run_file_search_for_ui(
                ctx,
                query or "",
                selected_files=selected_files,
                dismissed_source_ids=dismissed_source_ids,
            )
        if action == "continue":
            return await continue_file_search_for_ui(
                ctx,
                query or "",
                current_results=current_results,
                dismissed_source_ids=dismissed_source_ids,
                selected_files=selected_files,
                iteration=iteration,
            )
        if action == "dismiss":
            return dismiss_file_for_ui(
                source_id or "",
                current_results=current_results,
                dismissed_source_ids=dismissed_source_ids,
                selected_files=selected_files,
                query=query,
                iteration=iteration,
            )
        if action == "confirm":
            return await confirm_file_selection_for_ui(
                selected_source_ids=selected_source_ids,
                selected_files=selected_files,
                current_results=current_results,
            )
        return build_file_search_app()

    # FastMCP's @app.ui defaults to model-only. This UI reuses its visible
    # entry tool for widget state actions, so ChatGPT must also see app visibility.
    for component in cast(Any, sources_app)._local._components.values():
        if isinstance(component, Tool) and component.name == "open_file_search_ui":
            meta = dict(component.meta or {})
            ui_meta = dict(meta.get("ui") or {})
            ui_meta["visibility"] = ["model", "app"]
            meta["ui"] = ui_meta
            meta["openai/widgetAccessible"] = True
            component.meta = meta
            break

    server.add_provider(sources_app)


def _file_search_items_from_hits(
    hits: list[ChunkHit],
    *,
    exclude_source_ids: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for hit in hits:
        if hit.source_file_id in exclude_source_ids:
            continue
        score = round(float(hit.score), 3)
        if score >= 0.8:
            relevance_label = "Strong"
        elif score >= 0.6:
            relevance_label = "Good"
        else:
            relevance_label = "Possible"
        items.append(
            {
                "source_id": hit.source_file_id,
                "chunk_id": hit.chunk_id,
                "title": hit.source_title,
                "original_filename": hit.original_filename,
                "summary": hit.summary or hit.text[:220],
                "match_title": hit.title,
                "preview": _file_search_preview(hit),
                "snippet": hit.text[:900],
                "relevance_score": score,
                "relevance_label": relevance_label,
                "tags": hit.tags,
            }
        )
        exclude_source_ids.add(hit.source_file_id)
        if len(items) >= limit:
            break
    return items


def _file_search_selected_item(item: dict[str, Any]) -> dict[str, Any]:
    source_id = str(item.get("source_id") or "").strip()
    title = str(item.get("title") or source_id).strip()
    preview = str(item.get("preview") or item.get("snippet") or item.get("summary") or "").strip()
    summary = str(item.get("summary") or preview).strip()
    return {
        "source_id": source_id,
        "title": title,
        "original_filename": str(item.get("original_filename") or "").strip(),
        "summary": summary,
        "preview": preview,
        "relevance_score": item.get("relevance_score"),
    }


def _dedupe_selected_files(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected_by_id: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    for item in items:
        source_id = str(item.get("source_id") or "").strip()
        if not source_id:
            continue
        selected_item = _file_search_selected_item(item)
        if source_id in seen_ids:
            existing = selected_by_id[source_id]
            if _file_search_score(selected_item) > _file_search_score(existing):
                selected_by_id[source_id] = selected_item
            continue
        selected_by_id[source_id] = selected_item
        seen_ids.add(source_id)
    return sorted(
        selected_by_id.values(),
        key=lambda item: (_file_search_score(item), str(item.get("title") or "")),
        reverse=True,
    )[:10]


def _selected_file_ids(items: list[dict[str, Any]] | None) -> list[str]:
    return [item["source_id"] for item in _dedupe_selected_files(items or [])]


def _file_search_score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("relevance_score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _file_search_preview(hit: ChunkHit) -> str:
    match_title = hit.title.strip()
    text = " ".join(hit.text.split())
    if match_title and match_title != hit.source_title:
        return f"{match_title}: {text[:220]}".strip()
    return text[:260]


def _file_search_status_message(*, total: int, added: int) -> str:
    if total <= 0:
        return "No matching files."
    return f"Keeping top {total} file{'s' if total != 1 else ''}. Added {added}."


def _file_search_reference_context(items: list[dict[str, Any]]) -> str:
    reference_parts: list[str] = []
    ranked_items = sorted(
        items,
        key=lambda item: float(item.get("relevance_score") or 0.0),
        reverse=True,
    )
    for item in ranked_items[:3]:
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if summary == "OpenAI vector-store match from the indexed source file.":
            summary = str(item.get("preview") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if not title and not summary and not snippet:
            continue
        reference_parts.append("\n".join([title, summary, snippet[:700]]).strip())
    return "\n\n".join(reference_parts)


def _serialize_for_log(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _summarize_result(result: object) -> object:
    if isinstance(result, ToolResult):
        structured_content = result.structured_content or {}
        files = structured_content.get("files")
        file_items = files if isinstance(files, list) else []
        return {
            "content_blocks": len(result.content),
            "files": len(file_items),
            "truncated": any(
                isinstance(item, dict) and (item.get("original_truncated") or item.get("extracted_text_truncated"))
                for item in file_items
            ),
        }
    if isinstance(result, FileListResponse):
        return {"returned": len(result.sources), "total_count": result.total_count}
    if isinstance(result, SearchResponse):
        return {"returned": len(result.hits)}
    if isinstance(result, BranchSearchResponse):
        return {"levels": len(result.levels), "returned": sum(len(level.hits) for level in result.levels)}
    if isinstance(result, LibrarySourceDetail):
        return {
            "source_id": result.id,
            "title": result.display_title,
            "media_type": result.media_type,
            "byte_size": result.byte_size,
            "chunks": len(result.chunks),
            "download_url_present": result.download_url is not None,
            "content_retrieval_tool": result.content_retrieval_tool,
        }
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
    if isinstance(result, ReportMarkdownSaveResponse):
        return {
            "source_id": result.source.id,
            "source_status": result.source.status,
            "task_id": result.task.id if result.task is not None else None,
            "task_status": result.task.status if result.task is not None else None,
            "markdown_chars": len(result.markdown),
        }
    if isinstance(result, TaskListResponse):
        return {"returned": len(result.tasks)}
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, list):
        return {"returned": len(result)}
    return result

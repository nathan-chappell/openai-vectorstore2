from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

AppOperation: TypeAlias = Literal[
    "list_sources",
    "list_tags",
    "get_source_detail",
    "preview_semantic_split",
    "ingest_source",
    "delete_source",
    "search_chunks",
    "branch_search",
    "qa",
    "freeform",
    "generate_image",
    "generate_voice",
    "list_tasks",
    "get_task",
]


@dataclass(frozen=True, slots=True)
class AppCapability:
    """One app-core operation and the external surfaces currently mapped to it."""

    operation: AppOperation
    summary: str
    rest_routes: tuple[str, ...] = ()
    chatkit_tool: str | None = None
    mcp_tools: tuple[str, ...] = ()
    notes: str | None = None


APP_CAPABILITIES: tuple[AppCapability, ...] = (
    AppCapability(
        operation="list_sources",
        summary="List and filter source files in the user's semantic library.",
        rest_routes=("GET /api/sources",),
        chatkit_tool="list_sources",
        mcp_tools=("list_sources",),
    ),
    AppCapability(
        operation="list_tags",
        summary="List available auto and manual tags for retrieval filtering.",
        rest_routes=("GET /api/tags",),
        chatkit_tool="list_tags",
        mcp_tools=("list_tags",),
    ),
    AppCapability(
        operation="get_source_detail",
        summary="Load one source with its stored metadata and semantic chunks.",
        rest_routes=("GET /api/sources/{source_id}",),
        mcp_tools=("get_source_detail",),
        notes="ChatKit currently receives selected-source summaries as context instead of a dedicated detail tool.",
    ),
    AppCapability(
        operation="preview_semantic_split",
        summary="Preview semantic chunks and auto-tags without creating sources, chunks, or vector-store files.",
        rest_routes=("POST /api/sources/split-preview",),
        chatkit_tool="preview_semantic_split",
        mcp_tools=("preview_text_split", "preview_file_split"),
        notes="Preview is inspect-only; users iterate by rerunning with revised guidance.",
    ),
    AppCapability(
        operation="ingest_source",
        summary="Ingest raw material, split it semantically, tag it, and publish chunks to OpenAI vector stores.",
        rest_routes=("POST /api/sources",),
        mcp_tools=("ingest_text_source", "ingest_file_source"),
        notes="MCP file ingest currently accepts base64 payloads; richer Apps file inputs are planned.",
    ),
    AppCapability(
        operation="delete_source",
        summary="Delete an app-owned source record and its stored source payload.",
        rest_routes=("DELETE /api/sources/{source_id}",),
        mcp_tools=("delete_source",),
        notes="OpenAI vector-store file cleanup is planned.",
    ),
    AppCapability(
        operation="search_chunks",
        summary="Search OpenAI vector stores with source, kind, and tag filters, then hydrate app-owned chunks.",
        rest_routes=("POST /api/search",),
        chatkit_tool="search_chunks",
        mcp_tools=("search_chunks",),
    ),
    AppCapability(
        operation="branch_search",
        summary="Layer semantic search outward from retrieved chunks to explore related context.",
        rest_routes=("POST /api/search/branch",),
        chatkit_tool="branch_search",
        mcp_tools=("branch_search",),
    ),
    AppCapability(
        operation="qa",
        summary="Answer a user question from retrieved semantic chunks.",
        rest_routes=("POST /api/actions/qa",),
        chatkit_tool="answer_from_library",
        mcp_tools=("qa",),
    ),
    AppCapability(
        operation="freeform",
        summary="Generate grounded or creative text from retrieved semantic chunks.",
        rest_routes=("POST /api/actions/freeform",),
        chatkit_tool="freeform_from_library",
        mcp_tools=("freeform",),
    ),
    AppCapability(
        operation="generate_image",
        summary="Generate an image asset, optionally grounded in retrieved semantic chunks.",
        rest_routes=("POST /api/actions/image",),
        chatkit_tool="generate_image_from_library",
        mcp_tools=("generate_image",),
    ),
    AppCapability(
        operation="generate_voice",
        summary="Generate a narrated audio asset from a prompt or supplied source text.",
        rest_routes=("POST /api/actions/voice",),
        chatkit_tool="generate_voice_from_library",
        mcp_tools=("generate_voice",),
    ),
    AppCapability(
        operation="list_tasks",
        summary="List recent app tasks for the current user.",
        rest_routes=("GET /api/tasks",),
        mcp_tools=("list_tasks",),
    ),
    AppCapability(
        operation="get_task",
        summary="Load task status, inputs, state, result, and error information.",
        rest_routes=("GET /api/tasks/{task_id}",),
        mcp_tools=("get_task",),
    ),
)

MCP_RENDER_TOOLS: tuple[str, ...] = ("sources",)


def capability_by_operation() -> dict[AppOperation, AppCapability]:
    return {capability.operation: capability for capability in APP_CAPABILITIES}


def rest_route_names() -> set[str]:
    return {route for capability in APP_CAPABILITIES for route in capability.rest_routes}


def chatkit_tool_names() -> set[str]:
    return {capability.chatkit_tool for capability in APP_CAPABILITIES if capability.chatkit_tool is not None}


def mcp_tool_names() -> set[str]:
    return {
        *[tool_name for capability in APP_CAPABILITIES for tool_name in capability.mcp_tools],
        *MCP_RENDER_TOOLS,
    }

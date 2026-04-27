from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

AppOperation: TypeAlias = Literal[
    "name_thread",
    "list_sources",
    "list_filesystem",
    "search_filesystem",
    "create_folder",
    "update_filesystem_entry",
    "delete_filesystem_entries",
    "reveal_file",
    "set_file_search",
    "list_tags",
    "create_tag",
    "update_tag",
    "delete_tag",
    "get_source_detail",
    "preview_semantic_split",
    "start_research_import",
    "build_research_library",
    "list_research_candidates",
    "update_research_candidate_status",
    "ingest_research_candidates",
    "answer_research_library",
    "ingest_source",
    "resplit_source",
    "update_source_tags",
    "delete_source",
    "search_chunks",
    "branch_search",
    "qa",
    "freeform",
    "save_report_markdown",
    "generate_image",
    "generate_voice",
    "list_tasks",
    "get_task",
    "get_billing_status",
    "get_payment_integration_status",
    "admin_list_users",
    "admin_set_user_active",
    "admin_grant_credit",
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
        operation="name_thread",
        summary="Set a concise user-facing title for the current ChatKit thread.",
        chatkit_tool="name_thread",
    ),
    AppCapability(
        operation="list_sources",
        summary="List and filter source files in the user's indexed file library.",
        rest_routes=("GET /api/sources",),
        chatkit_tool="list_sources",
        mcp_tools=("list_sources",),
    ),
    AppCapability(
        operation="list_filesystem",
        summary="List the children of one virtual filesystem folder.",
        rest_routes=("GET /api/filesystem",),
        chatkit_tool="list_filesystem",
        mcp_tools=("list_filesystem",),
    ),
    AppCapability(
        operation="search_filesystem",
        summary="Find virtual files and folders by path, tags, and vector-store retrieval.",
        rest_routes=("GET /api/filesystem/search",),
        chatkit_tool="find_files",
        mcp_tools=("search_filesystem",),
    ),
    AppCapability(
        operation="create_folder",
        summary="Create a folder row in the app-owned virtual filesystem.",
        rest_routes=("POST /api/filesystem/folders",),
        chatkit_tool="create_folder",
        mcp_tools=("create_folder",),
    ),
    AppCapability(
        operation="update_filesystem_entry",
        summary="Rename or move a virtual file or folder and reindex affected file metadata.",
        rest_routes=("PATCH /api/filesystem/entries/{entry_id}",),
        chatkit_tool="update_filesystem_entry",
        mcp_tools=("update_filesystem_entry",),
    ),
    AppCapability(
        operation="delete_filesystem_entries",
        summary="Permanently delete files or folders from the virtual filesystem.",
        rest_routes=("POST /api/filesystem/delete",),
        chatkit_tool="delete_filesystem_entries",
        mcp_tools=("delete_filesystem_entries",),
    ),
    AppCapability(
        operation="reveal_file",
        summary="Ask the web explorer to navigate to one file or folder.",
        chatkit_tool="reveal_file",
    ),
    AppCapability(
        operation="set_file_search",
        summary="Ask the web explorer to apply a query and tag filter.",
        chatkit_tool="set_file_search",
    ),
    AppCapability(
        operation="list_tags",
        summary="List available auto and manual tags for retrieval filtering.",
        rest_routes=("GET /api/tags",),
        chatkit_tool="list_tags",
        mcp_tools=("list_tags",),
    ),
    AppCapability(
        operation="create_tag",
        summary="Create or promote a manual tag for source organization and retrieval filtering.",
        rest_routes=("POST /api/tags",),
        chatkit_tool="create_tag",
        mcp_tools=("create_tag",),
    ),
    AppCapability(
        operation="update_tag",
        summary="Rename or recolor a tag and reindex affected vector-store metadata when its slug changes.",
        rest_routes=("PATCH /api/tags/{tag_id}",),
        chatkit_tool="update_tag",
        mcp_tools=("update_tag",),
    ),
    AppCapability(
        operation="delete_tag",
        summary="Delete a tag and queue reindexing for affected sources.",
        rest_routes=("DELETE /api/tags/{tag_id}",),
        chatkit_tool="delete_tag",
        mcp_tools=("delete_tag",),
    ),
    AppCapability(
        operation="get_source_detail",
        summary="Load one source with its stored metadata and optional semantic split records.",
        rest_routes=("GET /api/sources/{source_id}",),
        chatkit_tool="get_source_detail",
        mcp_tools=("get_source_detail",),
    ),
    AppCapability(
        operation="preview_semantic_split",
        summary="Preview semantic split records and auto-tags without creating sources, chunks, or vector-store files.",
        rest_routes=("POST /api/sources/split-preview",),
        chatkit_tool="preview_semantic_split",
        mcp_tools=("preview_text_split", "preview_file_split"),
        notes="Preview is inspect-only; users iterate by rerunning with revised guidance.",
    ),
    AppCapability(
        operation="start_research_import",
        summary="Seed a lower-level research import task from a topic, paper, URL, or text and discover reference candidates.",
        rest_routes=("POST /api/research/imports",),
        chatkit_tool="start_research_import",
        mcp_tools=("start_research_import",),
    ),
    AppCapability(
        operation="build_research_library",
        summary="Create a foldered research library from a topic, paper, URL, or text, dedupe results, and auto-ingest bounded public candidates.",
        rest_routes=("POST /api/research/library-builds",),
        chatkit_tool="build_research_library",
        mcp_tools=("build_research_library",),
    ),
    AppCapability(
        operation="list_research_candidates",
        summary="List research import candidates by pending, approved, rejected, ingesting, ingested, duplicate, or failed status.",
        rest_routes=("GET /api/research/candidates",),
        chatkit_tool="list_research_candidates",
        mcp_tools=("list_research_candidates",),
    ),
    AppCapability(
        operation="update_research_candidate_status",
        summary="Approve, reject, or move lower-level research import candidates back to pending review.",
        rest_routes=("POST /api/research/candidates/status",),
        chatkit_tool="update_research_candidate_status",
        mcp_tools=("update_research_candidate_status",),
    ),
    AppCapability(
        operation="ingest_research_candidates",
        summary="Ingest approved lower-level research candidates through the canonical source ingestion path.",
        rest_routes=("POST /api/research/candidates/ingest",),
        chatkit_tool="ingest_research_candidates",
        mcp_tools=("ingest_research_candidates",),
    ),
    AppCapability(
        operation="answer_research_library",
        summary="Answer a question using sources linked to a research library build task.",
        chatkit_tool="answer_research_library",
        mcp_tools=("answer_research_library",),
        notes="Uses the canonical QA action after resolving ingested research candidates to source IDs.",
    ),
    AppCapability(
        operation="ingest_source",
        summary="Ingest raw material, tag it, and publish a source-level file to OpenAI vector stores.",
        rest_routes=("POST /api/sources",),
        chatkit_tool="ingest_text_source",
        mcp_tools=("ingest_text_source", "ingest_file_source"),
        notes="ChatKit supports text ingest; web and MCP also support file/PDF ingest.",
    ),
    AppCapability(
        operation="resplit_source",
        summary="Replace one source's optional semantic split records using the stored source payload.",
        rest_routes=("POST /api/sources/{source_id}/resplit",),
        chatkit_tool="resplit_source",
        mcp_tools=("resplit_source",),
        notes="Re-split runs as a queued app task and preserves existing tags unless explicit tag IDs are supplied.",
    ),
    AppCapability(
        operation="update_source_tags",
        summary="Replace a source's tag assignments and queue source-level vector-store reindexing.",
        rest_routes=("POST /api/sources/{source_id}/tags",),
        chatkit_tool="update_source_tags",
        mcp_tools=("update_source_tags",),
        notes="Reindexing refreshes OpenAI vector attributes so tag-filtered retrieval stays aligned with app-owned tags.",
    ),
    AppCapability(
        operation="delete_source",
        summary="Delete an app-owned source record and its stored source payload.",
        rest_routes=("DELETE /api/sources/{source_id}",),
        chatkit_tool="delete_source",
        mcp_tools=("delete_source",),
        notes="Deletes the stored payload plus tracked OpenAI original, source-vector, and legacy chunk files.",
    ),
    AppCapability(
        operation="search_chunks",
        summary="Search OpenAI vector stores with source, kind, path, date, and tag filters, then hydrate source-level hits.",
        rest_routes=("POST /api/search",),
        chatkit_tool="search_chunks",
        mcp_tools=("search_chunks",),
    ),
    AppCapability(
        operation="branch_search",
        summary="Layer source-file vector search outward from retrieved hits to explore related context.",
        rest_routes=("POST /api/search/branch",),
        chatkit_tool="branch_search",
        mcp_tools=("branch_search",),
    ),
    AppCapability(
        operation="qa",
        summary="Answer a user question from retrieved source-file vector matches.",
        rest_routes=("POST /api/actions/qa",),
        chatkit_tool="answer_from_library",
        mcp_tools=("qa",),
    ),
    AppCapability(
        operation="freeform",
        summary="Generate grounded or creative text from retrieved source-file vector matches.",
        rest_routes=("POST /api/actions/freeform",),
        chatkit_tool="freeform_from_library",
        mcp_tools=("freeform",),
    ),
    AppCapability(
        operation="save_report_markdown",
        summary="Render a structured report document to Markdown and save it as a first-class library source.",
        rest_routes=("POST /api/reports/markdown",),
        chatkit_tool="save_report_markdown",
        mcp_tools=("save_report_markdown",),
        notes="The saved report follows the canonical source ingestion path so it can be searched, selected, downloaded, and cited.",
    ),
    AppCapability(
        operation="generate_image",
        summary="Generate an image asset, optionally grounded in retrieved source-file vector matches.",
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
        chatkit_tool="list_tasks",
        mcp_tools=("list_tasks",),
    ),
    AppCapability(
        operation="get_task",
        summary="Load task status, inputs, state, result, and error information.",
        rest_routes=("GET /api/tasks/{task_id}",),
        chatkit_tool="get_task",
        mcp_tools=("get_task",),
    ),
    AppCapability(
        operation="get_billing_status",
        summary="Load current credit balance and billable status for the signed-in user.",
        rest_routes=("GET /api/billing/me",),
    ),
    AppCapability(
        operation="get_payment_integration_status",
        summary="Load checkout availability for the configured admin/payment integration.",
        rest_routes=("GET /api/billing/payment-status",),
    ),
    AppCapability(
        operation="admin_list_users",
        summary="List Clerk users with activation state, credit floor, and current balance for admins.",
        rest_routes=("GET /api/admin/users",),
    ),
    AppCapability(
        operation="admin_set_user_active",
        summary="Activate or deactivate a Clerk user and apply the default credit floor on activation.",
        rest_routes=("POST /api/admin/users/set-active",),
    ),
    AppCapability(
        operation="admin_grant_credit",
        summary="Grant manual USD credit with an audit note.",
        rest_routes=("POST /api/admin/credits/grant",),
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

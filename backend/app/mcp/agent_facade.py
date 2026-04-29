from __future__ import annotations

from base64 import b64decode
import binascii
from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import logging
from time import perf_counter
from typing import Any, Literal, cast

from agents import Agent, RunContextWrapper, Runner, function_tool, handoff
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent
from pydantic import BaseModel, Field

from backend.app.bootstrap import AppServices
from backend.app.schemas import (
    BranchSearchRequest,
    FreeformRequest,
    ImageGenerationRequest,
    QaRequest,
    ResearchCandidateIngestRequest,
    ResearchImportCreateRequest,
    ResearchLibraryBuildRequest,
    ReportDocument,
    ReportMarkdownSaveRequest,
    SearchRequest,
    VoiceGenerationRequest,
)
from backend.app.services.reports import save_report_markdown_source

logger = logging.getLogger(__name__)

DEFAULT_RETRIEVE_MAX_BYTES_PER_FILE = 2_000_000
DEFAULT_RETRIEVE_MAX_EXTRACTED_CHARS_PER_FILE = 120_000


@dataclass(slots=True)
class McpAgentContext:
    services: AppServices
    clerk_user_id: str
    origin_surface: str = "mcp"
    operations: list[dict[str, object]] = field(default_factory=list)
    content_blocks: list[TextContent | EmbeddedResource] = field(default_factory=list)


class McpFacadeInput(BaseModel):
    instruction: str = Field(min_length=1, max_length=16_000)
    query: str | None = None
    prompt: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    selected_source_ids: list[str] = Field(default_factory=list)
    folder_id: str | None = None
    folder_name: str | None = None
    tag_ids: list[str] = Field(default_factory=list)
    task_id: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    max_results: int = Field(default=8, ge=1, le=24)
    max_sources: int = Field(default=12, ge=1, le=50)
    include_file_contents: bool = False
    confirm: bool = False
    mode: Literal["grounded", "creative"] = "grounded"
    filename: str | None = None
    text: str | None = None
    payload_base64: str | None = None
    media_type: str | None = None
    document: dict[str, Any] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


async def run_library_search_agent(
    *,
    services: AppServices,
    clerk_user_id: str,
    payload: McpFacadeInput,
) -> ToolResult:
    context = McpAgentContext(services=services, clerk_user_id=clerk_user_id)
    return await _run_facade_agent(
        context=context,
        facade_name="library_search",
        intake_agent=_library_intake_agent(services=services),
        payload=payload,
    )


async def run_research_library_agent(
    *,
    services: AppServices,
    clerk_user_id: str,
    payload: McpFacadeInput,
) -> ToolResult:
    context = McpAgentContext(services=services, clerk_user_id=clerk_user_id)
    return await _run_facade_agent(
        context=context,
        facade_name="research_library",
        intake_agent=_research_intake_agent(services=services),
        payload=payload,
    )


async def run_answer_from_library_agent(
    *,
    services: AppServices,
    clerk_user_id: str,
    payload: McpFacadeInput,
) -> ToolResult:
    context = McpAgentContext(services=services, clerk_user_id=clerk_user_id)
    return await _run_facade_agent(
        context=context,
        facade_name="answer_from_library",
        intake_agent=_answer_intake_agent(services=services),
        payload=payload,
    )


async def run_manage_library_agent(
    *,
    services: AppServices,
    clerk_user_id: str,
    payload: McpFacadeInput,
) -> ToolResult:
    context = McpAgentContext(services=services, clerk_user_id=clerk_user_id)
    return await _run_facade_agent(
        context=context,
        facade_name="manage_library",
        intake_agent=_manage_intake_agent(services=services),
        payload=payload,
    )


async def _run_facade_agent(
    *,
    context: McpAgentContext,
    facade_name: str,
    intake_agent: Agent[McpAgentContext],
    payload: McpFacadeInput,
) -> ToolResult:
    started_at = perf_counter()
    logger.info(
        "mcp_agent_facade_started facade=%s clerk_user_id=%s arguments=%s",
        facade_name,
        context.clerk_user_id,
        _safe_json(payload.model_dump(mode="json", exclude={"payload_base64"})),
    )
    result = await Runner.run(
        intake_agent,
        _agent_input(payload),
        context=context,
        max_turns=12,
    )
    final_output = result.final_output
    final_text = final_output if isinstance(final_output, str) else json.dumps(final_output, default=str)
    structured_content = {
        "facade": facade_name,
        "final_output": final_output,
        "operations": context.operations,
        "last_agent": result.last_agent.name,
        "openai_response_id": result.last_response_id,
    }
    content: list[TextContent | EmbeddedResource] = [
        TextContent(type="text", text=final_text or f"{facade_name} completed.")
    ]
    content.extend(context.content_blocks)
    logger.info(
        "mcp_agent_facade_completed facade=%s clerk_user_id=%s operations=%s last_agent=%s response=%s duration_ms=%.1f",
        facade_name,
        context.clerk_user_id,
        len(context.operations),
        result.last_agent.name,
        result.last_response_id,
        (perf_counter() - started_at) * 1000,
    )
    return ToolResult(content=content, structured_content=structured_content)


def _agent_input(payload: McpFacadeInput) -> str:
    safe_payload = payload.model_dump(mode="json", exclude={"payload_base64"})
    if payload.payload_base64:
        safe_payload["payload_base64_present"] = True
    return (
        "Handle this MCP facade request by handing off to the specialist agent, using the available tools, "
        "and returning a concise final answer plus relevant IDs.\n\n"
        f"{json.dumps(safe_payload, sort_keys=True, default=str)}"
    )


def _model_name(services: AppServices) -> str:
    return services.settings.openai_fast_model


def _library_intake_agent(*, services: AppServices) -> Agent[McpAgentContext]:
    specialist = Agent[McpAgentContext](
        name="library_search_subagent",
        handoff_description="Search, inspect, and retrieve indexed library files.",
        model=_model_name(services),
        instructions=(
            "You search the vector library and retrieve file contents. Prefer concise metadata first; "
            "only call retrieve_library_files when file contents are explicitly requested."
        ),
        tools=_library_tools(),
    )
    return Agent[McpAgentContext](
        name="library_search_intake",
        model=_model_name(services),
        instructions="Immediately hand off every request to the library_search_subagent.",
        handoffs=[handoff(specialist, tool_name_override="handoff_to_library_search")],
    )


def _research_intake_agent(*, services: AppServices) -> Agent[McpAgentContext]:
    specialist = Agent[McpAgentContext](
        name="research_library_subagent",
        handoff_description="Build research libraries, review candidates, ingest approved research, and answer from research tasks.",
        model=_model_name(services),
        instructions=(
            "You operate research-library workflows. Use existing task IDs and candidate IDs when supplied. "
            "Do not invent candidate IDs."
        ),
        tools=_research_tools(),
    )
    return Agent[McpAgentContext](
        name="research_library_intake",
        model=_model_name(services),
        instructions="Immediately hand off every request to the research_library_subagent.",
        handoffs=[handoff(specialist, tool_name_override="handoff_to_research_library")],
    )


def _answer_intake_agent(*, services: AppServices) -> Agent[McpAgentContext]:
    specialist = Agent[McpAgentContext](
        name="answer_from_library_subagent",
        handoff_description="Answer, draft, report, or generate media from library context.",
        model=_model_name(services),
        instructions=(
            "You create grounded answers and artifacts from the library. Prefer answer_from_library for direct questions, "
            "write_from_library for prose drafts, and save_report_markdown only when the user asks to save a report."
        ),
        tools=_answer_tools(),
    )
    return Agent[McpAgentContext](
        name="answer_from_library_intake",
        model=_model_name(services),
        instructions="Immediately hand off every request to the answer_from_library_subagent.",
        handoffs=[handoff(specialist, tool_name_override="handoff_to_answer_from_library")],
    )


def _manage_intake_agent(*, services: AppServices) -> Agent[McpAgentContext]:
    specialist = Agent[McpAgentContext](
        name="manage_library_subagent",
        handoff_description="Ingest, organize, tag, split, reindex, delete, and inspect tasks with confirmations.",
        model=_model_name(services),
        instructions=(
            "You manage the library. Destructive operations must call their tools with confirm=true, and only when "
            "the facade payload says confirm=true. Otherwise return the confirmation_required response."
        ),
        tools=_management_tools(),
    )
    return Agent[McpAgentContext](
        name="manage_library_intake",
        model=_model_name(services),
        instructions="Immediately hand off every request to the manage_library_subagent.",
        handoffs=[handoff(specialist, tool_name_override="handoff_to_manage_library")],
    )


def _library_tools() -> list[Any]:
    @function_tool(name_override="list_sources")
    async def list_sources_tool(
        ctx: RunContextWrapper[McpAgentContext],
        query: str | None = None,
        tag_ids: list[str] | None = None,
        page_size: int = 20,
    ) -> dict[str, object]:
        response = await ctx.context.services.sources.list_sources(
            clerk_user_id=ctx.context.clerk_user_id,
            query=query,
            tag_ids=tag_ids or [],
            tag_match_mode="all",
            page=1,
            page_size=max(1, min(page_size, 100)),
        )
        payload = response.model_dump(mode="json")
        _record(ctx.context, "list_sources", payload)
        return payload

    @function_tool(name_override="search_filesystem")
    async def search_filesystem_tool(
        ctx: RunContextWrapper[McpAgentContext],
        query: str | None = None,
        tag_ids: list[str] | None = None,
        page_size: int = 50,
    ) -> dict[str, object]:
        response = await ctx.context.services.sources.search_filesystem(
            clerk_user_id=ctx.context.clerk_user_id,
            query=query,
            tag_ids=tag_ids or [],
            tag_match_mode="all",
            page=1,
            page_size=max(1, min(page_size, 100)),
        )
        payload = response.model_dump(mode="json")
        _record(ctx.context, "search_filesystem", payload)
        return payload

    @function_tool(name_override="get_source_detail")
    async def get_source_detail_tool(ctx: RunContextWrapper[McpAgentContext], source_id: str) -> dict[str, object]:
        response = await ctx.context.services.sources.get_source(
            clerk_user_id=ctx.context.clerk_user_id,
            source_id=source_id,
        )
        payload = response.model_dump(mode="json")
        _record(ctx.context, "get_source_detail", payload)
        return payload

    @function_tool(name_override="search_chunks")
    async def search_chunks_tool(
        ctx: RunContextWrapper[McpAgentContext],
        query: str,
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        max_results: int = 8,
    ) -> dict[str, object]:
        response = await ctx.context.services.sources.search(
            clerk_user_id=ctx.context.clerk_user_id,
            request=SearchRequest(
                query=query,
                selected_source_ids=selected_source_ids or [],
                tag_ids=tag_ids or [],
                max_results=max(1, min(max_results, 24)),
            ),
            origin_surface=ctx.context.origin_surface,
        )
        payload = response.model_dump(mode="json")
        _record(ctx.context, "search_chunks", payload)
        return payload

    @function_tool(name_override="branch_search")
    async def branch_search_tool(
        ctx: RunContextWrapper[McpAgentContext],
        query: str,
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        descend: int = 2,
        max_width: int = 3,
    ) -> dict[str, object]:
        response = await ctx.context.services.sources.branch_search(
            clerk_user_id=ctx.context.clerk_user_id,
            request=BranchSearchRequest(
                query=query,
                selected_source_ids=selected_source_ids or [],
                tag_ids=tag_ids or [],
                descend=max(0, min(descend, 4)),
                max_width=max(1, min(max_width, 8)),
            ),
        )
        payload = response.model_dump(mode="json")
        _record(ctx.context, "branch_search", payload)
        return payload

    @function_tool(name_override="retrieve_library_files")
    async def retrieve_library_files_tool(
        ctx: RunContextWrapper[McpAgentContext],
        source_ids: list[str],
        include_extracted_text: bool = True,
    ) -> dict[str, object]:
        result = await _retrieve_files_result(
            context=ctx.context,
            source_ids=source_ids,
            include_extracted_text=include_extracted_text,
            max_bytes_per_file=DEFAULT_RETRIEVE_MAX_BYTES_PER_FILE,
            max_extracted_chars_per_file=DEFAULT_RETRIEVE_MAX_EXTRACTED_CHARS_PER_FILE,
        )
        ctx.context.content_blocks.extend(
            block for block in result.content[1:] if isinstance(block, (TextContent, EmbeddedResource))
        )
        structured = result.structured_content or {}
        _record(ctx.context, "retrieve_library_files", structured)
        return structured

    return [
        list_sources_tool,
        search_filesystem_tool,
        get_source_detail_tool,
        search_chunks_tool,
        branch_search_tool,
        retrieve_library_files_tool,
    ]


def _research_tools() -> list[Any]:
    @function_tool(name_override="start_research_import", strict_mode=False)
    async def start_research_import_tool(
        ctx: RunContextWrapper[McpAgentContext],
        seed_type: str = "topic",
        text: str | None = None,
        url: str | None = None,
        title: str | None = None,
        tag_ids: list[str] | None = None,
        folder_id: str | None = None,
        folder_name: str | None = None,
        max_depth: int = 2,
    ) -> dict[str, object]:
        payload = ResearchImportCreateRequest(
            seed_type=cast(Any, seed_type),
            text=text,
            url=url,
            title=title,
            tag_ids=tag_ids or [],
            folder_id=folder_id,
            folder_name=folder_name,
            max_depth=max(0, min(max_depth, 4)),
        )
        response = await ctx.context.services.research.create_import(
            clerk_user_id=ctx.context.clerk_user_id,
            payload=payload,
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "start_research_import", output)
        return output

    @function_tool(name_override="build_research_library", strict_mode=False)
    async def build_research_library_tool(
        ctx: RunContextWrapper[McpAgentContext],
        query: str,
        seed_type: str = "topic",
        title: str | None = None,
        folder_id: str | None = None,
        folder_name: str | None = None,
        tag_ids: list[str] | None = None,
        max_sources: int = 12,
    ) -> dict[str, object]:
        payload = ResearchLibraryBuildRequest(
            seed_type=cast(Any, seed_type),
            query=query,
            title=title,
            folder_id=folder_id,
            folder_name=folder_name,
            tag_ids=tag_ids or [],
            max_sources=max(1, min(max_sources, 50)),
        )
        response = await ctx.context.services.research.build_library(
            clerk_user_id=ctx.context.clerk_user_id,
            payload=payload,
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "build_research_library", output)
        return output

    @function_tool(name_override="list_research_candidates")
    async def list_research_candidates_tool(
        ctx: RunContextWrapper[McpAgentContext],
        task_id: str | None = None,
        status: str | None = None,
        page_size: int = 50,
    ) -> dict[str, object]:
        response = await ctx.context.services.research.list_candidates(
            clerk_user_id=ctx.context.clerk_user_id,
            task_id=task_id,
            status=cast(Any, status),
            page=1,
            page_size=max(1, min(page_size, 100)),
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "list_research_candidates", output)
        return output

    @function_tool(name_override="update_research_candidate_status")
    async def update_research_candidate_status_tool(
        ctx: RunContextWrapper[McpAgentContext],
        candidate_ids: list[str],
        status: Literal["approved", "rejected", "pending"],
    ) -> dict[str, object]:
        response = await ctx.context.services.research.update_candidate_status(
            clerk_user_id=ctx.context.clerk_user_id,
            candidate_ids=candidate_ids,
            status=status,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "update_research_candidate_status", output)
        return output

    @function_tool(name_override="ingest_research_candidates")
    async def ingest_research_candidates_tool(
        ctx: RunContextWrapper[McpAgentContext],
        candidate_ids: list[str] | None = None,
        task_id: str | None = None,
        tag_ids: list[str] | None = None,
        folder_id: str | None = None,
    ) -> dict[str, object]:
        response = await ctx.context.services.research.ingest_approved_candidates(
            clerk_user_id=ctx.context.clerk_user_id,
            payload=ResearchCandidateIngestRequest(
                candidate_ids=candidate_ids,
                task_id=task_id,
                tag_ids=tag_ids,
                folder_id=folder_id,
            ),
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "ingest_research_candidates", output)
        return output

    @function_tool(name_override="answer_research_library")
    async def answer_research_library_tool(
        ctx: RunContextWrapper[McpAgentContext],
        question: str,
        task_id: str | None = None,
        source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        max_results: int = 8,
    ) -> dict[str, object]:
        selected_source_ids = list(dict.fromkeys(source_ids or []))
        if task_id:
            linked_scope = await ctx.context.services.research.linked_source_scope_for_task(
                clerk_user_id=ctx.context.clerk_user_id,
                task_id=task_id,
            )
            selected_source_ids = list(dict.fromkeys([*selected_source_ids, *linked_scope.ready_source_ids]))
        response = await ctx.context.services.actions.qa(
            clerk_user_id=ctx.context.clerk_user_id,
            payload=QaRequest(
                prompt=question,
                selected_source_ids=selected_source_ids,
                tag_ids=tag_ids or [],
                max_results=max(1, min(max_results, 16)),
            ),
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "answer_research_library", output)
        return output

    return [
        start_research_import_tool,
        build_research_library_tool,
        list_research_candidates_tool,
        update_research_candidate_status_tool,
        ingest_research_candidates_tool,
        answer_research_library_tool,
    ]


def _answer_tools() -> list[Any]:
    @function_tool(name_override="answer_from_library")
    async def answer_from_library_tool(
        ctx: RunContextWrapper[McpAgentContext],
        prompt: str,
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        max_results: int = 8,
    ) -> dict[str, object]:
        response = await ctx.context.services.actions.qa(
            clerk_user_id=ctx.context.clerk_user_id,
            payload=QaRequest(
                prompt=prompt,
                selected_source_ids=selected_source_ids or [],
                tag_ids=tag_ids or [],
                max_results=max(1, min(max_results, 16)),
            ),
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "answer_from_library", output)
        return output

    @function_tool(name_override="write_from_library")
    async def write_from_library_tool(
        ctx: RunContextWrapper[McpAgentContext],
        prompt: str,
        mode: Literal["grounded", "creative"] = "grounded",
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        max_results: int = 8,
    ) -> dict[str, object]:
        response = await ctx.context.services.actions.freeform(
            clerk_user_id=ctx.context.clerk_user_id,
            payload=FreeformRequest(
                prompt=prompt,
                mode=mode,
                selected_source_ids=selected_source_ids or [],
                tag_ids=tag_ids or [],
                max_results=max(1, min(max_results, 16)),
            ),
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "write_from_library", output)
        return output

    @function_tool(name_override="save_report_markdown", strict_mode=False)
    async def save_report_markdown_tool(
        ctx: RunContextWrapper[McpAgentContext],
        document: dict[str, Any],
        filename: str | None = None,
        folder_id: str | None = None,
        tag_ids: list[str] | None = None,
        user_guidance: str | None = None,
    ) -> dict[str, object]:
        request = ReportMarkdownSaveRequest(
            document=ReportDocument.model_validate(document),
            filename=filename,
            folder_id=folder_id,
            tag_ids=tag_ids or [],
            user_guidance=user_guidance,
        )
        response = await save_report_markdown_source(
            sources=ctx.context.services.sources,
            clerk_user_id=ctx.context.clerk_user_id,
            request=request,
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "save_report_markdown", output)
        return output

    @function_tool(name_override="generate_image")
    async def generate_image_tool(
        ctx: RunContextWrapper[McpAgentContext],
        prompt: str,
        size: str = "1024x1024",
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
    ) -> dict[str, object]:
        response = await ctx.context.services.actions.image(
            clerk_user_id=ctx.context.clerk_user_id,
            payload=ImageGenerationRequest(
                prompt=prompt,
                size=size,
                selected_source_ids=selected_source_ids or [],
                tag_ids=tag_ids or [],
            ),
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "generate_image", output)
        return output

    @function_tool(name_override="generate_voice")
    async def generate_voice_tool(
        ctx: RunContextWrapper[McpAgentContext],
        prompt: str,
        source_text: str | None = None,
        voice: str | None = None,
        response_format: Literal["mp3", "wav", "opus"] = "mp3",
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
    ) -> dict[str, object]:
        response = await ctx.context.services.actions.voice(
            clerk_user_id=ctx.context.clerk_user_id,
            payload=VoiceGenerationRequest(
                prompt=prompt,
                source_text=source_text,
                voice=voice,
                response_format=response_format,
                selected_source_ids=selected_source_ids or [],
                tag_ids=tag_ids or [],
            ),
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "generate_voice", output)
        return output

    return [
        answer_from_library_tool,
        write_from_library_tool,
        save_report_markdown_tool,
        generate_image_tool,
        generate_voice_tool,
    ]


def _management_tools() -> list[Any]:
    @function_tool(name_override="ingest_text_source")
    async def ingest_text_source_tool(
        ctx: RunContextWrapper[McpAgentContext],
        filename: str,
        text: str,
        tag_ids: list[str] | None = None,
        folder_id: str | None = None,
        virtual_name: str | None = None,
        user_guidance: str | None = None,
    ) -> dict[str, object]:
        response = await ctx.context.services.sources.ingest_source(
            clerk_user_id=ctx.context.clerk_user_id,
            filename=filename,
            declared_media_type="text/plain",
            payload=text.encode("utf-8"),
            tag_ids=tag_ids or [],
            user_guidance=user_guidance,
            origin_surface=ctx.context.origin_surface,
            folder_id=folder_id,
            virtual_name=virtual_name,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "ingest_text_source", output)
        return output

    @function_tool(name_override="ingest_file_source")
    async def ingest_file_source_tool(
        ctx: RunContextWrapper[McpAgentContext],
        filename: str,
        payload_base64: str,
        media_type: str | None = None,
        tag_ids: list[str] | None = None,
        folder_id: str | None = None,
        virtual_name: str | None = None,
        user_guidance: str | None = None,
    ) -> dict[str, object]:
        try:
            payload = b64decode(payload_base64, validate=True)
        except binascii.Error as exc:
            raise ValueError("payload_base64 must be valid base64 data.") from exc
        response = await ctx.context.services.sources.ingest_source(
            clerk_user_id=ctx.context.clerk_user_id,
            filename=filename,
            declared_media_type=media_type,
            payload=payload,
            tag_ids=tag_ids or [],
            user_guidance=user_guidance,
            origin_surface=ctx.context.origin_surface,
            folder_id=folder_id,
            virtual_name=virtual_name,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "ingest_file_source", output)
        return output

    @function_tool(name_override="create_folder")
    async def create_folder_tool(
        ctx: RunContextWrapper[McpAgentContext],
        name: str,
        parent_id: str | None = None,
    ) -> dict[str, object]:
        response = await ctx.context.services.sources.create_folder(
            clerk_user_id=ctx.context.clerk_user_id,
            parent_id=parent_id,
            name=name,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "create_folder", output)
        return output

    @function_tool(name_override="update_filesystem_entry")
    async def update_filesystem_entry_tool(
        ctx: RunContextWrapper[McpAgentContext],
        entry_id: str,
        name: str | None = None,
        parent_id: str | None = None,
    ) -> dict[str, object]:
        response = await ctx.context.services.sources.update_filesystem_entry(
            clerk_user_id=ctx.context.clerk_user_id,
            entry_id=entry_id,
            name=name,
            parent_id=parent_id,
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "update_filesystem_entry", output)
        return output

    @function_tool(name_override="delete_filesystem_entries")
    async def delete_filesystem_entries_tool(
        ctx: RunContextWrapper[McpAgentContext],
        entry_ids: list[str],
        confirm: bool = False,
    ) -> dict[str, object]:
        if not confirm:
            return _confirmation_required("delete_filesystem_entries", {"entry_ids": entry_ids})
        response = await ctx.context.services.sources.delete_filesystem_entries(
            clerk_user_id=ctx.context.clerk_user_id,
            entry_ids=entry_ids,
            confirm=confirm,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "delete_filesystem_entries", output)
        return output

    @function_tool(name_override="list_tags")
    async def list_tags_tool(ctx: RunContextWrapper[McpAgentContext]) -> dict[str, object]:
        tags = await ctx.context.services.sources.list_tags(clerk_user_id=ctx.context.clerk_user_id)
        output: dict[str, object] = {"tags": [tag.model_dump(mode="json") for tag in tags]}
        _record(ctx.context, "list_tags", output)
        return output

    @function_tool(name_override="create_tag")
    async def create_tag_tool(
        ctx: RunContextWrapper[McpAgentContext],
        name: str,
        color: str | None = None,
    ) -> dict[str, object]:
        response = await ctx.context.services.sources.create_tag(
            clerk_user_id=ctx.context.clerk_user_id,
            name=name,
            color=color,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "create_tag", output)
        return output

    @function_tool(name_override="update_tag")
    async def update_tag_tool(
        ctx: RunContextWrapper[McpAgentContext],
        tag_id: str,
        name: str | None = None,
        color: str | None = None,
    ) -> dict[str, object]:
        response = await ctx.context.services.sources.update_tag(
            clerk_user_id=ctx.context.clerk_user_id,
            tag_id=tag_id,
            name=name,
            color=color,
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "update_tag", output)
        return output

    @function_tool(name_override="delete_tag")
    async def delete_tag_tool(
        ctx: RunContextWrapper[McpAgentContext],
        tag_id: str,
        confirm: bool = False,
    ) -> dict[str, object]:
        if not confirm:
            return _confirmation_required("delete_tag", {"tag_id": tag_id})
        response = await ctx.context.services.sources.delete_tag(
            clerk_user_id=ctx.context.clerk_user_id,
            tag_id=tag_id,
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "delete_tag", output)
        return output

    @function_tool(name_override="preview_text_split")
    async def preview_text_split_tool(
        ctx: RunContextWrapper[McpAgentContext],
        filename: str,
        text: str,
        media_type: str | None = "text/plain",
        user_guidance: str | None = None,
    ) -> dict[str, object]:
        response = await ctx.context.services.sources.preview_semantic_split(
            clerk_user_id=ctx.context.clerk_user_id,
            filename=filename,
            declared_media_type=media_type,
            payload=text.encode("utf-8"),
            user_guidance=user_guidance,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "preview_text_split", output)
        return output

    @function_tool(name_override="resplit_source")
    async def resplit_source_tool(
        ctx: RunContextWrapper[McpAgentContext],
        source_id: str,
        tag_ids: list[str] | None = None,
        user_guidance: str | None = None,
        confirm: bool = False,
    ) -> dict[str, object]:
        if not confirm:
            return _confirmation_required("resplit_source", {"source_id": source_id})
        response = await ctx.context.services.sources.resplit_source(
            clerk_user_id=ctx.context.clerk_user_id,
            source_id=source_id,
            tag_ids=tag_ids,
            user_guidance=user_guidance,
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "resplit_source", output)
        return output

    @function_tool(name_override="update_source_tags")
    async def update_source_tags_tool(
        ctx: RunContextWrapper[McpAgentContext],
        source_id: str,
        tag_ids: list[str],
    ) -> dict[str, object]:
        response = await ctx.context.services.sources.update_source_tags(
            clerk_user_id=ctx.context.clerk_user_id,
            source_id=source_id,
            tag_ids=tag_ids,
            origin_surface=ctx.context.origin_surface,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "update_source_tags", output)
        return output

    @function_tool(name_override="delete_source")
    async def delete_source_tool(
        ctx: RunContextWrapper[McpAgentContext],
        source_id: str,
        confirm: bool = False,
    ) -> dict[str, object]:
        if not confirm:
            return _confirmation_required("delete_source", {"source_id": source_id})
        deleted_id = await ctx.context.services.sources.delete_source(
            clerk_user_id=ctx.context.clerk_user_id,
            source_id=source_id,
        )
        output: dict[str, object] = {"deleted_source_id": deleted_id}
        _record(ctx.context, "delete_source", output)
        return output

    @function_tool(name_override="list_tasks")
    async def list_tasks_tool(
        ctx: RunContextWrapper[McpAgentContext],
        kind: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        response = await ctx.context.services.actions.list_tasks(
            clerk_user_id=ctx.context.clerk_user_id,
            kind=cast(Any, kind),
            limit=max(1, min(limit, 200)),
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "list_tasks", output)
        return output

    @function_tool(name_override="get_task")
    async def get_task_tool(ctx: RunContextWrapper[McpAgentContext], task_id: str) -> dict[str, object]:
        response = await ctx.context.services.actions.get_task(
            clerk_user_id=ctx.context.clerk_user_id,
            task_id=task_id,
        )
        output = response.model_dump(mode="json")
        _record(ctx.context, "get_task", output)
        return output

    return [
        ingest_text_source_tool,
        ingest_file_source_tool,
        create_folder_tool,
        update_filesystem_entry_tool,
        delete_filesystem_entries_tool,
        list_tags_tool,
        create_tag_tool,
        update_tag_tool,
        delete_tag_tool,
        preview_text_split_tool,
        resplit_source_tool,
        update_source_tags_tool,
        delete_source_tool,
        list_tasks_tool,
        get_task_tool,
    ]


async def _retrieve_files_result(
    *,
    context: McpAgentContext,
    source_ids: list[str],
    include_extracted_text: bool,
    max_bytes_per_file: int,
    max_extracted_chars_per_file: int,
) -> ToolResult:
    from backend.app.mcp.server import _retrieve_files_result as server_retrieve_files_result  # pyright: ignore[reportPrivateUsage]

    return await server_retrieve_files_result(
        services=context.services,
        clerk_user_id=context.clerk_user_id,
        source_ids=source_ids,
        include_extracted_text=include_extracted_text,
        max_bytes_per_file=max_bytes_per_file,
        max_extracted_chars_per_file=max_extracted_chars_per_file,
    )


def _record(context: McpAgentContext, operation: str, payload: Mapping[str, object]) -> None:
    context.operations.append(
        {
            "operation": operation,
            "summary": _compact_summary(payload),
        }
    )


def _compact_summary(payload: Mapping[str, object]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key in (
        "source_id",
        "task_id",
        "deleted_source_id",
        "total_count",
        "has_more",
        "kind",
        "answer",
        "message",
        "confirmation_required",
    ):
        if key in payload:
            output[key] = payload[key]
    for key in ("sources", "hits", "candidates", "ingested", "tasks", "files"):
        value = payload.get(key)
        if isinstance(value, list):
            output[f"{key}_count"] = len(value)
    return output or {"keys": sorted(payload.keys())[:12]}


def _confirmation_required(operation: str, values: dict[str, object]) -> dict[str, object]:
    return {
        "confirmation_required": True,
        "operation": operation,
        **values,
        "message": f"Ask the user to confirm {operation}, then retry with confirm=true.",
    }


def _safe_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))

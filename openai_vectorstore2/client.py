from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Literal

from backend.app.bootstrap import AppServices, create_services
from backend.app.core.config import AppSettings, get_settings
from backend.app.schemas import (
    ActionResponse,
    BranchSearchRequest,
    BranchSearchResponse,
    FileListResponse,
    FreeformRequest,
    IngestFinalizeResponse,
    LibraryCreateRequest,
    LibraryListResponse,
    LibrarySummary,
    QaRequest,
    ResearchLibraryBuildRequest,
    ResearchLibraryBuildResponse,
    SearchRequest,
    SearchResponse,
    TagMutationResponse,
    TagSummary,
    TaskDetail,
)
from backend.app.schemas.records import LibraryVisibility

ProgressSink = Callable[["ProgressEvent"], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    stage: str
    status: str
    message: str
    task_id: str | None = None
    source_id: str | None = None
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RAGStreamEvent:
    progress: ProgressEvent
    result: IngestFinalizeResponse | ResearchLibraryBuildResponse | None = None


class RAGLibrary:
    """Small public facade for the app's OpenAI-backed RAG/IR service layer."""

    def __init__(
        self,
        *,
        services: AppServices,
        clerk_user_id: str,
        origin_surface: str = "system",
    ) -> None:
        self._services = services
        self._clerk_user_id = clerk_user_id
        self._origin_surface = origin_surface

    @classmethod
    def from_settings(
        cls,
        settings: AppSettings | None = None,
        *,
        clerk_user_id: str = "local-dev",
        origin_surface: str = "system",
    ) -> RAGLibrary:
        return cls(
            services=create_services(settings or get_settings()),
            clerk_user_id=clerk_user_id,
            origin_surface=origin_surface,
        )

    async def __aenter__(self) -> RAGLibrary:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        await self.close()

    async def close(self) -> None:
        await self._services.close()

    async def list_libraries(self) -> LibraryListResponse:
        return await self._services.sources.list_libraries(clerk_user_id=self._clerk_user_id)

    async def create_library(
        self,
        *,
        title: str,
        description: str | None = None,
        visibility: LibraryVisibility = "public",
        slug: str | None = None,
    ) -> LibrarySummary:
        request = LibraryCreateRequest(title=title, description=description, visibility=visibility, slug=slug)
        return await self._services.sources.create_library(clerk_user_id=self._clerk_user_id, payload=request)

    async def list_sources(
        self,
        *,
        library_id: str | None = None,
        query: str | None = None,
        tag_ids: list[str] | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> FileListResponse:
        return await self._services.sources.list_sources(
            clerk_user_id=self._clerk_user_id,
            library_id=library_id,
            query=query,
            tag_ids=tag_ids or [],
            tag_match_mode="all",
            page=page,
            page_size=page_size,
        )

    async def list_tags(self, *, library_id: str | None = None) -> list[TagSummary]:
        return await self._services.sources.list_tags(clerk_user_id=self._clerk_user_id, library_id=library_id)

    async def create_tag(self, *, name: str, color: str | None = None) -> TagMutationResponse:
        return await self._services.sources.create_tag(clerk_user_id=self._clerk_user_id, name=name, color=color)

    async def ingest_path(
        self,
        path: str | Path,
        *,
        media_type: str | None = None,
        tag_ids: list[str] | None = None,
        user_guidance: str | None = None,
        folder_id: str | None = None,
        virtual_name: str | None = None,
        library_id: str | None = None,
        metadata: dict[str, object] | None = None,
        wait: bool = False,
        progress: ProgressSink | None = None,
    ) -> IngestFinalizeResponse:
        source_path = Path(path)
        payload = source_path.read_bytes()
        return await self.ingest_bytes(
            filename=source_path.name,
            payload=payload,
            media_type=media_type,
            tag_ids=tag_ids,
            user_guidance=user_guidance,
            folder_id=folder_id,
            virtual_name=virtual_name,
            library_id=library_id,
            metadata=metadata,
            wait=wait,
            progress=progress,
        )

    async def ingest_text(
        self,
        text: str,
        *,
        filename: str = "source.txt",
        media_type: str = "text/plain",
        tag_ids: list[str] | None = None,
        user_guidance: str | None = None,
        folder_id: str | None = None,
        virtual_name: str | None = None,
        library_id: str | None = None,
        metadata: dict[str, object] | None = None,
        wait: bool = False,
        progress: ProgressSink | None = None,
    ) -> IngestFinalizeResponse:
        return await self.ingest_bytes(
            filename=filename,
            payload=text.encode("utf-8"),
            media_type=media_type,
            tag_ids=tag_ids,
            user_guidance=user_guidance,
            folder_id=folder_id,
            virtual_name=virtual_name,
            library_id=library_id,
            metadata=metadata,
            wait=wait,
            progress=progress,
        )

    async def ingest_bytes(
        self,
        *,
        filename: str,
        payload: bytes,
        media_type: str | None = None,
        tag_ids: list[str] | None = None,
        user_guidance: str | None = None,
        folder_id: str | None = None,
        virtual_name: str | None = None,
        library_id: str | None = None,
        metadata: dict[str, object] | None = None,
        wait: bool = False,
        progress: ProgressSink | None = None,
    ) -> IngestFinalizeResponse:
        await _emit(
            progress,
            ProgressEvent(
                stage="ingest",
                status="started",
                message=f"Queueing ingest for {filename}.",
                payload={"filename": filename, "byte_size": len(payload)},
            ),
        )
        response = await self._services.sources.ingest_source(
            clerk_user_id=self._clerk_user_id,
            filename=filename,
            declared_media_type=media_type,
            payload=payload,
            tag_ids=tag_ids or [],
            user_guidance=user_guidance,
            origin_surface=self._origin_surface,
            folder_id=folder_id,
            virtual_name=virtual_name,
            library_id=library_id,
            metadata=metadata,
        )
        await _emit(
            progress,
            ProgressEvent(
                stage="ingest",
                status="queued",
                message=f"Queued ingest for {response.source.display_title}.",
                task_id=response.task.id if response.task is not None else None,
                source_id=response.source.id,
            ),
        )
        if wait and response.task is not None:
            await self.wait_for_task(response.task.id, progress=progress)
        return response

    async def ingest_path_events(
        self,
        path: str | Path,
        *,
        media_type: str | None = None,
        tag_ids: list[str] | None = None,
        user_guidance: str | None = None,
        folder_id: str | None = None,
        virtual_name: str | None = None,
        library_id: str | None = None,
        metadata: dict[str, object] | None = None,
        wait: bool = True,
    ) -> AsyncIterator[RAGStreamEvent]:
        queue: asyncio.Queue[ProgressEvent] = asyncio.Queue()

        async def progress(event: ProgressEvent) -> None:
            await queue.put(event)

        task = asyncio.create_task(
            self.ingest_path(
                path,
                media_type=media_type,
                tag_ids=tag_ids,
                user_guidance=user_guidance,
                folder_id=folder_id,
                virtual_name=virtual_name,
                library_id=library_id,
                metadata=metadata,
                wait=wait,
                progress=progress,
            )
        )
        while not task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield RAGStreamEvent(progress=event)
            except TimeoutError:
                continue
        result = await task
        yield RAGStreamEvent(
            progress=ProgressEvent(
                stage="ingest",
                status="completed",
                message=f"Ingest returned source {result.source.id}.",
                task_id=result.task.id if result.task is not None else None,
                source_id=result.source.id,
            ),
            result=result,
        )

    async def search(
        self,
        query: str,
        *,
        library_id: str | None = None,
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        max_results: int = 8,
    ) -> SearchResponse:
        request = SearchRequest(
            query=query,
            library_id=library_id,
            selected_source_ids=selected_source_ids or [],
            tag_ids=tag_ids or [],
            max_results=max_results,
        )
        return await self._services.sources.search(
            clerk_user_id=self._clerk_user_id,
            request=request,
            origin_surface=self._origin_surface,
        )

    async def branch_search(
        self,
        query: str,
        *,
        library_id: str | None = None,
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        max_width: int = 3,
        descend: int = 2,
    ) -> BranchSearchResponse:
        request = BranchSearchRequest(
            query=query,
            library_id=library_id,
            selected_source_ids=selected_source_ids or [],
            tag_ids=tag_ids or [],
            max_width=max_width,
            descend=descend,
        )
        return await self._services.sources.branch_search(clerk_user_id=self._clerk_user_id, request=request)

    async def qa(
        self,
        prompt: str,
        *,
        library_id: str | None = None,
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        max_results: int = 8,
    ) -> ActionResponse:
        request = QaRequest(
            prompt=prompt,
            library_id=library_id,
            selected_source_ids=selected_source_ids or [],
            tag_ids=tag_ids or [],
            max_results=max_results,
        )
        return await self._services.actions.qa(
            clerk_user_id=self._clerk_user_id,
            payload=request,
            origin_surface=self._origin_surface,
        )

    async def write(
        self,
        prompt: str,
        *,
        mode: Literal["grounded", "creative"] = "grounded",
        library_id: str | None = None,
        selected_source_ids: list[str] | None = None,
        tag_ids: list[str] | None = None,
        max_results: int = 8,
    ) -> ActionResponse:
        request = FreeformRequest(
            prompt=prompt,
            mode=mode,
            library_id=library_id,
            selected_source_ids=selected_source_ids or [],
            tag_ids=tag_ids or [],
            max_results=max_results,
        )
        return await self._services.actions.freeform(
            clerk_user_id=self._clerk_user_id,
            payload=request,
            origin_surface=self._origin_surface,
        )

    async def build_research_library(
        self,
        query: str,
        *,
        auto_ingest: bool = True,
        max_sources: int = 12,
        tag_ids: list[str] | None = None,
        folder_name: str | None = None,
        progress: ProgressSink | None = None,
    ) -> ResearchLibraryBuildResponse:
        await _emit(
            progress,
            ProgressEvent(stage="research", status="started", message=f"Starting research library build: {query}"),
        )

        async def research_progress(stage: str, message: str) -> None:
            await _emit(progress, ProgressEvent(stage=stage, status="running", message=message))

        response = await self._services.research.build_library(
            clerk_user_id=self._clerk_user_id,
            payload=ResearchLibraryBuildRequest(
                query=query,
                auto_ingest=auto_ingest,
                max_sources=max_sources,
                tag_ids=tag_ids or [],
                folder_name=folder_name,
            ),
            origin_surface=self._origin_surface,
            progress_callback=research_progress,
        )
        await _emit(
            progress,
            ProgressEvent(
                stage="research",
                status="completed",
                message=f"Research library build returned {len(response.candidates)} candidates.",
                task_id=response.task.id,
            ),
        )
        return response

    async def wait_for_task(
        self,
        task_id: str,
        *,
        poll_interval_seconds: float = 1.0,
        timeout_seconds: float | None = None,
        progress: ProgressSink | None = None,
    ) -> TaskDetail:
        deadline = monotonic() + timeout_seconds if timeout_seconds is not None else None
        last_status: str | None = None
        while True:
            task = await self._services.actions.get_task(clerk_user_id=self._clerk_user_id, task_id=task_id)
            if task.status != last_status:
                await _emit(
                    progress,
                    ProgressEvent(
                        stage=str(task.kind),
                        status=str(task.status),
                        message=f"Task {task.id} is {task.status}.",
                        task_id=task.id,
                        source_id=task.source_file_id,
                        payload={"kind": task.kind, "state": task.state_json},
                    ),
                )
                last_status = task.status
            if task.status in {"completed", "failed", "cancelled"}:
                return task
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for task {task_id}.")
            await asyncio.sleep(max(0.1, poll_interval_seconds))


def create_rag_library(
    settings: AppSettings | None = None,
    *,
    clerk_user_id: str = "local-dev",
    origin_surface: str = "system",
) -> RAGLibrary:
    return RAGLibrary.from_settings(settings, clerk_user_id=clerk_user_id, origin_surface=origin_surface)


async def _emit(progress: ProgressSink | None, event: ProgressEvent) -> None:
    if progress is None:
        return
    result = progress(event)
    if result is not None:
        await result

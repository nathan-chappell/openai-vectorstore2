from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import mimetypes
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from time import perf_counter
from typing import Any, Literal, cast

from openai.types.file_purpose import FilePurpose
from openai.types.shared_params.comparison_filter import ComparisonFilter
from openai.types.shared_params.compound_filter import CompoundFilter
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from backend.app.core.config import AppSettings
from backend.app.db.session import DatabaseManager
from backend.app.integrations.openai_gateway import OpenAIGateway
from backend.app.models import AppTask, AppUser, SemanticChunk, SourceFile, SourceTagLink, Tag, UserLibrary
from backend.app.schemas import (
    BranchSearchLevel,
    BranchSearchRequest,
    BranchSearchResponse,
    ChunkHit,
    ChunkLocator,
    ChunkSummary,
    FileListResponse,
    IngestFinalizeResponse,
    LibrarySourceDetail,
    LibrarySourceSummary,
    SearchRequest,
    SearchResponse,
    SemanticChunkDraft,
    SemanticSplitResult,
    SplitPreviewResponse,
    SourceKind,
    SourceStatus,
    TagMatchMode,
    TagSummary,
    TaskStatus,
    TaskSummary,
)
from backend.app.services.auth import AuthService
from backend.app.storage import StorageService

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {
    ".c",
    ".cpp",
    ".css",
    ".csv",
    ".go",
    ".html",
    ".htm",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".markdown",
    ".py",
    ".rb",
    ".rs",
    ".rst",
    ".scss",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

TAG_SLOT_COUNT = 8
VECTOR_ATTRIBUTES_VERSION = 1
PDF_PAGE_BLOCK_RE = re.compile(r"(?ms)^\[page (?P<page>\d+)\]\n(?P<text>.*?)(?=^\[page \d+\]\n|\Z)")


@dataclass(frozen=True, slots=True)
class PdfTextBatch:
    start_page: int | None
    end_page: int | None
    text: str

    @property
    def label(self) -> str:
        if self.start_page is None or self.end_page is None:
            return "PDF text"
        if self.start_page == self.end_page:
            return f"page {self.start_page}"
        return f"pages {self.start_page}-{self.end_page}"


class SourceService:
    """Own source ingestion, semantic chunk publication, and retrieval."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        database: DatabaseManager,
        auth: AuthService,
        storage: StorageService,
        openai: OpenAIGateway,
    ) -> None:
        self._settings = settings
        self._database = database
        self._auth = auth
        self._storage = storage
        self._openai = openai
        self._ingest_semaphore: asyncio.Semaphore | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        if not self._background_tasks:
            return
        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _ingest_runner_semaphore(self) -> asyncio.Semaphore:
        if self._ingest_semaphore is None:
            self._ingest_semaphore = asyncio.Semaphore(max(1, self._settings.task_runner_max_concurrency))
        return self._ingest_semaphore

    def _schedule_ingest_job(
        self,
        *,
        clerk_user_id: str,
        source_id: str,
        task_id: str,
        tag_ids: list[str],
        user_guidance: str | None,
    ) -> None:
        if self._closed:
            raise RuntimeError("Source service is closed.")
        task = asyncio.create_task(
            self._run_ingest_job(
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                task_id=task_id,
                tag_ids=list(tag_ids),
                user_guidance=user_guidance,
            ),
            name=f"ingest-source-{source_id}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _schedule_resplit_job(
        self,
        *,
        clerk_user_id: str,
        source_id: str,
        task_id: str,
        tag_ids: list[str],
        user_guidance: str | None,
    ) -> None:
        if self._closed:
            raise RuntimeError("Source service is closed.")
        task = asyncio.create_task(
            self._run_resplit_job(
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                task_id=task_id,
                tag_ids=list(tag_ids),
                user_guidance=user_guidance,
            ),
            name=f"resplit-source-{source_id}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _schedule_reindex_job(
        self,
        *,
        clerk_user_id: str,
        source_id: str,
        task_id: str,
    ) -> None:
        if self._closed:
            raise RuntimeError("Source service is closed.")
        task = asyncio.create_task(
            self._run_reindex_job(
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                task_id=task_id,
            ),
            name=f"reindex-source-{source_id}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_ingest_job(
        self,
        *,
        clerk_user_id: str,
        source_id: str,
        task_id: str,
        tag_ids: list[str],
        user_guidance: str | None,
    ) -> None:
        try:
            async with self._ingest_runner_semaphore():
                await self._execute_ingest_job(
                    clerk_user_id=clerk_user_id,
                    source_id=source_id,
                    task_id=task_id,
                    tag_ids=tag_ids,
                    user_guidance=user_guidance,
                )
        except asyncio.CancelledError:
            await self._cancel_ingest_job(clerk_user_id=clerk_user_id, source_id=source_id, task_id=task_id)
            raise
        except Exception:
            logger.exception(
                "source_ingest_background_task_crashed clerk_user_id=%s source_id=%s task_id=%s",
                clerk_user_id,
                source_id,
                task_id,
            )

    async def _run_resplit_job(
        self,
        *,
        clerk_user_id: str,
        source_id: str,
        task_id: str,
        tag_ids: list[str],
        user_guidance: str | None,
    ) -> None:
        try:
            async with self._ingest_runner_semaphore():
                await self._execute_resplit_job(
                    clerk_user_id=clerk_user_id,
                    source_id=source_id,
                    task_id=task_id,
                    tag_ids=tag_ids,
                    user_guidance=user_guidance,
                )
        except asyncio.CancelledError:
            await self._cancel_resplit_job(clerk_user_id=clerk_user_id, source_id=source_id, task_id=task_id)
            raise
        except Exception:
            logger.exception(
                "source_resplit_background_task_crashed clerk_user_id=%s source_id=%s task_id=%s",
                clerk_user_id,
                source_id,
                task_id,
            )

    async def _run_reindex_job(
        self,
        *,
        clerk_user_id: str,
        source_id: str,
        task_id: str,
    ) -> None:
        try:
            async with self._ingest_runner_semaphore():
                await self._execute_reindex_job(
                    clerk_user_id=clerk_user_id,
                    source_id=source_id,
                    task_id=task_id,
                )
        except asyncio.CancelledError:
            await self._cancel_reindex_job(clerk_user_id=clerk_user_id, source_id=source_id, task_id=task_id)
            raise
        except Exception:
            logger.exception(
                "source_reindex_background_task_crashed clerk_user_id=%s source_id=%s task_id=%s",
                clerk_user_id,
                source_id,
                task_id,
            )

    async def ensure_app_user(self, session: Any, *, clerk_user_id: str) -> AppUser:
        existing = await session.scalar(select(AppUser).where(AppUser.clerk_user_id == clerk_user_id))
        record = await self._auth.get_user_record(clerk_user_id)
        now = _utcnow()
        if existing is None:
            existing = AppUser(
                clerk_user_id=record.clerk_user_id,
                primary_email=record.primary_email,
                display_name=record.display_name,
                active=record.active,
                role=record.role,
                last_seen_at=now,
            )
            session.add(existing)
        else:
            existing.primary_email = record.primary_email
            existing.display_name = record.display_name
            existing.active = record.active
            existing.role = record.role
            existing.last_seen_at = now
        await session.commit()
        await session.refresh(existing)
        return existing

    async def list_sources(
        self,
        *,
        clerk_user_id: str,
        query: str | None,
        tag_ids: list[str],
        tag_match_mode: TagMatchMode,
        page: int,
        page_size: int,
    ) -> FileListResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            library = await self._library_for_user(session, app_user=app_user)
            selected_tag_ids = set(tag_ids)
            normalized_query = query.casefold().strip() if isinstance(query, str) else ""
            sources = sorted(library.sources, key=lambda source: source.created_at, reverse=True)
            if normalized_query:
                sources = [
                    source
                    for source in sources
                    if normalized_query in source.display_title.casefold()
                    or normalized_query in source.original_filename.casefold()
                    or normalized_query in source.media_type.casefold()
                    or normalized_query in source.source_kind.casefold()
                ]
            if selected_tag_ids:

                def matches_tags(source: SourceFile) -> bool:
                    source_tag_ids = {link.tag_id for link in source.tag_links}
                    return (
                        selected_tag_ids.issubset(source_tag_ids)
                        if tag_match_mode == "all"
                        else bool(selected_tag_ids & source_tag_ids)
                    )

                sources = [source for source in sources if matches_tags(source)]

            start = max(page - 1, 0) * page_size
            end = start + page_size
            page_sources = sources[start:end]
            return FileListResponse(
                sources=[self._source_summary(source) for source in page_sources],
                total_count=len(sources),
                page=page,
                page_size=page_size,
                has_more=end < len(sources),
            )

    async def list_tags(self, *, clerk_user_id: str) -> list[TagSummary]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            library = await self._library_for_user(session, app_user=app_user)
            return [self._tag_summary(tag) for tag in sorted(library.tags, key=lambda item: item.name.casefold())]

    async def get_source(self, *, clerk_user_id: str, source_id: str) -> LibrarySourceDetail:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            source = await self._source_for_user(session, clerk_user_id=clerk_user_id, source_id=source_id)
            return self._source_detail(source)

    async def read_source_bytes(self, *, clerk_user_id: str, source_id: str) -> tuple[LibrarySourceDetail, bytes]:
        detail = await self.get_source(clerk_user_id=clerk_user_id, source_id=source_id)
        payload = await self._storage.get_bytes(key=detail.storage_key)
        return detail, payload

    async def delete_source(self, *, clerk_user_id: str, source_id: str) -> str:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            source = await self._source_for_user(session, clerk_user_id=clerk_user_id, source_id=source_id)
            cleanup_result = await self._delete_openai_files_for_source(source=source)
            await self._storage.delete_object(key=source.storage_key)
            await session.execute(
                update(AppTask)
                .where(AppTask.source_file_id == source.id)
                .values(source_file_id=None, updated_at=_utcnow())
            )
            await session.delete(source)
            await session.commit()
            logger.info(
                "source_deleted clerk_user_id=%s source_id=%s openai_chunk_files=%s openai_original_file=%s",
                clerk_user_id,
                source_id,
                cleanup_result["chunk_file_count"],
                cleanup_result["original_file_deleted"],
            )
        return source_id

    async def ingest_source(
        self,
        *,
        clerk_user_id: str,
        filename: str,
        declared_media_type: str | None,
        payload: bytes,
        tag_ids: list[str],
        user_guidance: str | None,
        origin_surface: str,
    ) -> IngestFinalizeResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to ingest sources.")
            library = await self._library_for_user(session, app_user=app_user)
            await self._ensure_vector_store(session, library=library, app_user=app_user)

            media_type = guess_media_type(filename=filename, declared_media_type=declared_media_type)
            source_kind = classify_source_kind(filename=filename, media_type=media_type)
            stored = await self._storage.put_bytes(
                scope="sources",
                filename=filename,
                media_type=media_type,
                payload=payload,
            )
            display_title = await self._unique_source_title(
                session,
                library_id=library.id,
                base_title=Path(filename).stem or filename,
            )
            source = SourceFile(
                library_id=library.id,
                uploaded_by_user_id=app_user.id,
                display_title=display_title,
                original_filename=filename,
                media_type=media_type,
                source_kind=source_kind,
                status="processing",
                byte_size=stored.byte_size,
                storage_provider=stored.provider,
                storage_key=stored.key,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(source)
            await session.flush()
            task = AppTask(
                user_id=app_user.id,
                library_id=library.id,
                kind="ingest",
                status="queued",
                title=f"Ingest: {display_title}",
                origin_surface=origin_surface,
                source_file_id=source.id,
                input_json={
                    "filename": filename,
                    "declared_media_type": declared_media_type,
                    "media_type": media_type,
                    "byte_size": len(payload),
                    "tag_ids": tag_ids,
                    "user_guidance": user_guidance,
                },
                state_json={"stage": "queued", "source_id": source.id},
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(task)
            await session.commit()
            await session.refresh(source)
            await session.refresh(task)
            logger.info(
                "source_ingest_queued clerk_user_id=%s source_id=%s task_id=%s kind=%s bytes=%s",
                clerk_user_id,
                source.id,
                task.id,
                source.source_kind,
                source.byte_size,
            )
            self._schedule_ingest_job(
                clerk_user_id=clerk_user_id,
                source_id=source.id,
                task_id=task.id,
                tag_ids=tag_ids,
                user_guidance=user_guidance,
            )
            return IngestFinalizeResponse(source=self._source_summary(source), task=_task_summary(task))

    async def preview_semantic_split(
        self,
        *,
        clerk_user_id: str,
        filename: str,
        declared_media_type: str | None,
        payload: bytes,
        user_guidance: str | None,
    ) -> SplitPreviewResponse:
        preview_started_at = perf_counter()
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to preview splits.")

        media_type = guess_media_type(filename=filename, declared_media_type=declared_media_type)
        source_kind = classify_source_kind(filename=filename, media_type=media_type)
        source_title = Path(filename).stem or filename
        extracted_text, strategy_hint = await self._extract_searchable_text(
            filename=filename,
            source_kind=source_kind,
            media_type=media_type,
            payload=payload,
        )
        split_result = await self._split_semantic_text(
            source_id=None,
            source_title=source_title,
            source_kind=source_kind,
            extracted_text=extracted_text,
            user_guidance=user_guidance,
        )
        normalized_split = SemanticSplitResult(
            strategy_label=split_result.strategy_label,
            tags=_dedupe_text_values(split_result.tags),
            chunks=_normalize_chunk_drafts(split_result.chunks, fallback_text=extracted_text),
        )
        logger.info(
            "semantic_split_previewed clerk_user_id=%s filename=%s kind=%s strategy_hint=%s chunks=%s duration_ms=%.1f",
            clerk_user_id,
            filename,
            source_kind,
            strategy_hint,
            len(normalized_split.chunks),
            (perf_counter() - preview_started_at) * 1000,
        )
        return SplitPreviewResponse(
            filename=filename,
            media_type=media_type,
            source_kind=source_kind,
            byte_size=len(payload),
            ingest_strategy=strategy_hint,
            extracted_character_count=len(extracted_text),
            split=normalized_split,
            previewed_at=_utcnow(),
        )

    async def resplit_source(
        self,
        *,
        clerk_user_id: str,
        source_id: str,
        tag_ids: list[str] | None,
        user_guidance: str | None,
        origin_surface: str,
        origin_thread_id: str | None = None,
    ) -> IngestFinalizeResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to re-split sources.")
            source = await self._source_for_user(session, clerk_user_id=clerk_user_id, source_id=source_id)
            if source.status == "processing":
                raise ValueError("Wait for the current source processing task to finish before re-splitting.")
            library = source.library
            await self._ensure_vector_store(session, library=library, app_user=app_user)

            raw_tag_ids = list(tag_ids) if tag_ids is not None else [link.tag_id for link in source.tag_links]
            selected_tag_ids = list(dict.fromkeys(raw_tag_ids))
            selected_tags = await self._tags_by_ids(session, library_id=library.id, tag_ids=selected_tag_ids)
            replaced_chunk_count = len(source.chunks)
            source.tag_links = [SourceTagLink(source_file_id=source.id, tag_id=tag.id) for tag in selected_tags]
            previous_status = source.status
            previous_error_message = source.error_message
            source.status = "processing"
            source.error_message = None
            source.updated_at = _utcnow()
            task = AppTask(
                user_id=app_user.id,
                library_id=library.id,
                kind="resplit",
                status="queued",
                title=f"Re-split: {source.display_title}",
                origin_surface=origin_surface,
                origin_thread_id=origin_thread_id,
                source_file_id=source.id,
                input_json={
                    "source_id": source.id,
                    "filename": source.original_filename,
                    "media_type": source.media_type,
                    "tag_ids": selected_tag_ids,
                    "user_guidance": user_guidance,
                    "replaced_chunk_count": replaced_chunk_count,
                    "previous_status": previous_status,
                    "previous_error_message": previous_error_message,
                },
                state_json={"stage": "queued", "source_id": source.id},
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(task)
            await session.commit()
            await session.refresh(source)
            await session.refresh(task)
            logger.info(
                "source_resplit_queued clerk_user_id=%s source_id=%s task_id=%s replaced_chunks=%s",
                clerk_user_id,
                source.id,
                task.id,
                replaced_chunk_count,
            )
            self._schedule_resplit_job(
                clerk_user_id=clerk_user_id,
                source_id=source.id,
                task_id=task.id,
                tag_ids=selected_tag_ids,
                user_guidance=user_guidance,
            )
            return IngestFinalizeResponse(source=self._source_summary(source), task=_task_summary(task))

    async def update_source_tags(
        self,
        *,
        clerk_user_id: str,
        source_id: str,
        tag_ids: list[str],
        origin_surface: str,
        origin_thread_id: str | None = None,
    ) -> IngestFinalizeResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to update source tags.")
            source = await self._source_for_user(session, clerk_user_id=clerk_user_id, source_id=source_id)
            if source.status == "processing":
                raise ValueError("Wait for the current source processing task to finish before updating tags.")
            library = source.library
            await self._ensure_vector_store(session, library=library, app_user=app_user)

            selected_tag_ids = list(dict.fromkeys(tag_ids))
            selected_tags = await self._tags_by_ids(session, library_id=library.id, tag_ids=selected_tag_ids)
            previous_status = source.status
            previous_error_message = source.error_message
            previous_tag_ids = [link.tag_id for link in source.tag_links]
            chunk_count = len(source.chunks)
            source.tag_links = [SourceTagLink(source_file_id=source.id, tag_id=tag.id) for tag in selected_tags]
            if chunk_count:
                source.status = "processing"
                source.error_message = None
            source.updated_at = _utcnow()
            task = AppTask(
                user_id=app_user.id,
                library_id=library.id,
                kind="reindex",
                status="queued",
                title=f"Reindex tags: {source.display_title}",
                origin_surface=origin_surface,
                origin_thread_id=origin_thread_id,
                source_file_id=source.id,
                input_json={
                    "source_id": source.id,
                    "tag_ids": selected_tag_ids,
                    "previous_tag_ids": previous_tag_ids,
                    "previous_status": previous_status,
                    "previous_error_message": previous_error_message,
                    "chunk_count": chunk_count,
                },
                state_json={"stage": "queued", "source_id": source.id},
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(task)
            await session.commit()
            await session.refresh(source)
            await session.refresh(task)
            logger.info(
                "source_reindex_queued clerk_user_id=%s source_id=%s task_id=%s chunks=%s tags=%s",
                clerk_user_id,
                source.id,
                task.id,
                chunk_count,
                len(selected_tag_ids),
            )
            self._schedule_reindex_job(clerk_user_id=clerk_user_id, source_id=source.id, task_id=task.id)
            return IngestFinalizeResponse(source=self._source_summary(source), task=_task_summary(task))

    async def _execute_ingest_job(
        self,
        *,
        clerk_user_id: str,
        source_id: str,
        task_id: str,
        tag_ids: list[str],
        user_guidance: str | None,
    ) -> None:
        ingest_started_at = perf_counter()
        await self._database.ensure_ready()
        async with self._database.session() as session:
            source = await self._source_by_id(session, source_id=source_id)
            task = await self._task_by_id(session, task_id=task_id)
            library = source.library
            now = _utcnow()
            task.status = "running"
            task.started_at = task.started_at or now
            task.state_json = {"stage": "validating_tags", "source_id": source.id}
            task.updated_at = now
            await session.commit()
            logger.info(
                "source_ingest_started clerk_user_id=%s source_id=%s task_id=%s kind=%s bytes=%s",
                clerk_user_id,
                source.id,
                task.id,
                source.source_kind,
                source.byte_size,
            )

            try:
                selected_tags = await self._tags_by_ids(session, library_id=library.id, tag_ids=tag_ids)
                source.tag_links = [SourceTagLink(source_file_id=source.id, tag_id=tag.id) for tag in selected_tags]
                task.state_json = {"stage": "reading_source_payload", "source_id": source.id}
                task.updated_at = _utcnow()
                await session.commit()

                payload = await self._storage.get_bytes(key=source.storage_key)
                source_kind = cast(SourceKind, source.source_kind)
                task.state_json = {"stage": "uploading_original_file", "source_id": source.id}
                task.updated_at = _utcnow()
                await session.commit()
                source.openai_original_file_id = await self._openai.upload_file_bytes(
                    filename=source.original_filename,
                    payload=payload,
                    purpose=_openai_file_purpose(source_kind=source_kind),
                )
                task.state_json = {
                    "stage": "extracting_text",
                    "source_id": source.id,
                    "openai_original_file_id": source.openai_original_file_id,
                }
                task.updated_at = _utcnow()
                await session.commit()

                extracted_text, strategy_hint = await self._extract_searchable_text(
                    filename=source.original_filename,
                    source_kind=source_kind,
                    media_type=source.media_type,
                    payload=payload,
                )
                source.ingest_strategy = strategy_hint
                task.state_json = {
                    "stage": "splitting_semantically",
                    "source_id": source.id,
                    "strategy_hint": strategy_hint,
                    "extracted_character_count": len(extracted_text),
                }
                task.updated_at = _utcnow()
                await session.commit()
                split_started_at = perf_counter()
                split_result = await self._split_semantically(
                    source=source,
                    extracted_text=extracted_text,
                    user_guidance=user_guidance,
                )
                logger.info(
                    "source_ingest_split_completed clerk_user_id=%s source_id=%s task_id=%s tags=%s chunks=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    len(split_result.tags),
                    len(split_result.chunks),
                    (perf_counter() - split_started_at) * 1000,
                )
                auto_tags = await self._ensure_auto_tags(session, library=library, tag_names=split_result.tags)
                merged_tags = _merge_tags([*selected_tags, *auto_tags])
                source.tag_links = [SourceTagLink(source_file_id=source.id, tag_id=tag.id) for tag in merged_tags]

                normalized_chunks = _normalize_chunk_drafts(split_result.chunks, fallback_text=extracted_text)
                publish_started_at = perf_counter()
                task.state_json = {
                    "stage": "publishing_chunks",
                    "source_id": source.id,
                    "chunk_count": len(normalized_chunks),
                    "published_chunk_count": 0,
                }
                task.updated_at = _utcnow()
                await session.commit()
                for draft in normalized_chunks:
                    chunk = SemanticChunk(
                        source_file_id=source.id,
                        sequence=draft.sequence,
                        title=draft.title,
                        summary=draft.summary,
                        text_content=draft.text,
                        keywords_json=draft.keywords,
                        locator_type=draft.locator.type,
                        start_page=draft.locator.start_page,
                        end_page=draft.locator.end_page,
                        start_line=draft.locator.start_line,
                        end_line=draft.locator.end_line,
                        start_seconds=draft.locator.start_seconds,
                        end_seconds=draft.locator.end_seconds,
                        strategy_label=draft.strategy_label,
                        status="processing",
                        created_at=_utcnow(),
                        updated_at=_utcnow(),
                    )
                    session.add(chunk)
                    await session.flush()
                    attributes = build_vector_attributes(
                        library_id=library.id,
                        source_id=source.id,
                        chunk_id=chunk.id,
                        source_kind=source.source_kind,
                        content_kind="semantic_chunk",
                        title=chunk.title,
                        tag_slugs=[tag.slug for tag in merged_tags],
                    )
                    chunk.vector_attributes_json = {key: value for key, value in attributes.items()}
                    chunk.openai_file_id = await self._openai.attach_chunk_to_vector_store(
                        vector_store_id=library.openai_vector_store_id or "",
                        filename=f"{source.original_filename}.chunk-{chunk.sequence}.md",
                        text_content=render_chunk_markdown(source=source, chunk=chunk),
                        attributes=attributes,
                    )
                    chunk.status = "ready"
                    chunk.updated_at = _utcnow()
                    task.state_json = {
                        "stage": "publishing_chunks",
                        "source_id": source.id,
                        "chunk_count": len(normalized_chunks),
                        "published_chunk_count": draft.sequence,
                        "last_openai_file_id": chunk.openai_file_id,
                    }
                    task.updated_at = _utcnow()
                    await session.commit()
                logger.info(
                    "source_ingest_chunks_published clerk_user_id=%s source_id=%s task_id=%s chunks=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    len(normalized_chunks),
                    (perf_counter() - publish_started_at) * 1000,
                )

                source.status = "ready"
                source.error_message = None
                source.updated_at = _utcnow()
                library.updated_at = _utcnow()
                task.status = "completed"
                task.state_json = {
                    "stage": "completed",
                    "source_id": source.id,
                    "chunk_count": len(normalized_chunks),
                    "tag_count": len(merged_tags),
                }
                task.result_json = {"source_id": source.id, "chunk_count": len(normalized_chunks)}
                task.error_message = None
                task.completed_at = _utcnow()
                task.updated_at = _utcnow()
                await session.commit()
                logger.info(
                    "source_ingested clerk_user_id=%s source_id=%s task_id=%s kind=%s chunks=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    source.source_kind,
                    len(normalized_chunks),
                    (perf_counter() - ingest_started_at) * 1000,
                )
            except Exception as exc:
                source = await self._source_by_id(session, source_id=source_id, populate_existing=True)
                task = await self._task_by_id(session, task_id=task_id)
                try:
                    cleanup_result = await self._delete_openai_files_for_source(source=source)
                except Exception as cleanup_error:
                    cleanup_result = {"chunk_file_count": 0, "original_file_deleted": False}
                    logger.warning(
                        "source_ingest_cleanup_failed clerk_user_id=%s source_id=%s cleanup_error=%s",
                        clerk_user_id,
                        source.id,
                        cleanup_error,
                    )
                else:
                    source.openai_original_file_id = None
                    for chunk in source.chunks:
                        chunk.openai_file_id = None
                source.status = "failed"
                source.error_message = str(exc)
                source.updated_at = _utcnow()
                task.status = "failed"
                task.state_json = {
                    "stage": "failed",
                    "source_id": source.id,
                    "cleanup": cleanup_result,
                }
                task.error_message = str(exc)
                task.completed_at = _utcnow()
                task.updated_at = _utcnow()
                await session.commit()
                logger.error(
                    "source_ingest_failed clerk_user_id=%s source_id=%s task_id=%s error=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    exc,
                    (perf_counter() - ingest_started_at) * 1000,
                )

    async def _cancel_ingest_job(self, *, clerk_user_id: str, source_id: str, task_id: str) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            try:
                source = await self._source_by_id(session, source_id=source_id, populate_existing=True)
                task = await self._task_by_id(session, task_id=task_id)
            except FileNotFoundError:
                logger.warning(
                    "source_ingest_cancel_missing_record clerk_user_id=%s source_id=%s task_id=%s",
                    clerk_user_id,
                    source_id,
                    task_id,
                )
                return

            try:
                cleanup_result = await self._delete_openai_files_for_source(source=source)
            except Exception as cleanup_error:
                cleanup_result = {"chunk_file_count": 0, "original_file_deleted": False}
                logger.warning(
                    "source_ingest_cancel_cleanup_failed clerk_user_id=%s source_id=%s cleanup_error=%s",
                    clerk_user_id,
                    source.id,
                    cleanup_error,
                )
            else:
                source.openai_original_file_id = None
                for chunk in source.chunks:
                    chunk.openai_file_id = None
            source.status = "failed"
            source.error_message = "Ingest cancelled during shutdown."
            source.updated_at = _utcnow()
            task.status = "cancelled"
            task.state_json = {
                "stage": "cancelled",
                "source_id": source.id,
                "cleanup": cleanup_result,
            }
            task.error_message = "Ingest cancelled during shutdown."
            task.completed_at = _utcnow()
            task.updated_at = _utcnow()
            await session.commit()
            logger.warning(
                "source_ingest_cancelled clerk_user_id=%s source_id=%s task_id=%s",
                clerk_user_id,
                source.id,
                task.id,
            )

    async def _execute_resplit_job(
        self,
        *,
        clerk_user_id: str,
        source_id: str,
        task_id: str,
        tag_ids: list[str],
        user_guidance: str | None,
    ) -> None:
        resplit_started_at = perf_counter()
        old_chunks_replaced = False
        await self._database.ensure_ready()
        async with self._database.session() as session:
            source = await self._source_by_id(session, source_id=source_id)
            task = await self._task_by_id(session, task_id=task_id)
            library = source.library
            task_input = _dict_payload(task.input_json)
            previous_status = str(task_input.get("previous_status") or "failed")
            previous_error_raw = task_input.get("previous_error_message")
            previous_error_message = previous_error_raw if isinstance(previous_error_raw, str) else None
            now = _utcnow()
            task.status = "running"
            task.started_at = task.started_at or now
            task.state_json = {"stage": "reading_source_payload", "source_id": source.id}
            task.updated_at = now
            await session.commit()
            logger.info(
                "source_resplit_started clerk_user_id=%s source_id=%s task_id=%s kind=%s old_chunks=%s",
                clerk_user_id,
                source.id,
                task.id,
                source.source_kind,
                len(source.chunks),
            )

            try:
                selected_tags = await self._tags_by_ids(session, library_id=library.id, tag_ids=tag_ids)
                payload = await self._storage.get_bytes(key=source.storage_key)
                source_kind = cast(SourceKind, source.source_kind)
                extracted_text, strategy_hint = await self._extract_searchable_text(
                    filename=source.original_filename,
                    source_kind=source_kind,
                    media_type=source.media_type,
                    payload=payload,
                )
                task.state_json = {
                    "stage": "splitting_semantically",
                    "source_id": source.id,
                    "strategy_hint": strategy_hint,
                    "extracted_character_count": len(extracted_text),
                }
                task.updated_at = _utcnow()
                await session.commit()
                split_started_at = perf_counter()
                split_result = await self._split_semantically(
                    source=source,
                    extracted_text=extracted_text,
                    user_guidance=user_guidance,
                )
                logger.info(
                    "source_resplit_split_completed clerk_user_id=%s source_id=%s task_id=%s tags=%s chunks=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    len(split_result.tags),
                    len(split_result.chunks),
                    (perf_counter() - split_started_at) * 1000,
                )
                auto_tags = await self._ensure_auto_tags(session, library=library, tag_names=split_result.tags)
                merged_tags = _merge_tags([*selected_tags, *auto_tags])
                normalized_chunks = _normalize_chunk_drafts(split_result.chunks, fallback_text=extracted_text)

                if source.openai_original_file_id is None:
                    task.state_json = {"stage": "uploading_original_file", "source_id": source.id}
                    task.updated_at = _utcnow()
                    await session.commit()
                    source.openai_original_file_id = await self._openai.upload_file_bytes(
                        filename=source.original_filename,
                        payload=payload,
                        purpose=_openai_file_purpose(source_kind=source_kind),
                    )
                    task.state_json = {
                        "stage": "replacing_old_chunks",
                        "source_id": source.id,
                        "openai_original_file_id": source.openai_original_file_id,
                    }
                    task.updated_at = _utcnow()
                    await session.commit()
                else:
                    task.state_json = {"stage": "replacing_old_chunks", "source_id": source.id}
                    task.updated_at = _utcnow()
                    await session.commit()

                replaced_chunk_count = len(source.chunks)
                cleanup_result = await self._delete_openai_chunk_files_for_source(source=source)
                for chunk in source.chunks:
                    chunk.openai_file_id = None
                source.chunks.clear()
                source.tag_links = [SourceTagLink(source_file_id=source.id, tag_id=tag.id) for tag in merged_tags]
                source.ingest_strategy = strategy_hint
                source.updated_at = _utcnow()
                old_chunks_replaced = True
                task.state_json = {
                    "stage": "publishing_chunks",
                    "source_id": source.id,
                    "chunk_count": len(normalized_chunks),
                    "published_chunk_count": 0,
                    "cleanup": cleanup_result,
                }
                task.updated_at = _utcnow()
                await session.commit()

                publish_started_at = perf_counter()
                for draft in normalized_chunks:
                    chunk = SemanticChunk(
                        source_file_id=source.id,
                        sequence=draft.sequence,
                        title=draft.title,
                        summary=draft.summary,
                        text_content=draft.text,
                        keywords_json=draft.keywords,
                        locator_type=draft.locator.type,
                        start_page=draft.locator.start_page,
                        end_page=draft.locator.end_page,
                        start_line=draft.locator.start_line,
                        end_line=draft.locator.end_line,
                        start_seconds=draft.locator.start_seconds,
                        end_seconds=draft.locator.end_seconds,
                        strategy_label=draft.strategy_label,
                        status="processing",
                        created_at=_utcnow(),
                        updated_at=_utcnow(),
                    )
                    session.add(chunk)
                    await session.flush()
                    attributes = build_vector_attributes(
                        library_id=library.id,
                        source_id=source.id,
                        chunk_id=chunk.id,
                        source_kind=source.source_kind,
                        content_kind="semantic_chunk",
                        title=chunk.title,
                        tag_slugs=[tag.slug for tag in merged_tags],
                    )
                    chunk.vector_attributes_json = {key: value for key, value in attributes.items()}
                    chunk.openai_file_id = await self._openai.attach_chunk_to_vector_store(
                        vector_store_id=library.openai_vector_store_id or "",
                        filename=f"{source.original_filename}.chunk-{chunk.sequence}.md",
                        text_content=render_chunk_markdown(source=source, chunk=chunk),
                        attributes=attributes,
                    )
                    chunk.status = "ready"
                    chunk.updated_at = _utcnow()
                    task.state_json = {
                        "stage": "publishing_chunks",
                        "source_id": source.id,
                        "chunk_count": len(normalized_chunks),
                        "published_chunk_count": draft.sequence,
                        "last_openai_file_id": chunk.openai_file_id,
                    }
                    task.updated_at = _utcnow()
                    await session.commit()
                logger.info(
                    "source_resplit_chunks_published clerk_user_id=%s source_id=%s task_id=%s chunks=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    len(normalized_chunks),
                    (perf_counter() - publish_started_at) * 1000,
                )

                source.status = "ready"
                source.error_message = None
                source.updated_at = _utcnow()
                library.updated_at = _utcnow()
                task.status = "completed"
                task.state_json = {
                    "stage": "completed",
                    "source_id": source.id,
                    "chunk_count": len(normalized_chunks),
                    "replaced_chunk_count": replaced_chunk_count,
                    "tag_count": len(merged_tags),
                }
                task.result_json = {
                    "source_id": source.id,
                    "chunk_count": len(normalized_chunks),
                    "replaced_chunk_count": replaced_chunk_count,
                }
                task.error_message = None
                task.completed_at = _utcnow()
                task.updated_at = _utcnow()
                await session.commit()
                logger.info(
                    "source_resplit_completed clerk_user_id=%s source_id=%s task_id=%s chunks=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    len(normalized_chunks),
                    (perf_counter() - resplit_started_at) * 1000,
                )
            except Exception as exc:
                source = await self._source_by_id(session, source_id=source_id, populate_existing=True)
                task = await self._task_by_id(session, task_id=task_id)
                if old_chunks_replaced:
                    try:
                        cleanup_result = await self._delete_openai_chunk_files_for_source(source=source)
                    except Exception as cleanup_error:
                        cleanup_result = {"chunk_file_count": 0}
                        logger.warning(
                            "source_resplit_cleanup_failed clerk_user_id=%s source_id=%s cleanup_error=%s",
                            clerk_user_id,
                            source.id,
                            cleanup_error,
                        )
                    else:
                        for chunk in source.chunks:
                            chunk.openai_file_id = None
                    source.status = "failed"
                    source.error_message = str(exc)
                else:
                    cleanup_result = {"chunk_file_count": 0}
                    source.status = previous_status
                    source.error_message = previous_error_message
                source.updated_at = _utcnow()
                task.status = "failed"
                task.state_json = {
                    "stage": "failed",
                    "source_id": source.id,
                    "old_chunks_replaced": old_chunks_replaced,
                    "cleanup": cleanup_result,
                }
                task.error_message = str(exc)
                task.completed_at = _utcnow()
                task.updated_at = _utcnow()
                await session.commit()
                logger.error(
                    "source_resplit_failed clerk_user_id=%s source_id=%s task_id=%s old_chunks_replaced=%s error=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    old_chunks_replaced,
                    exc,
                    (perf_counter() - resplit_started_at) * 1000,
                )

    async def _cancel_resplit_job(self, *, clerk_user_id: str, source_id: str, task_id: str) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            try:
                source = await self._source_by_id(session, source_id=source_id, populate_existing=True)
                task = await self._task_by_id(session, task_id=task_id)
            except FileNotFoundError:
                logger.warning(
                    "source_resplit_cancel_missing_record clerk_user_id=%s source_id=%s task_id=%s",
                    clerk_user_id,
                    source_id,
                    task_id,
                )
                return

            task_input = _dict_payload(task.input_json)
            previous_status = str(task_input.get("previous_status") or "failed")
            previous_error_raw = task_input.get("previous_error_message")
            previous_error_message = previous_error_raw if isinstance(previous_error_raw, str) else None
            state = _dict_payload(task.state_json)
            old_chunks_replaced = state.get("stage") in {"replacing_old_chunks", "publishing_chunks"}
            if old_chunks_replaced:
                try:
                    cleanup_result = await self._delete_openai_chunk_files_for_source(source=source)
                except Exception as cleanup_error:
                    cleanup_result = {"chunk_file_count": 0}
                    logger.warning(
                        "source_resplit_cancel_cleanup_failed clerk_user_id=%s source_id=%s cleanup_error=%s",
                        clerk_user_id,
                        source.id,
                        cleanup_error,
                    )
                else:
                    for chunk in source.chunks:
                        chunk.openai_file_id = None
                source.status = "failed"
                source.error_message = "Re-split cancelled during shutdown after replacement started."
            else:
                cleanup_result = {"chunk_file_count": 0}
                source.status = previous_status
                source.error_message = previous_error_message
            source.updated_at = _utcnow()
            task.status = "cancelled"
            task.state_json = {
                "stage": "cancelled",
                "source_id": source.id,
                "old_chunks_replaced": old_chunks_replaced,
                "cleanup": cleanup_result,
            }
            task.error_message = "Re-split cancelled during shutdown."
            task.completed_at = _utcnow()
            task.updated_at = _utcnow()
            await session.commit()
            logger.warning(
                "source_resplit_cancelled clerk_user_id=%s source_id=%s task_id=%s old_chunks_replaced=%s",
                clerk_user_id,
                source.id,
                task.id,
                old_chunks_replaced,
            )

    async def _execute_reindex_job(
        self,
        *,
        clerk_user_id: str,
        source_id: str,
        task_id: str,
    ) -> None:
        reindex_started_at = perf_counter()
        reindexed_chunk_count = 0
        cleanup_failed_file_ids: list[str] = []
        await self._database.ensure_ready()
        async with self._database.session() as session:
            source = await self._source_by_id(session, source_id=source_id)
            task = await self._task_by_id(session, task_id=task_id)
            library = source.library
            task_input = _dict_payload(task.input_json)
            raw_tag_ids = task_input.get("tag_ids")
            tag_ids = (
                [item.strip() for item in raw_tag_ids if isinstance(item, str) and item.strip()]
                if isinstance(raw_tag_ids, list)
                else []
            )
            previous_status = str(task_input.get("previous_status") or "failed")
            if previous_status not in {"ready", "failed"}:
                previous_status = "failed"
            previous_error_raw = task_input.get("previous_error_message")
            previous_error_message = previous_error_raw if isinstance(previous_error_raw, str) else None
            now = _utcnow()
            task.status = "running"
            task.started_at = task.started_at or now
            task.state_json = {"stage": "validating_tags", "source_id": source.id}
            task.updated_at = now
            await session.commit()
            logger.info(
                "source_reindex_started clerk_user_id=%s source_id=%s task_id=%s chunks=%s",
                clerk_user_id,
                source.id,
                task.id,
                len(source.chunks),
            )

            try:
                selected_tags = await self._tags_by_ids(session, library_id=library.id, tag_ids=tag_ids)
                source.tag_links = [SourceTagLink(source_file_id=source.id, tag_id=tag.id) for tag in selected_tags]
                chunks = sorted(source.chunks, key=lambda item: item.sequence)
                task.state_json = {
                    "stage": "reindexing_chunks",
                    "source_id": source.id,
                    "chunk_count": len(chunks),
                    "reindexed_chunk_count": 0,
                }
                task.updated_at = _utcnow()
                await session.commit()

                for chunk in chunks:
                    old_file_id = chunk.openai_file_id
                    attributes = build_vector_attributes(
                        library_id=library.id,
                        source_id=source.id,
                        chunk_id=chunk.id,
                        source_kind=source.source_kind,
                        content_kind="semantic_chunk",
                        title=chunk.title,
                        tag_slugs=[tag.slug for tag in selected_tags],
                    )
                    new_file_id = await self._openai.attach_chunk_to_vector_store(
                        vector_store_id=library.openai_vector_store_id or "",
                        filename=f"{source.original_filename}.chunk-{chunk.sequence}.md",
                        text_content=render_chunk_markdown(source=source, chunk=chunk),
                        attributes=attributes,
                    )
                    chunk.openai_file_id = new_file_id
                    chunk.vector_attributes_json = {key: value for key, value in attributes.items()}
                    chunk.status = "ready"
                    chunk.updated_at = _utcnow()
                    reindexed_chunk_count += 1
                    task.state_json = {
                        "stage": "reindexing_chunks",
                        "source_id": source.id,
                        "chunk_count": len(chunks),
                        "reindexed_chunk_count": reindexed_chunk_count,
                        "last_openai_file_id": new_file_id,
                    }
                    task.updated_at = _utcnow()
                    await session.commit()

                    if old_file_id is not None:
                        try:
                            if library.openai_vector_store_id is not None:
                                await self._openai.detach_file_from_vector_store(
                                    vector_store_id=library.openai_vector_store_id,
                                    file_id=old_file_id,
                                )
                            await self._openai.delete_file(file_id=old_file_id)
                        except Exception as cleanup_error:
                            cleanup_failed_file_ids.append(old_file_id)
                            logger.warning(
                                "source_reindex_old_chunk_cleanup_failed clerk_user_id=%s source_id=%s task_id=%s file_id=%s error=%s",
                                clerk_user_id,
                                source.id,
                                task.id,
                                old_file_id,
                                cleanup_error,
                            )

                source.status = "ready" if chunks else previous_status
                source.error_message = None if chunks else previous_error_message
                source.updated_at = _utcnow()
                library.updated_at = _utcnow()
                task.status = "completed"
                task.state_json = {
                    "stage": "completed",
                    "source_id": source.id,
                    "chunk_count": len(chunks),
                    "tag_count": len(selected_tags),
                    "cleanup_failed_file_count": len(cleanup_failed_file_ids),
                }
                task.result_json = {
                    "source_id": source.id,
                    "chunk_count": len(chunks),
                    "tag_count": len(selected_tags),
                    "cleanup_failed_file_count": len(cleanup_failed_file_ids),
                }
                task.error_message = None
                task.completed_at = _utcnow()
                task.updated_at = _utcnow()
                await session.commit()
                logger.info(
                    "source_reindex_completed clerk_user_id=%s source_id=%s task_id=%s chunks=%s cleanup_failures=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    len(chunks),
                    len(cleanup_failed_file_ids),
                    (perf_counter() - reindex_started_at) * 1000,
                )
            except Exception as exc:
                source = await self._source_by_id(session, source_id=source_id, populate_existing=True)
                task = await self._task_by_id(session, task_id=task_id)
                source.status = "failed" if source.chunks else previous_status
                source.error_message = str(exc) if source.chunks else previous_error_message
                source.updated_at = _utcnow()
                task.status = "failed"
                task.state_json = {
                    "stage": "failed",
                    "source_id": source.id,
                    "reindexed_chunk_count": reindexed_chunk_count,
                    "cleanup_failed_file_count": len(cleanup_failed_file_ids),
                }
                task.error_message = str(exc)
                task.completed_at = _utcnow()
                task.updated_at = _utcnow()
                await session.commit()
                logger.error(
                    "source_reindex_failed clerk_user_id=%s source_id=%s task_id=%s reindexed_chunks=%s error=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    reindexed_chunk_count,
                    exc,
                    (perf_counter() - reindex_started_at) * 1000,
                )

    async def _cancel_reindex_job(self, *, clerk_user_id: str, source_id: str, task_id: str) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            try:
                source = await self._source_by_id(session, source_id=source_id, populate_existing=True)
                task = await self._task_by_id(session, task_id=task_id)
            except FileNotFoundError:
                logger.warning(
                    "source_reindex_cancel_missing_record clerk_user_id=%s source_id=%s task_id=%s",
                    clerk_user_id,
                    source_id,
                    task_id,
                )
                return

            task_input = _dict_payload(task.input_json)
            previous_status = str(task_input.get("previous_status") or "failed")
            if previous_status not in {"ready", "failed"}:
                previous_status = "failed"
            previous_error_raw = task_input.get("previous_error_message")
            previous_error_message = previous_error_raw if isinstance(previous_error_raw, str) else None
            state = _dict_payload(task.state_json)
            reindexed_raw = state.get("reindexed_chunk_count")
            reindexed_chunk_count = reindexed_raw if isinstance(reindexed_raw, int) else 0
            source.status = "failed" if source.chunks else previous_status
            source.error_message = "Tag reindex cancelled during shutdown." if source.chunks else previous_error_message
            source.updated_at = _utcnow()
            task.status = "cancelled"
            task.state_json = {
                "stage": "cancelled",
                "source_id": source.id,
                "reindexed_chunk_count": reindexed_chunk_count,
            }
            task.error_message = "Tag reindex cancelled during shutdown."
            task.completed_at = _utcnow()
            task.updated_at = _utcnow()
            await session.commit()
            logger.warning(
                "source_reindex_cancelled clerk_user_id=%s source_id=%s task_id=%s reindexed_chunks=%s",
                clerk_user_id,
                source.id,
                task.id,
                reindexed_chunk_count,
            )

    async def search(self, *, clerk_user_id: str, request: SearchRequest) -> SearchResponse:
        hits = await self.search_chunks(clerk_user_id=clerk_user_id, request=request)
        return SearchResponse(query=request.query, hits=hits)

    async def search_chunks(self, *, clerk_user_id: str, request: SearchRequest) -> list[ChunkHit]:
        normalized_query = request.query.strip()
        if not normalized_query:
            return []
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            library = await self._library_for_user(session, app_user=app_user)
            if library.openai_vector_store_id is None:
                return []
            tags = await self._tags_by_ids(session, library_id=library.id, tag_ids=request.tag_ids)
            filters = build_filter_groups(
                source_ids=request.selected_source_ids,
                source_kinds=request.source_kinds,
                tag_slugs=[tag.slug for tag in tags],
                tag_match_mode=request.tag_match_mode,
            )
            candidates = await self._openai.search_vector_store(
                vector_store_id=library.openai_vector_store_id,
                query=normalized_query,
                max_results=request.max_results,
                filters=filters,
            )
            chunk_ids = [
                str(candidate.attributes.get("chunk_id"))
                for candidate in candidates
                if isinstance(candidate.attributes.get("chunk_id"), str)
            ]
            if not chunk_ids:
                return []
            chunks = (
                (
                    await session.execute(
                        select(SemanticChunk)
                        .options(
                            selectinload(SemanticChunk.source_file)
                            .selectinload(SourceFile.tag_links)
                            .selectinload(SourceTagLink.tag)
                        )
                        .where(SemanticChunk.id.in_(chunk_ids))
                    )
                )
                .scalars()
                .all()
            )
            chunk_map = {chunk.id: chunk for chunk in chunks}
            candidates_by_chunk_id = {str(candidate.attributes.get("chunk_id")): candidate for candidate in candidates}
            output: list[ChunkHit] = []
            for chunk_id in chunk_ids:
                chunk = chunk_map.get(chunk_id)
                candidate = candidates_by_chunk_id.get(chunk_id)
                if chunk is None or candidate is None:
                    continue
                if not _chunk_matches_request_filters(
                    chunk,
                    selected_source_ids=request.selected_source_ids,
                    source_kinds=request.source_kinds,
                    tag_ids=[tag.id for tag in tags],
                    tag_match_mode=request.tag_match_mode,
                ):
                    continue
                output.append(self._chunk_hit(chunk, score=candidate.score, attributes=candidate.attributes))
            return output

    async def branch_search(self, *, clerk_user_id: str, request: BranchSearchRequest) -> BranchSearchResponse:
        levels: list[BranchSearchLevel] = []
        current_hits = await self.search_chunks(
            clerk_user_id=clerk_user_id,
            request=SearchRequest(
                query=request.query,
                selected_source_ids=request.selected_source_ids,
                source_kinds=request.source_kinds,
                tag_ids=request.tag_ids,
                tag_match_mode=request.tag_match_mode,
                max_results=request.max_width,
            ),
        )
        if current_hits:
            levels.append(BranchSearchLevel(depth=0, hits=current_hits))
        seen_chunk_ids = {hit.chunk_id for hit in current_hits}
        for depth in range(1, request.descend + 1):
            next_hits: list[ChunkHit] = []
            for seed in current_hits:
                branch_query = "\n\n".join([request.query, seed.title, seed.summary, seed.text[:1200]]).strip()
                branch_hits = await self.search_chunks(
                    clerk_user_id=clerk_user_id,
                    request=SearchRequest(
                        query=branch_query,
                        selected_source_ids=request.selected_source_ids,
                        source_kinds=request.source_kinds,
                        tag_ids=request.tag_ids,
                        tag_match_mode=request.tag_match_mode,
                        max_results=request.max_width,
                    ),
                )
                for candidate in branch_hits:
                    if candidate.chunk_id in seen_chunk_ids:
                        continue
                    seen_chunk_ids.add(candidate.chunk_id)
                    next_hits.append(candidate)
                    if len(next_hits) >= request.max_width:
                        break
                if len(next_hits) >= request.max_width:
                    break
            if not next_hits:
                break
            levels.append(BranchSearchLevel(depth=depth, hits=next_hits))
            current_hits = next_hits
        return BranchSearchResponse(
            query=request.query,
            descend=request.descend,
            max_width=request.max_width,
            levels=levels,
        )

    async def _library_for_user(self, session: Any, *, app_user: AppUser) -> UserLibrary:
        library = await session.scalar(
            select(UserLibrary)
            .where(UserLibrary.user_id == app_user.id)
            .options(
                selectinload(UserLibrary.sources).selectinload(SourceFile.chunks),
                selectinload(UserLibrary.sources).selectinload(SourceFile.tag_links).selectinload(SourceTagLink.tag),
                selectinload(UserLibrary.tags).selectinload(Tag.source_links),
            )
        )
        if library is not None:
            return library
        library = UserLibrary(
            user_id=app_user.id,
            title=f"{app_user.display_name or app_user.clerk_user_id}'s semantic library",
            description="Personal semantic RAG library",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(library)
        await session.commit()
        await session.refresh(library)
        return await self._library_for_user(session, app_user=app_user)

    async def library_for_user(self, session: Any, *, app_user: AppUser) -> UserLibrary:
        return await self._library_for_user(session, app_user=app_user)

    async def _ensure_vector_store(self, session: Any, *, library: UserLibrary, app_user: AppUser) -> None:
        if library.openai_vector_store_id is not None:
            return
        library.openai_vector_store_id = await self._openai.create_vector_store(
            name=library.title,
            metadata={"clerk_user_id": app_user.clerk_user_id, "library_id": library.id},
        )
        library.updated_at = _utcnow()
        await session.flush()

    async def _source_for_user(self, session: Any, *, clerk_user_id: str, source_id: str) -> SourceFile:
        app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
        source = await session.scalar(
            select(SourceFile)
            .join(UserLibrary, UserLibrary.id == SourceFile.library_id)
            .where(SourceFile.id == source_id, UserLibrary.user_id == app_user.id)
            .options(
                selectinload(SourceFile.chunks),
                selectinload(SourceFile.library),
                selectinload(SourceFile.tag_links).selectinload(SourceTagLink.tag),
            )
        )
        if source is None:
            raise FileNotFoundError("Source not found.")
        return source

    async def _source_by_id(
        self,
        session: Any,
        *,
        source_id: str,
        populate_existing: bool = False,
    ) -> SourceFile:
        query = (
            select(SourceFile)
            .where(SourceFile.id == source_id)
            .options(
                selectinload(SourceFile.chunks),
                selectinload(SourceFile.library),
                selectinload(SourceFile.tag_links).selectinload(SourceTagLink.tag),
            )
        )
        if populate_existing:
            query = query.execution_options(populate_existing=True)
        source = await session.scalar(query)
        if source is None:
            raise FileNotFoundError("Source not found.")
        return source

    async def _task_by_id(self, session: Any, *, task_id: str) -> AppTask:
        task = await session.get(AppTask, task_id)
        if task is None:
            raise FileNotFoundError("Task not found.")
        return task

    async def _delete_openai_files_for_source(self, *, source: SourceFile) -> dict[str, object]:
        cleanup_started_at = perf_counter()
        chunk_cleanup = await self._delete_openai_chunk_files_for_source(source=source)
        original_file_deleted = source.openai_original_file_id is not None
        if source.openai_original_file_id is not None:
            await self._openai.delete_file(file_id=source.openai_original_file_id)
        logger.info(
            "source_openai_files_cleaned source_id=%s openai_chunk_files=%s openai_original_file=%s duration_ms=%.1f",
            source.id,
            chunk_cleanup["chunk_file_count"],
            original_file_deleted,
            (perf_counter() - cleanup_started_at) * 1000,
        )
        return {"chunk_file_count": chunk_cleanup["chunk_file_count"], "original_file_deleted": original_file_deleted}

    async def _delete_openai_chunk_files_for_source(self, *, source: SourceFile) -> dict[str, int]:
        cleanup_started_at = perf_counter()
        vector_store_id = source.library.openai_vector_store_id
        chunk_file_ids = [
            chunk.openai_file_id
            for chunk in sorted(source.chunks, key=lambda item: item.sequence)
            if chunk.openai_file_id is not None
        ]
        for file_id in chunk_file_ids:
            if vector_store_id is not None:
                await self._openai.detach_file_from_vector_store(vector_store_id=vector_store_id, file_id=file_id)
            await self._openai.delete_file(file_id=file_id)
        logger.info(
            "source_openai_chunk_files_cleaned source_id=%s openai_chunk_files=%s duration_ms=%.1f",
            source.id,
            len(chunk_file_ids),
            (perf_counter() - cleanup_started_at) * 1000,
        )
        return {"chunk_file_count": len(chunk_file_ids)}

    async def _tags_by_ids(self, session: Any, *, library_id: str, tag_ids: list[str]) -> list[Tag]:
        if not tag_ids:
            return []
        records = (
            (await session.execute(select(Tag).where(Tag.library_id == library_id, Tag.id.in_(tag_ids))))
            .scalars()
            .all()
        )
        if len(records) != len(set(tag_ids)):
            raise ValueError("One or more tag IDs are invalid for this library.")
        return sorted(records, key=lambda tag: tag.name.casefold())

    async def _ensure_auto_tags(self, session: Any, *, library: UserLibrary, tag_names: list[str]) -> list[Tag]:
        output: list[Tag] = []
        for raw_name in tag_names[:TAG_SLOT_COUNT]:
            name = _clean_tag_name(raw_name)
            if not name:
                continue
            slug = slugify(name)
            existing = await session.scalar(select(Tag).where(Tag.library_id == library.id, Tag.slug == slug))
            if existing is None:
                existing = Tag(
                    library_id=library.id,
                    name=name,
                    slug=slug,
                    source="auto",
                    created_at=_utcnow(),
                )
                session.add(existing)
                await session.flush()
            output.append(existing)
        return output

    async def _unique_source_title(self, session: Any, *, library_id: str, base_title: str) -> str:
        candidate = base_title.strip() or "Untitled source"
        suffix = 2
        while True:
            existing = await session.scalar(
                select(SourceFile.id).where(
                    SourceFile.library_id == library_id,
                    func.lower(SourceFile.display_title) == candidate.lower(),
                )
            )
            if existing is None:
                return candidate
            candidate = f"{base_title} ({suffix})"
            suffix += 1

    async def _extract_searchable_text(
        self,
        *,
        filename: str,
        source_kind: SourceKind,
        media_type: str,
        payload: bytes,
    ) -> tuple[str, str]:
        if source_kind == "pdf":
            return extract_pdf_text(filename=filename, payload=payload), "pdf_text_semantic"
        if source_kind == "text":
            return decode_text(payload), "text_semantic"
        if source_kind in {"audio", "video", "conversation"}:
            if source_kind == "conversation" and media_type.startswith("text/"):
                return decode_text(payload), "conversation_text_semantic"
            transcript, transcript_payload = await self._openai.transcribe_audio_bytes(
                filename=filename, payload=payload
            )
            del transcript_payload
            return transcript, "conversation_transcript_semantic"
        return decode_text(payload) if media_type.startswith(
            "text/"
        ) else f"{filename}\n\nNo text extraction is available.", "basic_metadata"

    async def _split_semantically(
        self,
        *,
        source: SourceFile,
        extracted_text: str,
        user_guidance: str | None,
    ) -> SemanticSplitResult:
        return await self._split_semantic_text(
            source_id=source.id,
            source_title=source.display_title,
            source_kind=cast(SourceKind, source.source_kind),
            extracted_text=extracted_text,
            user_guidance=user_guidance,
        )

    async def _split_semantic_text(
        self,
        *,
        source_id: str | None,
        source_title: str,
        source_kind: SourceKind,
        extracted_text: str,
        user_guidance: str | None,
    ) -> SemanticSplitResult:
        if source_kind != "pdf":
            return await self._openai.split_semantically(
                source_title=source_title,
                source_kind=source_kind,
                text=extracted_text,
                user_guidance=user_guidance,
            )

        batches = build_pdf_text_batches(
            extracted_text,
            pages_per_batch=max(1, self._settings.semantic_split_pdf_batch_pages),
        )
        if len(batches) <= 1:
            return await self._openai.split_semantically(
                source_title=source_title,
                source_kind=source_kind,
                text=extracted_text,
                user_guidance=user_guidance,
            )

        tags: list[str] = []
        chunks: list[SemanticChunkDraft] = []
        for batch in batches:
            result = await self._openai.split_semantically(
                source_title=f"{source_title} ({batch.label})",
                source_kind=source_kind,
                text=batch.text,
                user_guidance=user_guidance,
            )
            tags.extend(result.tags)
            chunks.extend(result.chunks)
        logger.info(
            "source_pdf_split_batched source_id=%s batches=%s pages_per_batch=%s chunks=%s",
            source_id or "preview",
            len(batches),
            self._settings.semantic_split_pdf_batch_pages,
            len(chunks),
        )
        return SemanticSplitResult(
            strategy_label="pdf_page_batched_semantic", tags=_dedupe_text_values(tags), chunks=chunks
        )

    def _source_summary(self, source: SourceFile) -> LibrarySourceSummary:
        return LibrarySourceSummary(
            id=source.id,
            display_title=source.display_title,
            original_filename=source.original_filename,
            media_type=source.media_type,
            source_kind=cast(SourceKind, source.source_kind),
            status=cast(SourceStatus, source.status),
            byte_size=source.byte_size,
            chunk_count=len(source.chunks),
            error_message=source.error_message,
            created_at=source.created_at,
            updated_at=source.updated_at,
            tags=[
                self._tag_summary(link.tag)
                for link in sorted(source.tag_links, key=lambda link: link.tag.name.casefold())
            ],
            openai_original_file_id=source.openai_original_file_id,
        )

    def _source_detail(self, source: SourceFile) -> LibrarySourceDetail:
        summary = self._source_summary(source)
        return LibrarySourceDetail(
            **summary.model_dump(mode="python"),
            storage_provider=source.storage_provider,
            storage_key=source.storage_key,
            ingest_strategy=source.ingest_strategy,
            chunks=[self._chunk_summary(chunk) for chunk in sorted(source.chunks, key=lambda item: item.sequence)],
        )

    def _chunk_summary(self, chunk: SemanticChunk) -> ChunkSummary:
        return ChunkSummary(
            id=chunk.id,
            source_file_id=chunk.source_file_id,
            sequence=chunk.sequence,
            title=chunk.title,
            summary=chunk.summary,
            text=chunk.text_content,
            keywords=list(chunk.keywords_json or []),
            locator=_chunk_locator(chunk),
            strategy_label=chunk.strategy_label,
            openai_file_id=chunk.openai_file_id,
            created_at=chunk.created_at,
            updated_at=chunk.updated_at,
        )

    def _chunk_hit(
        self,
        chunk: SemanticChunk,
        *,
        score: float,
        attributes: dict[str, str | float | bool] | None = None,
    ) -> ChunkHit:
        source = chunk.source_file
        return ChunkHit(
            chunk_id=chunk.id,
            source_file_id=source.id,
            source_title=source.display_title,
            original_filename=source.original_filename,
            score=score,
            title=chunk.title,
            summary=chunk.summary,
            text=chunk.text_content,
            tags=[link.tag.name for link in sorted(source.tag_links, key=lambda link: link.tag.name.casefold())],
            locator=_chunk_locator(chunk),
            openai_file_id=chunk.openai_file_id,
            attributes=attributes,
        )

    @staticmethod
    def _tag_summary(tag: Tag) -> TagSummary:
        return TagSummary(
            id=tag.id,
            name=tag.name,
            slug=tag.slug,
            color=tag.color,
            source=cast(Literal["auto", "manual"], tag.source),
            source_count=len(tag.source_links),
        )


def build_vector_attributes(
    *,
    library_id: str,
    source_id: str,
    chunk_id: str,
    source_kind: str,
    content_kind: str,
    title: str,
    tag_slugs: list[str],
) -> dict[str, str | float | bool]:
    attributes: dict[str, str | float | bool] = {
        "attributes_version": float(VECTOR_ATTRIBUTES_VERSION),
        "library_id": library_id,
        "source_id": source_id,
        "chunk_id": chunk_id,
        "source_kind": source_kind,
        "content_kind": content_kind,
        "title": title[:256],
    }
    for index, slug in enumerate(tag_slugs[:TAG_SLOT_COUNT], start=1):
        attributes[f"tag_{index}"] = slug
    return attributes


def build_filter_groups(
    *,
    source_ids: Sequence[str],
    source_kinds: Sequence[str],
    tag_slugs: Sequence[str],
    tag_match_mode: TagMatchMode,
) -> ComparisonFilter | CompoundFilter | None:
    filters: list[ComparisonFilter | CompoundFilter] = []
    if source_ids:
        filters.append(_or_filter("source_id", source_ids))
    if source_kinds:
        filters.append(_or_filter("source_kind", source_kinds))
    if tag_slugs:
        tag_filters: list[CompoundFilter] = [_tag_slug_filter(slug) for slug in tag_slugs]
        if len(tag_filters) == 1:
            filters.append(tag_filters[0])
        else:
            filters.append({"type": "and" if tag_match_mode == "all" else "or", "filters": tag_filters})
    if not filters:
        return None
    if len(filters) == 1:
        return filters[0]
    return {"type": "and", "filters": filters}


def render_chunk_markdown(*, source: SourceFile, chunk: SemanticChunk) -> str:
    lines = [
        f"# {chunk.title}",
        "",
        f"Source: {source.display_title}",
        f"Original filename: {source.original_filename}",
        f"Locator: {_chunk_locator(chunk).label()}",
        f"Strategy: {chunk.strategy_label}",
        "",
        "## Summary",
        chunk.summary,
    ]
    if chunk.keywords_json:
        lines.extend(["", "## Keywords", ", ".join(chunk.keywords_json)])
    lines.extend(["", "## Text", chunk.text_content])
    return "\n".join(lines).strip()


def extract_pdf_text(*, filename: str, payload: bytes) -> str:
    from pypdf import PdfReader

    suffix = Path(filename).suffix or ".pdf"
    with NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(payload)
    try:
        reader = PdfReader(str(temp_path))
        page_blocks: list[str] = []
        for page_index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                page_blocks.append(f"[page {page_index}]\n{text.strip()}")
        return "\n\n".join(page_blocks).strip() or f"{filename}\n\nNo extractable PDF text was found."
    finally:
        temp_path.unlink(missing_ok=True)


def build_pdf_text_batches(extracted_text: str, *, pages_per_batch: int) -> list[PdfTextBatch]:
    normalized_batch_size = max(1, pages_per_batch)
    pages: list[tuple[int, str]] = []
    for match in PDF_PAGE_BLOCK_RE.finditer(extracted_text):
        page_number = int(match.group("page"))
        page_text = match.group("text").strip()
        if page_text:
            pages.append((page_number, f"[page {page_number}]\n{page_text}"))
    if not pages:
        stripped_text = extracted_text.strip()
        return [PdfTextBatch(start_page=None, end_page=None, text=stripped_text)] if stripped_text else []

    batches: list[PdfTextBatch] = []
    for start in range(0, len(pages), normalized_batch_size):
        page_batch = pages[start : start + normalized_batch_size]
        batches.append(
            PdfTextBatch(
                start_page=page_batch[0][0],
                end_page=page_batch[-1][0],
                text="\n\n".join(page_text for _page_number, page_text in page_batch),
            )
        )
    return batches


def decode_text(payload: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode text payload.")


def guess_media_type(*, filename: str, declared_media_type: str | None) -> str:
    if isinstance(declared_media_type, str) and declared_media_type.strip():
        return declared_media_type.strip()
    guessed, _encoding = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def classify_source_kind(*, filename: str, media_type: str) -> SourceKind:
    suffix = Path(filename).suffix.lower()
    if media_type == "application/pdf" or suffix == ".pdf":
        return "pdf"
    if media_type.startswith("audio/"):
        return "audio"
    if media_type.startswith("video/"):
        return "video"
    if media_type.startswith("image/"):
        return "image"
    if media_type.startswith("text/") or suffix in TEXT_EXTENSIONS:
        if suffix in {".vtt", ".srt"} or "transcript" in filename.casefold():
            return "conversation"
        return "text"
    return "other"


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:80] or "tag"


def _clean_tag_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())[:80]


def _merge_tags(tags: list[Tag]) -> list[Tag]:
    output: list[Tag] = []
    seen: set[str] = set()
    for tag in tags:
        if tag.id in seen:
            continue
        seen.add(tag.id)
        output.append(tag)
    return output[:TAG_SLOT_COUNT]


def _dedupe_text_values(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_tag_name(value)
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def _dict_payload(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _normalize_chunk_drafts(chunks: list[SemanticChunkDraft], *, fallback_text: str) -> list[SemanticChunkDraft]:
    if not chunks:
        return [
            SemanticChunkDraft(
                sequence=1,
                title="Full source",
                summary="The complete extracted source text.",
                text=fallback_text,
                keywords=[],
                locator=ChunkLocator(type="line_range", start_line=1, end_line=max(1, len(fallback_text.splitlines()))),
                strategy_label="fallback",
            )
        ]
    normalized = sorted(chunks, key=lambda item: item.sequence)
    return [chunk.model_copy(update={"sequence": index}) for index, chunk in enumerate(normalized, start=1)]


def _chunk_locator(chunk: SemanticChunk) -> ChunkLocator:
    return ChunkLocator(
        type=cast(Any, chunk.locator_type),
        start_page=chunk.start_page,
        end_page=chunk.end_page,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        start_seconds=chunk.start_seconds,
        end_seconds=chunk.end_seconds,
    )


def _chunk_matches_request_filters(
    chunk: SemanticChunk,
    *,
    selected_source_ids: Sequence[str],
    source_kinds: Sequence[str],
    tag_ids: Sequence[str],
    tag_match_mode: TagMatchMode,
) -> bool:
    source = chunk.source_file
    if selected_source_ids and source.id not in set(selected_source_ids):
        return False
    if source_kinds and source.source_kind not in set(source_kinds):
        return False
    if tag_ids:
        selected_tag_ids = set(tag_ids)
        source_tag_ids = {link.tag_id for link in source.tag_links}
        return (
            selected_tag_ids.issubset(source_tag_ids)
            if tag_match_mode == "all"
            else bool(selected_tag_ids & source_tag_ids)
        )
    return True


def _or_filter(key: str, values: Sequence[str]) -> ComparisonFilter | CompoundFilter:
    if len(values) == 1:
        return {"type": "eq", "key": key, "value": values[0]}
    return {"type": "or", "filters": [{"type": "eq", "key": key, "value": value} for value in values]}


def _tag_slug_filter(slug: str) -> CompoundFilter:
    return {
        "type": "or",
        "filters": [{"type": "eq", "key": f"tag_{index}", "value": slug} for index in range(1, TAG_SLOT_COUNT + 1)],
    }


def _openai_file_purpose(*, source_kind: str) -> FilePurpose:
    if source_kind == "image":
        return "vision"
    return "assistants"


def _task_summary(task: AppTask) -> TaskSummary:
    return TaskSummary(
        id=task.id,
        kind=cast(Any, task.kind),
        status=cast(TaskStatus, task.status),
        title=task.title,
        origin_surface=cast(Any, task.origin_surface),
        origin_thread_id=task.origin_thread_id,
        source_file_id=task.source_file_id,
        input_json=task.input_json,
        result_json=task.result_json,
        error_message=task.error_message,
        started_at=task.started_at,
        completed_at=task.completed_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)

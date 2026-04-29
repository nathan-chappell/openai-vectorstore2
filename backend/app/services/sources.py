from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
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
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from backend.app.core.config import AppSettings
from backend.app.db.session import DatabaseManager
from backend.app.integrations.openai_gateway import OpenAIGateway, VectorSearchCandidate
from backend.app.models import (
    AppTask,
    AppUser,
    FilesystemEntry,
    ResearchImportCandidate,
    SemanticChunk,
    SourceFile,
    UserLibrary,
    new_id,
)
from backend.app.schemas import (
    BranchSearchLevel,
    BranchSearchRequest,
    BranchSearchResponse,
    ChunkHit,
    ChunkLocator,
    ChunkSummary,
    FileListResponse,
    FilesystemBreadcrumb,
    FilesystemDeleteResponse,
    FilesystemEntrySummary,
    FilesystemListResponse,
    FilesystemSearchResponse,
    IngestFinalizeResponse,
    LibrarySourceDetail,
    LibrarySourceSummary,
    LibraryCreateRequest,
    LibraryListResponse,
    LibrarySummary,
    OpenAIAttributes,
    SearchRequest,
    SearchResponse,
    SemanticChunkDraft,
    SemanticSplitResult,
    SplitPreviewResponse,
    SourceKind,
    SourceMetadata,
    SourceStatus,
    TagMatchMode,
    TagMutationResponse,
    TagSummary,
    TaskStatus,
    TaskSummary,
)
from backend.app.services.auth import AuthService
from backend.app.services.billing import BillingService
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

TAG_SLOT_COUNT = 1
AUTO_TAG_LIMIT = 1
VECTOR_ATTRIBUTES_VERSION = 3
CHAT_FILE_INPUT_LIMIT = 10
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


@dataclass(frozen=True, slots=True)
class SourceFileInput:
    source_id: str
    file_id: str
    filename: str
    virtual_path: str
    display_title: str
    media_type: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class VectorIndexMaterial:
    filename: str
    media_type: str
    payload: bytes
    strategy_label: str
    part_index: int = 1
    part_count: int = 1
    start_page: int | None = None
    end_page: int | None = None


@dataclass(frozen=True, slots=True)
class PdfPayloadPart:
    filename: str
    payload: bytes
    start_page: int
    end_page: int


class SourceService:
    """Own source ingestion, source-level vector indexing, and retrieval."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        database: DatabaseManager,
        auth: AuthService,
        storage: StorageService,
        openai: OpenAIGateway,
        billing: BillingService,
    ) -> None:
        self._settings = settings
        self._database = database
        self._auth = auth
        self._storage = storage
        self._openai = openai
        self._billing = billing
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
            try:
                await session.commit()
                await session.refresh(existing)
                return existing
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(select(AppUser).where(AppUser.clerk_user_id == clerk_user_id))
                if existing is None:
                    raise
                logger.info("app_user_concurrent_create_recovered clerk_user_id=%s", clerk_user_id)

        should_update = (
            existing.primary_email != record.primary_email
            or existing.display_name != record.display_name
            or existing.active != record.active
            or existing.role != record.role
            or existing.last_seen_at is None
            or _as_utc(existing.last_seen_at) < now - timedelta(minutes=5)
        )
        if not should_update:
            return existing
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
        library_id: str | None = None,
        query: str | None,
        tag_ids: list[str],
        tag_match_mode: TagMatchMode,
        page: int,
        page_size: int,
    ) -> FileListResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            library = await self._library_for_request(session, app_user=app_user, library_id=library_id)
            selected_tag_slugs = await self._tags_by_ids(session, library_id=library.id, tag_ids=tag_ids)
            selected_tag_slug = selected_tag_slugs[0] if selected_tag_slugs else None
            normalized_query = query.casefold().strip() if isinstance(query, str) else ""
            sources = sorted(library.sources, key=lambda source: source.created_at, reverse=True)
            if normalized_query:
                sources = [
                    source
                    for source in sources
                    if normalized_query in source.display_title.casefold()
                    or normalized_query in source.original_filename.casefold()
                    or normalized_query in source.id.casefold()
                    or normalized_query in _virtual_name(source).casefold()
                    or normalized_query in _virtual_path(source).casefold()
                    or normalized_query in source.media_type.casefold()
                    or normalized_query in source.source_kind.casefold()
                    or normalized_query in _metadata_search_text(source.source_metadata)
                ]
            if selected_tag_slug:
                sources = [source for source in sources if source.tag_slug == selected_tag_slug]

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

    async def list_filesystem(
        self, *, clerk_user_id: str, folder_id: str | None = None, library_id: str | None = None
    ) -> FilesystemListResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            library = await self._library_for_request(session, app_user=app_user, library_id=library_id)
            root = await self._root_entry_for_library(session, library=library)
            folder = (
                root
                if folder_id is None
                else await self._filesystem_entry_for_user(
                    session,
                    clerk_user_id=clerk_user_id,
                    entry_id=folder_id,
                    library_id=library.id,
                )
            )
            if folder.kind != "folder":
                raise ValueError("Only folders can be listed.")
            entries = (
                (
                    await session.execute(
                        select(FilesystemEntry)
                        .where(FilesystemEntry.library_id == library.id, FilesystemEntry.parent_id == folder.id)
                        .options(
                            selectinload(FilesystemEntry.source_file).selectinload(SourceFile.chunks),
                        )
                    )
                )
                .scalars()
                .all()
            )
            await session.commit()
            return FilesystemListResponse(
                current=self._filesystem_entry_summary(folder),
                breadcrumbs=await self._breadcrumbs_for_entry(session, entry=folder),
                entries=[self._filesystem_entry_summary(entry) for entry in _sort_filesystem_entries(entries)],
            )

    async def search_filesystem(
        self,
        *,
        clerk_user_id: str,
        library_id: str | None = None,
        query: str | None,
        tag_ids: list[str],
        tag_match_mode: TagMatchMode,
        page: int,
        page_size: int,
    ) -> FilesystemSearchResponse:
        normalized_query = query.casefold().strip() if isinstance(query, str) else ""
        vector_source_ids: list[str] = []
        if normalized_query:
            hits = await self.search_chunks(
                clerk_user_id=clerk_user_id,
                request=SearchRequest(
                    query=normalized_query,
                    library_id=library_id,
                    tag_ids=tag_ids,
                    tag_match_mode=tag_match_mode,
                    max_results=min(24, max(page * page_size, page_size)),
                ),
            )
            vector_source_ids = list(dict.fromkeys(hit.source_file_id for hit in hits))

        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            library = await self._library_for_request(session, app_user=app_user, library_id=library_id)
            await self._root_entry_for_library(session, library=library)
            await session.commit()
            entries = (
                (
                    await session.execute(
                        select(FilesystemEntry)
                        .where(
                            FilesystemEntry.library_id == library.id,
                            FilesystemEntry.normalized_path != "/",
                        )
                        .options(
                            selectinload(FilesystemEntry.source_file).selectinload(SourceFile.chunks),
                        )
                    )
                )
                .scalars()
                .all()
            )
            selected_tag_slugs = await self._tags_by_ids(session, library_id=library.id, tag_ids=tag_ids)
            selected_tag_slug = selected_tag_slugs[0] if selected_tag_slugs else None
            vector_source_id_set = set(vector_source_ids)

            def matches_entry(entry: FilesystemEntry) -> bool:
                source = entry.source_file
                if selected_tag_slug:
                    if source is None:
                        return False
                    if source.tag_slug != selected_tag_slug:
                        return False
                if not normalized_query:
                    return True
                name_matches = normalized_query in entry.name.casefold() or normalized_query in entry.path.casefold()
                source_matches = source is not None and (
                    normalized_query in source.display_title.casefold()
                    or normalized_query in source.original_filename.casefold()
                    or normalized_query in source.id.casefold()
                    or normalized_query in source.media_type.casefold()
                    or normalized_query in source.source_kind.casefold()
                    or normalized_query in _metadata_search_text(source.source_metadata)
                    or source.id in vector_source_id_set
                )
                return name_matches or source_matches

            matches = [entry for entry in entries if matches_entry(entry)]
            start = max(page - 1, 0) * page_size
            end = start + page_size
            page_entries = _sort_filesystem_entries(matches)[start:end]
            return FilesystemSearchResponse(
                query=query,
                entries=[self._filesystem_entry_summary(entry) for entry in page_entries],
                total_count=len(matches),
                page=page,
                page_size=page_size,
                has_more=end < len(matches),
            )

    async def create_folder(
        self,
        *,
        clerk_user_id: str,
        parent_id: str | None,
        name: str,
        library_id: str | None = None,
    ) -> FilesystemEntrySummary:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to create folders.")
            library = await self._writable_library_for_request(session, app_user=app_user, library_id=library_id)
            root = await self._root_entry_for_library(session, library=library)
            parent = (
                root
                if parent_id is None
                else await self._filesystem_entry_for_user(
                    session,
                    clerk_user_id=clerk_user_id,
                    entry_id=parent_id,
                    library_id=library.id,
                    writable=True,
                )
            )
            if parent.kind != "folder":
                raise ValueError("Folders can only be created inside folders.")
            cleaned_name = _clean_entry_name(name)
            await self._assert_unique_child_name(session, parent=parent, name=cleaned_name, excluded_entry_id=None)
            now = _utcnow()
            entry = FilesystemEntry(
                id=new_id(),
                library_id=library.id,
                parent_id=parent.id,
                kind="folder",
                name=cleaned_name,
                normalized_name=_normalize_entry_name(cleaned_name),
                path=_join_entry_path(parent.path, cleaned_name),
                normalized_path=_normalize_entry_path(_join_entry_path(parent.path, cleaned_name)),
                created_at=now,
                updated_at=now,
            )
            session.add(entry)
            library.updated_at = now
            await session.commit()
            await session.refresh(entry)
            logger.info(
                "filesystem_folder_created clerk_user_id=%s entry_id=%s path=%s",
                clerk_user_id,
                entry.id,
                entry.path,
            )
            return self._filesystem_entry_summary(entry)

    async def update_filesystem_entry(
        self,
        *,
        clerk_user_id: str,
        entry_id: str,
        name: str | None,
        parent_id: str | None,
        origin_surface: str,
        origin_thread_id: str | None = None,
    ) -> FilesystemEntrySummary:
        await self._database.ensure_ready()
        reindex_source_ids: list[str] = []
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to update filesystem entries.")
            entry = await self._filesystem_entry_for_user(session, clerk_user_id=clerk_user_id, entry_id=entry_id)
            if entry.parent_id is None:
                raise ValueError("The root folder cannot be renamed or moved.")
            library = entry.library
            new_parent = entry.parent
            if parent_id is not None and parent_id != entry.parent_id:
                new_parent = await self._filesystem_entry_for_user(
                    session, clerk_user_id=clerk_user_id, entry_id=parent_id
                )
                if new_parent.kind != "folder":
                    raise ValueError("Entries can only be moved into folders.")
                if entry.kind == "folder":
                    await self._assert_not_descendant(session, entry=entry, candidate_parent=new_parent)
            if new_parent is None:
                raise ValueError("A non-root entry must have a parent folder.")
            new_name = _clean_entry_name(name) if name is not None else entry.name
            await self._assert_unique_child_name(
                session,
                parent=new_parent,
                name=new_name,
                excluded_entry_id=entry.id,
            )
            old_path = entry.path
            old_normalized_path = entry.normalized_path
            new_path = _join_entry_path(new_parent.path, new_name)
            now = _utcnow()
            moved_or_renamed = entry.parent_id != new_parent.id or entry.name != new_name
            if moved_or_renamed:
                if entry.kind == "folder":
                    descendants = (
                        (
                            await session.execute(
                                select(FilesystemEntry)
                                .where(
                                    FilesystemEntry.library_id == library.id,
                                    FilesystemEntry.normalized_path.like(f"{old_normalized_path.rstrip('/')}/%"),
                                )
                                .options(
                                    selectinload(FilesystemEntry.source_file).selectinload(SourceFile.chunks),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                else:
                    descendants = []
                entry.parent_id = new_parent.id
                entry.name = new_name
                entry.normalized_name = _normalize_entry_name(new_name)
                entry.path = new_path
                entry.normalized_path = _normalize_entry_path(new_path)
                entry.updated_at = now
                if entry.source_file is not None:
                    entry.source_file.display_title = Path(new_name).stem or new_name
                    entry.source_file.updated_at = now
                    if entry.source_file.status == "ready" and entry.source_file.openai_vector_file_id is not None:
                        reindex_source_ids.append(entry.source_file.id)
                for descendant in descendants:
                    relative_path = descendant.path[len(old_path.rstrip("/")) :].lstrip("/")
                    descendant.path = _join_entry_path(new_path, relative_path)
                    descendant.normalized_path = _normalize_entry_path(descendant.path)
                    descendant.updated_at = now
                    if descendant.source_file is not None:
                        descendant.source_file.updated_at = now
                        if (
                            descendant.source_file.status == "ready"
                            and descendant.source_file.openai_vector_file_id is not None
                        ):
                            reindex_source_ids.append(descendant.source_file.id)
                library.updated_at = now
            await session.commit()
            await session.refresh(entry)
            summary = self._filesystem_entry_summary(entry)
            logger.info(
                "filesystem_entry_updated clerk_user_id=%s entry_id=%s path=%s reindex_sources=%s",
                clerk_user_id,
                entry.id,
                entry.path,
                len(set(reindex_source_ids)),
            )

        for source_id in list(dict.fromkeys(reindex_source_ids)):
            source_detail = await self.get_source(clerk_user_id=clerk_user_id, source_id=source_id)
            await self.update_source_tags(
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                tag_ids=[source_detail.tags[0].slug] if source_detail.tags else [],
                origin_surface=origin_surface,
                origin_thread_id=origin_thread_id,
            )
        return summary

    async def delete_filesystem_entries(
        self,
        *,
        clerk_user_id: str,
        entry_ids: list[str],
        confirm: bool,
    ) -> FilesystemDeleteResponse:
        if not confirm:
            raise ValueError("Permanent delete requires confirmation.")
        await self._database.ensure_ready()
        ordered_entry_ids = list(dict.fromkeys(entry_id.strip() for entry_id in entry_ids if entry_id.strip()))
        source_ids: list[str] = []
        folder_entry_ids: list[str] = []
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to delete filesystem entries.")
            library = await self._library_for_user(session, app_user=app_user)
            for requested_entry_id in ordered_entry_ids:
                entry = await self._filesystem_entry_for_user(
                    session, clerk_user_id=clerk_user_id, entry_id=requested_entry_id
                )
                if entry.parent_id is None:
                    raise ValueError("The root folder cannot be deleted.")
                scoped_entries = [entry]
                if entry.kind == "folder":
                    descendants = (
                        (
                            await session.execute(
                                select(FilesystemEntry)
                                .where(
                                    FilesystemEntry.library_id == library.id,
                                    FilesystemEntry.normalized_path.like(f"{entry.normalized_path.rstrip('/')}/%"),
                                )
                                .options(selectinload(FilesystemEntry.source_file))
                            )
                        )
                        .scalars()
                        .all()
                    )
                    scoped_entries.extend(descendants)
                for scoped_entry in scoped_entries:
                    if scoped_entry.source_file_id is not None:
                        source_ids.append(scoped_entry.source_file_id)
                    elif scoped_entry.kind == "folder":
                        folder_entry_ids.append(scoped_entry.id)

        deleted_source_ids: list[str] = []
        for source_id in list(dict.fromkeys(source_ids)):
            deleted_source_ids.append(await self.delete_source(clerk_user_id=clerk_user_id, source_id=source_id))

        async with self._database.session() as session:
            remaining_folders = (
                (
                    await session.execute(
                        select(FilesystemEntry)
                        .where(FilesystemEntry.id.in_(list(dict.fromkeys(folder_entry_ids))))
                        .order_by(FilesystemEntry.normalized_path.desc())
                    )
                )
                .scalars()
                .all()
            )
            deleted_entry_ids: list[str] = []
            for folder in remaining_folders:
                deleted_entry_ids.append(folder.id)
                await session.delete(folder)
            await session.commit()
        logger.info(
            "filesystem_entries_deleted clerk_user_id=%s entries=%s sources=%s",
            clerk_user_id,
            len(set([*ordered_entry_ids, *folder_entry_ids])),
            len(deleted_source_ids),
        )
        return FilesystemDeleteResponse(
            deleted_entry_ids=list(dict.fromkeys([*ordered_entry_ids, *folder_entry_ids])),
            deleted_source_ids=deleted_source_ids,
        )

    async def ensure_source_file_inputs(
        self,
        *,
        clerk_user_id: str,
        source_ids: list[str],
        limit: int = CHAT_FILE_INPUT_LIMIT,
        max_file_bytes: int | None = None,
        max_total_bytes: int | None = None,
    ) -> list[SourceFileInput]:
        await self._database.ensure_ready()
        bounded_source_ids = list(dict.fromkeys(source_id.strip() for source_id in source_ids if source_id.strip()))
        output_limit = max(0, limit)
        total_bytes = 0
        output: list[SourceFileInput] = []
        async with self._database.session() as session:
            for source_id in bounded_source_ids:
                if len(output) >= output_limit:
                    break
                try:
                    source = await self._source_for_user(session, clerk_user_id=clerk_user_id, source_id=source_id)
                except FileNotFoundError:
                    continue
                if source.status != "ready":
                    continue
                if max_file_bytes is not None and source.byte_size > max_file_bytes:
                    logger.debug(
                        "source_file_input_skipped_size clerk_user_id=%s source_id=%s bytes=%s max_file_bytes=%s",
                        clerk_user_id,
                        source.id,
                        source.byte_size,
                        max_file_bytes,
                    )
                    continue
                if max_total_bytes is not None and total_bytes + source.byte_size > max_total_bytes:
                    logger.debug(
                        "source_file_input_skipped_total clerk_user_id=%s source_id=%s bytes=%s total_bytes=%s max_total_bytes=%s",
                        clerk_user_id,
                        source.id,
                        source.byte_size,
                        total_bytes,
                        max_total_bytes,
                    )
                    continue
                if source.openai_original_file_id is None or source.openai_original_file_purpose != "user_data":
                    payload = await self._storage.get_bytes(key=source.storage_key)
                    old_file_id = source.openai_original_file_id
                    source.openai_original_file_id = await self._openai.upload_file_bytes(
                        filename=_virtual_name(source),
                        payload=payload,
                        purpose="user_data",
                    )
                    source.openai_original_file_purpose = "user_data"
                    source.updated_at = _utcnow()
                    await session.commit()
                    if old_file_id is not None:
                        await self._openai.delete_file(file_id=old_file_id)
                output.append(
                    SourceFileInput(
                        source_id=source.id,
                        file_id=source.openai_original_file_id,
                        filename=_virtual_name(source),
                        virtual_path=_virtual_path(source),
                        display_title=source.display_title,
                        media_type=source.media_type,
                        byte_size=source.byte_size,
                    )
                )
                total_bytes += source.byte_size
            await session.commit()
        return output

    async def list_libraries(self, *, clerk_user_id: str) -> LibraryListResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            personal = await self._library_for_user(session, app_user=app_user)
            libraries = (
                (
                    await session.execute(
                        select(UserLibrary)
                        .where(
                            or_(
                                UserLibrary.user_id == app_user.id,
                                UserLibrary.visibility == "public",
                            )
                        )
                        .options(selectinload(UserLibrary.sources))
                        .order_by(UserLibrary.visibility.asc(), UserLibrary.updated_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            libraries_by_id = {library.id: library for library in libraries}
            libraries_by_id[personal.id] = personal
            return LibraryListResponse(
                default_library_id=personal.id,
                libraries=[
                    self._library_summary(library, app_user=app_user, personal_library_id=personal.id)
                    for library in sorted(
                        libraries_by_id.values(),
                        key=lambda item: (item.id != personal.id, item.visibility != "public", item.title.casefold()),
                    )
                ],
            )

    async def create_library(self, *, clerk_user_id: str, payload: LibraryCreateRequest) -> LibrarySummary:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to create libraries.")
            personal = await self._library_for_user(session, app_user=app_user)
            slug = _clean_library_slug(payload.slug or payload.title) if payload.visibility == "public" else None
            if slug is not None:
                existing_id = await session.scalar(select(UserLibrary.id).where(UserLibrary.slug == slug))
                if existing_id is not None:
                    raise ValueError("Another public library already uses that slug.")
            now = _utcnow()
            library = UserLibrary(
                user_id=app_user.id,
                title=payload.title.strip(),
                description=payload.description.strip()
                if payload.description and payload.description.strip()
                else None,
                visibility=payload.visibility,
                slug=slug,
                created_at=now,
                updated_at=now,
            )
            session.add(library)
            await session.flush()
            await self._root_entry_for_library(session, library=library)
            await session.commit()
            await session.refresh(library)
            logger.info(
                "library_created clerk_user_id=%s library_id=%s visibility=%s slug=%s",
                clerk_user_id,
                library.id,
                library.visibility,
                library.slug,
            )
            return self._library_summary(library, app_user=app_user, personal_library_id=personal.id)

    async def list_tags(self, *, clerk_user_id: str, library_id: str | None = None) -> list[TagSummary]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            library = await self._library_for_request(session, app_user=app_user, library_id=library_id)
            counts: dict[str, int] = {}
            for source in library.sources:
                if source.tag_slug:
                    counts[source.tag_slug] = counts.get(source.tag_slug, 0) + 1
            return [
                self._tag_summary_from_slug(slug, source_count=count)
                for slug, count in sorted(counts.items(), key=lambda item: item[0].casefold())
            ]

    async def ensure_auto_tags(self, *, clerk_user_id: str, tag_names: list[str]) -> list[TagSummary]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to create tags.")
            return [
                self._tag_summary_from_slug(slug)
                for slug in _dedupe_text_values([slugify(name) for name in tag_names], limit=AUTO_TAG_LIMIT)
            ]

    async def create_tag(
        self,
        *,
        clerk_user_id: str,
        name: str,
        color: str | None = None,
    ) -> TagMutationResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to create tags.")
            del color
            cleaned_name = _clean_tag_name(name)
            if not cleaned_name:
                raise ValueError("Tag name is required.")
            slug = slugify(cleaned_name)
            logger.info("tag_created clerk_user_id=%s slug=%s", clerk_user_id, slug)
            return TagMutationResponse(tag=self._tag_summary_from_slug(slug, source="manual"), tasks=[])

    async def update_tag(
        self,
        *,
        clerk_user_id: str,
        tag_id: str,
        name: str | None,
        color: str | None,
        origin_surface: str,
        origin_thread_id: str | None = None,
    ) -> TagMutationResponse:
        await self._database.ensure_ready()
        reindex_source_ids: list[str] = []
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to update tags.")
            del color
            library = await self._library_for_user(session, app_user=app_user)
            current_slug = _clean_tag_slug(tag_id)
            if current_slug is None:
                raise FileNotFoundError("Tag not found.")
            new_name = _clean_tag_name(name) if name is not None else current_slug
            if not new_name:
                raise ValueError("Tag name is required.")
            new_slug = slugify(new_name)
            linked_sources = sorted(
                [source for source in library.sources if source.tag_slug == current_slug],
                key=lambda source: source.created_at,
                reverse=True,
            )
            if not linked_sources:
                raise FileNotFoundError("Tag not found.")
            processing_sources = [source.display_title for source in linked_sources if source.status == "processing"]
            if processing_sources:
                raise ValueError("Wait for current source processing tasks to finish before updating this tag.")
            for source in linked_sources:
                source.tag_slug = new_slug
                source.updated_at = _utcnow()
                reindex_source_ids.append(source.id)
            await session.commit()
            tag_summary = self._tag_summary_from_slug(new_slug, source="manual", source_count=len(linked_sources))

        tasks: list[TaskSummary] = []
        for source_id in reindex_source_ids:
            response = await self.update_source_tags(
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                tag_ids=[new_slug],
                origin_surface=origin_surface,
                origin_thread_id=origin_thread_id,
            )
            if response.task is not None:
                tasks.append(response.task)
        logger.info(
            "tag_updated clerk_user_id=%s tag_id=%s reindex_tasks=%s",
            clerk_user_id,
            tag_id,
            len(tasks),
        )
        return TagMutationResponse(tag=tag_summary, tasks=tasks)

    async def delete_tag(
        self,
        *,
        clerk_user_id: str,
        tag_id: str,
        origin_surface: str,
        origin_thread_id: str | None = None,
    ) -> TagMutationResponse:
        await self._database.ensure_ready()
        reindex_source_ids: list[str] = []
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to delete tags.")
            library = await self._library_for_user(session, app_user=app_user)
            deleted_slug = _clean_tag_slug(tag_id)
            if deleted_slug is None:
                raise FileNotFoundError("Tag not found.")
            linked_sources = sorted(
                [source for source in library.sources if source.tag_slug == deleted_slug],
                key=lambda source: source.created_at,
                reverse=True,
            )
            if not linked_sources:
                raise FileNotFoundError("Tag not found.")
            processing_sources = [source.display_title for source in linked_sources if source.status == "processing"]
            if processing_sources:
                raise ValueError("Wait for current source processing tasks to finish before deleting this tag.")
            for source in linked_sources:
                source.tag_slug = None
                source.updated_at = _utcnow()
                reindex_source_ids.append(source.id)
            await session.commit()

        tasks: list[TaskSummary] = []
        for source_id in reindex_source_ids:
            response = await self.update_source_tags(
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                tag_ids=[],
                origin_surface=origin_surface,
                origin_thread_id=origin_thread_id,
            )
            if response.task is not None:
                tasks.append(response.task)
        logger.info(
            "tag_deleted clerk_user_id=%s tag_id=%s slug=%s reindex_tasks=%s",
            clerk_user_id,
            tag_id,
            deleted_slug,
            len(tasks),
        )
        return TagMutationResponse(tag=None, tasks=tasks)

    async def get_source(
        self, *, clerk_user_id: str, source_id: str, library_id: str | None = None
    ) -> LibrarySourceDetail:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            source = await self._source_for_user(
                session,
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                library_id=library_id,
                writable=False,
            )
            return self._source_detail(source)

    async def read_source_bytes(
        self, *, clerk_user_id: str, source_id: str, library_id: str | None = None
    ) -> tuple[LibrarySourceDetail, bytes]:
        detail = await self.get_source(clerk_user_id=clerk_user_id, source_id=source_id, library_id=library_id)
        payload = await self._storage.get_bytes(key=detail.storage_key)
        return detail, payload

    async def delete_source(self, *, clerk_user_id: str, source_id: str) -> str:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            source = await self._source_for_user(
                session,
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                writable=True,
            )
            cleanup_result = await self._delete_openai_files_for_source(source=source)
            await self._storage.delete_object(key=source.storage_key)
            await session.execute(
                update(AppTask)
                .where(AppTask.source_file_id == source.id)
                .values(source_file_id=None, updated_at=_utcnow())
            )
            if source.filesystem_entry is not None:
                await session.delete(source.filesystem_entry)
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
        origin_thread_id: str | None = None,
        folder_id: str | None = None,
        virtual_name: str | None = None,
        library_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> IngestFinalizeResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to ingest sources.")
            library = await self._writable_library_for_request(session, app_user=app_user, library_id=library_id)
            await self._ensure_vector_store(session, library=library, app_user=app_user)
            root = await self._root_entry_for_library(session, library=library)
            parent = (
                root
                if folder_id is None
                else await self._filesystem_entry_for_user(
                    session,
                    clerk_user_id=clerk_user_id,
                    entry_id=folder_id,
                    library_id=library.id,
                    writable=True,
                )
            )
            if parent.kind != "folder":
                raise ValueError("Sources can only be uploaded into folders.")

            selected_tag_slugs = await self._tags_by_ids(session, library_id=library.id, tag_ids=tag_ids)
            selected_tag_slug = selected_tag_slugs[0] if selected_tag_slugs else None
            media_type = guess_media_type(filename=filename, declared_media_type=declared_media_type)
            source_kind = classify_source_kind(filename=filename, media_type=media_type)
            stored = await self._storage.put_bytes(
                scope="sources",
                filename=filename,
                media_type=media_type,
                payload=payload,
            )
            entry_name = await self._unique_child_name(
                session,
                parent=parent,
                base_name=_clean_entry_name(virtual_name or filename),
            )
            display_title = Path(entry_name).stem or entry_name
            now = _utcnow()
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
                metadata_json=metadata or {},
                tag_slug=selected_tag_slug,
                created_at=now,
                updated_at=now,
            )
            session.add(source)
            await session.flush()
            entry_path = _join_entry_path(parent.path, entry_name)
            filesystem_entry = FilesystemEntry(
                id=new_id(),
                library_id=library.id,
                parent_id=parent.id,
                source_file_id=source.id,
                kind="file",
                name=entry_name,
                normalized_name=_normalize_entry_name(entry_name),
                path=entry_path,
                normalized_path=_normalize_entry_path(entry_path),
                created_at=now,
                updated_at=now,
            )
            source.filesystem_entry = filesystem_entry
            session.add(filesystem_entry)
            task = AppTask(
                user_id=app_user.id,
                library_id=library.id,
                kind="ingest",
                status="queued",
                title=f"Ingest: {display_title}",
                origin_surface=origin_surface,
                origin_thread_id=origin_thread_id,
                source_file_id=source.id,
                input_json={
                    "filename": filename,
                    "declared_media_type": declared_media_type,
                    "media_type": media_type,
                    "byte_size": len(payload),
                    "tag_ids": selected_tag_slugs,
                    "user_guidance": user_guidance,
                    "folder_id": parent.id,
                    "virtual_name": entry_name,
                    "virtual_path": entry_path,
                    "metadata": metadata or {},
                },
                state_json={"stage": "queued", "source_id": source.id},
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(task)
            await session.commit()
            await session.refresh(source)
            await session.refresh(filesystem_entry)
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
                tag_ids=selected_tag_slugs,
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
        await self._billing.record_fixed_cost_event(
            clerk_user_id=clerk_user_id,
            operation_kind="semantic_split_preview",
            origin_surface="web",
            platform_cost_usd=self._settings.billing_semantic_split_cost_usd,
            model=self._settings.openai_agent_model,
            note=f"Semantic split preview for {source_kind}.",
        )
        normalized_split = SemanticSplitResult(
            strategy_label=split_result.strategy_label,
            tags=_dedupe_text_values(split_result.tags, limit=AUTO_TAG_LIMIT),
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
            source = await self._source_for_user(
                session,
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                writable=True,
            )
            if source.status == "processing":
                raise ValueError("Wait for the current source processing task to finish before re-splitting.")
            library = source.library
            await self._ensure_vector_store(session, library=library, app_user=app_user)

            raw_tag_ids = list(tag_ids) if tag_ids is not None else ([source.tag_slug] if source.tag_slug else [])
            selected_tag_slugs = await self._tags_by_ids(session, library_id=library.id, tag_ids=raw_tag_ids)
            replaced_chunk_count = len(source.chunks)
            source.tag_slug = selected_tag_slugs[0] if selected_tag_slugs else None
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
                    "tag_ids": selected_tag_slugs,
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
                tag_ids=selected_tag_slugs,
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
            source = await self._source_for_user(
                session,
                clerk_user_id=clerk_user_id,
                source_id=source_id,
                writable=True,
            )
            if source.status == "processing":
                raise ValueError("Wait for the current source processing task to finish before updating tags.")
            library = source.library
            await self._ensure_vector_store(session, library=library, app_user=app_user)

            selected_tag_slugs = await self._tags_by_ids(session, library_id=library.id, tag_ids=tag_ids)
            previous_status = source.status
            previous_error_message = source.error_message
            previous_tag_ids = [source.tag_slug] if source.tag_slug else []
            chunk_count = len(source.chunks)
            should_reindex_source = source.status == "ready" or source.openai_vector_file_id is not None
            source.tag_slug = selected_tag_slugs[0] if selected_tag_slugs else None
            if should_reindex_source:
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
                    "tag_ids": selected_tag_slugs,
                    "previous_tag_ids": previous_tag_ids,
                    "previous_status": previous_status,
                    "previous_error_message": previous_error_message,
                    "chunk_count": chunk_count,
                    "openai_vector_file_id": source.openai_vector_file_id,
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
                "source_reindex_queued clerk_user_id=%s source_id=%s task_id=%s chunks=%s vector_file=%s tags=%s",
                clerk_user_id,
                source.id,
                task.id,
                chunk_count,
                source.openai_vector_file_id is not None,
                len(selected_tag_slugs),
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
            task.state_json = {"stage": "validating_tag", "source_id": source.id}
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
                selected_tag_slugs = await self._tags_by_ids(session, library_id=library.id, tag_ids=tag_ids)
                source.tag_slug = selected_tag_slugs[0] if selected_tag_slugs else source.tag_slug
                task.state_json = {"stage": "reading_source_payload", "source_id": source.id}
                task.updated_at = _utcnow()
                await session.commit()

                payload = await self._storage.get_bytes(key=source.storage_key)
                source_kind = cast(SourceKind, source.source_kind)
                if source.tag_slug is None:
                    task.state_json = {"stage": "assigning_tag", "source_id": source.id}
                    task.updated_at = _utcnow()
                    await session.commit()
                    extracted_text, _strategy_hint = await self._extract_searchable_text(
                        filename=source.original_filename,
                        source_kind=source_kind,
                        media_type=source.media_type,
                        payload=payload,
                    )
                    existing_tag_slugs = sorted(
                        dict.fromkeys(item.tag_slug for item in library.sources if item.tag_slug)
                    )
                    tag_guidance_parts = [user_guidance or ""]
                    if existing_tag_slugs:
                        tag_guidance_parts.append(
                            "Existing file tags: "
                            + ", ".join(existing_tag_slugs[:50])
                            + ". Prefer one of these when it fits."
                        )
                    tag_split = await self._split_semantic_text(
                        source_id=source.id,
                        source_title=source.display_title,
                        source_kind=source_kind,
                        extracted_text=extracted_text,
                        user_guidance="\n".join(part for part in tag_guidance_parts if part).strip() or None,
                    )
                    await self._billing.record_fixed_cost_event(
                        clerk_user_id=clerk_user_id,
                        operation_kind="semantic_tag_assignment",
                        origin_surface=task.origin_surface,
                        platform_cost_usd=self._settings.billing_semantic_split_cost_usd,
                        event_key=f"source:{source.id}:task:{task.id}:tag_assignment",
                        task_id=task.id,
                        source_file_id=source.id,
                        model=self._settings.openai_agent_model,
                        note="Semantic split call used for single-tag assignment.",
                    )
                    auto_tag_slugs = _dedupe_text_values([slugify(tag) for tag in tag_split.tags], limit=AUTO_TAG_LIMIT)
                    source.tag_slug = auto_tag_slugs[0] if auto_tag_slugs else None
                    source.updated_at = _utcnow()
                task.state_json = {"stage": "uploading_original_file", "source_id": source.id}
                task.updated_at = _utcnow()
                await session.commit()
                if len(payload) <= self._settings.openai_file_upload_max_bytes:
                    source.openai_original_file_id = await self._openai.upload_file_bytes(
                        filename=_virtual_name(source),
                        payload=payload,
                        purpose=_openai_file_purpose(source_kind=source_kind),
                    )
                    source.openai_original_file_purpose = _openai_file_purpose(source_kind=source_kind)
                elif source_kind == "pdf":
                    source.openai_original_file_id = None
                    source.openai_original_file_purpose = None
                    logger.info(
                        "source_original_file_upload_skipped source_id=%s bytes=%s max_bytes=%s reason=oversized_pdf",
                        source.id,
                        len(payload),
                        self._settings.openai_file_upload_max_bytes,
                    )
                else:
                    raise ValueError(
                        f"{source.original_filename} is larger than the OpenAI file upload limit "
                        f"({self._settings.openai_file_upload_max_bytes} bytes)."
                    )
                task.state_json = {
                    "stage": "extracting_text",
                    "source_id": source.id,
                    "openai_original_file_id": source.openai_original_file_id,
                }
                task.updated_at = _utcnow()
                await session.commit()

                index_materials = await self._vector_index_materials(
                    source=source, source_kind=source_kind, payload=payload
                )
                source.ingest_strategy = index_materials[0].strategy_label
                task.state_json = {
                    "stage": "indexing_source_file",
                    "source_id": source.id,
                    "strategy_hint": source.ingest_strategy,
                    "part_count": len(index_materials),
                }
                task.updated_at = _utcnow()
                await session.commit()
                index_started_at = perf_counter()
                vector_file_ids = await self._replace_source_vector_files(
                    source=source,
                    materials=index_materials,
                    tag_slugs=[cast(str, source.tag_slug)] if source.tag_slug else [],
                )
                await self._billing.record_fixed_cost_event(
                    clerk_user_id=clerk_user_id,
                    operation_kind="vector_index",
                    origin_surface=task.origin_surface,
                    platform_cost_usd=self._settings.billing_vector_index_file_cost_usd * len(index_materials),
                    event_key=f"source:{source.id}:task:{task.id}:vector_index",
                    task_id=task.id,
                    source_file_id=source.id,
                    note=f"OpenAI vector indexing for {len(index_materials)} file part(s).",
                )
                task.state_json = {
                    "stage": "indexing_source_file",
                    "source_id": source.id,
                    "openai_vector_file_id": source.openai_vector_file_id,
                    "openai_vector_file_ids": vector_file_ids,
                }
                task.updated_at = _utcnow()
                await session.commit()
                logger.info(
                    "source_ingest_vector_indexed clerk_user_id=%s source_id=%s task_id=%s vector_file_id=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    source.openai_vector_file_id,
                    (perf_counter() - index_started_at) * 1000,
                )

                source.status = "ready"
                source.error_message = None
                source.updated_at = _utcnow()
                library.updated_at = _utcnow()
                research_candidate_count = await self._sync_linked_research_candidates(
                    session,
                    source_id=source.id,
                    status="ingested",
                    error_message=None,
                )
                task.status = "completed"
                task.state_json = {
                    "stage": "completed",
                    "source_id": source.id,
                    "chunk_count": len(source.chunks),
                    "tag_count": 1 if source.tag_slug else 0,
                    "openai_vector_file_id": source.openai_vector_file_id,
                    "openai_vector_file_count": len(vector_file_ids),
                }
                task.result_json = {
                    "source_id": source.id,
                    "chunk_count": len(source.chunks),
                    "openai_vector_file_id": source.openai_vector_file_id,
                    "openai_vector_file_ids": vector_file_ids,
                }
                task.error_message = None
                task.completed_at = _utcnow()
                task.updated_at = _utcnow()
                await session.commit()
                logger.info(
                    "source_ingested clerk_user_id=%s source_id=%s task_id=%s kind=%s indexed_file=%s research_candidates=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    source.source_kind,
                    source.openai_vector_file_id is not None,
                    research_candidate_count,
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
                    _clear_source_vector_state(source)
                    for chunk in source.chunks:
                        chunk.openai_file_id = None
                source.status = "failed"
                source.error_message = str(exc)
                source.updated_at = _utcnow()
                research_candidate_count = await self._sync_linked_research_candidates(
                    session,
                    source_id=source.id,
                    status="failed",
                    error_message=str(exc),
                )
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
                    "source_ingest_failed clerk_user_id=%s source_id=%s task_id=%s research_candidates=%s error=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    research_candidate_count,
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
                _clear_source_vector_state(source)
                for chunk in source.chunks:
                    chunk.openai_file_id = None
            source.status = "failed"
            source.error_message = "Ingest cancelled during shutdown."
            source.updated_at = _utcnow()
            research_candidate_count = await self._sync_linked_research_candidates(
                session,
                source_id=source.id,
                status="failed",
                error_message="Ingest cancelled during shutdown.",
            )
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
                "source_ingest_cancelled clerk_user_id=%s source_id=%s task_id=%s research_candidates=%s",
                clerk_user_id,
                source.id,
                task.id,
                research_candidate_count,
            )

    async def _sync_linked_research_candidates(
        self,
        session: Any,
        *,
        source_id: str,
        status: Literal["ingested", "failed"],
        error_message: str | None,
    ) -> int:
        status_filter = (
            ResearchImportCandidate.status.in_(["ingesting", "ingested"])
            if status == "failed"
            else ResearchImportCandidate.status == "ingesting"
        )
        result = await session.execute(
            update(ResearchImportCandidate)
            .where(
                ResearchImportCandidate.linked_source_file_id == source_id,
                status_filter,
            )
            .values(status=status, error_message=error_message, updated_at=_utcnow())
        )
        return int(getattr(result, "rowcount", 0) or 0)

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
            task_input = task.input_object
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
                selected_tag_slugs = await self._tags_by_ids(session, library_id=library.id, tag_ids=tag_ids)
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
                await self._billing.record_fixed_cost_event(
                    clerk_user_id=clerk_user_id,
                    operation_kind="semantic_resplit",
                    origin_surface=task.origin_surface,
                    platform_cost_usd=self._settings.billing_semantic_split_cost_usd,
                    event_key=f"source:{source.id}:task:{task.id}:semantic_resplit",
                    task_id=task.id,
                    source_file_id=source.id,
                    model=self._settings.openai_agent_model,
                    note="Semantic re-split request.",
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
                auto_tag_slugs = _dedupe_text_values([slugify(tag) for tag in split_result.tags], limit=AUTO_TAG_LIMIT)
                source.tag_slug = (
                    selected_tag_slugs[0]
                    if selected_tag_slugs
                    else auto_tag_slugs[0]
                    if auto_tag_slugs
                    else source.tag_slug
                )
                normalized_chunks = _normalize_chunk_drafts(split_result.chunks, fallback_text=extracted_text)

                task.state_json = {"stage": "replacing_old_chunks", "source_id": source.id}
                task.updated_at = _utcnow()
                await session.commit()
                replaced_chunk_count = len(source.chunks)
                cleanup_result = await self._delete_openai_chunk_files_for_source(source=source)
                for chunk in source.chunks:
                    chunk.openai_file_id = None
                source.chunks.clear()
                source.updated_at = _utcnow()
                old_chunks_replaced = True
                task.state_json = {
                    "stage": "saving_semantic_chunks",
                    "source_id": source.id,
                    "chunk_count": len(normalized_chunks),
                    "saved_chunk_count": 0,
                    "cleanup": cleanup_result,
                }
                task.updated_at = _utcnow()
                await session.commit()

                save_started_at = perf_counter()
                for draft in normalized_chunks:
                    chunk = SemanticChunk(
                        id=new_id(),
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
                        status="ready",
                        created_at=_utcnow(),
                        updated_at=_utcnow(),
                    )
                    session.add(chunk)
                    task.state_json = {
                        "stage": "saving_semantic_chunks",
                        "source_id": source.id,
                        "chunk_count": len(normalized_chunks),
                        "saved_chunk_count": draft.sequence,
                    }
                    task.updated_at = _utcnow()
                    await session.commit()
                logger.info(
                    "source_resplit_chunks_saved clerk_user_id=%s source_id=%s task_id=%s chunks=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    len(normalized_chunks),
                    (perf_counter() - save_started_at) * 1000,
                )

                index_materials = await self._vector_index_materials(
                    source=source, source_kind=source_kind, payload=payload
                )
                vector_file_ids = await self._replace_source_vector_files(
                    source=source,
                    materials=index_materials,
                    tag_slugs=[cast(str, source.tag_slug)] if source.tag_slug else [],
                )
                await self._billing.record_fixed_cost_event(
                    clerk_user_id=clerk_user_id,
                    operation_kind="vector_index",
                    origin_surface=task.origin_surface,
                    platform_cost_usd=self._settings.billing_vector_index_file_cost_usd * len(index_materials),
                    event_key=f"source:{source.id}:task:{task.id}:vector_index",
                    task_id=task.id,
                    source_file_id=source.id,
                    note=f"OpenAI vector indexing for {len(index_materials)} file part(s).",
                )

                source.status = "ready"
                source.error_message = None
                source.ingest_strategy = index_materials[0].strategy_label
                source.updated_at = _utcnow()
                library.updated_at = _utcnow()
                task.status = "completed"
                task.state_json = {
                    "stage": "completed",
                    "source_id": source.id,
                    "chunk_count": len(normalized_chunks),
                    "replaced_chunk_count": replaced_chunk_count,
                    "tag_count": 1 if source.tag_slug else 0,
                    "openai_vector_file_id": source.openai_vector_file_id,
                    "openai_vector_file_count": len(vector_file_ids),
                }
                task.result_json = {
                    "source_id": source.id,
                    "chunk_count": len(normalized_chunks),
                    "replaced_chunk_count": replaced_chunk_count,
                    "openai_vector_file_id": source.openai_vector_file_id,
                    "openai_vector_file_ids": vector_file_ids,
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

            task_input = task.input_object
            previous_status = str(task_input.get("previous_status") or "failed")
            previous_error_raw = task_input.get("previous_error_message")
            previous_error_message = previous_error_raw if isinstance(previous_error_raw, str) else None
            state = task.state_object
            old_chunks_replaced = state.get("stage") in {"replacing_old_chunks", "saving_semantic_chunks"}
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
        reindexed_source_file = False
        cleanup_failed_file_ids: list[str] = []
        await self._database.ensure_ready()
        async with self._database.session() as session:
            source = await self._source_by_id(session, source_id=source_id)
            task = await self._task_by_id(session, task_id=task_id)
            library = source.library
            task_input = task.input_object
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
            task.state_json = {"stage": "validating_tag", "source_id": source.id}
            task.updated_at = now
            await session.commit()
            logger.info(
                "source_reindex_started clerk_user_id=%s source_id=%s task_id=%s vector_file=%s",
                clerk_user_id,
                source.id,
                task.id,
                source.openai_vector_file_id is not None,
            )

            try:
                selected_tag_slugs = await self._tags_by_ids(session, library_id=library.id, tag_ids=tag_ids)
                source.tag_slug = selected_tag_slugs[0] if selected_tag_slugs else None
                task.state_json = {
                    "stage": "reindexing_source_file",
                    "source_id": source.id,
                    "openai_vector_file_id": source.openai_vector_file_id,
                }
                task.updated_at = _utcnow()
                await session.commit()

                payload = await self._storage.get_bytes(key=source.storage_key)
                source_kind = cast(SourceKind, source.source_kind)
                index_materials = await self._vector_index_materials(
                    source=source, source_kind=source_kind, payload=payload
                )
                source.ingest_strategy = index_materials[0].strategy_label
                vector_file_ids = await self._replace_source_vector_files(
                    source=source,
                    materials=index_materials,
                    tag_slugs=[cast(str, source.tag_slug)] if source.tag_slug else [],
                )
                await self._billing.record_fixed_cost_event(
                    clerk_user_id=clerk_user_id,
                    operation_kind="vector_reindex",
                    origin_surface=task.origin_surface,
                    platform_cost_usd=self._settings.billing_vector_index_file_cost_usd * len(index_materials),
                    event_key=f"source:{source.id}:task:{task.id}:vector_reindex",
                    task_id=task.id,
                    source_file_id=source.id,
                    note=f"OpenAI vector reindexing for {len(index_materials)} file part(s).",
                )
                reindexed_source_file = True
                task.state_json = {
                    "stage": "reindexing_source_file",
                    "source_id": source.id,
                    "openai_vector_file_id": source.openai_vector_file_id,
                    "openai_vector_file_count": len(vector_file_ids),
                }
                task.updated_at = _utcnow()
                await session.commit()

                source.status = "ready"
                source.error_message = None
                source.updated_at = _utcnow()
                library.updated_at = _utcnow()
                task.status = "completed"
                task.state_json = {
                    "stage": "completed",
                    "source_id": source.id,
                    "openai_vector_file_id": source.openai_vector_file_id,
                    "tag_count": 1 if source.tag_slug else 0,
                    "openai_vector_file_count": len(vector_file_ids),
                    "cleanup_failed_file_count": len(cleanup_failed_file_ids),
                }
                task.result_json = {
                    "source_id": source.id,
                    "openai_vector_file_id": source.openai_vector_file_id,
                    "openai_vector_file_ids": vector_file_ids,
                    "tag_count": 1 if source.tag_slug else 0,
                    "cleanup_failed_file_count": len(cleanup_failed_file_ids),
                }
                task.error_message = None
                task.completed_at = _utcnow()
                task.updated_at = _utcnow()
                await session.commit()
                logger.info(
                    "source_reindex_completed clerk_user_id=%s source_id=%s task_id=%s vector_file_id=%s cleanup_failures=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    source.openai_vector_file_id,
                    len(cleanup_failed_file_ids),
                    (perf_counter() - reindex_started_at) * 1000,
                )
            except Exception as exc:
                source = await self._source_by_id(session, source_id=source_id, populate_existing=True)
                task = await self._task_by_id(session, task_id=task_id)
                source.status = "failed" if reindexed_source_file else previous_status
                source.error_message = str(exc) if reindexed_source_file else previous_error_message
                source.updated_at = _utcnow()
                task.status = "failed"
                task.state_json = {
                    "stage": "failed",
                    "source_id": source.id,
                    "reindexed_source_file": reindexed_source_file,
                    "cleanup_failed_file_count": len(cleanup_failed_file_ids),
                }
                task.error_message = str(exc)
                task.completed_at = _utcnow()
                task.updated_at = _utcnow()
                await session.commit()
                logger.error(
                    "source_reindex_failed clerk_user_id=%s source_id=%s task_id=%s reindexed_source_file=%s error=%s duration_ms=%.1f",
                    clerk_user_id,
                    source.id,
                    task.id,
                    reindexed_source_file,
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

            task_input = task.input_object
            previous_status = str(task_input.get("previous_status") or "failed")
            if previous_status not in {"ready", "failed"}:
                previous_status = "failed"
            previous_error_raw = task_input.get("previous_error_message")
            previous_error_message = previous_error_raw if isinstance(previous_error_raw, str) else None
            state = task.state_object
            reindexed_source_file = state.get("stage") == "reindexing_source_file"
            source.status = previous_status
            source.error_message = previous_error_message
            source.updated_at = _utcnow()
            task.status = "cancelled"
            task.state_json = {
                "stage": "cancelled",
                "source_id": source.id,
                "reindexed_source_file": reindexed_source_file,
            }
            task.error_message = "Tag reindex cancelled during shutdown."
            task.completed_at = _utcnow()
            task.updated_at = _utcnow()
            await session.commit()
            logger.warning(
                "source_reindex_cancelled clerk_user_id=%s source_id=%s task_id=%s reindexed_source_file=%s",
                clerk_user_id,
                source.id,
                task.id,
                reindexed_source_file,
            )

    async def search(
        self,
        *,
        clerk_user_id: str,
        request: SearchRequest,
        origin_surface: str = "system",
    ) -> SearchResponse:
        hits = await self.search_chunks(clerk_user_id=clerk_user_id, request=request, origin_surface=origin_surface)
        return SearchResponse(query=request.query, hits=hits)

    async def search_chunks(
        self,
        *,
        clerk_user_id: str,
        request: SearchRequest,
        origin_surface: str = "system",
    ) -> list[ChunkHit]:
        normalized_query = request.query.strip()
        if not normalized_query:
            return []
        searched_vector_store = False
        output: list[ChunkHit] = []
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
            library = await self._library_for_request(session, app_user=app_user, library_id=request.library_id)
            if library.openai_vector_store_id is None:
                return []
            selected_source_ids = await _resolve_source_or_filesystem_entry_ids(
                session,
                library_id=library.id,
                source_or_entry_ids=request.selected_source_ids,
            )
            if request.selected_source_ids and not selected_source_ids:
                return []
            tag_slugs = await self._tags_by_ids(session, library_id=library.id, tag_ids=request.tag_ids)
            include_created_at_filters = _library_supports_vector_created_at_filter(library)
            filters = build_filter_groups(
                source_ids=selected_source_ids,
                source_kinds=request.source_kinds,
                virtual_paths=request.virtual_paths,
                tag_slugs=tag_slugs,
                tag_match_mode=request.tag_match_mode,
                created_after=request.created_after,
                created_before=request.created_before,
                include_created_at_filters=include_created_at_filters,
            )
            candidates = await self._openai.search_vector_store(
                vector_store_id=library.openai_vector_store_id,
                query=normalized_query,
                max_results=request.max_results,
                filters=filters,
            )
            searched_vector_store = True
            source_ids = [
                str(candidate.attributes.get("source_id"))
                for candidate in candidates
                if isinstance(candidate.attributes.get("source_id"), str)
            ]
            vector_file_ids = [candidate.openai_file_id for candidate in candidates]
            if not source_ids and not vector_file_ids:
                sources = []
            else:
                sources = (
                    (
                        await session.execute(
                            select(SourceFile)
                            .options(
                                selectinload(SourceFile.chunks),
                                selectinload(SourceFile.filesystem_entry),
                            )
                            .where(
                                SourceFile.library_id == library.id,
                                or_(
                                    SourceFile.id.in_(source_ids),
                                    SourceFile.openai_vector_file_id.in_(vector_file_ids),
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            source_map = {source.id: source for source in sources}
            source_by_vector_file_id = {
                source.openai_vector_file_id: source for source in sources if source.openai_vector_file_id is not None
            }
            seen_source_ids: set[str] = set()
            for candidate in candidates:
                candidate_source_id = candidate.attributes.get("source_id")
                source = (
                    source_map.get(candidate_source_id)
                    if isinstance(candidate_source_id, str)
                    else source_by_vector_file_id.get(candidate.openai_file_id)
                )
                if source is None or source.id in seen_source_ids:
                    continue
                if not _source_matches_request_filters(
                    source,
                    selected_source_ids=selected_source_ids,
                    source_kinds=request.source_kinds,
                    virtual_paths=request.virtual_paths,
                    tag_ids=tag_slugs,
                    tag_match_mode=request.tag_match_mode,
                    created_after=request.created_after,
                    created_before=request.created_before,
                ):
                    continue
                seen_source_ids.add(source.id)
                output.append(self._source_hit(source, candidate=candidate))
        if searched_vector_store:
            await self._billing.record_fixed_cost_event(
                clerk_user_id=clerk_user_id,
                operation_kind="vector_search",
                origin_surface=origin_surface,
                platform_cost_usd=self._settings.billing_vector_search_cost_usd,
                note="OpenAI vector-store search request.",
            )
        return output

    async def branch_search(self, *, clerk_user_id: str, request: BranchSearchRequest) -> BranchSearchResponse:
        levels: list[BranchSearchLevel] = []
        current_hits = await self.search_chunks(
            clerk_user_id=clerk_user_id,
            request=SearchRequest(
                query=request.query,
                library_id=request.library_id,
                selected_source_ids=request.selected_source_ids,
                source_kinds=request.source_kinds,
                virtual_paths=request.virtual_paths,
                tag_ids=request.tag_ids,
                tag_match_mode=request.tag_match_mode,
                created_after=request.created_after,
                created_before=request.created_before,
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
                        library_id=request.library_id,
                        selected_source_ids=request.selected_source_ids,
                        source_kinds=request.source_kinds,
                        virtual_paths=request.virtual_paths,
                        tag_ids=request.tag_ids,
                        tag_match_mode=request.tag_match_mode,
                        created_after=request.created_after,
                        created_before=request.created_before,
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
            .where(UserLibrary.user_id == app_user.id, UserLibrary.visibility == "private", UserLibrary.slug.is_(None))
            .order_by(UserLibrary.created_at.asc())
            .options(
                selectinload(UserLibrary.sources).selectinload(SourceFile.chunks),
                selectinload(UserLibrary.sources).selectinload(SourceFile.filesystem_entry),
                selectinload(UserLibrary.filesystem_entries),
            )
        )
        if library is not None:
            return library
        library = UserLibrary(
            user_id=app_user.id,
            title=f"{app_user.display_name or app_user.clerk_user_id}'s indexed file library",
            description="Personal OpenAI vector-store backed file library",
            visibility="private",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        session.add(library)
        await session.commit()
        await session.refresh(library)
        return await self._library_for_user(session, app_user=app_user)

    async def library_for_user(self, session: Any, *, app_user: AppUser) -> UserLibrary:
        return await self._library_for_user(session, app_user=app_user)

    async def _library_for_request(
        self,
        session: Any,
        *,
        app_user: AppUser,
        library_id: str | None,
    ) -> UserLibrary:
        if library_id is None:
            return await self._library_for_user(session, app_user=app_user)
        library = await session.scalar(
            select(UserLibrary)
            .where(
                UserLibrary.id == library_id,
                or_(UserLibrary.user_id == app_user.id, UserLibrary.visibility == "public"),
            )
            .options(
                selectinload(UserLibrary.sources).selectinload(SourceFile.chunks),
                selectinload(UserLibrary.sources).selectinload(SourceFile.filesystem_entry),
                selectinload(UserLibrary.filesystem_entries),
            )
        )
        if library is None:
            raise FileNotFoundError("Library not found.")
        return library

    async def _writable_library_for_request(
        self,
        session: Any,
        *,
        app_user: AppUser,
        library_id: str | None,
    ) -> UserLibrary:
        library = await self._library_for_request(session, app_user=app_user, library_id=library_id)
        if library.user_id != app_user.id:
            raise PermissionError("This library is read-only for the active user.")
        return library

    async def _root_entry_for_library(self, session: Any, *, library: UserLibrary) -> FilesystemEntry:
        root = await session.scalar(
            select(FilesystemEntry)
            .where(FilesystemEntry.library_id == library.id, FilesystemEntry.normalized_path == "/")
            .options(selectinload(FilesystemEntry.library), selectinload(FilesystemEntry.children))
        )
        if root is not None:
            return root
        now = _utcnow()
        root = FilesystemEntry(
            id=new_id(),
            library_id=library.id,
            kind="folder",
            name="",
            normalized_name="",
            path="/",
            normalized_path="/",
            created_at=now,
            updated_at=now,
        )
        session.add(root)
        await session.flush()
        return root

    async def _filesystem_entry_for_user(
        self,
        session: Any,
        *,
        clerk_user_id: str,
        entry_id: str,
        library_id: str | None = None,
        writable: bool = False,
    ) -> FilesystemEntry:
        app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
        library = await self._library_for_request(session, app_user=app_user, library_id=library_id)
        if writable and library.user_id != app_user.id:
            raise PermissionError("This library is read-only for the active user.")
        entry = await session.scalar(
            select(FilesystemEntry)
            .join(UserLibrary, UserLibrary.id == FilesystemEntry.library_id)
            .where(FilesystemEntry.id == entry_id, FilesystemEntry.library_id == library.id)
            .options(
                selectinload(FilesystemEntry.library),
                selectinload(FilesystemEntry.parent),
                selectinload(FilesystemEntry.source_file).selectinload(SourceFile.chunks),
            )
        )
        if entry is None:
            raise FileNotFoundError("Filesystem entry not found.")
        return entry

    async def _breadcrumbs_for_entry(self, session: Any, *, entry: FilesystemEntry) -> list[FilesystemBreadcrumb]:
        entries = (
            (await session.execute(select(FilesystemEntry).where(FilesystemEntry.library_id == entry.library_id)))
            .scalars()
            .all()
        )
        entries_by_id = {item.id: item for item in entries}
        chain: list[FilesystemEntry] = []
        cursor: FilesystemEntry | None = entry
        while cursor is not None:
            chain.append(cursor)
            cursor = entries_by_id.get(cursor.parent_id) if cursor.parent_id is not None else None
        chain.reverse()
        return [FilesystemBreadcrumb(id=item.id, name=item.name or "Files", path=item.path) for item in chain]

    async def _assert_unique_child_name(
        self,
        session: Any,
        *,
        parent: FilesystemEntry,
        name: str,
        excluded_entry_id: str | None,
    ) -> None:
        candidate_path = _join_entry_path(parent.path, name)
        existing_id = await session.scalar(
            select(FilesystemEntry.id).where(
                FilesystemEntry.library_id == parent.library_id,
                FilesystemEntry.normalized_path == _normalize_entry_path(candidate_path),
            )
        )
        if existing_id is not None and existing_id != excluded_entry_id:
            raise ValueError("Another entry already exists at that path.")

    async def _unique_child_name(self, session: Any, *, parent: FilesystemEntry, base_name: str) -> str:
        candidate = _clean_entry_name(base_name)
        suffix = 2
        while True:
            existing_id = await session.scalar(
                select(FilesystemEntry.id).where(
                    FilesystemEntry.library_id == parent.library_id,
                    FilesystemEntry.normalized_path == _normalize_entry_path(_join_entry_path(parent.path, candidate)),
                )
            )
            if existing_id is None:
                return candidate
            candidate = _suffix_entry_name(base_name, suffix)
            suffix += 1

    async def _assert_not_descendant(
        self,
        session: Any,
        *,
        entry: FilesystemEntry,
        candidate_parent: FilesystemEntry,
    ) -> None:
        del session
        if candidate_parent.id == entry.id:
            raise ValueError("A folder cannot be moved into itself.")
        descendant_prefix = f"{entry.normalized_path.rstrip('/')}/"
        if candidate_parent.normalized_path.startswith(descendant_prefix):
            raise ValueError("A folder cannot be moved into one of its descendants.")

    async def _ensure_vector_store(self, session: Any, *, library: UserLibrary, app_user: AppUser) -> None:
        if library.openai_vector_store_id is not None:
            return
        library.openai_vector_store_id = await self._openai.create_vector_store(
            name=library.title,
            metadata={"clerk_user_id": app_user.clerk_user_id, "library_id": library.id},
        )
        library.updated_at = _utcnow()
        await session.flush()

    async def _source_for_user(
        self,
        session: Any,
        *,
        clerk_user_id: str,
        source_id: str,
        library_id: str | None = None,
        writable: bool = False,
    ) -> SourceFile:
        app_user = await self.ensure_app_user(session, clerk_user_id=clerk_user_id)
        library = await self._library_for_request(session, app_user=app_user, library_id=library_id)
        if writable and library.user_id != app_user.id:
            raise PermissionError("This library is read-only for the active user.")
        source = await session.scalar(
            select(SourceFile)
            .where(SourceFile.id == source_id, SourceFile.library_id == library.id)
            .options(
                selectinload(SourceFile.chunks),
                selectinload(SourceFile.library),
                selectinload(SourceFile.filesystem_entry),
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
                selectinload(SourceFile.filesystem_entry),
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
        vector_file_ids = _source_vector_file_ids(source)
        if vector_file_ids:
            vector_store_id = source.library.openai_vector_store_id
            for file_id in vector_file_ids:
                if vector_store_id is not None:
                    await self._openai.detach_file_from_vector_store(
                        vector_store_id=vector_store_id,
                        file_id=file_id,
                    )
                await self._openai.delete_file(file_id=file_id)
        original_file_deleted = source.openai_original_file_id is not None
        if source.openai_original_file_id is not None:
            await self._openai.delete_file(file_id=source.openai_original_file_id)
        logger.info(
            "source_openai_files_cleaned source_id=%s openai_chunk_files=%s openai_vector_file=%s openai_original_file=%s duration_ms=%.1f",
            source.id,
            chunk_cleanup["chunk_file_count"],
            bool(vector_file_ids),
            original_file_deleted,
            (perf_counter() - cleanup_started_at) * 1000,
        )
        return {
            "chunk_file_count": chunk_cleanup["chunk_file_count"],
            "vector_file_deleted": bool(vector_file_ids),
            "vector_file_count": len(vector_file_ids),
            "original_file_deleted": original_file_deleted,
        }

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

    async def _replace_source_vector_files(
        self,
        *,
        source: SourceFile,
        materials: list[VectorIndexMaterial],
        tag_slugs: list[str],
    ) -> list[str]:
        vector_store_id = source.library.openai_vector_store_id
        if vector_store_id is None:
            raise ValueError("Source library does not have an OpenAI vector store.")
        if not materials:
            raise ValueError("No vector index material was produced.")
        old_file_ids = _source_vector_file_ids(source)
        new_file_ids: list[str] = []
        first_attributes: OpenAIAttributes | None = None
        try:
            for material in materials:
                attributes = build_vector_attributes(
                    source_id=source.id,
                    source_kind=source.source_kind,
                    virtual_path=_virtual_path(source),
                    virtual_name=material.filename,
                    source_created_at=source.created_at,
                    tag_slugs=tag_slugs,
                    split_part=material.part_index if material.part_count > 1 else None,
                    split_part_count=material.part_count if material.part_count > 1 else None,
                    page_start=material.start_page,
                    page_end=material.end_page,
                )
                new_file_id = await self._openai.upload_file_bytes(
                    filename=material.filename,
                    payload=material.payload,
                    purpose="assistants",
                )
                try:
                    await self._openai.attach_file_to_vector_store(
                        vector_store_id=vector_store_id,
                        file_id=new_file_id,
                        attributes=attributes,
                    )
                except Exception:
                    await self._openai.delete_file(file_id=new_file_id)
                    raise
                new_file_ids.append(new_file_id)
                first_attributes = first_attributes or attributes
        except Exception:
            for file_id in new_file_ids:
                try:
                    await self._openai.detach_file_from_vector_store(vector_store_id=vector_store_id, file_id=file_id)
                    await self._openai.delete_file(file_id=file_id)
                except Exception as cleanup_error:
                    logger.warning(
                        "source_vector_new_file_cleanup_failed source_id=%s file_id=%s error=%s",
                        source.id,
                        file_id,
                        cleanup_error,
                    )
            raise

        source.openai_vector_file_id = new_file_ids[0]
        source.vector_attributes = first_attributes or {}
        metadata = dict(source.source_metadata)
        metadata["openai_vector_file_ids"] = new_file_ids
        metadata["openai_vector_part_count"] = len(new_file_ids)
        source.source_metadata = cast(SourceMetadata, metadata)
        for old_file_id in old_file_ids:
            if old_file_id in new_file_ids:
                continue
            try:
                await self._openai.detach_file_from_vector_store(vector_store_id=vector_store_id, file_id=old_file_id)
                await self._openai.delete_file(file_id=old_file_id)
            except Exception as cleanup_error:
                logger.warning(
                    "source_vector_reindex_old_file_cleanup_failed source_id=%s file_id=%s error=%s",
                    source.id,
                    old_file_id,
                    cleanup_error,
                )
        return new_file_ids

    async def _vector_index_materials(
        self,
        *,
        source: SourceFile,
        source_kind: SourceKind,
        payload: bytes,
    ) -> list[VectorIndexMaterial]:
        max_upload_bytes = self._settings.openai_file_upload_max_bytes
        if source_kind == "pdf" and len(payload) > max_upload_bytes:
            target_bytes = min(max_upload_bytes, self._settings.openai_pdf_split_target_bytes)
            pdf_parts = split_pdf_payload_by_size(
                filename=_virtual_name(source),
                payload=payload,
                max_part_bytes=target_bytes,
            )
            if len(pdf_parts) > self._settings.openai_pdf_split_max_parts:
                raise ValueError(
                    f"{source.original_filename} needs {len(pdf_parts)} PDF parts, which exceeds the configured "
                    f"limit of {self._settings.openai_pdf_split_max_parts}."
                )
            return [
                VectorIndexMaterial(
                    filename=part.filename,
                    media_type="application/pdf",
                    payload=part.payload,
                    strategy_label="openai_vector_pdf_split_file",
                    part_index=index,
                    part_count=len(pdf_parts),
                    start_page=part.start_page,
                    end_page=part.end_page,
                )
                for index, part in enumerate(pdf_parts, start=1)
            ]
        material = await self._vector_index_material(source=source, source_kind=source_kind, payload=payload)
        if len(material.payload) > max_upload_bytes:
            raise ValueError(
                f"{material.filename} is larger than the OpenAI file upload limit ({max_upload_bytes} bytes)."
            )
        return [material]

    async def _tags_by_ids(self, session: Any, *, library_id: str, tag_ids: list[str]) -> list[str]:
        del session, library_id
        return bounded_tag_ids(tag_ids)

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

    async def _vector_index_material(
        self,
        *,
        source: SourceFile,
        source_kind: SourceKind,
        payload: bytes,
    ) -> VectorIndexMaterial:
        if source_kind in {"pdf", "text"}:
            return VectorIndexMaterial(
                filename=_virtual_name(source),
                media_type=source.media_type,
                payload=payload,
                strategy_label="openai_vector_source_file",
            )
        if source_kind == "conversation" and source.media_type.startswith("text/"):
            return VectorIndexMaterial(
                filename=_virtual_name(source),
                media_type="text/plain",
                payload=payload,
                strategy_label="openai_vector_conversation_text",
            )
        if source_kind in {"audio", "video", "conversation"}:
            transcript, transcript_payload = await self._openai.transcribe_audio_bytes(
                filename=source.original_filename,
                payload=payload,
            )
            del transcript_payload
            index_text = transcript or f"{source.original_filename}\n\nNo transcript text was returned."
            return VectorIndexMaterial(
                filename=f"{Path(_virtual_name(source)).stem or source.id}.transcript.txt",
                media_type="text/plain",
                payload=index_text.encode("utf-8"),
                strategy_label="openai_vector_transcript_file",
            )
        metadata_text = "\n".join(
            [
                source.display_title,
                f"Virtual path: {_virtual_path(source)}",
                f"Original filename: {source.original_filename}",
                f"Media type: {source.media_type}",
                f"Source kind: {source.source_kind}",
            ]
        )
        return VectorIndexMaterial(
            filename=f"{Path(_virtual_name(source)).stem or source.id}.metadata.txt",
            media_type="text/plain",
            payload=metadata_text.encode("utf-8"),
            strategy_label="openai_vector_metadata_file",
        )

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
            strategy_label="pdf_page_batched_semantic",
            tags=_dedupe_text_values(tags, limit=AUTO_TAG_LIMIT),
            chunks=chunks,
        )

    def _library_summary(
        self,
        library: UserLibrary,
        *,
        app_user: AppUser,
        personal_library_id: str,
    ) -> LibrarySummary:
        loaded_sources = library.__dict__.get("sources")
        source_count = len(loaded_sources) if isinstance(loaded_sources, list) else 0
        return LibrarySummary(
            id=library.id,
            title=library.title,
            description=library.description,
            visibility=cast(Any, library.visibility),
            slug=library.slug,
            source_count=source_count,
            writable=library.user_id == app_user.id,
            personal=library.id == personal_library_id,
            created_at=library.created_at,
            updated_at=library.updated_at,
        )

    def _source_summary(self, source: SourceFile) -> LibrarySourceSummary:
        metadata = source.source_metadata
        return LibrarySourceSummary(
            id=source.id,
            filesystem_entry_id=source.filesystem_entry.id if source.filesystem_entry is not None else None,
            virtual_name=_virtual_name(source),
            virtual_path=_virtual_path(source),
            display_title=source.display_title,
            original_filename=source.original_filename,
            media_type=source.media_type,
            source_kind=cast(SourceKind, source.source_kind),
            status=cast(SourceStatus, source.status),
            byte_size=source.byte_size,
            chunk_count=len(source.chunks),
            description=_metadata_string(metadata, "description"),
            summary=_metadata_string(metadata, "summary"),
            suggested_tags=_metadata_string_list(metadata, "suggested_tags"),
            error_message=source.error_message,
            created_at=source.created_at,
            updated_at=source.updated_at,
            tags=[self._tag_summary_from_slug(source.tag_slug)] if source.tag_slug else [],
            openai_original_file_id=source.openai_original_file_id,
            openai_original_file_purpose=source.openai_original_file_purpose,
            openai_vector_file_id=source.openai_vector_file_id,
            vector_attributes=source.vector_attributes or None,
        )

    def _source_detail(self, source: SourceFile) -> LibrarySourceDetail:
        summary = self._source_summary(source)
        download_url = self._storage.build_download_url(
            key=source.storage_key,
            filename=source.original_filename,
            media_type=source.media_type,
            inline=True,
        )
        if download_url is not None and download_url.startswith("/"):
            download_url = f"{self._settings.normalized_app_base_url}{download_url}"
        return LibrarySourceDetail(
            **summary.model_dump(mode="python"),
            storage_provider=source.storage_provider,
            storage_key=source.storage_key,
            download_url=download_url,
            download_url_expires_in_seconds=self._settings.storage_download_url_ttl_seconds,
            content_retrieval_source_ids=[source.id],
            ingest_strategy=source.ingest_strategy,
            metadata=source.source_metadata,
            chunks=[self._chunk_summary(chunk) for chunk in sorted(source.chunks, key=lambda item: item.sequence)],
        )

    def _filesystem_entry_summary(self, entry: FilesystemEntry) -> FilesystemEntrySummary:
        source = entry.source_file
        if source is None:
            return FilesystemEntrySummary(
                id=entry.id,
                kind=cast(Any, entry.kind),
                name=entry.name,
                path=entry.path,
                parent_id=entry.parent_id,
                created_at=entry.created_at,
                updated_at=entry.updated_at,
            )
        metadata = source.source_metadata
        return FilesystemEntrySummary(
            id=entry.id,
            kind=cast(Any, entry.kind),
            name=entry.name,
            path=entry.path,
            parent_id=entry.parent_id,
            source_id=source.id,
            source_kind=cast(SourceKind, source.source_kind),
            media_type=source.media_type,
            status=cast(SourceStatus, source.status),
            byte_size=source.byte_size,
            chunk_count=len(source.chunks),
            description=_metadata_string(metadata, "description"),
            summary=_metadata_string(metadata, "summary"),
            suggested_tags=_metadata_string_list(metadata, "suggested_tags"),
            tags=[self._tag_summary_from_slug(source.tag_slug)] if source.tag_slug else [],
            openai_original_file_id=source.openai_original_file_id,
            openai_vector_file_id=source.openai_vector_file_id,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
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
            tags=[source.tag_slug] if source.tag_slug else [],
            locator=_chunk_locator(chunk),
            openai_file_id=chunk.openai_file_id,
            attributes=attributes,
        )

    def _source_hit(self, source: SourceFile, *, candidate: VectorSearchCandidate) -> ChunkHit:
        text = candidate.text.strip()
        if not text:
            text = f"{source.display_title}\n{_virtual_path(source)}"
        return ChunkHit(
            chunk_id=f"source:{source.id}",
            source_file_id=source.id,
            source_title=source.display_title,
            original_filename=source.original_filename,
            score=candidate.score,
            title=source.display_title,
            summary="OpenAI vector-store match from the indexed source file.",
            text=text,
            tags=[source.tag_slug] if source.tag_slug else [],
            locator=ChunkLocator(type="generated"),
            openai_file_id=candidate.openai_file_id,
            attributes=candidate.attributes,
        )

    @staticmethod
    def _tag_summary_from_slug(
        slug: str, *, source: Literal["auto", "manual"] = "auto", source_count: int = 0
    ) -> TagSummary:
        return TagSummary(
            id=slug,
            name=slug,
            slug=slug,
            color=None,
            source=source,
            source_count=source_count,
        )


def build_vector_attributes(
    *,
    source_id: str,
    source_kind: str,
    virtual_path: str,
    virtual_name: str,
    source_created_at: datetime,
    tag_slugs: list[str],
    split_part: int | None = None,
    split_part_count: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
) -> dict[str, str | float | bool]:
    created_at = _as_utc(source_created_at)
    attributes: dict[str, str | float | bool] = {
        "attributes_version": float(VECTOR_ATTRIBUTES_VERSION),
        "index_kind": "source_file",
        "source_id": source_id,
        "source_kind": source_kind,
        "virtual_path": virtual_path[:256],
        "virtual_name": virtual_name[:256],
        "created_at": created_at.timestamp(),
        "tag": _tag_metadata_value(tag_slugs),
    }
    if split_part is not None:
        attributes["split_part"] = float(split_part)
    if split_part_count is not None:
        attributes["split_part_count"] = float(split_part_count)
    if page_start is not None:
        attributes["page_start"] = float(page_start)
    if page_end is not None:
        attributes["page_end"] = float(page_end)
    return attributes


def build_filter_groups(
    *,
    source_ids: Sequence[str],
    source_kinds: Sequence[str],
    tag_slugs: Sequence[str],
    tag_match_mode: TagMatchMode,
    virtual_paths: Sequence[str] = (),
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    include_created_at_filters: bool = True,
) -> ComparisonFilter | CompoundFilter | None:
    filters: list[ComparisonFilter | CompoundFilter] = []
    if source_ids:
        filters.append(_or_filter("source_id", source_ids))
    if source_kinds:
        filters.append(_or_filter("source_kind", source_kinds))
    if virtual_paths:
        filters.append(_or_filter("virtual_path", [_normalize_filter_path(path) for path in virtual_paths]))
    if include_created_at_filters and created_after is not None:
        filters.append({"type": "gte", "key": "created_at", "value": _as_utc(created_after).timestamp()})
    if include_created_at_filters and created_before is not None:
        filters.append({"type": "lte", "key": "created_at", "value": _as_utc(created_before).timestamp()})
    if tag_slugs:
        del tag_match_mode
        filters.append(_tag_slug_filter(tag_slugs[0]))
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
        f"Virtual path: {_virtual_path(source)}",
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


def split_pdf_payload_by_size(*, filename: str, payload: bytes, max_part_bytes: int) -> list[PdfPayloadPart]:
    if max_part_bytes <= 0:
        raise ValueError("PDF split target size must be positive.")
    if len(payload) <= max_part_bytes:
        return [PdfPayloadPart(filename=filename, payload=payload, start_page=1, end_page=_pdf_page_count(payload))]

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(payload))
    page_count = len(reader.pages)
    if page_count == 0:
        raise ValueError("PDF has no pages to split.")

    parts: list[PdfPayloadPart] = []
    current_pages: list[int] = []
    part_index = 1
    for page_index in range(page_count):
        candidate_pages = [*current_pages, page_index]
        candidate_payload = _write_pdf_pages(reader, candidate_pages)
        if len(candidate_payload) <= max_part_bytes:
            current_pages = candidate_pages
            continue
        if not current_pages:
            raise ValueError(f"PDF page {page_index + 1} is larger than the OpenAI file upload limit after splitting.")
        part_payload = _write_pdf_pages(reader, current_pages)
        parts.append(
            PdfPayloadPart(
                filename=_pdf_part_filename(filename, part_index),
                payload=part_payload,
                start_page=current_pages[0] + 1,
                end_page=current_pages[-1] + 1,
            )
        )
        part_index += 1
        single_page_payload = _write_pdf_pages(reader, [page_index])
        if len(single_page_payload) > max_part_bytes:
            raise ValueError(f"PDF page {page_index + 1} is larger than the OpenAI file upload limit after splitting.")
        current_pages = [page_index]

    if current_pages:
        parts.append(
            PdfPayloadPart(
                filename=_pdf_part_filename(filename, part_index),
                payload=_write_pdf_pages(reader, current_pages),
                start_page=current_pages[0] + 1,
                end_page=current_pages[-1] + 1,
            )
        )
    logger.info(
        "pdf_payload_split filename=%s bytes=%s max_part_bytes=%s pages=%s parts=%s",
        filename,
        len(payload),
        max_part_bytes,
        page_count,
        len(parts),
    )
    return parts


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


def _clean_library_slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:96] or "library"


def _clean_tag_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())[:80]


def _clean_tag_slug(value: str | None) -> str | None:
    if value is None:
        return None
    slug = slugify(value)
    return slug if slug else None


def _clean_tag_color(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned[:32] or None


def _dedupe_text_values(values: Sequence[str], *, limit: int | None = None) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_tag_name(value)
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
        if limit is not None and len(output) >= limit:
            break
    return output


def bounded_tag_ids(tag_ids: Sequence[str]) -> list[str]:
    output = list(dict.fromkeys(slug for tag_id in tag_ids if (slug := _clean_tag_slug(tag_id)) is not None))
    if len(output) > 1:
        raise ValueError("A source can have at most one tag.")
    return output


def _pdf_page_count(payload: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(BytesIO(payload)).pages)


def _write_pdf_pages(reader: Any, page_indexes: Sequence[int]) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for page_index in page_indexes:
        writer.add_page(reader.pages[page_index])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _pdf_part_filename(filename: str, part_index: int) -> str:
    path = Path(filename)
    suffix = path.suffix or ".pdf"
    stem = path.stem or "document"
    return f"{stem}.part-{part_index:03d}{suffix}"


def _source_vector_file_ids(source: SourceFile) -> list[str]:
    output: list[str] = []
    metadata_ids = source.source_metadata.get("openai_vector_file_ids")
    if isinstance(metadata_ids, list):
        output.extend(item for item in metadata_ids if item.strip())
    if source.openai_vector_file_id:
        output.append(source.openai_vector_file_id)
    return list(dict.fromkeys(output))


def _clear_source_vector_state(source: SourceFile) -> None:
    source.openai_vector_file_id = None
    source.vector_attributes = {}
    metadata = dict(source.source_metadata)
    metadata.pop("openai_vector_file_ids", None)
    metadata.pop("openai_vector_part_count", None)
    source.source_metadata = cast(SourceMetadata, metadata)


def _metadata_string(metadata: Mapping[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _metadata_string_list(metadata: Mapping[str, object], key: str) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _metadata_search_text(value: Mapping[str, object]) -> str:
    parts: list[str] = []
    for key in ["description", "summary", "authors", "published_at", "doi", "arxiv_id", "suggested_tags"]:
        item = value.get(key)
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            parts.extend(child for child in item if isinstance(child, str))
    return " ".join(parts).casefold()


async def _resolve_source_or_filesystem_entry_ids(
    session: Any,
    *,
    library_id: str,
    source_or_entry_ids: Sequence[str],
) -> list[str]:
    normalized_ids = list(dict.fromkeys(item.strip() for item in source_or_entry_ids if item.strip()))
    if not normalized_ids:
        return []
    rows = (
        await session.execute(
            select(SourceFile.id, FilesystemEntry.id)
            .outerjoin(FilesystemEntry, FilesystemEntry.source_file_id == SourceFile.id)
            .where(
                SourceFile.library_id == library_id,
                or_(
                    SourceFile.id.in_(normalized_ids),
                    FilesystemEntry.id.in_(normalized_ids),
                ),
            )
        )
    ).all()
    source_id_by_input: dict[str, str] = {}
    for source_id, filesystem_entry_id in rows:
        source_id_by_input[str(source_id)] = str(source_id)
        if filesystem_entry_id is not None:
            source_id_by_input[str(filesystem_entry_id)] = str(source_id)
    return list(dict.fromkeys(source_id_by_input[item] for item in normalized_ids if item in source_id_by_input))


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
    created_after: datetime | None,
    created_before: datetime | None,
) -> bool:
    source = chunk.source_file
    if selected_source_ids and source.id not in set(selected_source_ids):
        return False
    if source_kinds and source.source_kind not in set(source_kinds):
        return False
    source_created_at = _as_utc(source.created_at)
    if created_after is not None and source_created_at < _as_utc(created_after):
        return False
    if created_before is not None and source_created_at > _as_utc(created_before):
        return False
    if tag_ids:
        del tag_match_mode
        return source.tag_slug == tag_ids[0]
    return True


def _source_matches_request_filters(
    source: SourceFile,
    *,
    selected_source_ids: Sequence[str],
    source_kinds: Sequence[str],
    virtual_paths: Sequence[str],
    tag_ids: Sequence[str],
    tag_match_mode: TagMatchMode,
    created_after: datetime | None,
    created_before: datetime | None,
) -> bool:
    if selected_source_ids and source.id not in set(selected_source_ids):
        return False
    if source_kinds and source.source_kind not in set(source_kinds):
        return False
    if virtual_paths and _virtual_path(source) not in {_normalize_filter_path(path) for path in virtual_paths}:
        return False
    source_created_at = _as_utc(source.created_at)
    if created_after is not None and source_created_at < _as_utc(created_after):
        return False
    if created_before is not None and source_created_at > _as_utc(created_before):
        return False
    if tag_ids:
        del tag_match_mode
        return source.tag_slug == tag_ids[0]
    return True


def _library_supports_vector_created_at_filter(library: UserLibrary) -> bool:
    for source in library.sources:
        if source.openai_vector_file_id is None:
            continue
        attributes = source.vector_attributes
        if attributes.get("attributes_version") != float(VECTOR_ATTRIBUTES_VERSION):
            return False
        if attributes.get("index_kind") != "source_file":
            return False
        if not isinstance(attributes.get("created_at"), (int, float)):
            return False
    return True


def _or_filter(key: str, values: Sequence[str]) -> ComparisonFilter | CompoundFilter:
    if len(values) == 1:
        return {"type": "eq", "key": key, "value": values[0]}
    return {"type": "or", "filters": [{"type": "eq", "key": key, "value": value} for value in values]}


def _tag_slug_filter(slug: str) -> ComparisonFilter:
    return {"type": "eq", "key": "tag", "value": slug}


def _normalize_filter_path(path: str) -> str:
    stripped = path.strip()
    if not stripped:
        return "/"
    return stripped if stripped.startswith("/") else f"/{stripped}"


def _tag_metadata_value(tag_slugs: Sequence[str]) -> str:
    unique_slugs = list(dict.fromkeys(slug for slug in tag_slugs if slug))
    output: list[str] = []
    current_length = 0
    for slug in unique_slugs:
        next_length = current_length + len(slug) + (1 if output else 0)
        if next_length > 256:
            break
        output.append(slug)
        current_length = next_length
    return ",".join(output)


def _clean_entry_name(value: str) -> str:
    cleaned = value.replace("\\", "/").split("/")[-1].strip()
    if cleaned in {"", ".", ".."}:
        raise ValueError("Entry name is required.")
    return cleaned[:255]


def _normalize_entry_name(value: str) -> str:
    return _clean_entry_name(value).casefold()


def _normalize_entry_path(value: str) -> str:
    path = "/" + "/".join(part for part in value.replace("\\", "/").split("/") if part)
    return path.casefold()


def _join_entry_path(parent_path: str, name: str) -> str:
    parent_parts = [part for part in parent_path.replace("\\", "/").split("/") if part]
    name_parts = [part for part in name.replace("\\", "/").split("/") if part]
    joined = "/".join([*parent_parts, *name_parts])
    return f"/{joined}" if joined else "/"


def _suffix_entry_name(base_name: str, suffix: int) -> str:
    cleaned = _clean_entry_name(base_name)
    if "." in cleaned and not cleaned.startswith("."):
        stem, extension = cleaned.rsplit(".", 1)
        return f"{stem} ({suffix}).{extension}"[:255]
    return f"{cleaned} ({suffix})"[:255]


def _sort_filesystem_entries(entries: Sequence[FilesystemEntry]) -> list[FilesystemEntry]:
    return sorted(entries, key=lambda entry: (0 if entry.kind == "folder" else 1, entry.name.casefold(), entry.id))


def _virtual_name(source: SourceFile) -> str:
    if source.filesystem_entry is not None:
        return source.filesystem_entry.name
    return source.original_filename


def _virtual_path(source: SourceFile) -> str:
    if source.filesystem_entry is not None:
        return source.filesystem_entry.path
    return f"/{source.original_filename}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _openai_file_purpose(*, source_kind: str) -> FilePurpose:
    del source_kind
    return "user_data"


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

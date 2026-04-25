from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import logging
import mimetypes
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Any, Literal, cast

from openai.types.file_purpose import FilePurpose
from openai.types.shared_params.comparison_filter import ComparisonFilter
from openai.types.shared_params.compound_filter import CompoundFilter
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from backend.app.core.config import AppSettings
from backend.app.db.session import DatabaseManager
from backend.app.integrations.openai_gateway import OpenAIGateway
from backend.app.models import AppUser, SemanticChunk, SourceFile, SourceTagLink, Tag, UserLibrary
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
    SourceKind,
    SourceStatus,
    TagMatchMode,
    TagSummary,
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
                    return selected_tag_ids.issubset(source_tag_ids) if tag_match_mode == "all" else bool(selected_tag_ids & source_tag_ids)

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
            await self._storage.delete_object(key=source.storage_key)
            await session.delete(source)
            await session.commit()
            logger.info("source_deleted clerk_user_id=%s source_id=%s", clerk_user_id, source_id)
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

            try:
                selected_tags = await self._tags_by_ids(session, library_id=library.id, tag_ids=tag_ids)
                source.tag_links = [SourceTagLink(source_file_id=source.id, tag_id=tag.id) for tag in selected_tags]
                source.openai_original_file_id = await self._openai.upload_file_bytes(
                    filename=filename,
                    payload=payload,
                    purpose=_openai_file_purpose(source_kind=source_kind),
                )

                extracted_text, strategy_hint = await self._extract_searchable_text(
                    filename=filename,
                    source_kind=source_kind,
                    media_type=media_type,
                    payload=payload,
                )
                source.ingest_strategy = strategy_hint
                split_result = await self._openai.split_semantically(
                    source_title=source.display_title,
                    source_kind=source.source_kind,
                    text=extracted_text,
                    user_guidance=user_guidance,
                )
                auto_tags = await self._ensure_auto_tags(session, library=library, tag_names=split_result.tags)
                merged_tags = _merge_tags([*selected_tags, *auto_tags])
                source.tag_links = [SourceTagLink(source_file_id=source.id, tag_id=tag.id) for tag in merged_tags]

                normalized_chunks = _normalize_chunk_drafts(split_result.chunks, fallback_text=extracted_text)
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

                source.status = "ready"
                source.error_message = None
                source.updated_at = _utcnow()
                library.updated_at = _utcnow()
                await session.commit()
                await session.refresh(source)
            except Exception as exc:
                source.status = "failed"
                source.error_message = str(exc)
                source.updated_at = _utcnow()
                await session.commit()
                raise

            logger.info(
                "source_ingested clerk_user_id=%s source_id=%s kind=%s chunks=%s",
                clerk_user_id,
                source.id,
                source.source_kind,
                len(source.chunks),
            )
            return IngestFinalizeResponse(source=self._source_summary(source), task=None)

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
                await session.execute(
                    select(SemanticChunk)
                    .options(
                        selectinload(SemanticChunk.source_file)
                        .selectinload(SourceFile.tag_links)
                        .selectinload(SourceTagLink.tag)
                    )
                    .where(SemanticChunk.id.in_(chunk_ids))
                )
            ).scalars().all()
            chunk_map = {chunk.id: chunk for chunk in chunks}
            candidates_by_chunk_id = {str(candidate.attributes.get("chunk_id")): candidate for candidate in candidates}
            output: list[ChunkHit] = []
            for chunk_id in chunk_ids:
                chunk = chunk_map.get(chunk_id)
                candidate = candidates_by_chunk_id.get(chunk_id)
                if chunk is None or candidate is None:
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
                selectinload(SourceFile.tag_links).selectinload(SourceTagLink.tag),
            )
        )
        if source is None:
            raise FileNotFoundError("Source not found.")
        return source

    async def _tags_by_ids(self, session: Any, *, library_id: str, tag_ids: list[str]) -> list[Tag]:
        if not tag_ids:
            return []
        records = (
            await session.execute(select(Tag).where(Tag.library_id == library_id, Tag.id.in_(tag_ids)))
        ).scalars().all()
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
            transcript, transcript_payload = await self._openai.transcribe_audio_bytes(filename=filename, payload=payload)
            del transcript_payload
            return transcript, "conversation_transcript_semantic"
        return decode_text(payload) if media_type.startswith("text/") else f"{filename}\n\nNo text extraction is available.", "basic_metadata"

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
            tags=[self._tag_summary(link.tag) for link in sorted(source.tag_links, key=lambda link: link.tag.name.casefold())],
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


def _utcnow() -> datetime:
    return datetime.now(UTC)

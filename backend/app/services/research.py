from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import html
import logging
from pathlib import Path
import re
from time import perf_counter
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from backend.app.core.config import AppSettings
from backend.app.db.session import DatabaseManager
from backend.app.integrations.openai_gateway import OpenAIGateway
from backend.app.models import AppTask, AppUser, ResearchImportCandidate, UserLibrary
from backend.app.schemas import (
    IngestFinalizeResponse,
    ResearchCandidateIngestRequest,
    ResearchCandidateIngestResponse,
    ResearchCandidateListResponse,
    ResearchCandidateSourceType,
    ResearchCandidateStatus,
    ResearchCandidateStatusUpdateResponse,
    ResearchImportCandidateSummary,
    ResearchImportCreateRequest,
    ResearchImportResponse,
    TaskSummary,
)
from backend.app.services.sources import SourceService

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+")
SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
TAG_RE = re.compile(r"(?s)<[^>]+>")
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
ARXIV_ID_RE = re.compile(r"(?i)(?:arxiv\.org/(?:abs|pdf)/)?(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)")


@dataclass(frozen=True, slots=True)
class ResearchMaterial:
    filename: str
    media_type: str
    payload: bytes
    source_type: ResearchCandidateSourceType
    title: str
    normalized_url: str | None
    content_hash: str
    provenance: dict[str, object]


class ResearchImportService:
    """Coordinate research discovery, review candidates, and canonical source ingestion."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        database: DatabaseManager,
        sources: SourceService,
        openai: OpenAIGateway,
    ) -> None:
        self._settings = settings
        self._database = database
        self._sources = sources
        self._openai = openai

    async def create_import(
        self,
        *,
        clerk_user_id: str,
        payload: ResearchImportCreateRequest,
        origin_surface: str,
        origin_thread_id: str | None = None,
    ) -> ResearchImportResponse:
        started_at = perf_counter()
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user, library = await self._active_user_and_library(session, clerk_user_id=clerk_user_id)
            task = AppTask(
                user_id=app_user.id,
                library_id=library.id,
                kind="research_import",
                status="running",
                title=f"Research import: {_seed_title(payload)}",
                origin_surface=origin_surface,
                origin_thread_id=origin_thread_id,
                input_json=payload.model_dump(mode="json", exclude={"payload_base64"}),
                state_json={"stage": "started"},
                started_at=_utcnow(),
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)

        seed_material = await self._material_from_seed(payload)
        seed_source: IngestFinalizeResponse | None = None
        candidates: list[ResearchImportCandidateSummary] = []
        duplicate_count = 0

        try:
            if payload.ingest_seed:
                seed_source = await self._sources.ingest_source(
                    clerk_user_id=clerk_user_id,
                    filename=seed_material.filename,
                    declared_media_type=seed_material.media_type,
                    payload=seed_material.payload,
                    tag_ids=payload.tag_ids,
                    user_guidance=None,
                    origin_surface=origin_surface,
                    origin_thread_id=origin_thread_id,
                    folder_id=payload.folder_id,
                    virtual_name=seed_material.filename,
                    metadata={
                        **seed_material.provenance,
                        "research_import_task_id": task.id,
                        "content_hash": seed_material.content_hash,
                    },
                )

            candidate_inputs = await self._candidate_inputs_from_seed(
                seed_material=seed_material,
                request=payload,
            )
            if not payload.ingest_seed:
                candidate_inputs.insert(
                    0,
                    {
                        "source_type": seed_material.source_type,
                        "url": seed_material.provenance.get("url"),
                        "normalized_url": seed_material.normalized_url,
                        "title": seed_material.title,
                        "rationale": "Initial seed queued for review instead of direct ingestion.",
                        "score": 1.0,
                        "depth": 0,
                        "provenance": seed_material.provenance | {
                            "content_text": seed_material.payload.decode("utf-8", errors="replace")
                            if seed_material.media_type.startswith("text/")
                            else None,
                            "filename": seed_material.filename,
                            "media_type": seed_material.media_type,
                            "content_hash": seed_material.content_hash,
                        },
                        "content_hash": seed_material.content_hash,
                    }
                )

            async with self._database.session() as session:
                task = await self._task_for_user(session, clerk_user_id=clerk_user_id, task_id=task.id)
                app_user, library = await self._active_user_and_library(session, clerk_user_id=clerk_user_id)
                persisted, duplicate_count = await self._persist_candidate_inputs(
                    session=session,
                    app_user=app_user,
                    library=library,
                    task=task,
                    candidate_inputs=candidate_inputs[: payload.max_pending_candidates],
                )
                task.status = "completed"
                task.completed_at = _utcnow()
                task.state_json = {
                    "stage": "completed",
                    "candidate_count": len(persisted),
                    "duplicate_count": duplicate_count,
                    "seed_source_id": seed_source.source.id if seed_source is not None else None,
                }
                task.result_json = {
                    "candidate_count": len(persisted),
                    "duplicate_count": duplicate_count,
                    "seed_source_id": seed_source.source.id if seed_source is not None else None,
                }
                task.updated_at = _utcnow()
                await session.commit()
                candidates = [self._candidate_summary(candidate) for candidate in persisted]
                task_summary = _research_task_summary(task)
        except Exception:
            async with self._database.session() as session:
                failed_task = await session.get(AppTask, task.id)
                if failed_task is not None:
                    failed_task.status = "failed"
                    failed_task.error_message = "Research import failed."
                    failed_task.state_json = {"stage": "failed"}
                    failed_task.completed_at = _utcnow()
                    failed_task.updated_at = _utcnow()
                    await session.commit()
            raise

        logger.info(
            "research_import_completed clerk_user_id=%s task_id=%s candidates=%s duplicates=%s duration_ms=%.1f",
            clerk_user_id,
            task.id,
            len(candidates),
            duplicate_count,
            (perf_counter() - started_at) * 1000,
        )
        return ResearchImportResponse(
            task=task_summary,
            seed_source=seed_source.source if seed_source is not None else None,
            candidates=candidates,
            duplicate_count=duplicate_count,
        )

    async def list_candidates(
        self,
        *,
        clerk_user_id: str,
        task_id: str | None,
        status: ResearchCandidateStatus | None,
        page: int,
        page_size: int,
    ) -> ResearchCandidateListResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=clerk_user_id)
            query = (
                select(ResearchImportCandidate)
                .join(UserLibrary, UserLibrary.id == ResearchImportCandidate.library_id)
                .where(UserLibrary.user_id == app_user.id)
                .options(
                    selectinload(ResearchImportCandidate.linked_source_file),
                    selectinload(ResearchImportCandidate.parent_source_file),
                )
            )
            if task_id is not None:
                query = query.where(ResearchImportCandidate.task_id == task_id)
            if status is not None:
                query = query.where(ResearchImportCandidate.status == status)
            count_query = select(func.count()).select_from(query.subquery())
            total_count = int(await session.scalar(count_query) or 0)
            rows = (
                (
                    await session.execute(
                        query.order_by(
                            ResearchImportCandidate.created_at.desc(),
                            ResearchImportCandidate.id.desc(),
                        )
                        .offset(max(page - 1, 0) * page_size)
                        .limit(page_size)
                    )
                )
                .scalars()
                .all()
            )
            end = max(page - 1, 0) * page_size + page_size
            return ResearchCandidateListResponse(
                candidates=[self._candidate_summary(candidate) for candidate in rows],
                total_count=total_count,
                page=page,
                page_size=page_size,
                has_more=end < total_count,
            )

    async def update_candidate_status(
        self,
        *,
        clerk_user_id: str,
        candidate_ids: list[str],
        status: str,
    ) -> ResearchCandidateStatusUpdateResponse:
        if status not in {"approved", "rejected", "pending"}:
            raise ValueError("Candidate status must be approved, rejected, or pending.")
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user, _ = await self._active_user_and_library(session, clerk_user_id=clerk_user_id)
            candidates = (
                (
                    await session.execute(
                        select(ResearchImportCandidate)
                        .join(UserLibrary, UserLibrary.id == ResearchImportCandidate.library_id)
                        .where(
                            UserLibrary.user_id == app_user.id,
                            ResearchImportCandidate.id.in_(
                                list(dict.fromkeys(candidate_id for candidate_id in candidate_ids if candidate_id))
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(candidates) != len(set(candidate_ids)):
                raise FileNotFoundError("One or more research candidates were not found.")
            now = _utcnow()
            for candidate in candidates:
                if candidate.status == "ingested":
                    raise ValueError("Ingested candidates cannot be moved back to review.")
                candidate.status = status
                candidate.updated_at = now
            await session.commit()
            return ResearchCandidateStatusUpdateResponse(
                candidates=[self._candidate_summary(candidate) for candidate in candidates]
            )

    async def ingest_approved_candidates(
        self,
        *,
        clerk_user_id: str,
        payload: ResearchCandidateIngestRequest,
        origin_surface: str,
        origin_thread_id: str | None = None,
    ) -> ResearchCandidateIngestResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user, _ = await self._active_user_and_library(session, clerk_user_id=clerk_user_id)
            query = (
                select(ResearchImportCandidate)
                .join(UserLibrary, UserLibrary.id == ResearchImportCandidate.library_id)
                .where(UserLibrary.user_id == app_user.id, ResearchImportCandidate.status == "approved")
            )
            if payload.candidate_ids:
                query = query.where(ResearchImportCandidate.id.in_(payload.candidate_ids))
            if payload.task_id is not None:
                query = query.where(ResearchImportCandidate.task_id == payload.task_id)
            candidates = (
                (await session.execute(query.order_by(ResearchImportCandidate.created_at.asc()))).scalars().all()
            )
            if not candidates:
                return ResearchCandidateIngestResponse(ingested=[], candidates=[])
            now = _utcnow()
            for candidate in candidates:
                candidate.status = "ingesting"
                candidate.updated_at = now
            await session.commit()

        ingested: list[IngestFinalizeResponse] = []
        updated_candidates: list[ResearchImportCandidateSummary] = []
        for candidate in candidates:
            try:
                material = await self._material_from_candidate(candidate)
                ingest_response = await self._sources.ingest_source(
                    clerk_user_id=clerk_user_id,
                    filename=material.filename,
                    declared_media_type=material.media_type,
                    payload=material.payload,
                    tag_ids=payload.tag_ids if payload.tag_ids is not None else [],
                    user_guidance=None,
                    origin_surface=origin_surface,
                    origin_thread_id=origin_thread_id,
                    folder_id=payload.folder_id,
                    virtual_name=material.filename,
                    metadata={
                        **material.provenance,
                        "research_import_task_id": candidate.task_id,
                        "research_candidate_id": candidate.id,
                        "content_hash": material.content_hash,
                    },
                )
                async with self._database.session() as session:
                    current = await self._candidate_for_user(
                        session, clerk_user_id=clerk_user_id, candidate_id=candidate.id
                    )
                    current.status = "ingested"
                    current.linked_source_file_id = ingest_response.source.id
                    current.content_hash = material.content_hash
                    current.provenance_json = dict(current.provenance_json or {}) | material.provenance
                    current.error_message = None
                    current.updated_at = _utcnow()
                    await session.commit()
                    updated_candidates.append(self._candidate_summary(current))
                ingested.append(ingest_response)
            except Exception as exc:
                async with self._database.session() as session:
                    current = await self._candidate_for_user(
                        session, clerk_user_id=clerk_user_id, candidate_id=candidate.id
                    )
                    current.status = "failed"
                    current.error_message = str(exc)
                    current.updated_at = _utcnow()
                    await session.commit()
                    updated_candidates.append(self._candidate_summary(current))

        return ResearchCandidateIngestResponse(ingested=ingested, candidates=updated_candidates)

    async def _candidate_inputs_from_seed(
        self,
        *,
        seed_material: ResearchMaterial,
        request: ResearchImportCreateRequest,
    ) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        text = seed_material.payload.decode("utf-8", errors="replace")
        for url in _extract_urls(text):
            normalized_url = _normalize_url(url)
            candidates.append(
                {
                    "source_type": _source_type_from_url(url, default="url"),
                    "url": url,
                    "normalized_url": normalized_url,
                    "title": _title_from_url(url),
                    "rationale": "URL explicitly present in the seed material.",
                    "score": 0.9,
                    "depth": 1,
                    "provenance": {"discovery_source": "seed_url", "url": url},
                    "content_hash": None,
                }
            )
        if request.discover_references and request.max_candidates_per_source > 0:
            discovery_query = _discovery_query(seed_material=seed_material, request=request)
            discovery = await self._openai.discover_research_candidates(
                query=discovery_query,
                max_candidates=min(
                    request.max_candidates_per_source,
                    self._settings.research_import_max_candidates_per_source,
                ),
            )
            for item in discovery.candidates:
                candidates.append(
                    {
                        "source_type": item.source_type,
                        "url": item.url,
                        "normalized_url": _normalize_url(item.url),
                        "title": item.title,
                        "rationale": item.rationale,
                        "score": item.score,
                        "depth": 1,
                        "provenance": {"discovery_source": "openai_web_search", "url": item.url},
                        "content_hash": None,
                    }
                )
        return candidates

    async def _persist_candidate_inputs(
        self,
        *,
        session: Any,
        app_user: AppUser,
        library: UserLibrary,
        task: AppTask,
        candidate_inputs: list[dict[str, object]],
    ) -> tuple[list[ResearchImportCandidate], int]:
        existing_urls = {
            str(value)
            for value in (
                await session.execute(
                    select(ResearchImportCandidate.normalized_url).where(
                        ResearchImportCandidate.library_id == library.id,
                        ResearchImportCandidate.normalized_url.is_not(None),
                    )
                )
            ).scalars()
            if value is not None
        }
        existing_hashes = {
            str(value)
            for value in (
                await session.execute(
                    select(ResearchImportCandidate.content_hash).where(
                        ResearchImportCandidate.library_id == library.id,
                        ResearchImportCandidate.content_hash.is_not(None),
                    )
                )
            ).scalars()
            if value is not None
        }
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        persisted: list[ResearchImportCandidate] = []
        duplicate_count = 0
        now = _utcnow()
        for item in candidate_inputs:
            normalized_url = cast(str | None, item.get("normalized_url"))
            content_hash = cast(str | None, item.get("content_hash"))
            if normalized_url and (normalized_url in existing_urls or normalized_url in seen_urls):
                duplicate_count += 1
                continue
            if content_hash and (content_hash in existing_hashes or content_hash in seen_hashes):
                duplicate_count += 1
                continue
            if normalized_url:
                seen_urls.add(normalized_url)
            if content_hash:
                seen_hashes.add(content_hash)
            candidate = ResearchImportCandidate(
                library_id=library.id,
                user_id=app_user.id,
                task_id=task.id,
                status="pending",
                source_type=str(item["source_type"]),
                url=cast(str | None, item.get("url")),
                normalized_url=normalized_url,
                title=str(item["title"])[:512],
                rationale=cast(str | None, item.get("rationale")),
                score=cast(float | None, item.get("score")),
                depth=int(cast(int | str, item.get("depth") or 0)),
                provenance_json=cast(dict[str, object], item.get("provenance") or {}),
                content_hash=content_hash,
                created_at=now,
                updated_at=now,
            )
            session.add(candidate)
            persisted.append(candidate)
        await session.commit()
        for candidate in persisted:
            await session.refresh(candidate)
        return persisted, duplicate_count

    async def _material_from_seed(self, request: ResearchImportCreateRequest) -> ResearchMaterial:
        seed_type = request.seed_type
        if seed_type in {"url", "pdf_url", "arxiv_url"}:
            if request.url is None or not request.url.strip():
                raise ValueError("A URL seed requires url.")
            return await self._fetch_url_material(
                url=request.url.strip(),
                title=request.title,
                forced_source_type="pdf" if seed_type == "pdf_url" else "arxiv" if seed_type == "arxiv_url" else None,
            )
        if seed_type == "uploaded_file":
            if request.payload_base64 is None:
                raise ValueError("An uploaded_file seed requires payload_base64.")
            try:
                payload = base64.b64decode(request.payload_base64, validate=True)
            except binascii.Error as exc:
                raise ValueError("payload_base64 must be valid base64 data.") from exc
            if len(payload) > self._settings.research_import_max_fetch_bytes:
                raise ValueError("Uploaded research seed exceeds the configured byte limit.")
            filename = request.filename or "research-seed.bin"
            media_type = request.media_type or "application/octet-stream"
            content_hash = _content_hash(payload)
            return ResearchMaterial(
                filename=filename,
                media_type=media_type,
                payload=payload,
                source_type="uploaded_file",
                title=request.title or Path(filename).stem or filename,
                normalized_url=None,
                content_hash=content_hash,
                provenance={"seed_type": seed_type, "filename": filename, "media_type": media_type},
            )
        text = (request.text or "").strip()
        if not text:
            raise ValueError("A text or LinkedIn export seed requires text.")
        bounded_text = text[: self._settings.research_import_max_text_chars]
        filename = request.filename or ("linkedin-export.txt" if seed_type == "linkedin_export" else "research-seed.txt")
        payload = bounded_text.encode("utf-8")
        content_hash = _content_hash(payload)
        return ResearchMaterial(
            filename=filename,
            media_type="text/plain",
            payload=payload,
            source_type="linkedin_export" if seed_type == "linkedin_export" else "text",
            title=request.title or Path(filename).stem or "Research seed",
            normalized_url=None,
            content_hash=content_hash,
            provenance={"seed_type": seed_type, "filename": filename, "content_hash": content_hash},
        )

    async def _material_from_candidate(self, candidate: ResearchImportCandidate) -> ResearchMaterial:
        provenance = dict(candidate.provenance_json or {})
        content_text = provenance.get("content_text")
        if isinstance(content_text, str) and content_text.strip():
            payload = content_text.encode("utf-8")
            filename = str(provenance.get("filename") or f"{_safe_filename(candidate.title)}.txt")
            media_type = str(provenance.get("media_type") or "text/plain")
            content_hash = _content_hash(payload)
            return ResearchMaterial(
                filename=filename,
                media_type=media_type,
                payload=payload,
                source_type=cast(ResearchCandidateSourceType, candidate.source_type),
                title=candidate.title,
                normalized_url=candidate.normalized_url,
                content_hash=content_hash,
                provenance=provenance | {"candidate_id": candidate.id},
            )
        if candidate.url is None:
            raise ValueError("Candidate has no stored content or URL to ingest.")
        return await self._fetch_url_material(
            url=candidate.url,
            title=candidate.title,
            forced_source_type=cast(ResearchCandidateSourceType, candidate.source_type),
        )

    async def _fetch_url_material(
        self,
        *,
        url: str,
        title: str | None,
        forced_source_type: ResearchCandidateSourceType | None,
    ) -> ResearchMaterial:
        normalized_url = _normalize_url(_arxiv_pdf_url(url) if forced_source_type == "arxiv" else url)
        fetch_url = normalized_url or url
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self._settings.research_import_fetch_timeout_seconds,
            headers={"User-Agent": self._settings.research_import_user_agent},
        ) as client:
            response = await client.get(fetch_url)
        if response.status_code in {401, 403}:
            raise ValueError("URL appears to require login or permission.")
        response.raise_for_status()
        payload = response.content
        if len(payload) > self._settings.research_import_max_fetch_bytes:
            raise ValueError("Fetched research candidate exceeds the configured byte limit.")
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        source_type = forced_source_type or _source_type_from_url(str(response.url), default="url")
        if content_type == "application/pdf" or str(response.url).casefold().endswith(".pdf"):
            filename = _filename_from_url(str(response.url), title=title, extension=".pdf")
            media_type = "application/pdf"
            output_payload = payload
            source_type = "pdf" if source_type != "arxiv" else "arxiv"
        else:
            decoded = response.text
            if "html" in content_type or "<html" in decoded[:500].casefold():
                decoded = _html_to_text(decoded)
                source_type = "html" if source_type == "url" else source_type
            decoded = decoded[: self._settings.research_import_max_text_chars]
            output_payload = decoded.encode("utf-8")
            media_type = "text/plain"
            filename = _filename_from_url(str(response.url), title=title, extension=".txt")
        content_hash = _content_hash(output_payload)
        return ResearchMaterial(
            filename=filename,
            media_type=media_type,
            payload=output_payload,
            source_type=source_type,
            title=title or Path(filename).stem or fetch_url,
            normalized_url=normalized_url,
            content_hash=content_hash,
            provenance={
                "url": url,
                "normalized_url": normalized_url,
                "fetched_url": str(response.url),
                "http_status": response.status_code,
                "content_type": content_type,
                "content_hash": content_hash,
                "fetched_at": _utcnow().isoformat(),
            },
        )

    async def _active_user_and_library(self, session: Any, *, clerk_user_id: str) -> tuple[AppUser, UserLibrary]:
        app_user = await self._sources.ensure_app_user(session, clerk_user_id=clerk_user_id)
        if not app_user.active:
            raise PermissionError("The active user is not allowed to use research imports.")
        library = await self._sources.library_for_user(session, app_user=app_user)
        return app_user, library

    async def _candidate_for_user(
        self,
        session: Any,
        *,
        clerk_user_id: str,
        candidate_id: str,
    ) -> ResearchImportCandidate:
        app_user = await self._sources.ensure_app_user(session, clerk_user_id=clerk_user_id)
        candidate = await session.scalar(
            select(ResearchImportCandidate)
            .join(UserLibrary, UserLibrary.id == ResearchImportCandidate.library_id)
            .where(ResearchImportCandidate.id == candidate_id, UserLibrary.user_id == app_user.id)
            .options(
                selectinload(ResearchImportCandidate.linked_source_file),
                selectinload(ResearchImportCandidate.parent_source_file),
            )
        )
        if candidate is None:
            raise FileNotFoundError("Research candidate not found.")
        return candidate

    async def _task_for_user(self, session: Any, *, clerk_user_id: str, task_id: str) -> AppTask:
        app_user = await self._sources.ensure_app_user(session, clerk_user_id=clerk_user_id)
        task = await session.scalar(
            select(AppTask)
            .join(UserLibrary, UserLibrary.id == AppTask.library_id)
            .where(AppTask.id == task_id, UserLibrary.user_id == app_user.id)
        )
        if task is None:
            raise FileNotFoundError("Research import task not found.")
        return task

    def _candidate_summary(self, candidate: ResearchImportCandidate) -> ResearchImportCandidateSummary:
        return ResearchImportCandidateSummary(
            id=candidate.id,
            task_id=candidate.task_id,
            status=cast(ResearchCandidateStatus, candidate.status),
            source_type=cast(ResearchCandidateSourceType, candidate.source_type),
            url=candidate.url,
            normalized_url=candidate.normalized_url,
            title=candidate.title,
            rationale=candidate.rationale,
            score=candidate.score,
            depth=candidate.depth,
            parent_candidate_id=candidate.parent_candidate_id,
            parent_source_file_id=candidate.parent_source_file_id,
            linked_source_file_id=candidate.linked_source_file_id,
            provenance=dict(candidate.provenance_json or {}),
            content_hash=candidate.content_hash,
            error_message=candidate.error_message,
            created_at=candidate.created_at,
            updated_at=candidate.updated_at,
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _research_task_summary(task: AppTask) -> TaskSummary:
    return TaskSummary(
        id=task.id,
        kind=cast(Any, task.kind),
        status=cast(Any, task.status),
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


def _seed_title(payload: ResearchImportCreateRequest) -> str:
    return (payload.title or payload.url or payload.filename or payload.seed_type).strip()[:120]


def _extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:")
        if url not in urls:
            urls.append(url)
    return urls


def _normalize_url(url: str | None) -> str | None:
    if url is None:
        return None
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.casefold(), parsed.netloc.casefold(), path, "", parsed.query, ""))


def _arxiv_pdf_url(url: str) -> str:
    match = ARXIV_ID_RE.search(url)
    if match is None:
        return url
    return f"https://arxiv.org/pdf/{match.group('id')}.pdf"


def _source_type_from_url(url: str, *, default: ResearchCandidateSourceType) -> ResearchCandidateSourceType:
    lower = url.casefold()
    if "arxiv.org" in lower:
        return "arxiv"
    if lower.split("?", 1)[0].endswith(".pdf"):
        return "pdf"
    return default


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    candidate = Path(parsed.path.rstrip("/")).name or parsed.netloc or url
    return html.unescape(candidate.replace("-", " ").replace("_", " ")).strip()[:512] or url[:512]


def _filename_from_url(url: str, *, title: str | None, extension: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path.rstrip("/")).name
    if not name or "." not in name:
        name = f"{_safe_filename(title or parsed.netloc or 'research-source')}{extension}"
    return _safe_filename(name)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "-", value).strip(" .-")
    return (cleaned or "research-source")[:180]


def _html_to_text(value: str) -> str:
    without_scripts = SCRIPT_STYLE_RE.sub("\n", value)
    with_breaks = re.sub(r"(?i)</?(p|br|li|h[1-6]|section|article|div|tr)[^>]*>", "\n", without_scripts)
    text = TAG_RE.sub(" ", with_breaks)
    text = html.unescape(text)
    lines = [WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _content_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _discovery_query(*, seed_material: ResearchMaterial, request: ResearchImportCreateRequest) -> str:
    seed_text = seed_material.payload.decode("utf-8", errors="replace")
    trimmed = seed_text[:4_000]
    return "\n".join(
        part
        for part in [
            f"Title: {request.title or seed_material.title}",
            f"Seed type: {request.seed_type}",
            f"URL: {seed_material.normalized_url}" if seed_material.normalized_url else None,
            trimmed,
        ]
        if part
    )

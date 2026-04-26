from __future__ import annotations

import base64
import binascii
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import html
import logging
from pathlib import Path
import re
from time import perf_counter
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from backend.app.core.config import AppSettings
from backend.app.db.session import DatabaseManager
from backend.app.integrations.openai_gateway import OpenAIGateway
from backend.app.models import AppTask, AppUser, ResearchImportCandidate, SourceFile, UserLibrary
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
    ResearchLibraryBuildRequest,
    ResearchLibraryBuildResponse,
    TaskSummary,
)
from backend.app.services.sources import SourceService

logger = logging.getLogger(__name__)
ResearchProgressCallback = Callable[[str, str], Awaitable[None]]

URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+")
ANCHOR_HREF_RE = re.compile(r"(?is)<a\b(?P<attrs>[^>]*)>(?P<label>.*?)</a>")
HREF_RE = re.compile(r"(?is)\bhref\s*=\s*(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'|(?P<bare>[^\s>]+))")
HTML_SIGNAL_RE = re.compile(r"(?is)<(?:!doctype|html|body|article|section|p|br|div|span|a|h[1-6]|ul|ol|li)\b")
SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
TAG_RE = re.compile(r"(?s)<[^>]+>")
WHITESPACE_RE = re.compile(r"[ \t\r\f\v\u00a0]+")
ARXIV_ID_RE = re.compile(r"(?i)(?:arxiv\.org/(?:abs|pdf)/)?(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)")
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"}


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
    """Coordinate research discovery, dedupe, and canonical source ingestion."""

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
        progress_callback: ResearchProgressCallback | None = None,
    ) -> ResearchImportResponse:
        started_at = perf_counter()
        await _emit_research_progress(progress_callback, "search", f"Starting research discovery for {_seed_title(payload)}.")
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
        await _emit_research_progress(progress_callback, "folder", "Preparing the target research folder.")
        target_folder_id = await self._target_folder_for_request(
            clerk_user_id=clerk_user_id,
            request=payload,
            seed_material=seed_material,
        )
        seed_source: IngestFinalizeResponse | None = None
        candidates: list[ResearchImportCandidateSummary] = []
        duplicate_count = 0

        try:
            should_ingest_seed = payload.ingest_seed and payload.seed_type not in {"topic", "paper"}
            if should_ingest_seed:
                seed_source = await self._sources.ingest_source(
                    clerk_user_id=clerk_user_id,
                    filename=seed_material.filename,
                    declared_media_type=seed_material.media_type,
                    payload=seed_material.payload,
                    tag_ids=payload.tag_ids,
                    user_guidance=None,
                    origin_surface=origin_surface,
                    origin_thread_id=origin_thread_id,
                    folder_id=target_folder_id,
                    virtual_name=seed_material.filename,
                    metadata={
                        **seed_material.provenance,
                        "research_import_task_id": task.id,
                        "content_hash": seed_material.content_hash,
                    },
                )

            await _emit_research_progress(progress_callback, "search", "Discovering primary references.")
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
                        "description": seed_material.provenance.get("description"),
                        "summary": seed_material.provenance.get("summary"),
                        "suggested_tags": seed_material.provenance.get("suggested_tags") or [],
                        "rationale": "Initial seed queued as a candidate instead of direct ingestion.",
                        "score": 1.0,
                        "depth": 0,
                        "provenance": seed_material.provenance
                        | {
                            "content_text": seed_material.payload.decode("utf-8", errors="replace")
                            if seed_material.media_type.startswith("text/")
                            else None,
                            "filename": seed_material.filename,
                            "media_type": seed_material.media_type,
                            "content_hash": seed_material.content_hash,
                        },
                        "content_hash": seed_material.content_hash,
                    },
                )
            if target_folder_id is not None:
                for candidate_input in candidate_inputs:
                    provenance = cast(dict[str, object], candidate_input.get("provenance") or {})
                    provenance["target_folder_id"] = target_folder_id
                    candidate_input["provenance"] = provenance

            remaining_slots = payload.max_pending_candidates
            if remaining_slots > 0 and candidate_inputs:
                persisted, duplicates = await self._persist_candidate_batch(
                    clerk_user_id=clerk_user_id,
                    task_id=task.id,
                    candidate_inputs=candidate_inputs[:remaining_slots],
                )
                candidates.extend(persisted)
                duplicate_count += duplicates
                await _emit_research_progress(
                    progress_callback,
                    "search",
                    f"Found {len(persisted)} candidate{'' if len(persisted) == 1 else 's'}; skipped {duplicates} duplicate{'' if duplicates == 1 else 's'}.",
                )

            next_depth = 2
            frontier = [candidate for candidate in candidates if candidate.depth == 1]
            while (
                payload.discover_references
                and payload.max_candidates_per_source > 0
                and next_depth <= payload.max_depth
                and frontier
                and len(candidates) < payload.max_pending_candidates
            ):
                remaining_slots = payload.max_pending_candidates - len(candidates)
                await _emit_research_progress(
                    progress_callback,
                    "search",
                    f"Expanding references at depth {next_depth}.",
                )
                followup_inputs = await self._candidate_inputs_from_frontier(
                    parents=frontier,
                    request=payload,
                    depth=next_depth,
                    remaining_slots=remaining_slots,
                )
                if target_folder_id is not None:
                    for candidate_input in followup_inputs:
                        provenance = cast(dict[str, object], candidate_input.get("provenance") or {})
                        provenance["target_folder_id"] = target_folder_id
                        candidate_input["provenance"] = provenance
                if not followup_inputs:
                    break
                persisted, duplicates = await self._persist_candidate_batch(
                    clerk_user_id=clerk_user_id,
                    task_id=task.id,
                    candidate_inputs=followup_inputs[:remaining_slots],
                )
                duplicate_count += duplicates
                await _emit_research_progress(
                    progress_callback,
                    "search",
                    f"Depth {next_depth} added {len(persisted)} candidate{'' if len(persisted) == 1 else 's'}; skipped {duplicates} duplicate{'' if duplicates == 1 else 's'}.",
                )
                logger.info(
                    "research_import_expansion_completed clerk_user_id=%s task_id=%s depth=%s parents=%s candidates=%s duplicates=%s",
                    clerk_user_id,
                    task.id,
                    next_depth,
                    len(frontier),
                    len(persisted),
                    duplicates,
                )
                if not persisted:
                    break
                candidates.extend(persisted)
                frontier = [candidate for candidate in persisted if candidate.depth == next_depth]
                next_depth += 1

            max_depth_reached = max((candidate.depth for candidate in candidates), default=0)
            async with self._database.session() as session:
                task = await self._task_for_user(session, clerk_user_id=clerk_user_id, task_id=task.id)
                task.status = "completed"
                task.completed_at = _utcnow()
                task.state_json = {
                    "stage": "completed",
                    "candidate_count": len(candidates),
                    "duplicate_count": duplicate_count,
                    "max_depth_reached": max_depth_reached,
                    "seed_source_id": seed_source.source.id if seed_source is not None else None,
                    "target_folder_id": target_folder_id,
                }
                task.result_json = {
                    "candidate_count": len(candidates),
                    "duplicate_count": duplicate_count,
                    "max_depth_reached": max_depth_reached,
                    "seed_source_id": seed_source.source.id if seed_source is not None else None,
                    "target_folder_id": target_folder_id,
                }
                task.updated_at = _utcnow()
                await session.commit()
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
            target_folder_id=target_folder_id,
        )

    async def build_library(
        self,
        *,
        clerk_user_id: str,
        payload: ResearchLibraryBuildRequest,
        origin_surface: str,
        origin_thread_id: str | None = None,
        progress_callback: ResearchProgressCallback | None = None,
    ) -> ResearchLibraryBuildResponse:
        seed_type = payload.seed_type
        import_payload = ResearchImportCreateRequest(
            seed_type=seed_type,
            text=None if seed_type in {"url", "pdf_url", "arxiv_url"} else payload.query,
            url=payload.query if seed_type in {"url", "pdf_url", "arxiv_url"} else None,
            title=payload.title or payload.query[:512],
            tag_ids=payload.tag_ids,
            folder_id=payload.folder_id,
            folder_name=payload.folder_name,
            ingest_seed=True,
            discover_references=payload.discover_references,
            max_depth=payload.max_depth,
            max_candidates_per_source=payload.max_candidates_per_source,
            max_pending_candidates=max(payload.max_sources, payload.max_pending_candidates),
        )
        import_response = await self.create_import(
            clerk_user_id=clerk_user_id,
            payload=import_payload,
            origin_surface=origin_surface,
            origin_thread_id=origin_thread_id,
            progress_callback=progress_callback,
        )
        candidates = import_response.candidates[: payload.max_sources]
        ingested: list[IngestFinalizeResponse] = []
        updated_candidates = candidates
        duplicate_count = import_response.duplicate_count
        await _emit_research_progress(
            progress_callback,
            "search",
            f"Selected {len(candidates)} candidate{'' if len(candidates) == 1 else 's'} for the library cap.",
        )
        if payload.auto_ingest and candidates:
            candidate_ids = [candidate.id for candidate in candidates]
            ingest_response = await self._ingest_candidates(
                clerk_user_id=clerk_user_id,
                payload=ResearchCandidateIngestRequest(
                    candidate_ids=candidate_ids,
                    tag_ids=payload.tag_ids if payload.tag_ids else None,
                    folder_id=payload.folder_id or import_response.target_folder_id,
                ),
                origin_surface=origin_surface,
                origin_thread_id=origin_thread_id,
                allowed_statuses={"pending", "approved"},
                progress_callback=progress_callback,
            )
            ingested = ingest_response.ingested
            updated_candidates = ingest_response.candidates
            duplicate_count += sum(1 for candidate in updated_candidates if candidate.status == "duplicate")

        async with self._database.session() as session:
            task = await self._task_for_user(
                session,
                clerk_user_id=clerk_user_id,
                task_id=import_response.task.id,
            )
            current_state = task.state_json if isinstance(task.state_json, dict) else {}
            current_result = task.result_json if isinstance(task.result_json, dict) else {}
            task.state_json = current_state | {
                "stage": "built",
                "candidate_count": len(updated_candidates),
                "ingested_count": len(ingested),
                "duplicate_count": duplicate_count,
            }
            task.result_json = current_result | {
                "candidate_count": len(updated_candidates),
                "ingested_count": len(ingested),
                "duplicate_count": duplicate_count,
                "target_folder_id": import_response.target_folder_id,
            }
            task.updated_at = _utcnow()
            await session.commit()
            task_summary = _research_task_summary(task)

        return ResearchLibraryBuildResponse(
            task=task_summary,
            target_folder_id=import_response.target_folder_id,
            seed_source=import_response.seed_source,
            candidates=updated_candidates,
            ingested=ingested,
            duplicate_count=duplicate_count,
        )

    async def _persist_candidate_batch(
        self,
        *,
        clerk_user_id: str,
        task_id: str,
        candidate_inputs: list[dict[str, object]],
    ) -> tuple[list[ResearchImportCandidateSummary], int]:
        if not candidate_inputs:
            return [], 0
        async with self._database.session() as session:
            task = await self._task_for_user(session, clerk_user_id=clerk_user_id, task_id=task_id)
            app_user, library = await self._active_user_and_library(session, clerk_user_id=clerk_user_id)
            persisted, duplicate_count = await self._persist_candidate_inputs(
                session=session,
                app_user=app_user,
                library=library,
                task=task,
                candidate_inputs=candidate_inputs,
            )
            return [self._candidate_summary(candidate) for candidate in persisted], duplicate_count

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

    async def linked_source_ids_for_task(self, *, clerk_user_id: str, task_id: str) -> list[str]:
        response = await self.list_candidates(
            clerk_user_id=clerk_user_id,
            task_id=task_id,
            status="ingested",
            page=1,
            page_size=100,
        )
        source_ids: list[str] = []
        seen: set[str] = set()
        for candidate in response.candidates:
            source_id = candidate.linked_source_file_id
            if source_id is None or source_id in seen:
                continue
            seen.add(source_id)
            source_ids.append(source_id)
        return source_ids

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
        progress_callback: ResearchProgressCallback | None = None,
    ) -> ResearchCandidateIngestResponse:
        return await self._ingest_candidates(
            clerk_user_id=clerk_user_id,
            payload=payload,
            origin_surface=origin_surface,
            origin_thread_id=origin_thread_id,
            allowed_statuses={"approved"},
            progress_callback=progress_callback,
        )

    async def _ingest_candidates(
        self,
        *,
        clerk_user_id: str,
        payload: ResearchCandidateIngestRequest,
        origin_surface: str,
        origin_thread_id: str | None,
        allowed_statuses: set[str],
        progress_callback: ResearchProgressCallback | None = None,
    ) -> ResearchCandidateIngestResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user, library = await self._active_user_and_library(session, clerk_user_id=clerk_user_id)
            query = (
                select(ResearchImportCandidate)
                .join(UserLibrary, UserLibrary.id == ResearchImportCandidate.library_id)
                .where(
                    UserLibrary.user_id == app_user.id,
                    ResearchImportCandidate.status.in_(allowed_statuses),
                )
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
            source_metadata = (
                await session.execute(select(SourceFile.metadata_json).where(SourceFile.library_id == library.id))
            ).scalars()
            existing_urls: set[str] = set()
            existing_hashes: set[str] = set()
            for metadata in source_metadata:
                if not isinstance(metadata, dict):
                    continue
                normalized_url = metadata.get("normalized_url")
                content_hash = metadata.get("content_hash")
                if isinstance(normalized_url, str) and normalized_url:
                    existing_urls.add(normalized_url)
                if isinstance(content_hash, str) and content_hash:
                    existing_hashes.add(content_hash)
            await session.commit()

        ingested: list[IngestFinalizeResponse] = []
        updated_candidates: list[ResearchImportCandidateSummary] = []
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()
        for candidate in candidates:
            try:
                await _emit_research_progress(
                    progress_callback,
                    "download",
                    f"Downloading {candidate.title[:80]}.",
                )
                material = await self._material_from_candidate(candidate)
                normalized_url = material.normalized_url or candidate.normalized_url
                duplicate_reason: str | None = None
                if normalized_url and (normalized_url in existing_urls or normalized_url in seen_urls):
                    duplicate_reason = "Duplicate research candidate URL."
                elif material.content_hash in existing_hashes or material.content_hash in seen_hashes:
                    duplicate_reason = "Duplicate research candidate content."
                if duplicate_reason is not None:
                    async with self._database.session() as session:
                        current = await self._candidate_for_user(
                            session,
                            clerk_user_id=clerk_user_id,
                            candidate_id=candidate.id,
                        )
                        current.status = "duplicate"
                        current.content_hash = material.content_hash
                        current.provenance_json = dict(current.provenance_json or {}) | material.provenance
                        current.error_message = duplicate_reason
                        current.updated_at = _utcnow()
                        await session.commit()
                        updated_candidates.append(self._candidate_summary(current))
                    if normalized_url:
                        seen_urls.add(normalized_url)
                    seen_hashes.add(material.content_hash)
                    await _emit_research_progress(
                        progress_callback,
                        "copy-check",
                        f"Skipped duplicate: {candidate.title[:80]}.",
                    )
                    logger.info(
                        "research_candidate_duplicate_skipped clerk_user_id=%s candidate_id=%s reason=%s",
                        clerk_user_id,
                        candidate.id,
                        duplicate_reason,
                    )
                    continue

                candidate_folder_id = payload.folder_id or _candidate_target_folder_id(candidate)
                candidate_tag_ids = (
                    payload.tag_ids
                    if payload.tag_ids is not None
                    else await self._candidate_suggested_tag_ids(
                        clerk_user_id=clerk_user_id,
                        candidate=candidate,
                    )
                )
                ingest_response = await self._sources.ingest_source(
                    clerk_user_id=clerk_user_id,
                    filename=material.filename,
                    declared_media_type=material.media_type,
                    payload=material.payload,
                    tag_ids=candidate_tag_ids,
                    user_guidance=None,
                    origin_surface=origin_surface,
                    origin_thread_id=origin_thread_id,
                    folder_id=candidate_folder_id,
                    virtual_name=material.filename,
                    metadata={
                        **material.provenance,
                        "research_import_task_id": candidate.task_id,
                        "research_candidate_id": candidate.id,
                        "content_hash": material.content_hash,
                    },
                )
                await _emit_research_progress(
                    progress_callback,
                    "document",
                    f"Queued indexing for {material.filename}.",
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
                if normalized_url:
                    seen_urls.add(normalized_url)
                    existing_urls.add(normalized_url)
                seen_hashes.add(material.content_hash)
                existing_hashes.add(material.content_hash)
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
                await _emit_research_progress(
                    progress_callback,
                    "alert-circle",
                    f"Failed to ingest {candidate.title[:80]}.",
                )

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
                    "description": None,
                    "summary": None,
                    "suggested_tags": [],
                    "rationale": "URL explicitly present in the seed material.",
                    "score": 0.9,
                    "depth": 1,
                    "provenance": {"discovery_source": "seed_url", "url": url},
                    "content_hash": None,
                }
            )
        if request.discover_references and request.max_depth > 0 and request.max_candidates_per_source > 0:
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
                        "description": item.description,
                        "summary": item.summary,
                        "suggested_tags": item.suggested_tags,
                        "authors": item.authors,
                        "published_at": item.published_at,
                        "doi": item.doi,
                        "arxiv_id": item.arxiv_id,
                        "rationale": item.rationale,
                        "score": item.score,
                        "depth": 1,
                        "provenance": _discovery_provenance(item),
                        "content_hash": None,
                    }
                )
        return candidates

    async def _candidate_inputs_from_frontier(
        self,
        *,
        parents: list[ResearchImportCandidateSummary],
        request: ResearchImportCreateRequest,
        depth: int,
        remaining_slots: int,
    ) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        per_parent_limit = min(
            request.max_candidates_per_source,
            self._settings.research_import_max_candidates_per_source,
        )
        if per_parent_limit <= 0 or remaining_slots <= 0:
            return candidates
        for parent in parents:
            if len(candidates) >= remaining_slots:
                break
            discovery = await self._openai.discover_research_candidates(
                query=_followup_discovery_query(parent=parent, request=request, depth=depth),
                max_candidates=min(per_parent_limit, remaining_slots - len(candidates)),
            )
            for item in discovery.candidates:
                candidates.append(
                    {
                        "source_type": item.source_type,
                        "url": item.url,
                        "normalized_url": _normalize_url(item.url),
                        "title": item.title,
                        "description": item.description,
                        "summary": item.summary,
                        "suggested_tags": item.suggested_tags,
                        "authors": item.authors,
                        "published_at": item.published_at,
                        "doi": item.doi,
                        "arxiv_id": item.arxiv_id,
                        "rationale": item.rationale,
                        "score": item.score,
                        "depth": depth,
                        "parent_candidate_id": parent.id,
                        "provenance": _discovery_provenance(item)
                        | {
                            "discovery_depth": depth,
                            "parent_candidate_id": parent.id,
                            "parent_candidate_title": parent.title,
                            "parent_candidate_url": parent.normalized_url or parent.url,
                        },
                        "content_hash": None,
                    }
                )
                if len(candidates) >= remaining_slots:
                    break
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
        source_metadata = (
            await session.execute(select(SourceFile.metadata_json).where(SourceFile.library_id == library.id))
        ).scalars()
        for metadata in source_metadata:
            if not isinstance(metadata, dict):
                continue
            normalized_url = metadata.get("normalized_url")
            content_hash = metadata.get("content_hash")
            if isinstance(normalized_url, str) and normalized_url:
                existing_urls.add(normalized_url)
            if isinstance(content_hash, str) and content_hash:
                existing_hashes.add(content_hash)
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
                parent_candidate_id=cast(str | None, item.get("parent_candidate_id")),
                parent_source_file_id=cast(str | None, item.get("parent_source_file_id")),
                status="pending",
                source_type=str(item["source_type"]),
                url=cast(str | None, item.get("url")),
                normalized_url=normalized_url,
                title=str(item["title"])[:512],
                rationale=cast(str | None, item.get("rationale")),
                score=cast(float | None, item.get("score")),
                depth=int(cast(int | str, item.get("depth") or 0)),
                provenance_json=cast(dict[str, object], item.get("provenance") or {})
                | _candidate_metadata_from_input(item),
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
        if seed_type in {"topic", "paper"}:
            topic = (request.text or request.title or "").strip()
            if not topic:
                raise ValueError("A topic or paper seed requires text or title.")
            payload = topic.encode("utf-8")
            content_hash = _content_hash(payload)
            description = (
                f"Research library seed topic: {topic}"
                if seed_type == "topic"
                else f"Research library seed paper: {topic}"
            )
            return ResearchMaterial(
                filename=f"{_safe_filename(topic)}.txt",
                media_type="text/plain",
                payload=payload,
                source_type="text",
                title=request.title or topic,
                normalized_url=None,
                content_hash=content_hash,
                provenance={
                    "seed_type": seed_type,
                    "topic": topic,
                    "description": description,
                    "summary": topic,
                    "suggested_tags": _seed_suggested_tags(topic),
                    "content_hash": content_hash,
                },
            )
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
        raw_text = (request.text or "").strip()
        text = _html_to_text(raw_text) if seed_type == "linkedin_export" or _looks_like_html(raw_text) else raw_text
        if not text:
            raise ValueError("A text or LinkedIn export seed requires text.")
        bounded_text = text[: self._settings.research_import_max_text_chars]
        filename = request.filename or (
            "linkedin-export.txt" if seed_type == "linkedin_export" else "research-seed.txt"
        )
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
            candidate_provenance=provenance,
        )

    async def _fetch_url_material(
        self,
        *,
        url: str,
        title: str | None,
        forced_source_type: ResearchCandidateSourceType | None,
        candidate_provenance: dict[str, object] | None = None,
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
        material = ResearchMaterial(
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
        if candidate_provenance is None:
            return material
        merged_provenance = dict(candidate_provenance) | material.provenance
        for key in [
            "description",
            "summary",
            "suggested_tags",
            "authors",
            "published_at",
            "doi",
            "arxiv_id",
        ]:
            if key in candidate_provenance and key not in merged_provenance:
                merged_provenance[key] = candidate_provenance[key]
        return replace(material, provenance=merged_provenance)

    async def _target_folder_for_request(
        self,
        *,
        clerk_user_id: str,
        request: ResearchImportCreateRequest,
        seed_material: ResearchMaterial,
    ) -> str | None:
        if request.folder_id is not None:
            return request.folder_id
        if request.seed_type not in {"topic", "paper"} and request.folder_name is None:
            return None
        parent = await self._ensure_child_folder(
            clerk_user_id=clerk_user_id,
            parent_id=None,
            name="Research",
        )
        return await self._ensure_child_folder(
            clerk_user_id=clerk_user_id,
            parent_id=parent,
            name=request.folder_name or seed_material.title,
        )

    async def _ensure_child_folder(self, *, clerk_user_id: str, parent_id: str | None, name: str) -> str:
        folder_name = _safe_folder_name(name)
        listing = await self._sources.list_filesystem(clerk_user_id=clerk_user_id, folder_id=parent_id)
        for entry in listing.entries:
            if entry.kind == "folder" and entry.name.casefold() == folder_name.casefold():
                return entry.id
        try:
            created = await self._sources.create_folder(
                clerk_user_id=clerk_user_id,
                parent_id=parent_id,
                name=folder_name,
            )
        except ValueError:
            refreshed = await self._sources.list_filesystem(clerk_user_id=clerk_user_id, folder_id=parent_id)
            for entry in refreshed.entries:
                if entry.kind == "folder" and entry.name.casefold() == folder_name.casefold():
                    return entry.id
            raise
        return created.id

    async def _candidate_suggested_tag_ids(
        self,
        *,
        clerk_user_id: str,
        candidate: ResearchImportCandidate,
    ) -> list[str]:
        tag_names = _candidate_suggested_tags(candidate)
        if not tag_names:
            return []
        tags = await self._sources.ensure_auto_tags(clerk_user_id=clerk_user_id, tag_names=tag_names)
        return [tag.id for tag in tags]

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
            description=_provenance_string(candidate.provenance_json, "description"),
            summary=_provenance_string(candidate.provenance_json, "summary"),
            suggested_tags=_candidate_suggested_tags(candidate),
            authors=_provenance_string_list(candidate.provenance_json, "authors"),
            published_at=_provenance_string(candidate.provenance_json, "published_at"),
            doi=_provenance_string(candidate.provenance_json, "doi"),
            arxiv_id=_provenance_string(candidate.provenance_json, "arxiv_id"),
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


async def _emit_research_progress(
    callback: ResearchProgressCallback | None,
    icon: str,
    text: str,
) -> None:
    if callback is None:
        return
    await callback(icon, text)


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
    return (payload.title or payload.url or payload.filename or payload.text or payload.seed_type).strip()[:120]


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
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = (parsed.hostname or "").casefold()
    if not host:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = host
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key and not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_QUERY_KEYS
    ]
    query = urlencode(sorted(query_items))
    return urlunparse((scheme, netloc, path, "", query, ""))


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


def _safe_folder_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "-", value).strip(" .-")
    return (cleaned or "Research Library")[:120]


def _html_to_text(value: str) -> str:
    with_expanded_links = ANCHOR_HREF_RE.sub(_anchor_label_with_href, value)
    without_scripts = SCRIPT_STYLE_RE.sub("\n", with_expanded_links)
    with_breaks = re.sub(r"(?i)</?(p|br|li|h[1-6]|section|article|div|tr)[^>]*>", "\n", without_scripts)
    text = TAG_RE.sub(" ", with_breaks)
    text = html.unescape(text)
    lines = [WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _anchor_label_with_href(match: re.Match[str]) -> str:
    label = match.group("label")
    href_match = HREF_RE.search(match.group("attrs"))
    if href_match is None:
        return label
    href = html.unescape(href_match.group("double") or href_match.group("single") or href_match.group("bare") or "")
    if not href.casefold().startswith(("http://", "https://")):
        return label
    return f"{label} ({href})"


def _looks_like_html(value: str) -> bool:
    return bool(HTML_SIGNAL_RE.search(value))


def _content_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _discovery_query(*, seed_material: ResearchMaterial, request: ResearchImportCreateRequest) -> str:
    seed_text = seed_material.payload.decode("utf-8", errors="replace")
    trimmed = seed_text[:4_000]
    instruction = (
        "Find the primary paper and important cited or closely related public references."
        if request.seed_type == "paper"
        else "Find high-quality public sources that form a useful starter research library."
    )
    return "\n".join(
        part
        for part in [
            instruction,
            f"Title: {request.title or seed_material.title}",
            f"Seed type: {request.seed_type}",
            f"URL: {seed_material.normalized_url}" if seed_material.normalized_url else None,
            trimmed,
        ]
        if part
    )


def _followup_discovery_query(
    *,
    parent: ResearchImportCandidateSummary,
    request: ResearchImportCreateRequest,
    depth: int,
) -> str:
    metadata_parts = [
        f"Parent title: {parent.title}",
        f"Parent URL: {parent.normalized_url or parent.url}" if parent.normalized_url or parent.url else None,
        f"Description: {parent.description}" if parent.description else None,
        f"Summary: {parent.summary}" if parent.summary else None,
        f"Authors: {', '.join(parent.authors)}" if parent.authors else None,
        f"Published: {parent.published_at}" if parent.published_at else None,
        f"DOI: {parent.doi}" if parent.doi else None,
        f"arXiv: {parent.arxiv_id}" if parent.arxiv_id else None,
        f"Suggested tags: {', '.join(parent.suggested_tags)}" if parent.suggested_tags else None,
    ]
    return "\n".join(
        part
        for part in [
            "Follow-up discovery for a bounded research library.",
            "Find public references that are cited by, foundational to, or directly related to this parent candidate.",
            "Prefer original papers, arXiv/PDF pages, official project docs, datasets, and high-signal articles.",
            "Avoid duplicate versions of the parent candidate and avoid login-gated or paywalled pages when public alternatives exist.",
            f"Original seed type: {request.seed_type}",
            f"Discovery depth: {depth}",
            *metadata_parts,
        ]
        if part
    )


def _discovery_provenance(item: Any) -> dict[str, object]:
    metadata = {
        "discovery_source": "openai_web_search",
        "url": item.url,
        "description": item.description,
        "summary": item.summary,
        "suggested_tags": item.suggested_tags,
        "authors": item.authors,
        "published_at": item.published_at,
        "doi": item.doi,
        "arxiv_id": item.arxiv_id,
    }
    return {key: value for key, value in metadata.items() if _has_metadata_value(value)}


def _candidate_metadata_from_input(item: dict[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    for key in [
        "description",
        "summary",
        "suggested_tags",
        "authors",
        "published_at",
        "doi",
        "arxiv_id",
    ]:
        value = item.get(key)
        if _has_metadata_value(value):
            metadata[key] = value
    return metadata


def _has_metadata_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


def _candidate_target_folder_id(candidate: ResearchImportCandidate) -> str | None:
    value = dict(candidate.provenance_json or {}).get("target_folder_id")
    return value if isinstance(value, str) and value.strip() else None


def _candidate_suggested_tags(candidate: ResearchImportCandidate) -> list[str]:
    return _provenance_string_list(candidate.provenance_json, "suggested_tags")


def _provenance_string(provenance: object, key: str) -> str | None:
    if not isinstance(provenance, dict):
        return None
    value = provenance.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _provenance_string_list(provenance: object, key: str) -> list[str]:
    if not isinstance(provenance, dict):
        return []
    value = provenance.get(key)
    if not isinstance(value, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        key_value = cleaned.casefold()
        if not cleaned or key_value in seen:
            continue
        seen.add(key_value)
        output.append(cleaned)
    return output[:8]


def _seed_suggested_tags(topic: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", topic)
    tags: list[str] = []
    seen: set[str] = set()
    for word in words:
        cleaned = word.strip("-").lower()
        if cleaned in {"the", "and", "for", "with", "from", "paper", "topic"} or cleaned in seen:
            continue
        seen.add(cleaned)
        tags.append(cleaned)
        if len(tags) >= 4:
            break
    return tags

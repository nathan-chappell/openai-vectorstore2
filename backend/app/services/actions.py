from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any, cast

from sqlalchemy import select

from backend.app.core.config import AppSettings
from backend.app.db.session import DatabaseManager
from backend.app.integrations.openai_gateway import OpenAIGateway
from backend.app.models import AppTask, AppUser, StoredAsset, UserLibrary
from backend.app.schemas import (
    ActionResponse,
    BranchSearchRequest,
    FreeformRequest,
    GeneratedAsset,
    ImageGenerationRequest,
    QaRequest,
    SearchRequest,
    TaskDetail,
    TaskKind,
    TaskListResponse,
    TaskStatus,
    TaskSummary,
    VoiceGenerationRequest,
)
from backend.app.services.sources import SourceService
from backend.app.storage import StorageService

logger = logging.getLogger(__name__)


class ActionService:
    """Run app-owned generation workflows against retrieved indexed file matches."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        database: DatabaseManager,
        sources: SourceService,
        storage: StorageService,
        openai: OpenAIGateway,
    ) -> None:
        self._settings = settings
        self._database = database
        self._sources = sources
        self._storage = storage
        self._openai = openai

    async def list_tasks(
        self,
        *,
        clerk_user_id: str,
        kind: TaskKind | None = None,
        limit: int = 50,
    ) -> TaskListResponse:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=clerk_user_id)
            query = select(AppTask).where(AppTask.user_id == app_user.id)
            if kind is not None:
                query = query.where(AppTask.kind == kind)
            query = query.order_by(AppTask.created_at.desc()).limit(limit)
            tasks = (await session.execute(query)).scalars().all()
            return TaskListResponse(tasks=[_task_summary(task) for task in tasks])

    async def get_task(self, *, clerk_user_id: str, task_id: str) -> TaskDetail:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            task = await self._task_for_user(session, clerk_user_id=clerk_user_id, task_id=task_id)
            return _task_detail(task)

    async def qa(self, *, clerk_user_id: str, payload: QaRequest, origin_surface: str) -> ActionResponse:
        task, app_user, library = await self._create_task(
            clerk_user_id=clerk_user_id,
            kind="qa",
            title=f"QA: {payload.prompt[:72].strip()}",
            origin_surface=origin_surface,
            origin_thread_id=payload.origin_thread_id,
            input_json=payload.model_dump(mode="json"),
        )
        try:
            hits = await self._sources.search_chunks(
                clerk_user_id=clerk_user_id,
                request=SearchRequest(
                    query=payload.prompt,
                    selected_source_ids=payload.selected_source_ids,
                    tag_ids=payload.tag_ids,
                    tag_match_mode=payload.tag_match_mode,
                    max_results=payload.max_results,
                ),
            )
            answer = await self._openai.answer_with_chunks(prompt=payload.prompt, hits=hits)
            await self._complete_task(
                task_id=task.id,
                result_json={"answer": answer, "hits": [hit.model_dump(mode="json") for hit in hits]},
            )
            return ActionResponse(task_id=task.id, kind="qa", answer=answer, hits=hits)
        except Exception as exc:
            await self._fail_task(task_id=task.id, error_message=str(exc))
            raise
        finally:
            del app_user, library

    async def freeform(self, *, clerk_user_id: str, payload: FreeformRequest, origin_surface: str) -> ActionResponse:
        task, app_user, library = await self._create_task(
            clerk_user_id=clerk_user_id,
            kind="freeform",
            title=f"Freeform: {payload.prompt[:72].strip()}",
            origin_surface=origin_surface,
            origin_thread_id=payload.origin_thread_id,
            input_json=payload.model_dump(mode="json"),
        )
        try:
            hits = await self._sources.search_chunks(
                clerk_user_id=clerk_user_id,
                request=SearchRequest(
                    query=payload.prompt,
                    selected_source_ids=payload.selected_source_ids,
                    tag_ids=payload.tag_ids,
                    tag_match_mode=payload.tag_match_mode,
                    max_results=payload.max_results,
                ),
            )
            answer = await self._openai.freeform_with_chunks(prompt=payload.prompt, hits=hits, mode=payload.mode)
            await self._complete_task(
                task_id=task.id,
                result_json={
                    "answer": answer,
                    "mode": payload.mode,
                    "hits": [hit.model_dump(mode="json") for hit in hits],
                },
            )
            return ActionResponse(task_id=task.id, kind="freeform", answer=answer, hits=hits)
        except Exception as exc:
            await self._fail_task(task_id=task.id, error_message=str(exc))
            raise
        finally:
            del app_user, library

    async def image(
        self, *, clerk_user_id: str, payload: ImageGenerationRequest, origin_surface: str
    ) -> ActionResponse:
        task, _app_user, library = await self._create_task(
            clerk_user_id=clerk_user_id,
            kind="image_gen",
            title=f"Image: {payload.prompt[:72].strip()}",
            origin_surface=origin_surface,
            origin_thread_id=payload.origin_thread_id,
            input_json=payload.model_dump(mode="json"),
        )
        try:
            hits = await self._sources.search_chunks(
                clerk_user_id=clerk_user_id,
                request=SearchRequest(
                    query=payload.prompt,
                    selected_source_ids=payload.selected_source_ids,
                    tag_ids=payload.tag_ids,
                    tag_match_mode=payload.tag_match_mode,
                    max_results=4,
                ),
            )
            image_prompt = payload.prompt
            if hits:
                image_prompt += "\n\nGround visual details in these retrieved chunks:\n" + "\n\n".join(
                    f"- {hit.source_title} ({hit.locator.label()}): {hit.summary}" for hit in hits
                )
            image_bytes, metadata = await self._openai.generate_image_bytes(prompt=image_prompt, size=payload.size)
            asset = await self._store_asset(
                library=library,
                task_id=task.id,
                kind="image",
                filename=f"generated-image-{task.id[:8]}.png",
                media_type="image/png",
                payload=image_bytes,
                metadata={"prompt": payload.prompt, "image_prompt": image_prompt, **metadata},
            )
            await self._complete_task(
                task_id=task.id,
                result_json={
                    "asset": asset.model_dump(mode="json"),
                    "reference_hits": [hit.model_dump(mode="json") for hit in hits],
                },
            )
            return ActionResponse(task_id=task.id, kind="image_gen", hits=hits, asset=asset)
        except Exception as exc:
            await self._fail_task(task_id=task.id, error_message=str(exc))
            raise

    async def voice(
        self, *, clerk_user_id: str, payload: VoiceGenerationRequest, origin_surface: str
    ) -> ActionResponse:
        task, _app_user, library = await self._create_task(
            clerk_user_id=clerk_user_id,
            kind="voice_gen",
            title=f"Voice: {payload.prompt[:72].strip()}",
            origin_surface=origin_surface,
            origin_thread_id=payload.origin_thread_id,
            input_json=payload.model_dump(mode="json"),
        )
        try:
            hits = await self._sources.search_chunks(
                clerk_user_id=clerk_user_id,
                request=SearchRequest(
                    query=payload.prompt,
                    selected_source_ids=payload.selected_source_ids,
                    tag_ids=payload.tag_ids,
                    tag_match_mode=payload.tag_match_mode,
                    max_results=4,
                ),
            )
            speech_text = (
                payload.source_text.strip()
                if isinstance(payload.source_text, str) and payload.source_text.strip()
                else payload.prompt
            )
            voice = payload.voice or self._settings.openai_default_voice
            audio_bytes, metadata = await self._openai.generate_voice_bytes(
                text=speech_text,
                voice=voice,
                response_format=payload.response_format,
            )
            media_type = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/opus"}[payload.response_format]
            asset = await self._store_asset(
                library=library,
                task_id=task.id,
                kind="voice",
                filename=f"generated-voice-{task.id[:8]}.{payload.response_format}",
                media_type=media_type,
                payload=audio_bytes,
                metadata={"prompt": payload.prompt, "source_text": speech_text, **metadata},
            )
            await self._complete_task(task_id=task.id, result_json={"asset": asset.model_dump(mode="json")})
            return ActionResponse(task_id=task.id, kind="voice_gen", hits=hits, asset=asset)
        except Exception as exc:
            await self._fail_task(task_id=task.id, error_message=str(exc))
            raise

    async def branch_search(self, *, clerk_user_id: str, payload: BranchSearchRequest) -> Any:
        return await self._sources.branch_search(clerk_user_id=clerk_user_id, request=payload)

    async def _create_task(
        self,
        *,
        clerk_user_id: str,
        kind: TaskKind,
        title: str,
        origin_surface: str,
        origin_thread_id: str | None,
        input_json: dict[str, object],
    ) -> tuple[AppTask, AppUser, UserLibrary]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=clerk_user_id)
            if not app_user.active:
                raise PermissionError("The active user is not allowed to run actions.")
            library = await self._sources.library_for_user(session, app_user=app_user)
            task = AppTask(
                user_id=app_user.id,
                library_id=library.id,
                kind=kind,
                status="running",
                title=title,
                origin_surface=origin_surface,
                origin_thread_id=origin_thread_id,
                input_json=input_json,
                started_at=_utcnow(),
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task, app_user, library

    async def _store_asset(
        self,
        *,
        library: UserLibrary,
        task_id: str,
        kind: str,
        filename: str,
        media_type: str,
        payload: bytes,
        metadata: dict[str, object],
    ) -> GeneratedAsset:
        stored = await self._storage.put_bytes(
            scope=f"generated/{kind}", filename=filename, media_type=media_type, payload=payload
        )
        await self._database.ensure_ready()
        async with self._database.session() as session:
            record = StoredAsset(
                library_id=library.id,
                task_id=task_id,
                kind=kind,
                filename=filename,
                media_type=media_type,
                byte_size=stored.byte_size,
                storage_provider=stored.provider,
                storage_key=stored.key,
                metadata_json=metadata,
                created_at=_utcnow(),
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return GeneratedAsset(
                id=record.id,
                kind=cast(Any, record.kind),
                filename=record.filename,
                media_type=record.media_type,
                byte_size=record.byte_size,
                download_url=self._storage.build_download_url(
                    key=record.storage_key,
                    filename=record.filename,
                    media_type=record.media_type,
                    inline=True,
                ),
            )

    async def _complete_task(self, *, task_id: str, result_json: dict[str, object]) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            task = await session.get(AppTask, task_id)
            if task is None:
                return
            task.status = "completed"
            task.result_json = result_json
            task.error_message = None
            task.completed_at = _utcnow()
            task.updated_at = _utcnow()
            await session.commit()
        logger.info("task_completed task_id=%s", task_id)

    async def _fail_task(self, *, task_id: str, error_message: str) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            task = await session.get(AppTask, task_id)
            if task is None:
                return
            task.status = "failed"
            task.error_message = error_message
            task.completed_at = _utcnow()
            task.updated_at = _utcnow()
            await session.commit()
        logger.error("task_failed task_id=%s error=%s", task_id, error_message)

    async def _task_for_user(self, session: Any, *, clerk_user_id: str, task_id: str) -> AppTask:
        app_user = await self._sources.ensure_app_user(session, clerk_user_id=clerk_user_id)
        task = await session.scalar(select(AppTask).where(AppTask.id == task_id, AppTask.user_id == app_user.id))
        if task is None:
            raise FileNotFoundError("Task not found.")
        return task


def _task_summary(task: AppTask) -> TaskSummary:
    return TaskSummary(
        id=task.id,
        kind=cast(TaskKind, task.kind),
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


def _task_detail(task: AppTask) -> TaskDetail:
    summary = _task_summary(task)
    return TaskDetail(**summary.model_dump(mode="python"), state_json=task.state_json)


def _utcnow() -> datetime:
    return datetime.now(UTC)

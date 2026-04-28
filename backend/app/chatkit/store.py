from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from chatkit.store import AttachmentStore, NotFoundError, Store
from chatkit.types import Attachment, AttachmentCreateParams, FileAttachment, Page, ThreadItem, ThreadMetadata
from pydantic import TypeAdapter
from sqlalchemy import delete, select, update

from backend.app.db.session import DatabaseManager
from backend.app.models import AppChatAttachment, AppChatEntry, AppChatThread, AppTask
from backend.app.services.sources import SourceService

THREAD_ITEM_ADAPTER: TypeAdapter[ThreadItem] = TypeAdapter(ThreadItem)
ATTACHMENT_ADAPTER: TypeAdapter[Attachment] = TypeAdapter(Attachment)


@dataclass(slots=True)
class VectorstoreChatContext:
    clerk_user_id: str
    user_email: str | None
    display_name: str
    role: str | None
    credit_floor_usd: float
    bearer_token: str
    library_id: str | None
    selected_source_ids: list[str]
    thread_origin: str | None


class VectorstoreChatStore(Store[VectorstoreChatContext], AttachmentStore[VectorstoreChatContext]):
    def __init__(self, *, database: DatabaseManager, sources: SourceService) -> None:
        self._database = database
        self._sources = sources

    def generate_thread_id(self, context: VectorstoreChatContext) -> str:
        del context
        return f"chat_{uuid4().hex}"

    async def load_thread(self, thread_id: str, context: VectorstoreChatContext) -> ThreadMetadata:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            record = await session.scalar(
                select(AppChatThread).where(AppChatThread.id == thread_id, AppChatThread.user_id == app_user.id)
            )
            if record is None:
                raise NotFoundError(f"Thread {thread_id} was not found")
            return self._to_thread_metadata(record)

    async def save_thread(self, thread: ThreadMetadata, context: VectorstoreChatContext) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            record = await session.scalar(
                select(AppChatThread).where(AppChatThread.id == thread.id, AppChatThread.user_id == app_user.id)
            )
            next_sequence = await self._next_thread_sequence(session)
            metadata_json = thread_metadata_with_scope(thread.metadata, context)
            if record is None:
                session.add(
                    AppChatThread(
                        id=thread.id,
                        user_id=app_user.id,
                        title=thread.title,
                        metadata_json=metadata_json,
                        status_json=thread.status.model_dump(mode="json"),
                        allowed_image_domains_json=thread.allowed_image_domains,
                        updated_sequence=next_sequence,
                        created_at=thread.created_at,
                        updated_at=datetime.now(UTC),
                    )
                )
            else:
                record.title = thread.title
                record.thread_metadata = metadata_json
                record.status_payload = thread.status.model_dump(mode="json")
                record.allowed_image_domains_json = thread.allowed_image_domains
                record.updated_sequence = next_sequence
                record.updated_at = datetime.now(UTC)
            await session.commit()

    async def load_thread_items(
        self,
        thread_id: str,
        after: str | None,
        limit: int,
        order: str,
        context: VectorstoreChatContext,
    ) -> Page[ThreadItem]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            await self._require_thread(session, thread_id=thread_id, user_id=app_user.id)
            query = select(AppChatEntry).where(AppChatEntry.thread_id == thread_id)
            if after:
                cursor = await session.get(AppChatEntry, after)
                if cursor is not None:
                    query = query.where(
                        AppChatEntry.sequence < cursor.sequence
                        if order == "desc"
                        else AppChatEntry.sequence > cursor.sequence
                    )
            query = query.where(AppChatEntry.visibility == "active")
            query = query.order_by(
                AppChatEntry.sequence.desc() if order == "desc" else AppChatEntry.sequence.asc()
            ).limit(limit + 1)
            records = list((await session.execute(query)).scalars().all())
            has_more = len(records) > limit
            page_records = records[:limit]
            return Page[ThreadItem](
                data=[THREAD_ITEM_ADAPTER.validate_python(record.payload) for record in page_records],
                has_more=has_more,
                after=page_records[-1].id if has_more and page_records else None,
            )

    async def add_thread_item(self, thread_id: str, item: ThreadItem, context: VectorstoreChatContext) -> None:
        await self._save_item(thread_id=thread_id, item=item, context=context, create_only=True)

    async def save_item(self, thread_id: str, item: ThreadItem, context: VectorstoreChatContext) -> None:
        await self._save_item(thread_id=thread_id, item=item, context=context, create_only=False)

    async def load_item(self, thread_id: str, item_id: str, context: VectorstoreChatContext) -> ThreadItem:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            await self._require_thread(session, thread_id=thread_id, user_id=app_user.id)
            record = await session.get(AppChatEntry, item_id)
            if record is None or record.thread_id != thread_id:
                raise NotFoundError(f"Thread item {item_id} was not found")
            return THREAD_ITEM_ADAPTER.validate_python(record.payload)

    async def compact_thread_items(
        self,
        *,
        thread_id: str,
        item_ids: list[str],
        summary_item: ThreadItem,
        compaction_group_id: str,
        context: VectorstoreChatContext,
    ) -> int:
        unique_item_ids = list(dict.fromkeys(item_id for item_id in item_ids if item_id != summary_item.id))
        if not unique_item_ids:
            await self.save_item(thread_id, summary_item, context=context)
            return 0

        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            await self._require_thread(session, thread_id=thread_id, user_id=app_user.id)
            compacted_at = datetime.now(UTC)
            records = list(
                (
                    await session.execute(
                        select(AppChatEntry).where(
                            AppChatEntry.thread_id == thread_id,
                            AppChatEntry.id.in_(unique_item_ids),
                            AppChatEntry.visibility == "active",
                        )
                    )
                )
                .scalars()
                .all()
            )
            for record in records:
                record.visibility = "compacted"
                record.compaction_group_id = compaction_group_id
                record.compacted_at = compacted_at
            summary_sequence = max(record.sequence for record in records) + 1
            later_records = list(
                (
                    await session.execute(
                        select(AppChatEntry)
                        .where(
                            AppChatEntry.thread_id == thread_id,
                            AppChatEntry.sequence >= summary_sequence,
                            AppChatEntry.id != summary_item.id,
                        )
                        .order_by(AppChatEntry.sequence.desc())
                    )
                )
                .scalars()
                .all()
            )
            for record in later_records:
                record.sequence += 1
                await session.flush()
            existing_summary = await session.get(AppChatEntry, summary_item.id)
            if existing_summary is None:
                session.add(
                    AppChatEntry(
                        id=summary_item.id,
                        thread_id=thread_id,
                        sequence=summary_sequence,
                        item_type=summary_item.type,
                        visibility="active",
                        payload=summary_item.model_dump(mode="json"),
                        compaction_group_id=compaction_group_id,
                    )
                )
            elif existing_summary.thread_id == thread_id:
                existing_summary.item_type = summary_item.type
                existing_summary.visibility = "active"
                existing_summary.payload = summary_item.model_dump(mode="json")
                existing_summary.compaction_group_id = compaction_group_id
                existing_summary.compacted_at = None
            await session.commit()
            return len(records)

    async def delete_thread(self, thread_id: str, context: VectorstoreChatContext) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            thread = await session.scalar(
                select(AppChatThread).where(AppChatThread.id == thread_id, AppChatThread.user_id == app_user.id)
            )
            if thread is None:
                return
            await session.delete(thread)
            await session.commit()

    async def delete_thread_item(self, thread_id: str, item_id: str, context: VectorstoreChatContext) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            await self._require_thread(session, thread_id=thread_id, user_id=app_user.id)
            await session.execute(
                delete(AppChatEntry).where(AppChatEntry.id == item_id, AppChatEntry.thread_id == thread_id)
            )
            await session.commit()

    async def load_threads(
        self, limit: int, after: str | None, order: str, context: VectorstoreChatContext
    ) -> Page[ThreadMetadata]:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            query = select(AppChatThread).where(AppChatThread.user_id == app_user.id)
            if after:
                cursor = await session.get(AppChatThread, after)
                if cursor is not None:
                    query = query.where(
                        AppChatThread.updated_sequence < cursor.updated_sequence
                        if order == "desc"
                        else AppChatThread.updated_sequence > cursor.updated_sequence
                    )
            query = query.order_by(
                AppChatThread.updated_sequence.desc() if order == "desc" else AppChatThread.updated_sequence.asc()
            ).limit(limit + 1)
            records = list((await session.execute(query)).scalars().all())
            has_more = len(records) > limit
            page_records = records[:limit]
            return Page[ThreadMetadata](
                data=[self._to_thread_metadata(record) for record in page_records],
                has_more=has_more,
                after=page_records[-1].id if has_more and page_records else None,
            )

    async def save_attachment(self, attachment: Attachment, context: VectorstoreChatContext) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            payload = attachment.model_dump(mode="json")
            metadata = _metadata_dict(payload.get("metadata"))
            if attachment.thread_id is not None:
                metadata["thread_id"] = attachment.thread_id
                payload["metadata"] = metadata
            record = await session.get(AppChatAttachment, attachment.id)
            if record is None:
                session.add(
                    AppChatAttachment(
                        id=attachment.id,
                        user_id=app_user.id,
                        kind=attachment.type,
                        payload=payload,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )
            else:
                record.kind = attachment.type
                record.payload = payload
                record.updated_at = datetime.now(UTC)
            task_id = metadata.get("task_id")
            if isinstance(task_id, str) and task_id.strip() and attachment.thread_id is not None:
                await session.execute(
                    update(AppTask)
                    .where(AppTask.id == task_id, AppTask.user_id == app_user.id, AppTask.origin_thread_id.is_(None))
                    .values(origin_thread_id=attachment.thread_id, updated_at=datetime.now(UTC))
                )
            await session.commit()

    async def load_attachment(self, attachment_id: str, context: VectorstoreChatContext) -> Attachment:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            record = await session.scalar(
                select(AppChatAttachment).where(
                    AppChatAttachment.id == attachment_id, AppChatAttachment.user_id == app_user.id
                )
            )
            if record is None:
                raise NotFoundError(f"Attachment {attachment_id} was not found")
            return ATTACHMENT_ADAPTER.validate_python(record.payload)

    async def delete_attachment(self, attachment_id: str, context: VectorstoreChatContext) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            await session.execute(
                delete(AppChatAttachment).where(
                    AppChatAttachment.id == attachment_id, AppChatAttachment.user_id == app_user.id
                )
            )
            await session.commit()

    async def create_attachment(self, input: AttachmentCreateParams, context: VectorstoreChatContext) -> Attachment:
        attachment_id = self.generate_attachment_id(input.mime_type, context)
        return FileAttachment(
            id=attachment_id,
            name=input.name,
            mime_type=input.mime_type,
            metadata={
                "attachment_id": attachment_id,
                "upload_status": "pending",
                "upload_endpoint": "/api/chatkit/attachments",
            },
        )

    async def create_source_attachment(
        self,
        *,
        filename: str,
        declared_media_type: str | None,
        payload: bytes,
        tag_ids: list[str],
        user_guidance: str | None,
        thread_id: str | None,
        context: VectorstoreChatContext,
    ) -> Attachment:
        ingest_response = await self._sources.ingest_source(
            clerk_user_id=context.clerk_user_id,
            filename=filename,
            declared_media_type=declared_media_type,
            payload=payload,
            tag_ids=tag_ids,
            user_guidance=user_guidance,
            origin_surface="chatkit",
            origin_thread_id=thread_id,
        )
        attachment_id = self.generate_attachment_id(ingest_response.source.media_type, context)
        task_payload = ingest_response.task.model_dump(mode="json") if ingest_response.task is not None else None
        metadata = {
            "attachment_id": attachment_id,
            "source_id": ingest_response.source.id,
            "source_title": ingest_response.source.display_title,
            "source_kind": ingest_response.source.source_kind,
            "source_status": ingest_response.source.status,
            "task_id": ingest_response.task.id if ingest_response.task is not None else None,
            "task_status": ingest_response.task.status if ingest_response.task is not None else None,
            "origin_surface": "chatkit",
            "thread_id": thread_id,
            "source": ingest_response.source.model_dump(mode="json"),
            "task": task_payload,
        }
        attachment = FileAttachment(
            id=attachment_id,
            name=filename,
            mime_type=ingest_response.source.media_type,
            thread_id=thread_id,
            metadata={key: value for key, value in metadata.items() if value is not None},
        )
        await self.save_attachment(attachment, context=context)
        return attachment

    async def _save_item(
        self, *, thread_id: str, item: ThreadItem, context: VectorstoreChatContext, create_only: bool
    ) -> None:
        await self._database.ensure_ready()
        async with self._database.session() as session:
            app_user = await self._sources.ensure_app_user(session, clerk_user_id=context.clerk_user_id)
            await self._require_thread(session, thread_id=thread_id, user_id=app_user.id)
            existing = await session.get(AppChatEntry, item.id)
            if existing is not None and create_only:
                return
            if existing is None:
                session.add(
                    AppChatEntry(
                        id=item.id,
                        thread_id=thread_id,
                        sequence=await self._next_item_sequence(session, thread_id=thread_id),
                        item_type=item.type,
                        visibility="active",
                        payload=item.model_dump(mode="json"),
                    )
                )
            else:
                existing.payload = item.model_dump(mode="json")
                existing.item_type = item.type
                existing.visibility = "active"
            await session.commit()

    @staticmethod
    def _to_thread_metadata(record: AppChatThread) -> ThreadMetadata:
        from chatkit.types import ActiveStatus

        return ThreadMetadata(
            id=record.id,
            title=record.title,
            metadata=record.thread_metadata,
            status=ActiveStatus.model_validate(record.status_payload),
            allowed_image_domains=record.allowed_image_domains_json,
            created_at=record.created_at,
        )

    @staticmethod
    async def _require_thread(session: Any, *, thread_id: str, user_id: int) -> AppChatThread:
        record = await session.scalar(
            select(AppChatThread).where(AppChatThread.id == thread_id, AppChatThread.user_id == user_id)
        )
        if record is None:
            raise NotFoundError(f"Thread {thread_id} was not found")
        return record

    @staticmethod
    async def _next_thread_sequence(session: Any) -> int:
        value = await session.scalar(
            select(AppChatThread.updated_sequence).order_by(AppChatThread.updated_sequence.desc()).limit(1)
        )
        return int(value or 0) + 1

    @staticmethod
    async def _next_item_sequence(session: Any, *, thread_id: str) -> int:
        value = await session.scalar(
            select(AppChatEntry.sequence)
            .where(AppChatEntry.thread_id == thread_id)
            .order_by(AppChatEntry.sequence.desc())
            .limit(1)
        )
        return int(value or 0) + 1


def _metadata_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def thread_metadata_with_scope(
    metadata: Mapping[str, Any] | None,
    context: VectorstoreChatContext,
) -> dict[str, object]:
    output: dict[str, object] = dict(metadata or {})
    output["scope_origin"] = context.thread_origin or "web"
    output["scope_updated_at"] = datetime.now(UTC).isoformat()
    return output

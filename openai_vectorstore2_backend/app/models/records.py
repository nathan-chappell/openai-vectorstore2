from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ai_portfolio_admin.orm import CreditGrantMixin, FreeCreditRequestMixin, PaymentAttemptMixin, UserCreditBalanceMixin

from openai_vectorstore2_backend.app.schemas import (
    OpenAIAttributes,
    OpenAIUsagePayload,
    ResearchProvenance,
    SourceMetadata,
    StructuredObject,
)


def new_id() -> str:
    return uuid4().hex


def _object_payload(value: dict[str, object] | None) -> StructuredObject:
    return dict(value or {})


def _openai_attributes_payload(value: dict[str, object] | None) -> OpenAIAttributes:
    attributes: OpenAIAttributes = {}
    for key, item in (value or {}).items():
        if isinstance(item, bool | str):
            attributes[key] = item
        elif isinstance(item, int | float):
            attributes[key] = float(item)
    return attributes


class Base(DeclarativeBase):
    """Declarative base for app-owned state."""


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    clerk_user_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    primary_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(nullable=False, default=False)
    role: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    library: Mapped["UserLibrary | None"] = relationship(back_populates="owner", uselist=False)
    tasks: Mapped[list["AppTask"]] = relationship(back_populates="user")
    chat_threads: Mapped[list["AppChatThread"]] = relationship(back_populates="user")
    chat_attachments: Mapped[list["AppChatAttachment"]] = relationship(back_populates="user")
    research_candidates: Mapped[list["ResearchImportCandidate"]] = relationship(back_populates="user")


class UserCreditBalance(UserCreditBalanceMixin, Base):
    __tablename__ = "user_credit_balances"


class CreditGrant(CreditGrantMixin, Base):
    __tablename__ = "credit_grants"
    __table_args__ = (Index("ix_credit_grants_user_created_at", "user_id", "created_at"),)


class PaymentAttempt(PaymentAttemptMixin, Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        Index("ix_payment_attempts_user_created_at", "user_id", "created_at"),
        Index("ix_payment_attempts_status_created_at", "status", "created_at"),
    )


class FreeCreditRequest(FreeCreditRequestMixin, Base):
    __tablename__ = "free_credit_requests"
    __table_args__ = (
        Index("ix_free_credit_requests_user_created_at", "user_id", "created_at"),
        Index("ix_free_credit_requests_status_created_at", "status", "created_at"),
        Index("ix_free_credit_requests_idempotency_key", "idempotency_key"),
    )


class CostEvent(Base):
    __tablename__ = "cost_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_cost_events_event_key"),
        Index("ix_cost_events_user_created_at", "clerk_user_id", "created_at"),
        Index("ix_cost_events_thread_created_at", "thread_id", "created_at"),
        Index("ix_cost_events_task_created_at", "task_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    event_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    clerk_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    operation_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    origin_surface: Mapped[str] = mapped_column(String(32), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    source_file_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    openai_response_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    openai_conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    openai_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pricing_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    raw_usage_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    openai_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    platform_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    platform_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    @property
    def raw_usage(self) -> OpenAIUsagePayload:
        return cast(OpenAIUsagePayload, _object_payload(self.raw_usage_json))

    @raw_usage.setter
    def raw_usage(self, value: OpenAIUsagePayload) -> None:
        self.raw_usage_json = dict(value)


class UserLibrary(Base):
    __tablename__ = "user_library"
    __table_args__ = (
        Index("ix_user_library_user_updated_at", "user_id", "updated_at"),
        Index("ix_user_library_visibility_updated_at", "visibility", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(24), nullable=False, default="private")
    slug: Mapped[str | None] = mapped_column(String(96), nullable=True, unique=True, index=True)
    openai_vector_store_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    owner: Mapped[AppUser] = relationship(back_populates="library")
    sources: Mapped[list["SourceFile"]] = relationship(back_populates="library", cascade="all, delete-orphan")
    filesystem_entries: Mapped[list["FilesystemEntry"]] = relationship(
        back_populates="library", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["AppTask"]] = relationship(back_populates="library")
    assets: Mapped[list["StoredAsset"]] = relationship(back_populates="library", cascade="all, delete-orphan")
    research_candidates: Mapped[list["ResearchImportCandidate"]] = relationship(
        back_populates="library", cascade="all, delete-orphan"
    )


class SourceFile(Base):
    __tablename__ = "source_file"
    __table_args__ = (
        Index("ix_source_file_library_status_created_at", "library_id", "status", "created_at"),
        Index("ix_source_file_library_tag_slug", "library_id", "tag_slug"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    library_id: Mapped[str] = mapped_column(ForeignKey("user_library.id"), nullable=False, index=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True, index=True)
    display_title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(nullable=False, default=0)
    storage_provider: Mapped[str] = mapped_column(String(48), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    openai_original_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    openai_original_file_purpose: Mapped[str | None] = mapped_column(String(32), nullable=True)
    openai_vector_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    vector_attributes_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    tag_slug: Mapped[str | None] = mapped_column(String(96), nullable=True)
    ingest_strategy: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    library: Mapped[UserLibrary] = relationship(back_populates="sources")
    filesystem_entry: Mapped["FilesystemEntry | None"] = relationship(
        back_populates="source_file", uselist=False, cascade="all, delete-orphan"
    )
    chunks: Mapped[list["SemanticChunk"]] = relationship(back_populates="source_file", cascade="all, delete-orphan")
    tasks: Mapped[list["AppTask"]] = relationship(back_populates="source_file")
    research_seed_candidates: Mapped[list["ResearchImportCandidate"]] = relationship(
        back_populates="parent_source_file",
        foreign_keys=lambda: ResearchImportCandidate.parent_source_file_id,
    )
    research_linked_candidates: Mapped[list["ResearchImportCandidate"]] = relationship(
        back_populates="linked_source_file",
        foreign_keys=lambda: ResearchImportCandidate.linked_source_file_id,
    )

    @property
    def source_metadata(self) -> SourceMetadata:
        return cast(SourceMetadata, _object_payload(self.metadata_json))

    @source_metadata.setter
    def source_metadata(self, value: SourceMetadata) -> None:
        self.metadata_json = dict(value)

    @property
    def vector_attributes(self) -> OpenAIAttributes:
        return _openai_attributes_payload(self.vector_attributes_json)

    @vector_attributes.setter
    def vector_attributes(self, value: OpenAIAttributes) -> None:
        self.vector_attributes_json = dict(value)


class FilesystemEntry(Base):
    __tablename__ = "filesystem_entry"
    __table_args__ = (
        UniqueConstraint("library_id", "normalized_path", name="uq_filesystem_entry_library_path"),
        Index(
            "ix_filesystem_entry_library_parent_kind_name",
            "library_id",
            "parent_id",
            "kind",
            "normalized_name",
        ),
        Index("ix_filesystem_entry_source_file_id", "source_file_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    library_id: Mapped[str] = mapped_column(ForeignKey("user_library.id"), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("filesystem_entry.id"), nullable=True, index=True)
    source_file_id: Mapped[str | None] = mapped_column(ForeignKey("source_file.id"), nullable=True, unique=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    normalized_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    library: Mapped[UserLibrary] = relationship(back_populates="filesystem_entries")
    parent: Mapped["FilesystemEntry | None"] = relationship(
        back_populates="children", remote_side=lambda: FilesystemEntry.id
    )
    children: Mapped[list["FilesystemEntry"]] = relationship(back_populates="parent", cascade="all, delete-orphan")
    source_file: Mapped[SourceFile | None] = relationship(back_populates="filesystem_entry")


class SemanticChunk(Base):
    __tablename__ = "semantic_chunk"
    __table_args__ = (
        UniqueConstraint("source_file_id", "sequence", name="uq_semantic_chunk_source_sequence"),
        Index("ix_semantic_chunk_source_status_sequence", "source_file_id", "status", "sequence"),
        Index("ix_semantic_chunk_openai_file_id", "openai_file_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source_file_id: Mapped[str] = mapped_column(ForeignKey("source_file.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    keywords_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    locator_type: Mapped[str] = mapped_column(String(32), nullable=False)
    start_page: Mapped[int | None] = mapped_column(nullable=True)
    end_page: Mapped[int | None] = mapped_column(nullable=True)
    start_line: Mapped[int | None] = mapped_column(nullable=True)
    end_line: Mapped[int | None] = mapped_column(nullable=True)
    start_seconds: Mapped[float | None] = mapped_column(nullable=True)
    end_seconds: Mapped[float | None] = mapped_column(nullable=True)
    strategy_label: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    openai_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    vector_attributes_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    source_file: Mapped[SourceFile] = relationship(back_populates="chunks")

    @property
    def vector_attributes(self) -> OpenAIAttributes:
        return _openai_attributes_payload(self.vector_attributes_json)

    @vector_attributes.setter
    def vector_attributes(self, value: OpenAIAttributes) -> None:
        self.vector_attributes_json = dict(value)


class StoredAsset(Base):
    __tablename__ = "stored_asset"
    __table_args__ = (Index("ix_stored_asset_library_kind_created_at", "library_id", "kind", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    library_id: Mapped[str] = mapped_column(ForeignKey("user_library.id"), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("app_task.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    byte_size: Mapped[int] = mapped_column(nullable=False, default=0)
    storage_provider: Mapped[str] = mapped_column(String(48), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    library: Mapped[UserLibrary] = relationship(back_populates="assets")
    task: Mapped["AppTask | None"] = relationship(back_populates="assets")

    @property
    def asset_metadata(self) -> StructuredObject:
        return _object_payload(self.metadata_json)

    @asset_metadata.setter
    def asset_metadata(self, value: StructuredObject) -> None:
        self.metadata_json = dict(value)


class AppTask(Base):
    __tablename__ = "app_task"
    __table_args__ = (
        Index("ix_app_task_user_created_at", "user_id", "created_at"),
        Index("ix_app_task_library_created_at", "library_id", "created_at"),
        Index("ix_app_task_status_created_at", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False, index=True)
    library_id: Mapped[str] = mapped_column(ForeignKey("user_library.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    origin_surface: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_file_id: Mapped[str | None] = mapped_column(ForeignKey("source_file.id"), nullable=True, index=True)
    input_json: Mapped[dict[str, object] | list[object] | None] = mapped_column(JSON, nullable=True)
    state_json: Mapped[dict[str, object] | list[object] | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict[str, object] | list[object] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[AppUser] = relationship(back_populates="tasks")
    library: Mapped[UserLibrary] = relationship(back_populates="tasks")
    source_file: Mapped[SourceFile | None] = relationship(back_populates="tasks")
    assets: Mapped[list[StoredAsset]] = relationship(back_populates="task")
    research_candidates: Mapped[list["ResearchImportCandidate"]] = relationship(back_populates="task")

    @property
    def input_object(self) -> StructuredObject:
        return self.input_json if isinstance(self.input_json, dict) else {}

    @property
    def state_object(self) -> StructuredObject:
        return self.state_json if isinstance(self.state_json, dict) else {}

    @property
    def result_object(self) -> StructuredObject:
        return self.result_json if isinstance(self.result_json, dict) else {}


class ResearchImportCandidate(Base):
    __tablename__ = "research_import_candidate"
    __table_args__ = (
        Index("ix_research_candidate_library_status_created", "library_id", "status", "created_at"),
        Index("ix_research_candidate_task_depth", "task_id", "depth"),
        Index("ix_research_candidate_normalized_url", "library_id", "normalized_url"),
        Index("ix_research_candidate_content_hash", "library_id", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    library_id: Mapped[str] = mapped_column(ForeignKey("user_library.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("app_task.id"), nullable=False, index=True)
    parent_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_import_candidate.id"), nullable=True, index=True
    )
    parent_source_file_id: Mapped[str | None] = mapped_column(ForeignKey("source_file.id"), nullable=True, index=True)
    linked_source_file_id: Mapped[str | None] = mapped_column(ForeignKey("source_file.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    normalized_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(nullable=True)
    depth: Mapped[int] = mapped_column(nullable=False, default=0)
    provenance_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    library: Mapped[UserLibrary] = relationship(back_populates="research_candidates")
    user: Mapped[AppUser] = relationship(back_populates="research_candidates")
    task: Mapped[AppTask] = relationship(back_populates="research_candidates")
    parent_candidate: Mapped["ResearchImportCandidate | None"] = relationship(
        back_populates="child_candidates",
        remote_side=lambda: ResearchImportCandidate.id,
    )
    child_candidates: Mapped[list["ResearchImportCandidate"]] = relationship(back_populates="parent_candidate")
    parent_source_file: Mapped[SourceFile | None] = relationship(
        back_populates="research_seed_candidates",
        foreign_keys=[parent_source_file_id],
    )
    linked_source_file: Mapped[SourceFile | None] = relationship(
        back_populates="research_linked_candidates",
        foreign_keys=[linked_source_file_id],
    )

    @property
    def provenance(self) -> ResearchProvenance:
        return cast(ResearchProvenance, _object_payload(self.provenance_json))

    @provenance.setter
    def provenance(self, value: ResearchProvenance) -> None:
        self.provenance_json = dict(value)


class AppChatThread(Base):
    __tablename__ = "app_chat_thread"
    __table_args__ = (Index("ix_app_chat_thread_user_updated_sequence", "user_id", "updated_sequence"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    status_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    allowed_image_domains_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    updated_sequence: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[AppUser] = relationship(back_populates="chat_threads")
    entries: Mapped[list["AppChatEntry"]] = relationship(back_populates="thread", cascade="all, delete-orphan")

    @property
    def thread_metadata(self) -> StructuredObject:
        return _object_payload(self.metadata_json)

    @thread_metadata.setter
    def thread_metadata(self, value: StructuredObject) -> None:
        self.metadata_json = dict(value)

    @property
    def status_payload(self) -> StructuredObject:
        return _object_payload(self.status_json)

    @status_payload.setter
    def status_payload(self, value: StructuredObject) -> None:
        self.status_json = dict(value)


class AppChatEntry(Base):
    __tablename__ = "app_chat_entry"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_app_chat_entry_thread_sequence"),
        Index("ix_app_chat_entry_thread_sequence", "thread_id", "sequence"),
        Index("ix_app_chat_entry_thread_visibility_sequence", "thread_id", "visibility", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("app_chat_thread.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(nullable=False)
    item_type: Mapped[str] = mapped_column(String(80), nullable=False)
    visibility: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    compaction_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    compacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    thread: Mapped[AppChatThread] = relationship(back_populates="entries")


class AppChatAttachment(Base):
    __tablename__ = "app_chat_attachment"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[AppUser | None] = relationship(back_populates="chat_attachments")

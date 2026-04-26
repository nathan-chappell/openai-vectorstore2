from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return uuid4().hex


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


class UserLibrary(Base):
    __tablename__ = "user_library"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_library_user_id"),
        Index("ix_user_library_user_updated_at", "user_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    openai_vector_store_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    owner: Mapped[AppUser] = relationship(back_populates="library")
    sources: Mapped[list["SourceFile"]] = relationship(back_populates="library", cascade="all, delete-orphan")
    filesystem_entries: Mapped[list["FilesystemEntry"]] = relationship(
        back_populates="library", cascade="all, delete-orphan"
    )
    tags: Mapped[list["Tag"]] = relationship(back_populates="library", cascade="all, delete-orphan")
    tasks: Mapped[list["AppTask"]] = relationship(back_populates="library")
    assets: Mapped[list["StoredAsset"]] = relationship(back_populates="library", cascade="all, delete-orphan")


class Tag(Base):
    __tablename__ = "tag"
    __table_args__ = (
        UniqueConstraint("library_id", "slug", name="uq_tag_library_slug"),
        UniqueConstraint("library_id", "name", name="uq_tag_library_name"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    library_id: Mapped[str] = mapped_column(ForeignKey("user_library.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(96), nullable=False)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="auto")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    library: Mapped[UserLibrary] = relationship(back_populates="tags")
    source_links: Mapped[list["SourceTagLink"]] = relationship(back_populates="tag", cascade="all, delete-orphan")


class SourceFile(Base):
    __tablename__ = "source_file"
    __table_args__ = (Index("ix_source_file_library_status_created_at", "library_id", "status", "created_at"),)

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
    ingest_strategy: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    library: Mapped[UserLibrary] = relationship(back_populates="sources")
    filesystem_entry: Mapped["FilesystemEntry | None"] = relationship(
        back_populates="source_file", uselist=False, cascade="all, delete-orphan"
    )
    chunks: Mapped[list["SemanticChunk"]] = relationship(back_populates="source_file", cascade="all, delete-orphan")
    tag_links: Mapped[list["SourceTagLink"]] = relationship(back_populates="source_file", cascade="all, delete-orphan")
    tasks: Mapped[list["AppTask"]] = relationship(back_populates="source_file")


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


class SourceTagLink(Base):
    __tablename__ = "source_tag_link"

    source_file_id: Mapped[str] = mapped_column(ForeignKey("source_file.id"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(ForeignKey("tag.id"), primary_key=True)

    source_file: Mapped[SourceFile] = relationship(back_populates="tag_links")
    tag: Mapped[Tag] = relationship(back_populates="source_links")


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


class AppChatEntry(Base):
    __tablename__ = "app_chat_entry"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_app_chat_entry_thread_sequence"),
        Index("ix_app_chat_entry_thread_sequence", "thread_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("app_chat_thread.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(nullable=False)
    item_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
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

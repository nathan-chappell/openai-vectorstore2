from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field

StructuredPayload: TypeAlias = dict[str, Any] | list[Any] | None
OpenAIAttributeValue: TypeAlias = str | float | bool
OpenAIAttributes: TypeAlias = dict[str, OpenAIAttributeValue]

SourceKind: TypeAlias = Literal["pdf", "text", "conversation", "image", "audio", "video", "other"]
SourceStatus: TypeAlias = Literal["processing", "ready", "failed"]
TaskKind: TypeAlias = Literal["ingest", "resplit", "qa", "freeform", "branch_search", "image_gen", "voice_gen"]
ActionKind: TypeAlias = Literal["qa", "freeform", "image_gen", "voice_gen"]
TaskStatus: TypeAlias = Literal["queued", "running", "completed", "failed", "cancelled"]
TaskOriginSurface: TypeAlias = Literal["web", "mcp", "chatkit", "system"]
TagMatchMode: TypeAlias = Literal["all", "any"]
LocatorType: TypeAlias = Literal["page_range", "line_range", "time_range", "generated"]
AssetKind: TypeAlias = Literal["image", "voice", "source_copy"]


class AuthUser(BaseModel):
    clerk_user_id: str
    display_name: str
    primary_email: str | None = None
    active: bool
    role: str | None = None


class TagSummary(BaseModel):
    id: str
    name: str
    slug: str
    color: str | None = None
    source: Literal["auto", "manual"] = "auto"
    source_count: int = 0


class ChunkLocator(BaseModel):
    type: LocatorType
    start_page: int | None = Field(default=None, ge=1)
    end_page: int | None = Field(default=None, ge=1)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)

    def label(self) -> str:
        if self.type == "page_range":
            if self.start_page == self.end_page:
                return f"p. {self.start_page}"
            return f"pp. {self.start_page}-{self.end_page}"
        if self.type == "line_range":
            if self.start_line == self.end_line:
                return f"line {self.start_line}"
            return f"lines {self.start_line}-{self.end_line}"
        if self.type == "time_range":
            return (
                f"{self.start_seconds:.1f}s-{self.end_seconds:.1f}s"
                if self.start_seconds is not None and self.end_seconds is not None
                else "time range"
            )
        return "generated"


class SemanticChunkDraft(BaseModel):
    sequence: int = Field(ge=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    text: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    locator: ChunkLocator
    strategy_label: str = "semantic"


class SemanticSplitResult(BaseModel):
    strategy_label: str = "semantic"
    tags: list[str] = Field(default_factory=list)
    chunks: list[SemanticChunkDraft] = Field(default_factory=list)


class LibrarySourceSummary(BaseModel):
    id: str
    display_title: str
    original_filename: str
    media_type: str
    source_kind: SourceKind
    status: SourceStatus
    byte_size: int
    chunk_count: int
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    tags: list[TagSummary] = Field(default_factory=list)
    openai_original_file_id: str | None = None


class ChunkSummary(BaseModel):
    id: str
    source_file_id: str
    sequence: int
    title: str
    summary: str
    text: str
    keywords: list[str] = Field(default_factory=list)
    locator: ChunkLocator
    strategy_label: str
    openai_file_id: str | None = None
    created_at: datetime
    updated_at: datetime


class LibrarySourceDetail(LibrarySourceSummary):
    storage_provider: str
    storage_key: str
    ingest_strategy: str | None = None
    chunks: list[ChunkSummary] = Field(default_factory=list)


class FileListResponse(BaseModel):
    sources: list[LibrarySourceSummary] = Field(default_factory=list)
    total_count: int
    page: int
    page_size: int
    has_more: bool


class IngestFinalizeResponse(BaseModel):
    source: LibrarySourceSummary
    task: "TaskSummary | None" = None


class ResplitSourceRequest(BaseModel):
    tag_ids: list[str] | None = None
    user_guidance: str | None = None


class SplitPreviewRequest(BaseModel):
    filename: str = Field(min_length=1)
    text: str | None = None
    payload_base64: str | None = None
    media_type: str | None = None
    user_guidance: str | None = None


class SplitPreviewResponse(BaseModel):
    filename: str
    media_type: str
    source_kind: SourceKind
    byte_size: int
    ingest_strategy: str
    extracted_character_count: int
    split: SemanticSplitResult
    previewed_at: datetime


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    selected_source_ids: list[str] = Field(default_factory=list)
    source_kinds: list[SourceKind] = Field(default_factory=list)
    tag_ids: list[str] = Field(default_factory=list)
    tag_match_mode: TagMatchMode = "all"
    max_results: int = Field(default=8, ge=1, le=24)


class ChunkHit(BaseModel):
    chunk_id: str
    source_file_id: str
    source_title: str
    original_filename: str
    score: float
    title: str
    summary: str
    text: str
    tags: list[str] = Field(default_factory=list)
    locator: ChunkLocator
    openai_file_id: str | None = None
    attributes: OpenAIAttributes | None = None


class SearchResponse(BaseModel):
    query: str
    hits: list[ChunkHit] = Field(default_factory=list)


class BranchSearchRequest(SearchRequest):
    descend: int = Field(default=2, ge=0, le=4)
    max_width: int = Field(default=3, ge=1, le=8)


class BranchSearchLevel(BaseModel):
    depth: int
    hits: list[ChunkHit] = Field(default_factory=list)


class BranchSearchResponse(BaseModel):
    query: str
    descend: int
    max_width: int
    levels: list[BranchSearchLevel] = Field(default_factory=list)


class ActionRequestBase(BaseModel):
    prompt: str = Field(min_length=1)
    selected_source_ids: list[str] = Field(default_factory=list)
    tag_ids: list[str] = Field(default_factory=list)
    tag_match_mode: TagMatchMode = "all"
    origin_thread_id: str | None = None


class QaRequest(ActionRequestBase):
    max_results: int = Field(default=8, ge=1, le=16)


class FreeformRequest(ActionRequestBase):
    mode: Literal["grounded", "creative"] = "grounded"
    max_results: int = Field(default=8, ge=1, le=16)


class ImageGenerationRequest(ActionRequestBase):
    size: str = "1024x1024"


class VoiceGenerationRequest(ActionRequestBase):
    voice: str | None = None
    source_text: str | None = None
    response_format: Literal["mp3", "wav", "opus"] = "mp3"


class GeneratedAsset(BaseModel):
    id: str
    kind: AssetKind
    filename: str
    media_type: str
    byte_size: int
    download_url: str | None = None


class ActionResponse(BaseModel):
    task_id: str
    kind: ActionKind
    answer: str | None = None
    hits: list[ChunkHit] = Field(default_factory=list)
    asset: GeneratedAsset | None = None


class StoredAssetSummary(BaseModel):
    id: str
    kind: AssetKind
    filename: str
    media_type: str
    byte_size: int
    created_at: datetime
    download_url: str | None = None


class TaskSummary(BaseModel):
    id: str
    kind: TaskKind
    status: TaskStatus
    title: str
    origin_surface: TaskOriginSurface
    origin_thread_id: str | None = None
    source_file_id: str | None = None
    input_json: StructuredPayload = None
    result_json: StructuredPayload = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TaskDetail(TaskSummary):
    state_json: StructuredPayload = None


class TaskListResponse(BaseModel):
    tasks: list[TaskSummary] = Field(default_factory=list)


IngestFinalizeResponse.model_rebuild()

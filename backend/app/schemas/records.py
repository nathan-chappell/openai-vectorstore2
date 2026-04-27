from __future__ import annotations

from datetime import datetime
from typing import Literal, NotRequired, TypeAlias, TypedDict

from pydantic import BaseModel, Field

StructuredObject: TypeAlias = dict[str, object]
StructuredPayload: TypeAlias = StructuredObject | list[object] | None
OpenAIAttributeValue: TypeAlias = str | float | bool
OpenAIAttributes: TypeAlias = dict[str, OpenAIAttributeValue]


class SourceMetadata(TypedDict, total=False):
    description: str
    summary: str
    suggested_tags: list[str]
    authors: list[str]
    published_at: str
    doi: str
    arxiv_id: str
    normalized_url: str | None
    fetched_url: str
    content_hash: str
    fetched_at: str
    research_import_task_id: str
    research_candidate_id: str
    artifact_kind: str
    report_title: str
    report_format: str


class ResearchProvenance(SourceMetadata, total=False):
    seed_type: str
    topic: str
    filename: str
    media_type: str
    url: str
    candidate_id: str
    http_status: int
    content_type: str
    content_text: str
    parent_candidate_id: str
    parent_candidate_url: str
    parent_source_file_id: str
    target_folder_id: str
    discovery_depth: int


def _empty_source_metadata() -> SourceMetadata:
    return {}


def _empty_research_provenance() -> ResearchProvenance:
    return {}


class OpenAIUsageDetails(TypedDict, total=False):
    cached_tokens: int
    reasoning_tokens: int


class OpenAIUsagePayload(TypedDict, total=False):
    requests: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_tokens_details: OpenAIUsageDetails
    output_tokens_details: OpenAIUsageDetails


class TaskPayload(TypedDict, total=False):
    stage: str
    source_id: str
    tag_ids: list[str]
    user_guidance: NotRequired[str | None]

SourceKind: TypeAlias = Literal["pdf", "text", "conversation", "image", "audio", "video", "other"]
SourceStatus: TypeAlias = Literal["processing", "ready", "failed"]
FilesystemEntryKind: TypeAlias = Literal["folder", "file"]
TaskKind: TypeAlias = Literal[
    "ingest", "resplit", "reindex", "research_import", "qa", "freeform", "branch_search", "image_gen", "voice_gen"
]
ActionKind: TypeAlias = Literal["qa", "freeform", "image_gen", "voice_gen"]
TaskStatus: TypeAlias = Literal["queued", "running", "completed", "failed", "cancelled"]
TaskOriginSurface: TypeAlias = Literal["web", "mcp", "chatkit", "system"]
TagMatchMode: TypeAlias = Literal["all", "any"]
LocatorType: TypeAlias = Literal["page_range", "line_range", "time_range", "generated"]
AssetKind: TypeAlias = Literal["image", "voice", "source_copy"]
ResearchSeedKind: TypeAlias = Literal[
    "topic", "paper", "text", "url", "pdf_url", "arxiv_url", "uploaded_file", "linkedin_export"
]
ResearchCandidateSourceType: TypeAlias = Literal[
    "text", "url", "html", "pdf", "arxiv", "linkedin_export", "uploaded_file"
]
ResearchCandidateStatus: TypeAlias = Literal[
    "pending", "approved", "rejected", "ingesting", "ingested", "failed", "duplicate"
]
PaymentAttemptStatus: TypeAlias = Literal[
    "pending_payment",
    "temporarily_approved",
    "confirmed_paid",
    "rejected_payment",
    "expired_temporary_access",
    "manual_review_required",
]


class AuthUser(BaseModel):
    clerk_user_id: str
    display_name: str
    primary_email: str | None = None
    active: bool
    role: str | None = None
    current_credit_usd: float = 0.0
    credit_floor_usd: float = 0.0


class CreditBalanceSummary(BaseModel):
    clerk_user_id: str
    current_credit_usd: float
    credit_floor_usd: float
    billable: bool
    billing_enabled: bool


class CreditGrantSummary(BaseModel):
    id: str
    clerk_user_id: str
    admin_clerk_user_id: str | None = None
    credit_amount_usd: float
    source: str
    note: str | None = None
    payment_provider: str | None = None
    payment_reference: str | None = None
    created_at: datetime


class CostEventSummary(BaseModel):
    id: str
    event_key: str | None = None
    clerk_user_id: str
    operation_kind: str
    origin_surface: str
    thread_id: str | None = None
    task_id: str | None = None
    source_file_id: str | None = None
    openai_response_id: str | None = None
    openai_conversation_id: str | None = None
    model: str | None = None
    pricing_version: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    openai_cost_usd: float
    platform_multiplier: float
    platform_cost_usd: float
    note: str | None = None
    created_at: datetime


class BillingStatusResponse(CreditBalanceSummary):
    active: bool
    role: str | None = None
    primary_email: str | None = None


class PaymentIntegrationResponse(BaseModel):
    provider: str
    checkout_enabled: bool
    receipt_upload_enabled: bool = False
    reason: str | None = None
    paypal_recipient_email: str | None = None
    paypal_payment_url: str | None = None
    min_payment_usd: float
    max_payment_usd: float


class AdminGrantCreditRequest(BaseModel):
    clerk_user_id: str = Field(min_length=1, max_length=128)
    credit_amount_usd: float = Field(gt=0)
    note: str | None = Field(default=None, max_length=500)


class AdminGrantCreditResponse(BaseModel):
    balance: CreditBalanceSummary
    grant: CreditGrantSummary


class AdminSetUserActiveRequest(BaseModel):
    clerk_user_id: str = Field(min_length=1, max_length=128)
    active: bool


class AdminSetUserActiveResponse(BaseModel):
    clerk_user_id: str
    active: bool
    current_credit_usd: float
    credit_floor_usd: float


class AdminUserSummary(BaseModel):
    clerk_user_id: str
    primary_email: str | None = None
    display_name: str | None = None
    image_url: str | None = None
    active: bool
    role: str | None = None
    current_credit_usd: float
    credit_floor_usd: float
    created_at_ms: int | None = None
    last_sign_in_at_ms: int | None = None


class AdminUserListResponse(BaseModel):
    items: list[AdminUserSummary] = Field(default_factory=list)
    limit: int
    offset: int
    has_more: bool
    query: str | None = None


class PayPalPaymentAttemptCreateRequest(BaseModel):
    expected_amount_usd: float = Field(gt=0)


class PaymentAttemptSummary(BaseModel):
    id: str
    clerk_user_id: str
    provider: str
    expected_amount_usd: float
    expected_currency: str
    reference_code: str
    status: PaymentAttemptStatus
    temporary_access_expires_at: datetime | None = None
    provider_reference: str | None = None
    credit_grant_id: str | None = None
    receipt_filename: str | None = None
    review_reason: str | None = None
    decision_note: str | None = None
    created_at: datetime
    updated_at: datetime


class PaymentAttemptListResponse(BaseModel):
    attempts: list[PaymentAttemptSummary] = Field(default_factory=list)


class AdminPaymentAttemptDecisionRequest(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=32)
    status: Literal["confirmed_paid", "rejected_payment", "manual_review_required"]
    decision_note: str = Field(min_length=1, max_length=500)
    credit_amount_usd: float | None = Field(default=None, gt=0)
    provider_reference: str | None = Field(default=None, max_length=255)


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
    filesystem_entry_id: str | None = None
    virtual_name: str | None = None
    virtual_path: str | None = None
    display_title: str
    original_filename: str
    media_type: str
    source_kind: SourceKind
    status: SourceStatus
    byte_size: int
    chunk_count: int
    description: str | None = None
    summary: str | None = None
    suggested_tags: list[str] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    tags: list[TagSummary] = Field(default_factory=list)
    openai_original_file_id: str | None = None
    openai_original_file_purpose: str | None = None
    openai_vector_file_id: str | None = None
    vector_attributes: OpenAIAttributes | None = None


class FilesystemEntrySummary(BaseModel):
    id: str
    kind: FilesystemEntryKind
    name: str
    path: str
    parent_id: str | None = None
    source_id: str | None = None
    source_kind: SourceKind | None = None
    media_type: str | None = None
    status: SourceStatus | None = None
    byte_size: int | None = None
    chunk_count: int | None = None
    description: str | None = None
    summary: str | None = None
    suggested_tags: list[str] = Field(default_factory=list)
    tags: list[TagSummary] = Field(default_factory=list)
    openai_original_file_id: str | None = None
    openai_vector_file_id: str | None = None
    created_at: datetime
    updated_at: datetime


class FilesystemBreadcrumb(BaseModel):
    id: str
    name: str
    path: str


class FilesystemListResponse(BaseModel):
    current: FilesystemEntrySummary
    breadcrumbs: list[FilesystemBreadcrumb] = Field(default_factory=list)
    entries: list[FilesystemEntrySummary] = Field(default_factory=list)


class FilesystemSearchResponse(BaseModel):
    query: str | None = None
    entries: list[FilesystemEntrySummary] = Field(default_factory=list)
    total_count: int
    page: int
    page_size: int
    has_more: bool


class FilesystemCreateFolderRequest(BaseModel):
    parent_id: str | None = None
    name: str = Field(min_length=1, max_length=255)


class FilesystemUpdateEntryRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: str | None = None


class FilesystemDeleteRequest(BaseModel):
    entry_ids: list[str] = Field(min_length=1, max_length=100)
    confirm: bool = False


class FilesystemDeleteResponse(BaseModel):
    deleted_entry_ids: list[str] = Field(default_factory=list)
    deleted_source_ids: list[str] = Field(default_factory=list)


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
    metadata: SourceMetadata = Field(default_factory=_empty_source_metadata)
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


class SourceTagsUpdateRequest(BaseModel):
    tag_ids: list[str] = Field(default_factory=list)


class TagCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str | None = Field(default=None, max_length=32)


class TagUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, max_length=32)


class TagMutationResponse(BaseModel):
    tag: TagSummary | None = None
    tasks: list["TaskSummary"] = Field(default_factory=list)


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


class ResearchDiscoveryCandidateDraft(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=512)
    source_type: ResearchCandidateSourceType = "url"
    description: str | None = Field(default=None, max_length=1024)
    summary: str | None = Field(default=None, max_length=4096)
    suggested_tags: list[str] = Field(default_factory=list, max_length=8)
    authors: list[str] = Field(default_factory=list, max_length=24)
    published_at: str | None = Field(default=None, max_length=64)
    doi: str | None = Field(default=None, max_length=128)
    arxiv_id: str | None = Field(default=None, max_length=64)
    rationale: str | None = None
    score: float | None = Field(default=None, ge=0, le=1)


class ResearchDiscoveryResult(BaseModel):
    candidates: list[ResearchDiscoveryCandidateDraft] = Field(default_factory=list)


class ResearchImportCreateRequest(BaseModel):
    seed_type: ResearchSeedKind = "topic"
    text: str | None = None
    url: str | None = Field(default=None, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    filename: str | None = Field(default=None, max_length=255)
    payload_base64: str | None = None
    media_type: str | None = Field(default=None, max_length=128)
    tag_ids: list[str] = Field(default_factory=list, max_length=8)
    folder_id: str | None = None
    folder_name: str | None = Field(default=None, max_length=255)
    ingest_seed: bool = True
    discover_references: bool = True
    max_depth: int = Field(default=2, ge=0, le=4)
    max_candidates_per_source: int = Field(default=8, ge=0, le=20)
    max_pending_candidates: int = Field(default=40, ge=0, le=200)


class ResearchImportCandidateSummary(BaseModel):
    id: str
    task_id: str
    status: ResearchCandidateStatus
    source_type: ResearchCandidateSourceType
    url: str | None = None
    normalized_url: str | None = None
    title: str
    description: str | None = None
    summary: str | None = None
    suggested_tags: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    published_at: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    rationale: str | None = None
    score: float | None = None
    depth: int
    parent_candidate_id: str | None = None
    parent_source_file_id: str | None = None
    linked_source_file_id: str | None = None
    provenance: ResearchProvenance = Field(default_factory=_empty_research_provenance)
    content_hash: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ResearchImportResponse(BaseModel):
    task: "TaskSummary"
    seed_source: LibrarySourceSummary | None = None
    candidates: list[ResearchImportCandidateSummary] = Field(default_factory=list)
    duplicate_count: int = 0
    target_folder_id: str | None = None


class ResearchLibraryBuildRequest(BaseModel):
    seed_type: ResearchSeedKind = "topic"
    query: str = Field(min_length=1, max_length=4096)
    title: str | None = Field(default=None, max_length=512)
    folder_id: str | None = None
    folder_name: str | None = Field(default=None, max_length=255)
    tag_ids: list[str] = Field(default_factory=list, max_length=8)
    auto_ingest: bool = True
    discover_references: bool = True
    max_depth: int = Field(default=2, ge=0, le=4)
    max_sources: int = Field(default=12, ge=1, le=50)
    max_candidates_per_source: int = Field(default=8, ge=0, le=20)
    max_pending_candidates: int = Field(default=50, ge=0, le=200)


class ResearchLibraryBuildResponse(BaseModel):
    task: "TaskSummary"
    target_folder_id: str | None = None
    seed_source: LibrarySourceSummary | None = None
    candidates: list[ResearchImportCandidateSummary] = Field(default_factory=list)
    ingested: list[IngestFinalizeResponse] = Field(default_factory=list)
    duplicate_count: int = 0


class ResearchCandidateListResponse(BaseModel):
    candidates: list[ResearchImportCandidateSummary] = Field(default_factory=list)
    total_count: int
    page: int
    page_size: int
    has_more: bool


class ResearchCandidateStatusUpdateRequest(BaseModel):
    candidate_ids: list[str] = Field(min_length=1, max_length=100)
    status: Literal["approved", "rejected", "pending"]


class ResearchCandidateStatusUpdateResponse(BaseModel):
    candidates: list[ResearchImportCandidateSummary] = Field(default_factory=list)


class ResearchCandidateIngestRequest(BaseModel):
    candidate_ids: list[str] | None = Field(default=None, max_length=100)
    task_id: str | None = None
    tag_ids: list[str] | None = Field(default=None, max_length=8)
    folder_id: str | None = None


class ResearchCandidateIngestResponse(BaseModel):
    ingested: list[IngestFinalizeResponse] = Field(default_factory=list)
    candidates: list[ResearchImportCandidateSummary] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    selected_source_ids: list[str] = Field(default_factory=list)
    source_kinds: list[SourceKind] = Field(default_factory=list)
    tag_ids: list[str] = Field(default_factory=list)
    tag_match_mode: TagMatchMode = "all"
    virtual_paths: list[str] = Field(default_factory=list)
    created_after: datetime | None = None
    created_before: datetime | None = None
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
TagMutationResponse.model_rebuild()
ResearchImportResponse.model_rebuild()

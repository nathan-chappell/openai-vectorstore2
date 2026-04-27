export type SourceKind = "pdf" | "text" | "conversation" | "image" | "audio" | "video" | "other";
export type SourceStatus = "processing" | "ready" | "failed";
export type TagMatchMode = "all" | "any";
export type StructuredPayload = Record<string, unknown> | unknown[] | null;
export type OpenAIAttributes = Record<string, string | number | boolean>;

export type SearchFilterPayload = {
  selectedSourceIds?: string[];
  sourceKinds?: SourceKind[];
  tagIds?: string[];
  tagMatchMode?: TagMatchMode;
  virtualPaths?: string[];
  createdAfter?: string | null;
  createdBefore?: string | null;
};

export type PaginationParams = {
  page?: number;
  pageSize?: number;
};

export type TaggedSearchParams = {
  query?: string | null;
  tagIds?: string[];
  tagMatchMode?: TagMatchMode;
};

export type SourceListParams = TaggedSearchParams & PaginationParams;

export type FilesystemListParams = {
  folderId?: string | null;
};

export type FilesystemSearchParams = TaggedSearchParams & PaginationParams;

export type ResearchCandidateListParams = PaginationParams & {
  taskId?: string | null;
  status?: ResearchCandidateStatus | null;
};

export type SearchChunksRequest = SearchFilterPayload & {
  query: string;
  maxResults?: number;
};

export type BranchSearchRequest = SearchFilterPayload & {
  query: string;
  descend?: number;
  maxWidth?: number;
};

export type QaActionRequest = {
  prompt: string;
  selectedSourceIds?: string[];
  tagIds?: string[];
  tagMatchMode?: TagMatchMode;
};

export type FreeformActionRequest = {
  prompt: string;
  mode: "grounded" | "creative";
  selectedSourceIds?: string[];
};

export type ImageActionRequest = {
  prompt: string;
  selectedSourceIds?: string[];
};

export type VoiceActionRequest = {
  prompt: string;
  sourceText?: string;
  selectedSourceIds?: string[];
};

export type AuthUser = {
  clerk_user_id: string;
  display_name: string;
  primary_email: string | null;
  active: boolean;
  role: string | null;
  current_credit_usd: number;
  credit_floor_usd: number;
};

export type CreditBalanceSummary = {
  clerk_user_id: string;
  current_credit_usd: number;
  credit_floor_usd: number;
  billable: boolean;
  billing_enabled: boolean;
};

export type BillingStatusResponse = {
  clerk_user_id: string;
  current_credit_usd: number;
  credit_floor_usd: number;
  billable: boolean;
  billing_enabled: boolean;
  active: boolean;
  role: string | null;
  primary_email: string | null;
};

export type PaymentIntegrationResponse = {
  provider: string;
  checkout_enabled: boolean;
  reason: string | null;
};

export type CreditGrantSummary = {
  id: string;
  clerk_user_id: string;
  admin_clerk_user_id: string | null;
  credit_amount_usd: number;
  source: string;
  note: string | null;
  payment_provider: string | null;
  payment_reference: string | null;
  created_at: string;
};

export type AdminGrantCreditRequest = {
  clerk_user_id: string;
  credit_amount_usd: number;
  note?: string | null;
};

export type AdminGrantCreditResponse = {
  balance: CreditBalanceSummary;
  grant: CreditGrantSummary;
};

export type AdminSetUserActiveRequest = {
  clerk_user_id: string;
  active: boolean;
};

export type AdminSetUserActiveResponse = {
  clerk_user_id: string;
  active: boolean;
  current_credit_usd: number;
  credit_floor_usd: number;
};

export type AdminUserSummary = {
  clerk_user_id: string;
  primary_email: string | null;
  display_name: string | null;
  image_url: string | null;
  active: boolean;
  role: string | null;
  current_credit_usd: number;
  credit_floor_usd: number;
  created_at_ms: number | null;
  last_sign_in_at_ms: number | null;
};

export type AdminUserListResponse = {
  items: AdminUserSummary[];
  limit: number;
  offset: number;
  has_more: boolean;
  query: string | null;
};

export type TagSummary = {
  id: string;
  name: string;
  slug: string;
  color: string | null;
  source: "auto" | "manual";
  source_count: number;
};

export type ChunkLocator = {
  type: "page_range" | "line_range" | "time_range" | "generated";
  start_page: number | null;
  end_page: number | null;
  start_line: number | null;
  end_line: number | null;
  start_seconds: number | null;
  end_seconds: number | null;
};

export type SourceSummary = {
  id: string;
  filesystem_entry_id: string | null;
  virtual_name: string | null;
  virtual_path: string | null;
  display_title: string;
  original_filename: string;
  media_type: string;
  source_kind: SourceKind;
  status: SourceStatus;
  byte_size: number;
  chunk_count: number;
  description: string | null;
  summary: string | null;
  suggested_tags: string[];
  error_message: string | null;
  created_at: string;
  updated_at: string;
  tags: TagSummary[];
  openai_original_file_id: string | null;
  openai_original_file_purpose: string | null;
  openai_vector_file_id: string | null;
  vector_attributes: OpenAIAttributes | null;
};

export type FilesystemEntryKind = "folder" | "file";

export type FilesystemEntrySummary = {
  id: string;
  kind: FilesystemEntryKind;
  name: string;
  path: string;
  parent_id: string | null;
  source_id: string | null;
  source_kind: SourceKind | null;
  media_type: string | null;
  status: SourceStatus | null;
  byte_size: number | null;
  chunk_count: number | null;
  description: string | null;
  summary: string | null;
  suggested_tags: string[];
  tags: TagSummary[];
  openai_original_file_id: string | null;
  openai_vector_file_id: string | null;
  created_at: string;
  updated_at: string;
};

export type FilesystemBreadcrumb = {
  id: string;
  name: string;
  path: string;
};

export type FilesystemListResponse = {
  current: FilesystemEntrySummary;
  breadcrumbs: FilesystemBreadcrumb[];
  entries: FilesystemEntrySummary[];
};

export type FilesystemSearchResponse = {
  query: string | null;
  entries: FilesystemEntrySummary[];
  total_count: number;
  page: number;
  page_size: number;
  has_more: boolean;
};

export type FilesystemCreateFolderRequest = {
  parent_id?: string | null;
  name: string;
};

export type FilesystemUpdateEntryRequest = {
  name?: string | null;
  parent_id?: string | null;
};

export type FilesystemDeleteRequest = {
  entry_ids: string[];
  confirm: boolean;
};

export type FilesystemDeleteResponse = {
  deleted_entry_ids: string[];
  deleted_source_ids: string[];
};

export type ChunkSummary = {
  id: string;
  source_file_id: string;
  sequence: number;
  title: string;
  summary: string;
  text: string;
  keywords: string[];
  locator: ChunkLocator;
  strategy_label: string;
  openai_file_id: string | null;
  created_at: string;
  updated_at: string;
};

export type SemanticChunkDraft = {
  sequence: number;
  title: string;
  summary: string;
  text: string;
  keywords: string[];
  locator: ChunkLocator;
  strategy_label: string;
};

export type SemanticSplitResult = {
  strategy_label: string;
  tags: string[];
  chunks: SemanticChunkDraft[];
};

export type SplitPreviewResponse = {
  filename: string;
  media_type: string;
  source_kind: SourceKind;
  byte_size: number;
  ingest_strategy: string;
  extracted_character_count: number;
  split: SemanticSplitResult;
  previewed_at: string;
};

export type ResplitSourceRequest = {
  tag_ids?: string[] | null;
  user_guidance?: string | null;
};

export type SourceTagsUpdateRequest = {
  tag_ids: string[];
};

export type TagCreateRequest = {
  name: string;
  color?: string | null;
};

export type TagUpdateRequest = {
  name?: string | null;
  color?: string | null;
};

export type TagMutationResponse = {
  tag: TagSummary | null;
  tasks: TaskSummary[];
};

export type SourceDetail = SourceSummary & {
  storage_provider: string;
  storage_key: string;
  ingest_strategy: string | null;
  metadata: Record<string, unknown>;
  chunks: ChunkSummary[];
};

export type SourceListResponse = {
  sources: SourceSummary[];
  total_count: number;
  page: number;
  page_size: number;
  has_more: boolean;
};

export type ChunkHit = {
  chunk_id: string;
  source_file_id: string;
  source_title: string;
  original_filename: string;
  score: number;
  title: string;
  summary: string;
  text: string;
  tags: string[];
  locator: ChunkLocator;
  openai_file_id: string | null;
  attributes: OpenAIAttributes | null;
};

export type SearchResponse = {
  query: string;
  hits: ChunkHit[];
};

export type BranchSearchResponse = {
  query: string;
  descend: number;
  max_width: number;
  levels: Array<{ depth: number; hits: ChunkHit[] }>;
};

export type GeneratedAsset = {
  id: string;
  kind: "image" | "voice" | "source_copy";
  filename: string;
  media_type: string;
  byte_size: number;
  download_url: string | null;
};

export type ActionResponse = {
  task_id: string;
  kind: "qa" | "freeform" | "image_gen" | "voice_gen";
  answer: string | null;
  hits: ChunkHit[];
  asset: GeneratedAsset | null;
};

export type IngestFinalizeResponse = {
  source: SourceSummary;
  task: TaskSummary | null;
};

export type ResearchSeedKind = "topic" | "paper" | "text" | "url" | "pdf_url" | "arxiv_url" | "uploaded_file" | "linkedin_export";
export type ResearchCandidateSourceType = "text" | "url" | "html" | "pdf" | "arxiv" | "linkedin_export" | "uploaded_file";
export type ResearchCandidateStatus = "pending" | "approved" | "rejected" | "ingesting" | "ingested" | "failed" | "duplicate";

export type ResearchImportCreateRequest = {
  seed_type: ResearchSeedKind;
  text?: string | null;
  url?: string | null;
  title?: string | null;
  filename?: string | null;
  payload_base64?: string | null;
  media_type?: string | null;
  tag_ids?: string[];
  folder_id?: string | null;
  folder_name?: string | null;
  ingest_seed?: boolean;
  discover_references?: boolean;
  max_depth?: number;
  max_candidates_per_source?: number;
  max_pending_candidates?: number;
};

export type ResearchImportCandidateSummary = {
  id: string;
  task_id: string;
  status: ResearchCandidateStatus;
  source_type: ResearchCandidateSourceType;
  url: string | null;
  normalized_url: string | null;
  title: string;
  description: string | null;
  summary: string | null;
  suggested_tags: string[];
  authors: string[];
  published_at: string | null;
  doi: string | null;
  arxiv_id: string | null;
  rationale: string | null;
  score: number | null;
  depth: number;
  parent_candidate_id: string | null;
  parent_source_file_id: string | null;
  linked_source_file_id: string | null;
  provenance: Record<string, unknown>;
  content_hash: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type ResearchImportResponse = {
  task: TaskSummary;
  seed_source: SourceSummary | null;
  candidates: ResearchImportCandidateSummary[];
  duplicate_count: number;
  target_folder_id: string | null;
};

export type ResearchLibraryBuildRequest = {
  seed_type: ResearchSeedKind;
  query: string;
  title?: string | null;
  folder_id?: string | null;
  folder_name?: string | null;
  tag_ids?: string[];
  auto_ingest?: boolean;
  discover_references?: boolean;
  max_depth?: number;
  max_sources?: number;
  max_candidates_per_source?: number;
  max_pending_candidates?: number;
};

export type ResearchLibraryBuildResponse = {
  task: TaskSummary;
  target_folder_id: string | null;
  seed_source: SourceSummary | null;
  candidates: ResearchImportCandidateSummary[];
  ingested: IngestFinalizeResponse[];
  duplicate_count: number;
};

export type ResearchCandidateListResponse = {
  candidates: ResearchImportCandidateSummary[];
  total_count: number;
  page: number;
  page_size: number;
  has_more: boolean;
};

export type ResearchCandidateStatusUpdateRequest = {
  candidate_ids: string[];
  status: "approved" | "rejected" | "pending";
};

export type ResearchCandidateStatusUpdateResponse = {
  candidates: ResearchImportCandidateSummary[];
};

export type ResearchCandidateIngestRequest = {
  candidate_ids?: string[] | null;
  task_id?: string | null;
  tag_ids?: string[] | null;
  folder_id?: string | null;
};

export type ResearchCandidateIngestResponse = {
  ingested: IngestFinalizeResponse[];
  candidates: ResearchImportCandidateSummary[];
};

export type TaskSummary = {
  id: string;
  kind: "ingest" | "resplit" | "reindex" | "research_import" | "qa" | "freeform" | "branch_search" | "image_gen" | "voice_gen";
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  title: string;
  origin_surface: "web" | "mcp" | "chatkit" | "system";
  origin_thread_id: string | null;
  source_file_id: string | null;
  input_json: StructuredPayload;
  result_json: StructuredPayload;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type TaskDetail = TaskSummary & {
  state_json: StructuredPayload;
};

export type TaskListResponse = {
  tasks: TaskSummary[];
};

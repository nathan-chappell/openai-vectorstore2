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
  createdAfter?: string | null;
  createdBefore?: string | null;
};

export type AuthUser = {
  clerk_user_id: string;
  display_name: string;
  primary_email: string | null;
  active: boolean;
  role: string | null;
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
  display_title: string;
  original_filename: string;
  media_type: string;
  source_kind: SourceKind;
  status: SourceStatus;
  byte_size: number;
  chunk_count: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  tags: TagSummary[];
  openai_original_file_id: string | null;
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

export type SourceDetail = SourceSummary & {
  storage_provider: string;
  storage_key: string;
  ingest_strategy: string | null;
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

export type TaskSummary = {
  id: string;
  kind: "ingest" | "resplit" | "reindex" | "qa" | "freeform" | "branch_search" | "image_gen" | "voice_gen";
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

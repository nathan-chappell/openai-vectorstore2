import type {
  ChatKitMetadata,
} from "./appTypes";
import type {
  ActionResponse,
  AdminFreeCreditDecisionRequest,
  AdminGrantCreditRequest,
  AdminGrantCreditResponse,
  AdminPaymentAttemptDecisionRequest,
  AdminSetUserActiveRequest,
  AdminSetUserActiveResponse,
  AdminUserListResponse,
  AuthUser,
  BranchSearchRequest,
  BranchSearchResponse,
  FilesystemListParams,
  FilesystemCreateFolderRequest,
  FilesystemDeleteRequest,
  FilesystemDeleteResponse,
  FilesystemEntrySummary,
  FilesystemListResponse,
  FilesystemSearchParams,
  FilesystemSearchResponse,
  FreeformActionRequest,
  ImageActionRequest,
  FilesystemUpdateEntryRequest,
  FreeCreditRequestCreate,
  FreeCreditRequestListResponse,
  FreeCreditRequestStatus,
  FreeCreditRequestSummary,
  IngestFinalizeResponse,
  PaginationParams,
  PaymentAttemptListResponse,
  PaymentAttemptStatus,
  PaymentAttemptSummary,
  PaymentIntegrationResponse,
  PayPalPaymentAttemptCreateRequest,
  QaActionRequest,
  ResearchCandidateIngestRequest,
  ResearchCandidateIngestResponse,
  ResearchCandidateListResponse,
  ResearchCandidateListParams,
  ResearchCandidateStatusUpdateRequest,
  ResearchCandidateStatusUpdateResponse,
  ResearchImportCreateRequest,
  ResearchImportResponse,
  ResearchLibraryBuildRequest,
  ResearchLibraryBuildResponse,
  ResplitSourceRequest,
  ReportMarkdownSaveRequest,
  ReportMarkdownSaveResponse,
  SearchChunksRequest,
  SearchFilterPayload,
  SearchResponse,
  SplitPreviewResponse,
  SourceDetail,
  SourceListParams,
  SourceListResponse,
  SourceTagsUpdateRequest,
  TagCreateRequest,
  TaggedSearchParams,
  TagMutationResponse,
  TagSummary,
  TagUpdateRequest,
  TaskListResponse,
  VoiceActionRequest,
} from "./types";

const API_BASE_URL = normalizeBase(import.meta.env.VITE_API_BASE_URL ?? "/api");
const CHATKIT_DOMAIN_KEY = import.meta.env.VITE_CHATKIT_DOMAIN_KEY ?? "domain_pk_local_vectorstore2";

let bearerTokenGetter: (() => Promise<string | null>) | null = null;
let chatKitMetadataGetter: (() => ChatKitMetadata | null) | null = null;

type SearchFilterRequestBody = {
  selected_source_ids: string[];
  source_kinds: NonNullable<SearchFilterPayload["sourceKinds"]>;
  tag_ids: string[];
  tag_match_mode: "all" | "any";
  virtual_paths: string[];
  created_after: string | null;
  created_before: string | null;
};

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function setBearerTokenGetter(getter: (() => Promise<string | null>) | null): void {
  bearerTokenGetter = getter;
}

export function setChatKitMetadataGetter(getter: (() => ChatKitMetadata | null) | null): void {
  chatKitMetadataGetter = getter;
}

export function getChatKitConfig(): { url: string; domainKey: string; attachmentUploadUrl: string } {
  return {
    url: `${API_BASE_URL}/chatkit`,
    domainKey: CHATKIT_DOMAIN_KEY,
    attachmentUploadUrl: `${API_BASE_URL}/chatkit/attachments`,
  };
}

export async function authenticatedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers ?? {});
  const token = (await bearerTokenGetter?.()) ?? "local-dev";
  headers.set("Authorization", `Bearer ${token}`);
  const prepared = prepareChatKitRequest(input, { ...init, headers });
  return fetch(prepared.input, prepared.init);
}

export async function getAuthenticatedUser(): Promise<AuthUser> {
  return apiRequest<AuthUser>("/auth/me");
}

export async function getPaymentIntegrationStatus(): Promise<PaymentIntegrationResponse> {
  return apiRequest<PaymentIntegrationResponse>("/billing/payment-status");
}

export async function listPayPalPaymentAttempts(): Promise<PaymentAttemptListResponse> {
  return apiRequest<PaymentAttemptListResponse>("/billing/paypal/attempts");
}

export async function createPayPalPaymentAttempt(payload: PayPalPaymentAttemptCreateRequest): Promise<PaymentAttemptSummary> {
  return apiRequest<PaymentAttemptSummary>("/billing/paypal/attempts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function uploadPayPalReceipt(attemptId: string, file: File): Promise<PaymentAttemptSummary> {
  const formData = new FormData();
  formData.set("file", file);
  return apiRequest<PaymentAttemptSummary>(`/billing/paypal/attempts/${encodeURIComponent(attemptId)}/receipt`, {
    method: "POST",
    body: formData,
  });
}

export async function listFreeCreditRequests(): Promise<FreeCreditRequestListResponse> {
  return apiRequest<FreeCreditRequestListResponse>("/billing/free-credit-requests");
}

export async function createFreeCreditRequest(payload: FreeCreditRequestCreate): Promise<FreeCreditRequestSummary> {
  return apiRequest<FreeCreditRequestSummary>("/billing/free-credit-requests", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listAdminUsers(params: {
  query?: string;
  limit: number;
  offset: number;
}): Promise<AdminUserListResponse> {
  const searchParams = new URLSearchParams();
  searchParams.set("limit", String(params.limit));
  searchParams.set("offset", String(params.offset));
  if (params.query?.trim()) {
    searchParams.set("query", params.query.trim());
  }
  return apiRequest<AdminUserListResponse>(`/admin/users?${searchParams.toString()}`);
}

export async function setAdminUserActive(payload: AdminSetUserActiveRequest): Promise<AdminSetUserActiveResponse> {
  return apiRequest<AdminSetUserActiveResponse>("/admin/users/set-active", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function grantAdminCredit(payload: AdminGrantCreditRequest): Promise<AdminGrantCreditResponse> {
  return apiRequest<AdminGrantCreditResponse>("/admin/credits/grant", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listAdminPaymentAttempts(status: PaymentAttemptStatus): Promise<PaymentAttemptListResponse> {
  const searchParams = new URLSearchParams();
  searchParams.set("status", status);
  return apiRequest<PaymentAttemptListResponse>(`/admin/payments?${searchParams.toString()}`);
}

export async function decideAdminPaymentAttempt(payload: AdminPaymentAttemptDecisionRequest): Promise<PaymentAttemptSummary> {
  return apiRequest<PaymentAttemptSummary>("/admin/payments/decide", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listAdminFreeCreditRequests(status: FreeCreditRequestStatus): Promise<FreeCreditRequestListResponse> {
  const searchParams = new URLSearchParams();
  searchParams.set("status", status);
  return apiRequest<FreeCreditRequestListResponse>(`/admin/free-credit-requests?${searchParams.toString()}`);
}

export async function decideAdminFreeCreditRequest(payload: AdminFreeCreditDecisionRequest): Promise<FreeCreditRequestSummary> {
  return apiRequest<FreeCreditRequestSummary>("/admin/free-credit-requests/decide", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listSources(params: SourceListParams): Promise<SourceListResponse> {
  const searchParams = new URLSearchParams();
  appendTaggedSearchParams(searchParams, params);
  appendPaginationParams(searchParams, params);
  const suffix = searchParams.toString();
  return apiRequest<SourceListResponse>(suffix ? `/sources?${suffix}` : "/sources");
}

export async function getSource(sourceId: string): Promise<SourceDetail> {
  return apiRequest<SourceDetail>(`/sources/${encodeURIComponent(sourceId)}`);
}

export async function listFilesystem(params: FilesystemListParams = {}): Promise<FilesystemListResponse> {
  const searchParams = new URLSearchParams();
  if (params.folderId) {
    searchParams.set("folder_id", params.folderId);
  }
  const suffix = searchParams.toString();
  return apiRequest<FilesystemListResponse>(suffix ? `/filesystem?${suffix}` : "/filesystem");
}

export async function searchFilesystem(params: FilesystemSearchParams): Promise<FilesystemSearchResponse> {
  const searchParams = new URLSearchParams();
  appendTaggedSearchParams(searchParams, params);
  appendPaginationParams(searchParams, params);
  const suffix = searchParams.toString();
  return apiRequest<FilesystemSearchResponse>(suffix ? `/filesystem/search?${suffix}` : "/filesystem/search");
}

export async function createFolder(payload: FilesystemCreateFolderRequest): Promise<FilesystemEntrySummary> {
  return apiRequest<FilesystemEntrySummary>("/filesystem/folders", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateFilesystemEntry(
  entryId: string,
  payload: FilesystemUpdateEntryRequest,
): Promise<FilesystemEntrySummary> {
  return apiRequest<FilesystemEntrySummary>(`/filesystem/entries/${encodeURIComponent(entryId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteFilesystemEntries(payload: FilesystemDeleteRequest): Promise<FilesystemDeleteResponse> {
  return apiRequest<FilesystemDeleteResponse>("/filesystem/delete", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function readSourceContentBlob(sourceId: string): Promise<{ blob: Blob; mediaType: string | null }> {
  const response = await authenticatedFetch(`${API_BASE_URL}/sources/${encodeURIComponent(sourceId)}/content`);
  if (!response.ok) {
    throw await buildApiError(response);
  }
  return { blob: await response.blob(), mediaType: response.headers.get("Content-Type") };
}

export async function uploadSource(
  file: File,
  userGuidance: string,
  tagIds: string[],
  folderId?: string | null,
  virtualName?: string | null,
): Promise<IngestFinalizeResponse> {
  const formData = new FormData();
  formData.set("file", file, file.name);
  if (userGuidance.trim()) {
    formData.set("user_guidance", userGuidance.trim());
  }
  for (const tagId of tagIds) {
    formData.append("tag_ids", tagId);
  }
  if (folderId) {
    formData.set("folder_id", folderId);
  }
  if (virtualName?.trim()) {
    formData.set("virtual_name", virtualName.trim());
  }
  return apiRequest<IngestFinalizeResponse>("/sources", {
    method: "POST",
    body: formData,
  });
}

export async function previewSemanticSplit(file: File, userGuidance: string): Promise<SplitPreviewResponse> {
  const formData = new FormData();
  formData.set("file", file, file.name);
  if (userGuidance.trim()) {
    formData.set("user_guidance", userGuidance.trim());
  }
  return apiRequest<SplitPreviewResponse>("/sources/split-preview", {
    method: "POST",
    body: formData,
  });
}

export async function resplitSource(sourceId: string, payload: ResplitSourceRequest): Promise<IngestFinalizeResponse> {
  return apiRequest<IngestFinalizeResponse>(`/sources/${encodeURIComponent(sourceId)}/resplit`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateSourceTags(sourceId: string, payload: SourceTagsUpdateRequest): Promise<IngestFinalizeResponse> {
  return apiRequest<IngestFinalizeResponse>(`/sources/${encodeURIComponent(sourceId)}/tags`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteSource(sourceId: string): Promise<void> {
  await apiRequest<{ deleted_source_id: string }>(`/sources/${encodeURIComponent(sourceId)}`, { method: "DELETE" });
}

export async function listTags(): Promise<TagSummary[]> {
  return apiRequest<TagSummary[]>("/tags");
}

export async function createTag(payload: TagCreateRequest): Promise<TagMutationResponse> {
  return apiRequest<TagMutationResponse>("/tags", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateTag(tagId: string, payload: TagUpdateRequest): Promise<TagMutationResponse> {
  return apiRequest<TagMutationResponse>(`/tags/${encodeURIComponent(tagId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteTag(tagId: string): Promise<TagMutationResponse> {
  return apiRequest<TagMutationResponse>(`/tags/${encodeURIComponent(tagId)}`, { method: "DELETE" });
}

export async function createResearchImport(payload: ResearchImportCreateRequest): Promise<ResearchImportResponse> {
  return apiRequest<ResearchImportResponse>("/research/imports", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function buildResearchLibrary(payload: ResearchLibraryBuildRequest): Promise<ResearchLibraryBuildResponse> {
  return apiRequest<ResearchLibraryBuildResponse>("/research/library-builds", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listResearchCandidates(params: ResearchCandidateListParams = {}): Promise<ResearchCandidateListResponse> {
  const searchParams = new URLSearchParams();
  if (params.taskId) {
    searchParams.set("task_id", params.taskId);
  }
  if (params.status) {
    searchParams.set("status", params.status);
  }
  appendPaginationParams(searchParams, params);
  const suffix = searchParams.toString();
  return apiRequest<ResearchCandidateListResponse>(suffix ? `/research/candidates?${suffix}` : "/research/candidates");
}

export async function updateResearchCandidateStatus(
  payload: ResearchCandidateStatusUpdateRequest,
): Promise<ResearchCandidateStatusUpdateResponse> {
  return apiRequest<ResearchCandidateStatusUpdateResponse>("/research/candidates/status", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function ingestResearchCandidates(payload: ResearchCandidateIngestRequest): Promise<ResearchCandidateIngestResponse> {
  return apiRequest<ResearchCandidateIngestResponse>("/research/candidates/ingest", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function searchChunks(payload: SearchChunksRequest): Promise<SearchResponse> {
  return apiRequest<SearchResponse>("/search", {
    method: "POST",
    body: JSON.stringify({
      query: payload.query,
      ...searchFilterRequestBody(payload),
      max_results: payload.maxResults ?? 8,
    }),
  });
}

export async function branchSearch(payload: BranchSearchRequest): Promise<BranchSearchResponse> {
  return apiRequest<BranchSearchResponse>("/search/branch", {
    method: "POST",
    body: JSON.stringify({
      query: payload.query,
      ...searchFilterRequestBody(payload),
      descend: payload.descend ?? 2,
      max_width: payload.maxWidth ?? 3,
    }),
  });
}

export async function qaAction(payload: QaActionRequest): Promise<ActionResponse> {
  return apiRequest<ActionResponse>("/actions/qa", {
    method: "POST",
    body: JSON.stringify({
      prompt: payload.prompt,
      selected_source_ids: payload.selectedSourceIds ?? [],
      tag_ids: payload.tagIds ?? [],
      tag_match_mode: payload.tagMatchMode ?? "all",
    }),
  });
}

export async function freeformAction(payload: FreeformActionRequest): Promise<ActionResponse> {
  return apiRequest<ActionResponse>("/actions/freeform", {
    method: "POST",
    body: JSON.stringify({
      prompt: payload.prompt,
      mode: payload.mode,
      selected_source_ids: payload.selectedSourceIds ?? [],
    }),
  });
}

export async function saveReportMarkdown(payload: ReportMarkdownSaveRequest): Promise<ReportMarkdownSaveResponse> {
  return apiRequest<ReportMarkdownSaveResponse>("/reports/markdown", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function imageAction(payload: ImageActionRequest): Promise<ActionResponse> {
  return apiRequest<ActionResponse>("/actions/image", {
    method: "POST",
    body: JSON.stringify({
      prompt: payload.prompt,
      selected_source_ids: payload.selectedSourceIds ?? [],
    }),
  });
}

export async function voiceAction(payload: VoiceActionRequest): Promise<ActionResponse> {
  return apiRequest<ActionResponse>("/actions/voice", {
    method: "POST",
    body: JSON.stringify({
      prompt: payload.prompt,
      source_text: payload.sourceText,
      selected_source_ids: payload.selectedSourceIds ?? [],
    }),
  });
}

export async function listTasks(): Promise<TaskListResponse> {
  return apiRequest<TaskListResponse>("/tasks");
}

function appendTaggedSearchParams(
  searchParams: URLSearchParams,
  params: TaggedSearchParams,
): void {
  if (params.query?.trim()) {
    searchParams.set("query", params.query.trim());
  }
  for (const tagId of params.tagIds ?? []) {
    searchParams.append("tag_ids", tagId);
  }
  if (params.tagMatchMode) {
    searchParams.set("tag_match_mode", params.tagMatchMode);
  }
}

function appendPaginationParams(searchParams: URLSearchParams, params: PaginationParams): void {
  if (params.page) {
    searchParams.set("page", String(params.page));
  }
  if (params.pageSize) {
    searchParams.set("page_size", String(params.pageSize));
  }
}

function searchFilterRequestBody(payload: SearchFilterPayload): SearchFilterRequestBody {
  return {
    selected_source_ids: payload.selectedSourceIds ?? [],
    source_kinds: payload.sourceKinds ?? [],
    tag_ids: payload.tagIds ?? [],
    tag_match_mode: payload.tagMatchMode ?? "all",
    virtual_paths: payload.virtualPaths ?? [],
    created_after: payload.createdAfter ?? null,
    created_before: payload.createdBefore ?? null,
  };
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  return (await fetchJson(path, init)) as T;
}

async function fetchJson(path: string, init?: RequestInit): Promise<unknown> {
  const headers = new Headers(init?.headers ?? {});
  if (!(init?.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await authenticatedFetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (!response.ok) {
    throw await buildApiError(response);
  }
  return response.json();
}

async function buildApiError(response: Response): Promise<ApiError> {
  let message = `Request failed with ${response.status}`;
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      message = payload.detail;
    }
  } catch {
    message = response.statusText || message;
  }
  return new ApiError(message, response.status);
}

function prepareChatKitRequest(input: RequestInfo | URL, init?: RequestInit): { input: RequestInfo | URL; init?: RequestInit } {
  if (!isChatKitRequest(input) || typeof init?.body !== "string") {
    return { input, init };
  }
  const metadata = chatKitMetadataGetter?.();
  if (!metadata || !Object.keys(metadata).length) {
    return { input, init };
  }
  try {
    const payload = JSON.parse(init.body) as { metadata?: Record<string, unknown> };
    return {
      input,
      init: {
        ...init,
        body: JSON.stringify({
          ...payload,
          metadata: {
            ...(typeof payload.metadata === "object" && payload.metadata && !Array.isArray(payload.metadata) ? payload.metadata : {}),
            ...metadata,
          },
        }),
      },
    };
  } catch {
    return { input, init };
  }
}

function isChatKitRequest(input: RequestInfo | URL): boolean {
  const requestUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
  return requestUrl.includes("/chatkit");
}

function normalizeBase(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

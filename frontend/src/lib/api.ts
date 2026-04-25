import type {
  ActionResponse,
  AuthUser,
  BranchSearchResponse,
  IngestFinalizeResponse,
  ResplitSourceRequest,
  SearchFilterPayload,
  SearchResponse,
  SplitPreviewResponse,
  SourceDetail,
  SourceListResponse,
  SourceTagsUpdateRequest,
  TagCreateRequest,
  TagMatchMode,
  TagMutationResponse,
  TagSummary,
  TagUpdateRequest,
  TaskListResponse,
} from "./types";

const API_BASE_URL = normalizeBase(import.meta.env.VITE_API_BASE_URL ?? "/api");
const CHATKIT_DOMAIN_KEY = import.meta.env.VITE_CHATKIT_DOMAIN_KEY ?? "domain_pk_local_vectorstore2";

let bearerTokenGetter: (() => Promise<string | null>) | null = null;
let chatKitMetadataGetter: (() => Record<string, unknown> | null) | null = null;

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

export function setChatKitMetadataGetter(getter: (() => Record<string, unknown> | null) | null): void {
  chatKitMetadataGetter = getter;
}

export function getChatKitConfig(): { url: string; domainKey: string } {
  return {
    url: `${API_BASE_URL}/chatkit`,
    domainKey: CHATKIT_DOMAIN_KEY,
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

export async function listSources(params: {
  query?: string;
  tagIds?: string[];
  tagMatchMode?: TagMatchMode;
  page?: number;
  pageSize?: number;
}): Promise<SourceListResponse> {
  const searchParams = new URLSearchParams();
  if (params.query) {
    searchParams.set("query", params.query);
  }
  for (const tagId of params.tagIds ?? []) {
    searchParams.append("tag_ids", tagId);
  }
  if (params.tagMatchMode) {
    searchParams.set("tag_match_mode", params.tagMatchMode);
  }
  if (params.page) {
    searchParams.set("page", String(params.page));
  }
  if (params.pageSize) {
    searchParams.set("page_size", String(params.pageSize));
  }
  const suffix = searchParams.toString();
  return apiRequest<SourceListResponse>(suffix ? `/sources?${suffix}` : "/sources");
}

export async function getSource(sourceId: string): Promise<SourceDetail> {
  return apiRequest<SourceDetail>(`/sources/${encodeURIComponent(sourceId)}`);
}

export async function readSourceContentBlob(sourceId: string): Promise<{ blob: Blob; mediaType: string | null }> {
  const response = await authenticatedFetch(`${API_BASE_URL}/sources/${encodeURIComponent(sourceId)}/content`);
  if (!response.ok) {
    throw await buildApiError(response);
  }
  return { blob: await response.blob(), mediaType: response.headers.get("Content-Type") };
}

export async function uploadSource(file: File, userGuidance: string, tagIds: string[]): Promise<IngestFinalizeResponse> {
  const formData = new FormData();
  formData.set("file", file, file.name);
  if (userGuidance.trim()) {
    formData.set("user_guidance", userGuidance.trim());
  }
  for (const tagId of tagIds) {
    formData.append("tag_ids", tagId);
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

export async function searchChunks(payload: {
  query: string;
  maxResults?: number;
} & SearchFilterPayload): Promise<SearchResponse> {
  return apiRequest<SearchResponse>("/search", {
    method: "POST",
    body: JSON.stringify({
      query: payload.query,
      selected_source_ids: payload.selectedSourceIds ?? [],
      source_kinds: payload.sourceKinds ?? [],
      tag_ids: payload.tagIds ?? [],
      tag_match_mode: payload.tagMatchMode ?? "all",
      created_after: payload.createdAfter ?? null,
      created_before: payload.createdBefore ?? null,
      max_results: payload.maxResults ?? 8,
    }),
  });
}

export async function branchSearch(payload: {
  query: string;
  descend?: number;
  maxWidth?: number;
} & SearchFilterPayload): Promise<BranchSearchResponse> {
  return apiRequest<BranchSearchResponse>("/search/branch", {
    method: "POST",
    body: JSON.stringify({
      query: payload.query,
      selected_source_ids: payload.selectedSourceIds ?? [],
      source_kinds: payload.sourceKinds ?? [],
      tag_ids: payload.tagIds ?? [],
      tag_match_mode: payload.tagMatchMode ?? "all",
      created_after: payload.createdAfter ?? null,
      created_before: payload.createdBefore ?? null,
      descend: payload.descend ?? 2,
      max_width: payload.maxWidth ?? 3,
    }),
  });
}

export async function qaAction(payload: {
  prompt: string;
  selectedSourceIds?: string[];
  tagIds?: string[];
  tagMatchMode?: TagMatchMode;
}): Promise<ActionResponse> {
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

export async function freeformAction(payload: {
  prompt: string;
  mode: "grounded" | "creative";
  selectedSourceIds?: string[];
}): Promise<ActionResponse> {
  return apiRequest<ActionResponse>("/actions/freeform", {
    method: "POST",
    body: JSON.stringify({
      prompt: payload.prompt,
      mode: payload.mode,
      selected_source_ids: payload.selectedSourceIds ?? [],
    }),
  });
}

export async function imageAction(payload: { prompt: string; selectedSourceIds?: string[] }): Promise<ActionResponse> {
  return apiRequest<ActionResponse>("/actions/image", {
    method: "POST",
    body: JSON.stringify({
      prompt: payload.prompt,
      selected_source_ids: payload.selectedSourceIds ?? [],
    }),
  });
}

export async function voiceAction(payload: { prompt: string; sourceText?: string; selectedSourceIds?: string[] }): Promise<ActionResponse> {
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

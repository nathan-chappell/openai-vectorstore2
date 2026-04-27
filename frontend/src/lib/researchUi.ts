import type {
  FilesystemEntrySummary,
  ResearchCandidateStatus,
  ResearchImportCandidateSummary,
  ResearchLibraryBuildResponse,
} from "./types";

export function displayResearchCandidateStatus(
  candidate: ResearchImportCandidateSummary,
  linkedEntry: FilesystemEntrySummary | undefined,
): ResearchCandidateStatus {
  if (!linkedEntry?.status || candidate.status === "duplicate" || candidate.status === "rejected") {
    return candidate.status;
  }
  if (linkedEntry.status === "ready") {
    return candidate.status === "ingesting" || candidate.status === "ingested" ? "ingested" : candidate.status;
  }
  if (linkedEntry.status === "failed") {
    return candidate.status === "ingesting" || candidate.status === "ingested" ? "failed" : candidate.status;
  }
  return candidate.status === "ingested" ? "ingesting" : candidate.status;
}

export function mergeResearchCandidates(
  current: ResearchImportCandidateSummary[],
  updates: ResearchImportCandidateSummary[],
): ResearchImportCandidateSummary[] {
  const updateById = new Map(updates.map((candidate) => [candidate.id, candidate]));
  const currentIds = new Set(current.map((candidate) => candidate.id));
  return [
    ...current.map((candidate) => updateById.get(candidate.id) ?? candidate),
    ...updates.filter((candidate) => !currentIds.has(candidate.id)),
  ];
}

export function mergeResearchIngested(
  current: ResearchLibraryBuildResponse["ingested"],
  updates: ResearchLibraryBuildResponse["ingested"],
): ResearchLibraryBuildResponse["ingested"] {
  const updateBySourceId = new Map(updates.map((item) => [item.source.id, item]));
  const currentSourceIds = new Set(current.map((item) => item.source.id));
  return [
    ...current.map((item) => updateBySourceId.get(item.source.id) ?? item),
    ...updates.filter((item) => !currentSourceIds.has(item.source.id)),
  ];
}

export function asResearchBuildResponse(value: unknown): ResearchLibraryBuildResponse | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const candidate = value as Partial<ResearchLibraryBuildResponse>;
  if (!candidate.task || !Array.isArray(candidate.candidates) || !Array.isArray(candidate.ingested)) {
    return null;
  }
  return candidate as ResearchLibraryBuildResponse;
}

export function asResearchCandidates(value: unknown): ResearchImportCandidateSummary[] {
  return Array.isArray(value) ? (value as ResearchImportCandidateSummary[]) : [];
}

export function asResearchIngested(value: unknown): ResearchLibraryBuildResponse["ingested"] {
  return Array.isArray(value) ? (value as ResearchLibraryBuildResponse["ingested"]) : [];
}

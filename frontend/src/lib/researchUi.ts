import type {
  ResearchImportCandidateSummary,
  ResearchLibraryBuildResponse,
} from "./types";

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

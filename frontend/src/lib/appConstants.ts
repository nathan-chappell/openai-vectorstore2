import type { ResearchBuilderSeedKind } from "./appTypes";
import type { ResearchCandidateStatus } from "./types";

export const MODEL_CHOICES = [
  { id: "balanced", label: "Balanced", description: "Everyday retrieval and synthesis" },
  { id: "powerful", label: "Powerful", description: "Best reasoning pass" },
  { id: "lightweight", label: "Lightweight", description: "Fast exploratory pass" },
] as const;

export const TEXT_PREVIEW_LIMIT = 40_000;
export const CHUNK_PREVIEW_LIMIT = 40;
export const SOURCE_TAG_LIMIT = 8;
export const SELECTED_FILE_LIMIT = 10;
export const SOURCE_PAGE_SIZE = 100;
export const EXPLORER_RENDER_LIMIT = 250;
export const ACTIVE_TASK_REFRESH_INTERVAL_MS = 5_000;
export const WORKSPACE_SPLIT_STORAGE_KEY = "openai-vectorstore2.workspaceSplitPercent";
export const PREVIEW_SPLIT_STORAGE_KEY = "openai-vectorstore2.previewSplitPercent";
export const DEFAULT_SPLIT_GUIDANCE = "Optional split notes; indexing keeps the source file intact.";
export const DEFAULT_LIBRARY_QUERY = "indexed files";

export const RESEARCH_SEED_CHOICES: { id: ResearchBuilderSeedKind; label: string }[] = [
  { id: "topic", label: "Topic" },
  { id: "paper", label: "Paper" },
];

export const RESEARCH_STATUS_LABELS: Record<ResearchCandidateStatus, string> = {
  pending: "queued",
  approved: "queued",
  rejected: "skipped",
  ingesting: "indexing",
  ingested: "indexed",
  failed: "failed",
  duplicate: "duplicate",
};

export const RESEARCH_STATUS_PROGRESS: Record<ResearchCandidateStatus, number> = {
  pending: 18,
  approved: 28,
  rejected: 100,
  ingesting: 62,
  ingested: 100,
  failed: 100,
  duplicate: 100,
};

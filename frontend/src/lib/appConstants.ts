export const MODEL_CHOICES = [
  { id: "balanced", label: "Balanced", description: "Everyday retrieval and synthesis" },
  { id: "powerful", label: "Powerful", description: "Best reasoning pass" },
  { id: "lightweight", label: "Lightweight", description: "Fast exploratory pass" },
] as const;

export const TEXT_PREVIEW_LIMIT = 40_000;
export const CHUNK_PREVIEW_LIMIT = 40;
export const SOURCE_TAG_LIMIT = 8;
export const ENTITY_FILE_HISTORY_LIMIT = 100;
export const EXPLORER_RENDER_LIMIT = 250;
export const WORKSPACE_SPLIT_STORAGE_KEY = "openai-vectorstore2.workspaceSplitPercent";
export const PREVIEW_SPLIT_STORAGE_KEY = "openai-vectorstore2.previewSplitPercent";
export const DEFAULT_SPLIT_GUIDANCE = "Optional split notes; indexing keeps the source file intact.";
export const DEFAULT_LIBRARY_QUERY = "indexed files";

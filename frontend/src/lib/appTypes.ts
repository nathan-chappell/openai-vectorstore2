import type {
  ChunkHit,
  FilesystemEntrySummary,
} from "./types";

export type AppProps = {
  authMode: "clerk" | "local-dev";
  onSignOut?: () => void;
};

export type PreviewResource =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "text"; text: string; truncated: boolean; mediaType: string }
  | { state: "file"; url: string; mediaType: string }
  | { state: "error"; message: string };

export type DeleteDialogState = {
  entries: FilesystemEntrySummary[];
  phase: "confirming" | "deleting";
};

export type RevealTarget = {
  sourceId?: string | null;
  entryId?: string | null;
};

export type WorkspaceFileView = "explorer" | "library" | "results";

export type LibrarySearchResult = {
  hit: ChunkHit;
  entry: FilesystemEntrySummary | null;
};

export type ChatResultItem = {
  key: string;
  sourceId: string;
  entryId: string | null;
  name: string;
  path: string | null;
  sourceType: string | null;
  score: number | null;
  title: string | null;
  summary: string | null;
  text: string | null;
  locator: string | null;
  origin: string;
  query: string | null;
  seenCount: number;
};

export type ChatKitClientToolCall = {
  name: string;
  params: Record<string, unknown>;
};

export type ChatKitClientToolResult = Record<string, unknown>;

export type ChatKitDeeplinkEvent = {
  name: string;
  data?: Record<string, unknown>;
};

export type ChatKitMetadata = {
  origin: "web";
} & Record<string, unknown>;

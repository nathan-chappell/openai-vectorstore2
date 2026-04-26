import { ChatKit, type UseChatKitOptions, useChatKit } from "@openai/chatkit-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from "react";

import {
  authenticatedFetch,
  buildResearchLibrary,
  createFolder,
  createTag,
  deleteSource,
  deleteFilesystemEntries,
  getAuthenticatedUser,
  getChatKitConfig,
  getSource,
  listFilesystem,
  listSources,
  listTags,
  listTasks,
  previewSemanticSplit,
  readSourceContentBlob,
  resplitSource,
  searchFilesystem,
  setChatKitMetadataGetter,
  updateFilesystemEntry,
  updateResearchCandidateStatus,
  updateSourceTags,
  ingestResearchCandidates,
  uploadSource,
} from "./lib/api";
import type {
  AuthUser,
  ChunkSummary,
  FilesystemBreadcrumb,
  FilesystemEntrySummary,
  FilesystemListResponse,
  ResearchImportCandidateSummary,
  ResearchLibraryBuildResponse,
  ResearchSeedKind,
  SourceDetail,
  SourceSummary,
  SplitPreviewResponse,
  TagSummary,
  TaskSummary,
} from "./lib/types";

type AppProps = {
  authMode: "clerk" | "local-dev";
};

type PreviewResource =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "text"; text: string; truncated: boolean; mediaType: string }
  | { state: "file"; url: string; mediaType: string }
  | { state: "error"; message: string };

const MODEL_CHOICES = [
  { id: "balanced", label: "Balanced", description: "Everyday retrieval and synthesis" },
  { id: "powerful", label: "Powerful", description: "Best reasoning pass" },
  { id: "lightweight", label: "Lightweight", description: "Fast exploratory pass" },
] as const;

const TEXT_PREVIEW_LIMIT = 40_000;
const CHUNK_PREVIEW_LIMIT = 40;
const SOURCE_TAG_LIMIT = 8;
const SELECTED_FILE_LIMIT = 10;
const SOURCE_PAGE_SIZE = 100;
const EXPLORER_RENDER_LIMIT = 250;
const WORKSPACE_SPLIT_STORAGE_KEY = "openai-vectorstore2.workspaceSplitPercent";
const DEFAULT_SPLIT_GUIDANCE = "Optional split notes; indexing keeps the source file intact.";
type ResearchBuilderSeedKind = Extract<ResearchSeedKind, "topic" | "paper">;
const RESEARCH_SEED_CHOICES: { id: ResearchBuilderSeedKind; label: string }[] = [
  { id: "topic", label: "Topic" },
  { id: "paper", label: "Paper" },
];

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

async function loadAllSources(): Promise<{ sources: SourceSummary[]; totalCount: number }> {
  const sources: SourceSummary[] = [];
  let page = 1;
  let totalCount = 0;
  while (true) {
    const response = await listSources({ page, pageSize: SOURCE_PAGE_SIZE });
    sources.push(...response.sources);
    totalCount = response.total_count;
    if (!response.has_more) {
      return { sources, totalCount };
    }
    page += 1;
  }
}

function mergeResearchCandidates(
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

export function App({ authMode }: AppProps) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [filesystem, setFilesystem] = useState<FilesystemListResponse | null>(null);
  const [searchEntries, setSearchEntries] = useState<FilesystemEntrySummary[]>([]);
  const [knownEntries, setKnownEntries] = useState<Record<string, FilesystemEntrySummary>>({});
  const [tags, setTags] = useState<TagSummary[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
  const [sourceQuery, setSourceQuery] = useState("");
  const [selectedExplorerTagIds, setSelectedExplorerTagIds] = useState<string[]>([]);
  const [selectedEntryIds, setSelectedEntryIds] = useState<string[]>([]);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [focusedEntryId, setFocusedEntryId] = useState<string | null>(null);
  const [selectedSource, setSelectedSource] = useState<SourceDetail | null>(null);
  const [selectedSourceTagDraftIds, setSelectedSourceTagDraftIds] = useState<string[]>([]);
  const [newTagName, setNewTagName] = useState("");
  const [uploadGuidance, setUploadGuidance] = useState(DEFAULT_SPLIT_GUIDANCE);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [splitPreview, setSplitPreview] = useState<SplitPreviewResponse | null>(null);
  const [researchQuery, setResearchQuery] = useState("");
  const [researchSeedType, setResearchSeedType] = useState<ResearchBuilderSeedKind>("topic");
  const [researchMaxSources, setResearchMaxSources] = useState(12);
  const [researchMaxDepth, setResearchMaxDepth] = useState(2);
  const [researchAutoIngest, setResearchAutoIngest] = useState(false);
  const [researchResult, setResearchResult] = useState<ResearchLibraryBuildResponse | null>(null);
  const [status, setStatus] = useState("Opening files.");
  const [busy, setBusy] = useState(false);
  const workspaceGridRef = useRef<HTMLElement | null>(null);
  const [workspaceSplitPercent, setWorkspaceSplitPercent] = useState(() => readStoredWorkspaceSplit());

  const selectedExplorerTagIdSet = useMemo(() => new Set(selectedExplorerTagIds), [selectedExplorerTagIds]);
  const selectedEntryIdSet = useMemo(() => new Set(selectedEntryIds), [selectedEntryIds]);
  const selectedSourceIdSet = useMemo(() => new Set(selectedSourceIds), [selectedSourceIds]);
  const selectedSourceTagDraftIdSet = useMemo(() => new Set(selectedSourceTagDraftIds), [selectedSourceTagDraftIds]);
  const searching = Boolean(sourceQuery.trim() || selectedExplorerTagIds.length);
  const visibleEntries = searching ? searchEntries : (filesystem?.entries ?? []);
  const selectedFileEntries = useMemo(
    () =>
      selectedSourceIds.flatMap((sourceId) => {
        const entry = Object.values(knownEntries).find((item) => item.source_id === sourceId);
        return entry ? [entry] : [];
      }),
    [knownEntries, selectedSourceIds],
  );
  const hasActiveTasks = useMemo(() => tasks.some(isActiveTask), [tasks]);
  const selectedSourceId = selectedSource?.id ?? null;
  const selectedSourceTagChanged = selectedSource
    ? !sameStringSet(selectedSourceTagDraftIds, selectedSource.tags.map((tag) => tag.id))
    : false;
  const workspaceStyle = useMemo(
    () =>
      ({
        "--workspace-explorer-width": `${workspaceSplitPercent}%`,
      }) as CSSProperties & Record<"--workspace-explorer-width", string>,
    [workspaceSplitPercent],
  );

  const cacheEntries = useCallback((entries: FilesystemEntrySummary[]): void => {
    setKnownEntries((current) => {
      const next = { ...current };
      for (const entry of entries) {
        next[entry.id] = entry;
      }
      return next;
    });
  }, []);

  const loadFolder = useCallback(
    async (folderId: string | null): Promise<FilesystemListResponse> => {
      const response = await listFilesystem({ folderId });
      setFilesystem(response);
      setCurrentFolderId(response.current.parent_id === null ? null : response.current.id);
      cacheEntries([response.current, ...response.entries]);
      setSearchEntries([]);
      return response;
    },
    [cacheEntries],
  );

  const refreshExplorer = useCallback(async (): Promise<void> => {
    if (sourceQuery.trim() || selectedExplorerTagIds.length) {
      const response = await searchFilesystem({
        query: sourceQuery,
        tagIds: selectedExplorerTagIds,
        tagMatchMode: "all",
        pageSize: 100,
      });
      setSearchEntries(response.entries);
      cacheEntries(response.entries);
      setStatus(`Found ${response.total_count} matching entr${response.total_count === 1 ? "y" : "ies"}.`);
      return;
    }
    const response = await loadFolder(currentFolderId);
    setStatus(`${response.current.path} has ${response.entries.length} entr${response.entries.length === 1 ? "y" : "ies"}.`);
  }, [cacheEntries, currentFolderId, loadFolder, selectedExplorerTagIds, sourceQuery]);

  const refreshAll = useCallback(async (): Promise<void> => {
    setBusy(true);
    try {
      const [me, tagList, taskList] = await Promise.all([getAuthenticatedUser(), listTags(), listTasks()]);
      setUser(me);
      setTags(tagList);
      setTasks(taskList.tasks);
      const response = await loadFolder(currentFolderId);
      setStatus(`Ready at ${response.current.path}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not load the workspace.");
    } finally {
      setBusy(false);
    }
  }, [currentFolderId, loadFolder]);

  const refreshActivity = useCallback(async (): Promise<void> => {
    try {
      const detailPromise = selectedSourceId
        ? getSource(selectedSourceId).catch(() => null)
        : Promise.resolve<SourceDetail | null>(null);
      const [tagList, taskList, detail] = await Promise.all([listTags(), listTasks(), detailPromise]);
      setTags(tagList);
      setTasks(taskList.tasks);
      if (detail) {
        setSelectedSource(detail);
      }
      await refreshExplorer();
      const activeTask = taskList.tasks.find(isActiveTask);
      if (activeTask) {
        setStatus(`${activeTask.kind} ${activeTask.status}: ${activeTask.title}.`);
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not refresh background activity.");
    }
  }, [refreshExplorer, selectedSourceId]);

  useEffect(() => {
    setChatKitMetadataGetter(() => ({
      origin: "web",
      selected_source_ids: selectedSourceIds,
      selected_virtual_paths: selectedFileEntries.map((entry) => entry.path),
    }));
    return () => setChatKitMetadataGetter(null);
  }, [selectedFileEntries, selectedSourceIds]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void refreshExplorer();
    }, 180);
    return () => window.clearTimeout(timeoutId);
  }, [refreshExplorer]);

  useEffect(() => {
    if (!hasActiveTasks) {
      return undefined;
    }
    const intervalId = window.setInterval(() => {
      void refreshActivity();
    }, 2_500);
    return () => window.clearInterval(intervalId);
  }, [hasActiveTasks, refreshActivity]);

  useEffect(() => {
    setSelectedSourceTagDraftIds(selectedSource?.tags.map((tag) => tag.id) ?? []);
  }, [selectedSource]);

  const openSource = useCallback(async (sourceId: string): Promise<void> => {
    setStatus("Loading file preview.");
    try {
      const detail = await getSource(sourceId);
      setSelectedSource(detail);
      setStatus(`Previewing ${detail.virtual_path ?? detail.display_title}.`);
    } catch (error) {
      setSelectedSource(null);
      setStatus(error instanceof Error ? error.message : "Could not load file detail.");
    }
  }, []);

  const syncChatSelection = useCallback(
    (entryIds: string[]): void => {
      const readySourceIds = entryIds
        .map((entryId) => knownEntries[entryId])
        .filter((entry): entry is FilesystemEntrySummary => Boolean(entry))
        .filter((entry) => entry.kind === "file" && entry.status === "ready" && Boolean(entry.source_id))
        .map((entry) => entry.source_id as string)
        .slice(0, SELECTED_FILE_LIMIT);
      setSelectedSourceIds(Array.from(new Set(readySourceIds)));
    },
    [knownEntries],
  );

  const chooseEntries = useCallback(
    (entry: FilesystemEntrySummary, event: ReactMouseEvent): void => {
      setFocusedEntryId(entry.id);
      setSelectedEntryIds((current) => {
        const currentVisibleIds = visibleEntries.map((item) => item.id);
        let next: string[];
        if (event.shiftKey && focusedEntryId) {
          const anchorIndex = currentVisibleIds.indexOf(focusedEntryId);
          const targetIndex = currentVisibleIds.indexOf(entry.id);
          if (anchorIndex >= 0 && targetIndex >= 0) {
            const [start, end] = [anchorIndex, targetIndex].sort((left, right) => left - right);
            next = currentVisibleIds.slice(start, end + 1);
          } else {
            next = [entry.id];
          }
        } else if (event.metaKey || event.ctrlKey) {
          next = current.includes(entry.id) ? current.filter((id) => id !== entry.id) : [...current, entry.id];
        } else {
          next = [entry.id];
        }
        syncChatSelection(next);
        return next;
      });
      if (entry.source_id) {
        void openSource(entry.source_id);
      }
    },
    [focusedEntryId, openSource, syncChatSelection, visibleEntries],
  );

  const openEntry = useCallback(
    (entry: FilesystemEntrySummary): void => {
      if (entry.kind === "folder") {
        setSourceQuery("");
        setSelectedExplorerTagIds([]);
        setSelectedEntryIds([]);
        setSelectedSourceIds([]);
        setFocusedEntryId(null);
        setSelectedSource(null);
        void loadFolder(entry.id);
        return;
      }
      if (entry.source_id) {
        void openSource(entry.source_id);
      }
    },
    [loadFolder, openSource],
  );

  const goToFolder = useCallback(
    (folderId: string | null): void => {
      setSourceQuery("");
      setSelectedExplorerTagIds([]);
      setSelectedEntryIds([]);
      setSelectedSourceIds([]);
      setFocusedEntryId(null);
      setSelectedSource(null);
      void loadFolder(folderId);
    },
    [loadFolder],
  );

  const createFolderInCurrentFolder = useCallback(async (): Promise<void> => {
    const name = window.prompt("Folder name", "New folder")?.trim();
    if (!name) {
      return;
    }
    setBusy(true);
    try {
      await createFolder({ parent_id: filesystem?.current.id ?? null, name });
      await refreshExplorer();
      setStatus(`Created ${name}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Folder create failed.");
    } finally {
      setBusy(false);
    }
  }, [filesystem?.current.id, refreshExplorer]);

  const renameFocusedEntry = useCallback(async (): Promise<void> => {
    const entry = selectedEntryIds.length === 1 ? knownEntries[selectedEntryIds[0]] : null;
    if (!entry) {
      return;
    }
    const name = window.prompt("Rename", entry.name)?.trim();
    if (!name || name === entry.name) {
      return;
    }
    setBusy(true);
    try {
      await updateFilesystemEntry(entry.id, { name });
      await refreshExplorer();
      setStatus(`Renamed ${entry.name} to ${name}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Rename failed.");
    } finally {
      setBusy(false);
    }
  }, [knownEntries, refreshExplorer, selectedEntryIds]);

  const deleteSelectedEntries = useCallback(async (): Promise<void> => {
    if (!selectedEntryIds.length) {
      return;
    }
    const confirmed = window.confirm(`Permanently delete ${selectedEntryIds.length} selected item${selectedEntryIds.length === 1 ? "" : "s"}?`);
    if (!confirmed) {
      return;
    }
    setBusy(true);
    try {
      await deleteFilesystemEntries({ entry_ids: selectedEntryIds, confirm: true });
      setSelectedEntryIds([]);
      setSelectedSourceIds([]);
      setFocusedEntryId(null);
      setSelectedSource(null);
      await refreshExplorer();
      setStatus("Deleted selected items.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Delete failed.");
    } finally {
      setBusy(false);
    }
  }, [refreshExplorer, selectedEntryIds]);

  const moveEntriesToFolder = useCallback(
    async (entryIds: string[], folderId: string): Promise<void> => {
      const movingIds = entryIds.filter((entryId) => entryId !== folderId);
      if (!movingIds.length) {
        return;
      }
      setBusy(true);
      try {
        await Promise.all(movingIds.map((entryId) => updateFilesystemEntry(entryId, { parent_id: folderId })));
        setSelectedEntryIds([]);
        setSelectedSourceIds([]);
        await refreshExplorer();
        setStatus(`Moved ${movingIds.length} item${movingIds.length === 1 ? "" : "s"}.`);
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Move failed.");
      } finally {
        setBusy(false);
      }
    },
    [refreshExplorer],
  );

  const chooseFiles = useCallback((files: FileList | null): void => {
    const nextFiles = Array.from(files ?? []);
    setPendingFiles(nextFiles);
    setSplitPreview(null);
    if (nextFiles.length) {
      setStatus(`Selected ${nextFiles.length} file${nextFiles.length === 1 ? "" : "s"} for upload.`);
    }
  }, []);

  const previewPendingSplit = useCallback(async (): Promise<void> => {
    const [file] = pendingFiles;
    if (!file) {
      return;
    }
    setBusy(true);
    try {
      const response = await previewSemanticSplit(file, uploadGuidance);
      setSplitPreview(response);
      setStatus(`Previewed ${response.split.chunks.length} proposed chunk${response.split.chunks.length === 1 ? "" : "s"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Split preview failed.");
    } finally {
      setBusy(false);
    }
  }, [pendingFiles, uploadGuidance]);

  const handleUpload = useCallback(async (): Promise<void> => {
    if (!pendingFiles.length) {
      return;
    }
    setBusy(true);
    try {
      for (const file of pendingFiles) {
        await uploadSource(file, uploadGuidance, [], filesystem?.current.id ?? null);
      }
      setPendingFiles([]);
      setSplitPreview(null);
      await refreshExplorer();
      setStatus("Indexing queued. Files will appear searchable when ready.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }, [filesystem?.current.id, pendingFiles, refreshExplorer, uploadGuidance]);

  const buildResearchLibraryFromPanel = useCallback(async (): Promise<void> => {
    const query = researchQuery.trim();
    if (!query) {
      setStatus("Enter a topic or paper title first.");
      return;
    }
    const maxSources = clamp(Math.round(researchMaxSources), 1, 50);
    const maxDepth = clamp(Math.round(researchMaxDepth), 0, 4);
    setBusy(true);
    try {
      const response = await buildResearchLibrary({
        seed_type: researchSeedType,
        query,
        title: query,
        auto_ingest: researchAutoIngest,
        discover_references: true,
        max_depth: maxDepth,
        max_sources: maxSources,
        max_candidates_per_source: Math.min(20, Math.max(4, maxSources)),
        max_pending_candidates: Math.max(50, maxSources * Math.max(1, maxDepth + 1)),
      });
      setResearchResult(response);
      setTasks((await listTasks()).tasks);
      setSourceQuery("");
      setSelectedExplorerTagIds([]);
      setSelectedEntryIds([]);
      setSelectedSourceIds([]);
      setFocusedEntryId(null);
      setSelectedSource(null);
      if (response.target_folder_id) {
        await loadFolder(response.target_folder_id);
      } else {
        await refreshExplorer();
      }
      const ingestedLabel = response.ingested.length
        ? `, ${response.ingested.length} indexed`
        : researchAutoIngest
          ? ", no public items indexed"
          : "";
      setStatus(`Research library build found ${response.candidates.length} candidate${response.candidates.length === 1 ? "" : "s"}${ingestedLabel}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Research library build failed.");
    } finally {
      setBusy(false);
    }
  }, [
    loadFolder,
    refreshExplorer,
    researchAutoIngest,
    researchMaxDepth,
    researchMaxSources,
    researchQuery,
    researchSeedType,
  ]);

  const updateResearchCandidateReview = useCallback(
    async (candidateId: string, nextStatus: "approved" | "rejected" | "pending"): Promise<void> => {
      setBusy(true);
      try {
        const response = await updateResearchCandidateStatus({
          candidate_ids: [candidateId],
          status: nextStatus,
        });
        setResearchResult((current) =>
          current ? { ...current, candidates: mergeResearchCandidates(current.candidates, response.candidates) } : current,
        );
        setStatus(`Marked research candidate ${nextStatus}.`);
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Could not update research candidate.");
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const ingestApprovedResearchCandidates = useCallback(async (): Promise<void> => {
    const approvedIds = researchResult?.candidates
      .filter((candidate) => candidate.status === "approved")
      .map((candidate) => candidate.id) ?? [];
    if (!approvedIds.length) {
      setStatus("Approve at least one research candidate before ingesting.");
      return;
    }
    setBusy(true);
    try {
      const response = await ingestResearchCandidates({
        candidate_ids: approvedIds,
        folder_id: researchResult?.target_folder_id ?? null,
      });
      setResearchResult((current) =>
        current
          ? {
              ...current,
              candidates: mergeResearchCandidates(current.candidates, response.candidates),
              ingested: [...current.ingested, ...response.ingested],
            }
          : current,
      );
      setTasks((await listTasks()).tasks);
      await refreshExplorer();
      setStatus(`Ingested ${response.ingested.length} approved research candidate${response.ingested.length === 1 ? "" : "s"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not ingest approved research candidates.");
    } finally {
      setBusy(false);
    }
  }, [refreshExplorer, researchResult]);

  const toggleExplorerTag = useCallback((tagId: string): void => {
    setSelectedExplorerTagIds((current) =>
      current.includes(tagId) ? current.filter((id) => id !== tagId) : [...current, tagId],
    );
  }, []);

  const clearExplorerFilters = useCallback((): void => {
    setSourceQuery("");
    setSelectedExplorerTagIds([]);
  }, []);

  const createExplorerTag = useCallback(async (): Promise<void> => {
    const name = newTagName.trim();
    if (!name) {
      return;
    }
    setBusy(true);
    try {
      const response = await createTag({ name });
      setTags(await listTags());
      setNewTagName("");
      setStatus(`Tag ready: ${response.tag?.name ?? name}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Tag create failed.");
    } finally {
      setBusy(false);
    }
  }, [newTagName]);

  const saveSelectedSourceTags = useCallback(async (): Promise<void> => {
    if (!selectedSource) {
      return;
    }
    setBusy(true);
    try {
      const response = await updateSourceTags(selectedSource.id, { tag_ids: selectedSourceTagDraftIds });
      const detail = await getSource(selectedSource.id);
      setSelectedSource(detail);
      setTags(await listTags());
      setTasks((await listTasks()).tasks);
      await refreshExplorer();
      setStatus(`Queued tag reindex for ${response.source.virtual_path ?? response.source.display_title}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Tag update failed.");
    } finally {
      setBusy(false);
    }
  }, [refreshExplorer, selectedSource, selectedSourceTagDraftIds]);

  const resplitSelectedSource = useCallback(async (): Promise<void> => {
    if (!selectedSource) {
      return;
    }
    setBusy(true);
    try {
      const response = await resplitSource(selectedSource.id, { user_guidance: uploadGuidance });
      const detail = await getSource(selectedSource.id);
      setSelectedSource(detail);
      setTasks((await listTasks()).tasks);
      await refreshExplorer();
      setStatus(`Queued re-split for ${response.source.virtual_path ?? response.source.display_title}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Re-split failed.");
    } finally {
      setBusy(false);
    }
  }, [refreshExplorer, selectedSource, uploadGuidance]);

  const toggleSelectedSourceTagDraft = useCallback((tagId: string): void => {
    setSelectedSourceTagDraftIds((current) =>
      current.includes(tagId) ? current.filter((id) => id !== tagId) : [...current, tagId],
    );
  }, []);

  const handleClientTool = useCallback(
    async (toolCall: { name: string; params: Record<string, unknown> }): Promise<Record<string, unknown>> => {
      if (toolCall.name === "set_file_selection") {
        const rawIds = Array.isArray(toolCall.params.source_ids) ? toolCall.params.source_ids : [];
        const sourceIds = rawIds.filter((id): id is string => typeof id === "string").slice(0, SELECTED_FILE_LIMIT);
        const mode = typeof toolCall.params.mode === "string" ? toolCall.params.mode : "replace";
        setSelectedSourceIds((current) => {
          if (mode === "add") {
            return Array.from(new Set([...current, ...sourceIds])).slice(0, SELECTED_FILE_LIMIT);
          }
          if (mode === "remove") {
            return current.filter((id) => !sourceIds.includes(id));
          }
          return sourceIds;
        });
        const entryIds = Object.values(knownEntries)
          .filter((entry) => entry.source_id && sourceIds.includes(entry.source_id))
          .map((entry) => entry.id);
        if (entryIds.length) {
          setSelectedEntryIds(entryIds);
          setFocusedEntryId(entryIds[0]);
        }
        return { ok: true, selected_source_ids: sourceIds };
      }
      if (toolCall.name === "set_file_search") {
        const query = typeof toolCall.params.query === "string" ? toolCall.params.query : "";
        const tagIds = Array.isArray(toolCall.params.tag_ids)
          ? toolCall.params.tag_ids.filter((id): id is string => typeof id === "string")
          : [];
        setSourceQuery(query);
        setSelectedExplorerTagIds(tagIds);
        return { ok: true, query, tag_ids: tagIds };
      }
      if (toolCall.name === "reveal_file") {
        const sourceId = typeof toolCall.params.source_id === "string" ? toolCall.params.source_id : null;
        const entryId = typeof toolCall.params.entry_id === "string" ? toolCall.params.entry_id : null;
        let entry = entryId ? knownEntries[entryId] : null;
        if (!entry && sourceId) {
          entry = Object.values(knownEntries).find((item) => item.source_id === sourceId) ?? null;
        }
        if (!entry && sourceId) {
          const search = await searchFilesystem({ query: sourceId, pageSize: 1 });
          entry = search.entries[0] ?? null;
          cacheEntries(search.entries);
        }
        if (!entry) {
          return { ok: false, message: "File was not found in the explorer." };
        }
        await loadFolder(entry.parent_id);
        setSelectedEntryIds([entry.id]);
        setFocusedEntryId(entry.id);
        if (entry.source_id) {
          setSelectedSourceIds([entry.source_id]);
          await openSource(entry.source_id);
        }
        return { ok: true, entry_id: entry.id, source_id: entry.source_id, path: entry.path };
      }
      return { ok: false, message: `Unknown client tool: ${toolCall.name}` };
    },
    [cacheEntries, knownEntries, loadFolder, openSource],
  );

  const beginWorkspaceResize = useCallback((event: ReactPointerEvent<HTMLButtonElement>): void => {
    const grid = workspaceGridRef.current;
    if (!grid) {
      return;
    }
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    document.body.classList.add("resizing-workspace");
    const updateFromPointer = (clientX: number): void => {
      const rect = grid.getBoundingClientRect();
      const nextPercent = clamp(((clientX - rect.left) / rect.width) * 100, 46, 76);
      setWorkspaceSplitPercent(nextPercent);
      window.localStorage.setItem(WORKSPACE_SPLIT_STORAGE_KEY, String(Math.round(nextPercent)));
    };
    updateFromPointer(event.clientX);
    const handlePointerMove = (moveEvent: PointerEvent): void => {
      updateFromPointer(moveEvent.clientX);
    };
    const stopResize = (): void => {
      document.body.classList.remove("resizing-workspace");
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
    };
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize, { once: true });
    window.addEventListener("pointercancel", stopResize, { once: true });
  }, []);

  return (
    <main className="app-shell">
      <WorkspaceHeader
        authMode={authMode}
        busy={busy}
        status={status}
        tasks={tasks}
        user={user}
        onRefresh={() => void refreshAll()}
      />

      <section ref={workspaceGridRef} className="workspace-grid" style={workspaceStyle} aria-label="Indexed file workspace">
        <FileExplorer
          breadcrumbs={filesystem?.breadcrumbs ?? []}
          busy={busy}
          currentFolder={filesystem?.current ?? null}
          entries={visibleEntries}
          focusedEntryId={focusedEntryId}
          newTagName={newTagName}
          pendingFiles={pendingFiles}
          researchAutoIngest={researchAutoIngest}
          researchMaxDepth={researchMaxDepth}
          researchMaxSources={researchMaxSources}
          researchQuery={researchQuery}
          researchResult={researchResult}
          researchSeedType={researchSeedType}
          searching={searching}
          selectedEntryIdSet={selectedEntryIdSet}
          selectedExplorerTagIdSet={selectedExplorerTagIdSet}
          selectedFileEntries={selectedFileEntries}
          selectedSource={selectedSource}
          selectedSourceIdSet={selectedSourceIdSet}
          selectedSourceTagChanged={selectedSourceTagChanged}
          selectedSourceTagDraftIdSet={selectedSourceTagDraftIdSet}
          sourceQuery={sourceQuery}
          splitPreview={splitPreview}
          tags={tags}
          uploadGuidance={uploadGuidance}
          onChooseEntries={chooseEntries}
          onChooseFiles={chooseFiles}
          onClearFilters={clearExplorerFilters}
          onCreateFolder={() => void createFolderInCurrentFolder()}
          onCreateTag={() => void createExplorerTag()}
          onDeleteSelected={() => void deleteSelectedEntries()}
          onDropEntries={(entryIds, folderId) => void moveEntriesToFolder(entryIds, folderId)}
          onGoToFolder={goToFolder}
          onNewTagNameChange={setNewTagName}
          onOpenEntry={openEntry}
          onPreviewSplit={() => void previewPendingSplit()}
          onResearchAutoIngestChange={setResearchAutoIngest}
          onResearchBuild={() => void buildResearchLibraryFromPanel()}
          onResearchCandidateStatus={(candidateId, nextStatus) => void updateResearchCandidateReview(candidateId, nextStatus)}
          onResearchIngestApproved={() => void ingestApprovedResearchCandidates()}
          onResearchMaxDepthChange={setResearchMaxDepth}
          onResearchMaxSourcesChange={setResearchMaxSources}
          onResearchQueryChange={setResearchQuery}
          onResearchSeedTypeChange={setResearchSeedType}
          onRenameSelected={() => void renameFocusedEntry()}
          onResplit={() => void resplitSelectedSource()}
          onSaveTags={() => void saveSelectedSourceTags()}
          onSourceQueryChange={setSourceQuery}
          onTagToggle={toggleSelectedSourceTagDraft}
          onToggleExplorerTag={toggleExplorerTag}
          onUpload={() => void handleUpload()}
          onUploadGuidanceChange={setUploadGuidance}
        />

        <button
          type="button"
          className="workspace-splitter"
          role="separator"
          aria-label="Resize workspace"
          aria-orientation="vertical"
          aria-valuemin={46}
          aria-valuemax={76}
          aria-valuenow={Math.round(workspaceSplitPercent)}
          onPointerDown={beginWorkspaceResize}
        />

        <aside className="chat-panel" aria-label="AI file assistant">
          <ChatPane selectedSourceIds={selectedSourceIds} onClientTool={handleClientTool} />
        </aside>
      </section>
    </main>
  );
}

function LegacyApp({ authMode }: AppProps) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [tags, setTags] = useState<TagSummary[]>([]);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [selectedSource, setSelectedSource] = useState<SourceDetail | null>(null);
  const [sourceQuery, setSourceQuery] = useState("");
  const [selectedExplorerTagIds, setSelectedExplorerTagIds] = useState<string[]>([]);
  const [newTagName, setNewTagName] = useState("");
  const [uploadGuidance, setUploadGuidance] = useState(DEFAULT_SPLIT_GUIDANCE);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [splitPreview, setSplitPreview] = useState<SplitPreviewResponse | null>(null);
  const [selectedSourceTagDraftIds, setSelectedSourceTagDraftIds] = useState<string[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [status, setStatus] = useState("Booting the indexed file library.");
  const [busy, setBusy] = useState(false);

  const selectedSourceIdSet = useMemo(() => new Set(selectedSourceIds), [selectedSourceIds]);
  const selectedExplorerTagIdSet = useMemo(() => new Set(selectedExplorerTagIds), [selectedExplorerTagIds]);
  const selectedSourceTagDraftIdSet = useMemo(() => new Set(selectedSourceTagDraftIds), [selectedSourceTagDraftIds]);
  const sortedSources = useMemo(
    () => [...sources].sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at)),
    [sources],
  );
  const filteredSources = useMemo(() => {
    const normalizedQuery = sourceQuery.trim().toLowerCase();
    return sortedSources.filter((source) => {
      const matchesQuery =
        !normalizedQuery ||
        [
          source.display_title,
          source.original_filename,
          source.source_kind,
          source.status,
          source.created_at,
          ...source.tags.map((tag) => tag.name),
        ]
          .join(" ")
          .toLowerCase()
          .includes(normalizedQuery);
      const matchesTags =
        !selectedExplorerTagIds.length || source.tags.some((tag) => selectedExplorerTagIdSet.has(tag.id));
      return matchesQuery && matchesTags;
    });
  }, [selectedExplorerTagIdSet, selectedExplorerTagIds.length, sortedSources, sourceQuery]);
  const selectedSourceSummaries = useMemo(() => {
    const byId = new Map(sources.map((source) => [source.id, source]));
    return selectedSourceIds.flatMap((sourceId) => {
      const source = byId.get(sourceId);
      return source ? [source] : [];
    });
  }, [selectedSourceIds, sources]);
  const visibleSources = useMemo(() => filteredSources.slice(0, EXPLORER_RENDER_LIMIT), [filteredSources]);
  const activeSourceId = selectedSource?.id ?? selectedSourceIds[0] ?? null;
  const selectedSourceId = selectedSource?.id ?? null;
  const hasActiveTasks = useMemo(() => tasks.some(isActiveTask), [tasks]);

  const refreshAll = useCallback(async (): Promise<void> => {
    setBusy(true);
    try {
      const [me, sourceList, tagList, taskList] = await Promise.all([
        getAuthenticatedUser(),
        loadAllSources(),
        listTags(),
        listTasks(),
      ]);
      setUser(me);
      setSources(sourceList.sources);
      setTags(tagList);
      setTasks(taskList.tasks);
      setStatus(`Ready with ${sourceList.totalCount} source${sourceList.totalCount === 1 ? "" : "s"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not load the workspace.");
    } finally {
      setBusy(false);
    }
  }, []);

  const refreshActivity = useCallback(async (): Promise<void> => {
    try {
      const detailPromise = selectedSourceId
        ? getSource(selectedSourceId).catch(() => null)
        : Promise.resolve<SourceDetail | null>(null);
      const [sourceList, tagList, taskList, detail] = await Promise.all([
        loadAllSources(),
        listTags(),
        listTasks(),
        detailPromise,
      ]);
      setSources(sourceList.sources);
      setTags(tagList);
      setTasks(taskList.tasks);
      setSelectedSource((current) => {
        if (!current || detail?.id !== current.id) {
          return current;
        }
        return detail.updated_at === current.updated_at &&
          detail.status === current.status &&
          detail.chunk_count === current.chunk_count
          ? current
          : detail;
      });

      const activeTask = taskList.tasks.find(isActiveTask);
      if (activeTask) {
        setStatus(`${activeTask.kind} ${activeTask.status}: ${activeTask.title}.`);
      } else {
        setStatus(`Ready with ${sourceList.totalCount} source${sourceList.totalCount === 1 ? "" : "s"}.`);
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not refresh background activity.");
    }
  }, [selectedSourceId]);

  useEffect(() => {
    setChatKitMetadataGetter(() => ({
      origin: "web",
      selected_source_ids: selectedSourceIds,
    }));
    return () => setChatKitMetadataGetter(null);
  }, [selectedSourceIds]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    if (!hasActiveTasks) {
      return undefined;
    }
    const intervalId = window.setInterval(() => {
      void refreshActivity();
    }, 2_500);
    void refreshActivity();
    return () => window.clearInterval(intervalId);
  }, [hasActiveTasks, refreshActivity]);

  useEffect(() => {
    setSelectedSourceTagDraftIds(selectedSource?.tags.map((tag) => tag.id) ?? []);
  }, [selectedSource]);

  const chooseFiles = useCallback((files: FileList | null): void => {
    const nextFiles = Array.from(files ?? []);
    setPendingFiles(nextFiles);
    setSplitPreview(null);
    if (nextFiles.length) {
      setStatus(`Selected ${nextFiles.length} file${nextFiles.length === 1 ? "" : "s"} for preview or upload.`);
    }
  }, []);

  const previewPendingSplit = useCallback(async (): Promise<void> => {
    const [file] = pendingFiles;
    if (!file) {
      return;
    }
    setBusy(true);
    try {
      const response = await previewSemanticSplit(file, uploadGuidance);
      setSplitPreview(response);
      setStatus(`Previewed ${response.split.chunks.length} proposed chunk${response.split.chunks.length === 1 ? "" : "s"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Split preview failed.");
    } finally {
      setBusy(false);
    }
  }, [pendingFiles, uploadGuidance]);

  const handleUpload = useCallback(async (): Promise<void> => {
    if (!pendingFiles.length) {
      return;
    }
    setBusy(true);
    try {
      const uploadedSources: SourceSummary[] = [];
      const uploadedTasks: TaskSummary[] = [];
      for (const file of pendingFiles) {
        const response = await uploadSource(file, uploadGuidance, []);
        uploadedSources.push(response.source);
        if (response.task) {
          uploadedTasks.push(response.task);
        }
        setStatus(`Uploaded ${response.source.display_title} as task ${response.task?.id.slice(0, 8) ?? "complete"}.`);
      }
      setPendingFiles([]);
      setSplitPreview(null);
      setSources((current) => {
        const byId = new Map(current.map((source) => [source.id, source]));
        for (const source of uploadedSources) {
          byId.set(source.id, source);
        }
        return Array.from(byId.values());
      });
      setTasks((current) => {
        const byId = new Map(current.map((task) => [task.id, task]));
        for (const task of uploadedTasks) {
          byId.set(task.id, task);
        }
        return Array.from(byId.values());
      });
      setStatus("Indexing queued. Files will appear searchable when ready.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }, [pendingFiles, uploadGuidance]);

  const openSource = useCallback(async (sourceId: string): Promise<void> => {
    setStatus("Loading source preview.");
    try {
      const detail = await getSource(sourceId);
      setSelectedSource(detail);
      setStatus(`Previewing ${detail.display_title}.`);
    } catch (error) {
      setSelectedSource(null);
      setStatus(error instanceof Error ? error.message : "Could not load source detail.");
    }
  }, []);

  const toggleSourceChatSelection = useCallback((sourceId: string): void => {
    setSelectedSourceIds((current) =>
      current.includes(sourceId) ? current.filter((id) => id !== sourceId) : [...current, sourceId],
    );
  }, []);

  const selectVisibleSourcesForChat = useCallback((): void => {
    setSelectedSourceIds((current) => {
      const next = new Set(current);
      for (const source of visibleSources) {
        next.add(source.id);
      }
      return Array.from(next);
    });
  }, [visibleSources]);

  const clearChatSourceSelection = useCallback((): void => {
    setSelectedSourceIds([]);
  }, []);

  const toggleExplorerTag = useCallback((tagId: string): void => {
    setSelectedExplorerTagIds((current) =>
      current.includes(tagId) ? current.filter((id) => id !== tagId) : [...current, tagId],
    );
  }, []);

  const clearExplorerFilters = useCallback((): void => {
    setSourceQuery("");
    setSelectedExplorerTagIds([]);
  }, []);

  const createExplorerTag = useCallback(async (): Promise<void> => {
    const name = newTagName.trim();
    if (!name) {
      return;
    }
    setBusy(true);
    try {
      const response = await createTag({ name });
      setTags(await listTags());
      setNewTagName("");
      setStatus(`Tag ready: ${response.tag?.name ?? name}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Tag create failed.");
    } finally {
      setBusy(false);
    }
  }, [newTagName]);

  const resplitSelectedSource = useCallback(async (): Promise<void> => {
    if (!selectedSource) {
      return;
    }
    setBusy(true);
    try {
      const response = await resplitSource(selectedSource.id, { user_guidance: uploadGuidance });
      const [sourceList, detail, taskList] = await Promise.all([
        loadAllSources(),
        getSource(selectedSource.id),
        listTasks(),
      ]);
      setSources(sourceList.sources);
      setSelectedSource(detail);
      setTasks(taskList.tasks);
      setStatus(`Queued re-split for ${response.source.display_title} as task ${response.task?.id.slice(0, 8) ?? "pending"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Re-split failed.");
    } finally {
      setBusy(false);
    }
  }, [selectedSource, uploadGuidance]);

  const saveSelectedSourceTags = useCallback(async (): Promise<void> => {
    if (!selectedSource) {
      return;
    }
    setBusy(true);
    try {
      const response = await updateSourceTags(selectedSource.id, { tag_ids: selectedSourceTagDraftIds });
      const [sourceList, detail, tagList, taskList] = await Promise.all([
        loadAllSources(),
        getSource(selectedSource.id),
        listTags(),
        listTasks(),
      ]);
      setSources(sourceList.sources);
      setSelectedSource(detail);
      setTags(tagList);
      setTasks(taskList.tasks);
      setStatus(`Queued tag reindex for ${response.source.display_title} as task ${response.task?.id.slice(0, 8) ?? "pending"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Tag update failed.");
    } finally {
      setBusy(false);
    }
  }, [selectedSource, selectedSourceTagDraftIds]);

  const toggleSelectedSourceTagDraft = useCallback((tagId: string): void => {
    setSelectedSourceTagDraftIds((current) =>
      current.includes(tagId) ? current.filter((id) => id !== tagId) : [...current, tagId],
    );
  }, []);

  const removeSource = useCallback(
    async (sourceId: string): Promise<void> => {
      setBusy(true);
      try {
        await deleteSource(sourceId);
        setSelectedSourceIds((current) => current.filter((id) => id !== sourceId));
        setSelectedSource((current) => (current?.id === sourceId ? null : current));
        await refreshAll();
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Delete failed.");
      } finally {
        setBusy(false);
      }
    },
    [refreshAll],
  );
  const openSourceFromExplorer = useCallback((sourceId: string): void => void openSource(sourceId), [openSource]);
  const deleteSourceFromExplorer = useCallback((sourceId: string): void => void removeSource(sourceId), [removeSource]);
  const previewSplitFromExplorer = useCallback((): void => void previewPendingSplit(), [previewPendingSplit]);
  const uploadFromExplorer = useCallback((): void => void handleUpload(), [handleUpload]);
  const createTagFromExplorer = useCallback((): void => void createExplorerTag(), [createExplorerTag]);
  const refreshWorkspace = useCallback((): void => void refreshAll(), [refreshAll]);
  const saveTagsFromExplorer = useCallback((): void => void saveSelectedSourceTags(), [saveSelectedSourceTags]);
  const resplitFromExplorer = useCallback((): void => void resplitSelectedSource(), [resplitSelectedSource]);

  const selectedSourceTagChanged = selectedSource
    ? !sameStringSet(selectedSourceTagDraftIds, selectedSource.tags.map((tag) => tag.id))
    : false;

  return (
    <main className="app-shell">
      <WorkspaceHeader
        authMode={authMode}
        busy={busy}
        status={status}
        tasks={tasks}
        user={user}
        onRefresh={refreshWorkspace}
      />

      <section className="workspace-grid" aria-label="Indexed file workspace">
        <SourceExplorer
          activeSourceId={activeSourceId}
          busy={busy}
          newTagName={newTagName}
          pendingFiles={pendingFiles}
          selectedExplorerTagIdSet={selectedExplorerTagIdSet}
          selectedSourceIdSet={selectedSourceIdSet}
          selectedSourceCount={selectedSourceIds.length}
          selectedSourceSummaries={selectedSourceSummaries}
          selectedSource={selectedSource}
          selectedSourceTagChanged={selectedSourceTagChanged}
          selectedSourceTagDraftIdSet={selectedSourceTagDraftIdSet}
          sourceQuery={sourceQuery}
          sources={visibleSources}
          visibleSourceCount={filteredSources.length}
          splitPreview={splitPreview}
          tags={tags}
          totalSourceCount={sources.length}
          uploadGuidance={uploadGuidance}
          onChooseFiles={chooseFiles}
          onClearChatSelection={clearChatSourceSelection}
          onClearExplorerFilters={clearExplorerFilters}
          onCreateTag={createTagFromExplorer}
          onDeleteSource={deleteSourceFromExplorer}
          onNewTagNameChange={setNewTagName}
          onOpenSource={openSourceFromExplorer}
          onPreviewSplit={previewSplitFromExplorer}
          onResplit={resplitFromExplorer}
          onSaveTags={saveTagsFromExplorer}
          onSelectVisibleSources={selectVisibleSourcesForChat}
          onSourceQueryChange={setSourceQuery}
          onTagToggle={toggleSelectedSourceTagDraft}
          onToggleExplorerTag={toggleExplorerTag}
          onToggleSourceSelection={toggleSourceChatSelection}
          onUpload={uploadFromExplorer}
          onUploadGuidanceChange={setUploadGuidance}
        />

        <aside className="chat-panel" aria-label="AI file assistant">
          <ChatPane selectedSourceIds={selectedSourceIds} onClientTool={async () => ({ ok: false })} />
        </aside>
      </section>
    </main>
  );
}

const FileExplorer = memo(function FileExplorer({
  breadcrumbs,
  busy,
  currentFolder,
  entries,
  focusedEntryId,
  newTagName,
  pendingFiles,
  researchAutoIngest,
  researchMaxDepth,
  researchMaxSources,
  researchQuery,
  researchResult,
  researchSeedType,
  searching,
  selectedEntryIdSet,
  selectedExplorerTagIdSet,
  selectedFileEntries,
  selectedSource,
  selectedSourceIdSet,
  selectedSourceTagChanged,
  selectedSourceTagDraftIdSet,
  sourceQuery,
  splitPreview,
  tags,
  uploadGuidance,
  onChooseEntries,
  onChooseFiles,
  onClearFilters,
  onCreateFolder,
  onCreateTag,
  onDeleteSelected,
  onDropEntries,
  onGoToFolder,
  onNewTagNameChange,
  onOpenEntry,
  onPreviewSplit,
  onResearchAutoIngestChange,
  onResearchBuild,
  onResearchCandidateStatus,
  onResearchIngestApproved,
  onResearchMaxDepthChange,
  onResearchMaxSourcesChange,
  onResearchQueryChange,
  onResearchSeedTypeChange,
  onRenameSelected,
  onResplit,
  onSaveTags,
  onSourceQueryChange,
  onTagToggle,
  onToggleExplorerTag,
  onUpload,
  onUploadGuidanceChange,
}: {
  breadcrumbs: FilesystemBreadcrumb[];
  busy: boolean;
  currentFolder: FilesystemEntrySummary | null;
  entries: FilesystemEntrySummary[];
  focusedEntryId: string | null;
  newTagName: string;
  pendingFiles: File[];
  researchAutoIngest: boolean;
  researchMaxDepth: number;
  researchMaxSources: number;
  researchQuery: string;
  researchResult: ResearchLibraryBuildResponse | null;
  researchSeedType: ResearchBuilderSeedKind;
  searching: boolean;
  selectedEntryIdSet: Set<string>;
  selectedExplorerTagIdSet: Set<string>;
  selectedFileEntries: FilesystemEntrySummary[];
  selectedSource: SourceDetail | null;
  selectedSourceIdSet: Set<string>;
  selectedSourceTagChanged: boolean;
  selectedSourceTagDraftIdSet: Set<string>;
  sourceQuery: string;
  splitPreview: SplitPreviewResponse | null;
  tags: TagSummary[];
  uploadGuidance: string;
  onChooseEntries: (entry: FilesystemEntrySummary, event: ReactMouseEvent) => void;
  onChooseFiles: (files: FileList | null) => void;
  onClearFilters: () => void;
  onCreateFolder: () => void;
  onCreateTag: () => void;
  onDeleteSelected: () => void;
  onDropEntries: (entryIds: string[], folderId: string) => void;
  onGoToFolder: (folderId: string | null) => void;
  onNewTagNameChange: (value: string) => void;
  onOpenEntry: (entry: FilesystemEntrySummary) => void;
  onPreviewSplit: () => void;
  onResearchAutoIngestChange: (value: boolean) => void;
  onResearchBuild: () => void;
  onResearchCandidateStatus: (candidateId: string, nextStatus: "approved" | "rejected" | "pending") => void;
  onResearchIngestApproved: () => void;
  onResearchMaxDepthChange: (value: number) => void;
  onResearchMaxSourcesChange: (value: number) => void;
  onResearchQueryChange: (value: string) => void;
  onResearchSeedTypeChange: (value: ResearchBuilderSeedKind) => void;
  onRenameSelected: () => void;
  onResplit: () => void;
  onSaveTags: () => void;
  onSourceQueryChange: (value: string) => void;
  onTagToggle: (tagId: string) => void;
  onToggleExplorerTag: (tagId: string) => void;
  onUpload: () => void;
  onUploadGuidanceChange: (value: string) => void;
}) {
  const selectedCount = selectedEntryIdSet.size;
  const selectedFileLabel =
    selectedFileEntries.length === 1 ? "1 indexed file selected" : `${selectedFileEntries.length} indexed files selected`;
  const dragEntryIds = useMemo(() => Array.from(selectedEntryIdSet), [selectedEntryIdSet]);
  return (
    <aside className="explorer-pane filesystem-pane" aria-label="Files">
      <div className="explorer-commandbar">
        <button type="button" className="secondary-button" onClick={() => onGoToFolder(currentFolder?.parent_id ?? null)} disabled={busy || !currentFolder?.parent_id}>
          Up
        </button>
        <button type="button" className="secondary-button" onClick={onCreateFolder} disabled={busy || searching}>
          New Folder
        </button>
        <button type="button" className="secondary-button" onClick={onRenameSelected} disabled={busy || selectedCount !== 1}>
          Rename
        </button>
        <button type="button" className="secondary-button danger-button" onClick={onDeleteSelected} disabled={busy || !selectedCount}>
          Delete
        </button>
        <div className="explorer-selection-summary" title={selectedFileEntries.map((entry) => entry.path).join(", ")}>
          <strong>{selectedFileLabel}</strong>
          <span>{selectedFileEntries.slice(0, 3).map((entry) => entry.name).join(", ") || "No ready files"}</span>
        </div>
      </div>

      <div className="explorer-filterbar">
        <label className="filesystem-query">
          <span>Query</span>
          <input
            value={sourceQuery}
            onChange={(event) => onSourceQueryChange(event.currentTarget.value)}
            placeholder="Find by name, path, tag, or indexed text"
          />
        </label>
        <div className="tag-strip filesystem-tags" aria-label="Tags">
          {tags.map((tag) => (
            <button
              key={tag.id}
              type="button"
              aria-pressed={selectedExplorerTagIdSet.has(tag.id)}
              className={selectedExplorerTagIdSet.has(tag.id) ? "tag-chip selected" : "tag-chip"}
              onClick={() => onToggleExplorerTag(tag.id)}
            >
              {tag.name}
            </button>
          ))}
          {!tags.length ? <span>No tag filters</span> : null}
        </div>
        <button type="button" className="secondary-button" onClick={onClearFilters} disabled={!searching}>
          Clear
        </button>
        <div className="tag-create-row compact-tag-create">
          <input
            aria-label="New tag name"
            value={newTagName}
            onChange={(event) => onNewTagNameChange(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                onCreateTag();
              }
            }}
            placeholder="New tag"
          />
          <button type="button" className="secondary-button" onClick={onCreateTag} disabled={busy || !newTagName.trim()}>
            Add
          </button>
        </div>
      </div>

      <nav className="breadcrumb-row" aria-label="Folder path">
        {breadcrumbs.map((crumb, index) => {
          const current = index === breadcrumbs.length - 1;
          return (
            <button
              key={crumb.id}
              type="button"
              className={current ? "breadcrumb-button current" : "breadcrumb-button"}
              onClick={() => {
                if (!current) {
                  onGoToFolder(crumb.id);
                }
              }}
              disabled={current}
            >
              {crumb.name || "Files"}
            </button>
          );
        })}
      </nav>

      <div className={selectedSource ? "filesystem-layout has-preview" : "filesystem-layout"}>
        <section className="file-browser" aria-label="File list">
          <div className="file-list-header">
            <span>Name</span>
            <span>Tags</span>
            <span>Status</span>
            <span>Size</span>
            <span>Modified</span>
          </div>
          <div className="file-rows" role="treegrid" aria-label="Files and folders">
            {entries.map((entry) => (
              <FileEntryRow
                key={entry.id}
                entry={entry}
                dragEntryIds={dragEntryIds.length ? dragEntryIds : [entry.id]}
                focused={focusedEntryId === entry.id}
                selected={selectedEntryIdSet.has(entry.id) || (entry.source_id ? selectedSourceIdSet.has(entry.source_id) : false)}
                onChoose={onChooseEntries}
                onDropEntries={onDropEntries}
                onOpen={onOpenEntry}
              />
            ))}
            {!entries.length ? <div className="empty-file-list">{searching ? "No matching entries." : "Folder is empty."}</div> : null}
          </div>

          <section className="upload-strip filesystem-upload" aria-label="Index files">
            <label className="file-picker">
              <input type="file" multiple onChange={(event) => onChooseFiles(event.currentTarget.files)} />
              <span>{pendingFiles.length ? `${pendingFiles.length} staged` : "Add files"}</span>
            </label>
            <textarea
              className="compact-textarea"
              value={uploadGuidance}
              onChange={(event) => onUploadGuidanceChange(event.currentTarget.value)}
              placeholder="Optional split notes; normal indexing stores the source file as-is"
            />
            <div className="button-row">
              <button type="button" className="secondary-button" onClick={onPreviewSplit} disabled={busy || !pendingFiles.length}>
                Preview split
              </button>
              <button type="button" onClick={onUpload} disabled={busy || !pendingFiles.length}>
                Index
              </button>
            </div>
            <p className="upload-hint">Index files first. Split preview is optional inspection tooling.</p>
            {pendingFiles.length ? <p className="pending-file-list">{pendingFiles.map((file) => file.name).join(", ")}</p> : null}
            {splitPreview ? (
              <div className="split-preview-summary">
                <strong>{splitPreview.split.chunks.length} optional split records</strong>
                <span>{splitPreview.split.tags.join(", ") || "no tags"}</span>
              </div>
            ) : null}
          </section>

          <ResearchBuilderPanel
            autoIngest={researchAutoIngest}
            busy={busy}
            maxDepth={researchMaxDepth}
            maxSources={researchMaxSources}
            query={researchQuery}
            result={researchResult}
            seedType={researchSeedType}
            onAutoIngestChange={onResearchAutoIngestChange}
            onBuild={onResearchBuild}
            onCandidateStatus={onResearchCandidateStatus}
            onIngestApproved={onResearchIngestApproved}
            onMaxDepthChange={onResearchMaxDepthChange}
            onMaxSourcesChange={onResearchMaxSourcesChange}
            onQueryChange={onResearchQueryChange}
            onSeedTypeChange={onResearchSeedTypeChange}
          />
        </section>

        {selectedSource ? (
          <div className="explorer-detail" aria-label="File detail">
            <SourcePreview
              busy={busy}
              selectedSource={selectedSource}
              selectedSourceTagChanged={selectedSourceTagChanged}
              selectedSourceTagDraftIdSet={selectedSourceTagDraftIdSet}
              tags={tags}
              uploadGuidance={uploadGuidance}
              onSaveTags={onSaveTags}
              onTagToggle={onTagToggle}
              onUploadGuidanceChange={onUploadGuidanceChange}
              onResplit={onResplit}
            />
          </div>
        ) : null}
      </div>
    </aside>
  );
});

const ResearchBuilderPanel = memo(function ResearchBuilderPanel({
  autoIngest,
  busy,
  maxDepth,
  maxSources,
  query,
  result,
  seedType,
  onAutoIngestChange,
  onBuild,
  onCandidateStatus,
  onIngestApproved,
  onMaxDepthChange,
  onMaxSourcesChange,
  onQueryChange,
  onSeedTypeChange,
}: {
  autoIngest: boolean;
  busy: boolean;
  maxDepth: number;
  maxSources: number;
  query: string;
  result: ResearchLibraryBuildResponse | null;
  seedType: ResearchBuilderSeedKind;
  onAutoIngestChange: (value: boolean) => void;
  onBuild: () => void;
  onCandidateStatus: (candidateId: string, nextStatus: "approved" | "rejected" | "pending") => void;
  onIngestApproved: () => void;
  onMaxDepthChange: (value: number) => void;
  onMaxSourcesChange: (value: number) => void;
  onQueryChange: (value: string) => void;
  onSeedTypeChange: (value: ResearchBuilderSeedKind) => void;
}) {
  const candidates = result?.candidates ?? [];
  const approvedCount = candidates.filter((candidate) => candidate.status === "approved").length;
  const pendingCount = candidates.filter((candidate) => candidate.status === "pending").length;
  const ingestedCount = Math.max(
    candidates.filter((candidate) => candidate.status === "ingested").length,
    result?.ingested.length ?? 0,
  );
  const visibleCandidates = candidates.slice(0, 6);
  const hiddenCandidateCount = Math.max(0, candidates.length - visibleCandidates.length);
  return (
    <section className="research-builder-strip" aria-label="Research library builder">
      <div className="research-builder-controls">
        <label className="research-query-field">
          <span>Research</span>
          <input
            value={query}
            onChange={(event) => onQueryChange(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && query.trim()) {
                event.preventDefault();
                onBuild();
              }
            }}
            placeholder="Topic or paper title"
          />
        </label>
        <label>
          <span>Seed</span>
          <select
            value={seedType}
            onChange={(event) => onSeedTypeChange(event.currentTarget.value as ResearchBuilderSeedKind)}
          >
            {RESEARCH_SEED_CHOICES.map((choice) => (
              <option key={choice.id} value={choice.id}>
                {choice.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Max</span>
          <input
            type="number"
            min={1}
            max={50}
            value={maxSources}
            onChange={(event) => {
              const nextValue = Number(event.currentTarget.value);
              onMaxSourcesChange(Number.isFinite(nextValue) ? clamp(nextValue, 1, 50) : 1);
            }}
          />
        </label>
        <label>
          <span>Depth</span>
          <input
            type="number"
            min={0}
            max={4}
            value={maxDepth}
            onChange={(event) => {
              const nextValue = Number(event.currentTarget.value);
              onMaxDepthChange(Number.isFinite(nextValue) ? clamp(nextValue, 0, 4) : 0);
            }}
          />
        </label>
        <label className="research-toggle">
          <input
            type="checkbox"
            checked={autoIngest}
            onChange={(event) => onAutoIngestChange(event.currentTarget.checked)}
          />
          <span>Auto ingest</span>
        </label>
        <button type="button" onClick={onBuild} disabled={busy || !query.trim()}>
          Build
        </button>
      </div>

      {result ? (
        <div className="research-builder-results">
          <div className="research-result-summary">
            <strong>
              {candidates.length} candidate{candidates.length === 1 ? "" : "s"}
            </strong>
            <span>
              {result.task.status} | {pendingCount} pending | {approvedCount} approved | {ingestedCount} ingested | {result.duplicate_count} duplicate
              {result.duplicate_count === 1 ? "" : "s"}
            </span>
            <button type="button" className="secondary-button" onClick={onIngestApproved} disabled={busy || approvedCount === 0}>
              Ingest approved
            </button>
          </div>
          <div className="research-candidate-list" aria-label="Research candidates">
            {visibleCandidates.map((candidate) => {
              const locked = ["ingested", "ingesting", "failed"].includes(candidate.status);
              const metaParts = [
                candidate.source_type.toUpperCase(),
                candidate.published_at?.slice(0, 10),
                candidate.authors.slice(0, 2).join(", "),
              ].filter(Boolean);
              return (
                <article key={candidate.id} className="research-candidate-row">
                  <div>
                    <strong>{candidate.title}</strong>
                    <span>{metaParts.join(" | ") || candidate.normalized_url || candidate.url || "Candidate"}</span>
                    {candidate.summary || candidate.description ? <p>{candidate.summary ?? candidate.description}</p> : null}
                    {candidate.suggested_tags.length ? <small>{candidate.suggested_tags.slice(0, 4).join(", ")}</small> : null}
                  </div>
                  <div className="research-candidate-actions">
                    <span className={`status-badge status-${candidate.status}`}>{candidate.status}</span>
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => onCandidateStatus(candidate.id, "approved")}
                      disabled={busy || locked || candidate.status === "approved"}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      className="secondary-button danger-button"
                      onClick={() => onCandidateStatus(candidate.id, "rejected")}
                      disabled={busy || locked || candidate.status === "rejected"}
                    >
                      Reject
                    </button>
                  </div>
                </article>
              );
            })}
            {hiddenCandidateCount ? <p className="research-candidate-more">+{hiddenCandidateCount} more candidates</p> : null}
            {!candidates.length ? <p className="research-candidate-more">No candidates returned.</p> : null}
          </div>
        </div>
      ) : null}
    </section>
  );
});

const FileEntryRow = memo(function FileEntryRow({
  dragEntryIds,
  entry,
  focused,
  selected,
  onChoose,
  onDropEntries,
  onOpen,
}: {
  dragEntryIds: string[];
  entry: FilesystemEntrySummary;
  focused: boolean;
  selected: boolean;
  onChoose: (entry: FilesystemEntrySummary, event: ReactMouseEvent) => void;
  onDropEntries: (entryIds: string[], folderId: string) => void;
  onOpen: (entry: FilesystemEntrySummary) => void;
}) {
  const rowClassName = [
    selected ? "selected-file-row" : "",
    focused ? "active-file-row" : "",
    entry.kind === "folder" ? "folder-row" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div
      role="row"
      tabIndex={0}
      className={rowClassName || undefined}
      draggable
      onClick={(event) => onChoose(entry, event)}
      onDoubleClick={() => onOpen(entry)}
      onDragStart={(event) => {
        event.dataTransfer.setData("application/x-entry-ids", JSON.stringify(dragEntryIds.includes(entry.id) ? dragEntryIds : [entry.id]));
        event.dataTransfer.effectAllowed = "move";
      }}
      onDragOver={(event) => {
        if (entry.kind === "folder") {
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
        }
      }}
      onDrop={(event) => {
        if (entry.kind !== "folder") {
          return;
        }
        event.preventDefault();
        const payload = event.dataTransfer.getData("application/x-entry-ids");
        const parsed = safeJsonStringArray(payload);
        if (parsed.length) {
          onDropEntries(parsed, entry.id);
        }
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          onOpen(entry);
        }
      }}
    >
      <span role="cell" className="filesystem-name-cell">
        <span className={entry.kind === "folder" ? "entry-icon folder-icon" : "entry-icon file-icon"}>{entry.kind === "folder" ? "" : entryTypeLabel(entry)}</span>
        <span>
          <strong>{entry.name || "Files"}</strong>
          <small>{entry.path}</small>
        </span>
      </span>
      <span role="cell" className="file-tag-list">
        {entry.tags.slice(0, 2).map((tag) => (
          <span key={tag.id}>{tag.name}</span>
        ))}
        {entry.tags.length > 2 ? <span>+{entry.tags.length - 2}</span> : null}
        {entry.kind === "file" && !entry.tags.length ? <span>untagged</span> : null}
      </span>
      <span role="cell">{entry.status ? <span className={`status-badge status-${entry.status}`}>{entry.status}</span> : ""}</span>
      <span role="cell" className="muted-cell">{entry.byte_size === null ? "" : formatBytes(entry.byte_size)}</span>
      <span role="cell" className="muted-cell">{formatDate(entry.updated_at)}</span>
    </div>
  );
});

function WorkspaceHeader({
  authMode,
  busy,
  status,
  tasks,
  user,
  onRefresh,
}: {
  authMode: "clerk" | "local-dev";
  busy: boolean;
  status: string;
  tasks: TaskSummary[];
  user: AuthUser | null;
  onRefresh: () => void;
}) {
  const latestTask = tasks[0] ?? null;
  return (
    <header className="app-bar">
      <div className="app-identity">
        <strong>AI Files</strong>
        <span>{authMode === "local-dev" ? "Local dev auth" : "Clerk auth"}</span>
      </div>
      <div className="app-status" title={status}>
        <span>{user?.display_name ?? "Connecting"}</span>
        <strong>{status}</strong>
      </div>
      <div className="task-summary" title={latestTask ? `${latestTask.kind}: ${latestTask.status}` : "No tasks yet"}>
        <span>Recent Tasks</span>
        <strong>{latestTask ? `${latestTask.kind} | ${latestTask.status}` : "No tasks yet"}</strong>
      </div>
      <button type="button" className="secondary-button" onClick={onRefresh} disabled={busy}>
        Refresh
      </button>
    </header>
  );
}

const SourceExplorer = memo(function SourceExplorer({
  activeSourceId,
  busy,
  newTagName,
  pendingFiles,
  selectedExplorerTagIdSet,
  selectedSourceIdSet,
  selectedSourceCount,
  selectedSourceSummaries,
  selectedSource,
  selectedSourceTagChanged,
  selectedSourceTagDraftIdSet,
  sourceQuery,
  sources,
  visibleSourceCount,
  splitPreview,
  tags,
  totalSourceCount,
  uploadGuidance,
  onChooseFiles,
  onClearChatSelection,
  onClearExplorerFilters,
  onCreateTag,
  onDeleteSource,
  onNewTagNameChange,
  onOpenSource,
  onPreviewSplit,
  onResplit,
  onSaveTags,
  onSelectVisibleSources,
  onSourceQueryChange,
  onTagToggle,
  onToggleExplorerTag,
  onToggleSourceSelection,
  onUpload,
  onUploadGuidanceChange,
}: {
  activeSourceId: string | null;
  busy: boolean;
  newTagName: string;
  pendingFiles: File[];
  selectedExplorerTagIdSet: Set<string>;
  selectedSourceIdSet: Set<string>;
  selectedSourceCount: number;
  selectedSourceSummaries: SourceSummary[];
  selectedSource: SourceDetail | null;
  selectedSourceTagChanged: boolean;
  selectedSourceTagDraftIdSet: Set<string>;
  sourceQuery: string;
  sources: SourceSummary[];
  visibleSourceCount: number;
  splitPreview: SplitPreviewResponse | null;
  tags: TagSummary[];
  totalSourceCount: number;
  uploadGuidance: string;
  onChooseFiles: (files: FileList | null) => void;
  onClearChatSelection: () => void;
  onClearExplorerFilters: () => void;
  onCreateTag: () => void;
  onDeleteSource: (sourceId: string) => void;
  onNewTagNameChange: (value: string) => void;
  onOpenSource: (sourceId: string) => void;
  onPreviewSplit: () => void;
  onResplit: () => void;
  onSaveTags: () => void;
  onSelectVisibleSources: () => void;
  onSourceQueryChange: (value: string) => void;
  onTagToggle: (tagId: string) => void;
  onToggleExplorerTag: (tagId: string) => void;
  onToggleSourceSelection: (sourceId: string) => void;
  onUpload: () => void;
  onUploadGuidanceChange: (value: string) => void;
}) {
  const filtering = Boolean(sourceQuery.trim() || selectedExplorerTagIdSet.size);
  const chatScopeLabel = selectedSourceCount === 1 ? "1 file for chat" : `${selectedSourceCount} files for chat`;
  const selectedScopeNames = selectedSourceSummaries.map((source) => source.display_title);
  return (
    <aside className="explorer-pane" aria-label="Files">
      <div className="pane-header">
        <div>
          <p className="eyebrow">Library</p>
          <h1>Files</h1>
        </div>
        <span className="count-pill">
          {sources.length}
          {visibleSourceCount === totalSourceCount
            ? ""
            : sources.length === visibleSourceCount
              ? ` / ${totalSourceCount}`
              : ` / ${visibleSourceCount} / ${totalSourceCount}`}
        </span>
      </div>

      <section className="explorer-controls" aria-label="Filter files">
        <label className="query-field">
          <span>Query</span>
          <input
            value={sourceQuery}
            onChange={(event) => onSourceQueryChange(event.currentTarget.value)}
            placeholder="Search files, tags, type, status"
          />
        </label>
        <div className="explorer-filter-row">
          <span>Tags</span>
          <button type="button" className="link-button" onClick={onClearExplorerFilters} disabled={!filtering}>
            Clear
          </button>
        </div>
        <div className="tag-strip" aria-label="Tags">
          {tags.map((tag) => (
            <button
              key={tag.id}
              type="button"
              aria-pressed={selectedExplorerTagIdSet.has(tag.id)}
              className={selectedExplorerTagIdSet.has(tag.id) ? "tag-chip selected" : "tag-chip"}
              onClick={() => onToggleExplorerTag(tag.id)}
            >
              {tag.name}
            </button>
          ))}
          {!tags.length ? <span>No tags</span> : null}
        </div>
        <div className="tag-create-row">
          <input
            aria-label="New tag name"
            value={newTagName}
            onChange={(event) => onNewTagNameChange(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                onCreateTag();
              }
            }}
            placeholder="New tag"
          />
          <button type="button" className="secondary-button" onClick={onCreateTag} disabled={busy || !newTagName.trim()}>
            Add
          </button>
        </div>
      </section>

      <section className="chat-scope-strip" aria-label="Chat file scope">
        <div>
          <strong>{chatScopeLabel}</strong>
          <span>
            {selectedScopeNames.length ? selectedScopeNames.slice(0, 3).join(", ") : "Selected files guide ChatKit."}
            {selectedScopeNames.length > 3 ? `, +${selectedScopeNames.length - 3}` : ""}
          </span>
        </div>
        <div className="button-row">
          <button type="button" className="secondary-button" onClick={onSelectVisibleSources} disabled={!sources.length}>
            Select visible
          </button>
          <button type="button" className="secondary-button" onClick={onClearChatSelection} disabled={!selectedSourceCount}>
            Clear
          </button>
        </div>
      </section>

      <div className="explorer-body">
        <div className="file-list-column">
          <div className="file-table-wrap">
            <table className="file-table">
              <thead>
                <tr>
                  <th aria-label="Chat scope" />
                  <th>Name</th>
                  <th>Tags</th>
                  <th>Info</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {sources.map((source) => (
                  <SourceRow
                    key={source.id}
                    active={activeSourceId === source.id}
                    selected={selectedSourceIdSet.has(source.id)}
                    source={source}
                    onDelete={onDeleteSource}
                    onOpen={onOpenSource}
                    onToggleSelection={onToggleSourceSelection}
                  />
                ))}
                {!sources.length ? (
                  <tr>
                    <td colSpan={5} className="empty-cell">
                      {filtering ? "No files match the current query or tags." : "No files yet."}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
            {visibleSourceCount > sources.length ? (
              <p className="list-window-note">
                Showing the first {sources.length} matching files. Narrow the query or tags to focus the explorer.
              </p>
            ) : null}
          </div>

          <section className="upload-strip" aria-label="Upload source">
            <div className="upload-heading">
              <strong>Add files</strong>
              <label className="file-picker">
                <input type="file" multiple onChange={(event) => onChooseFiles(event.currentTarget.files)} />
                <span>{pendingFiles.length ? `${pendingFiles.length} selected` : "Choose files"}</span>
              </label>
            </div>
            <textarea
              className="compact-textarea"
              value={uploadGuidance}
              onChange={(event) => onUploadGuidanceChange(event.currentTarget.value)}
              placeholder="Optional split notes; normal indexing stores the source file as-is"
            />
            <div className="button-row">
              <button type="button" className="secondary-button" onClick={onPreviewSplit} disabled={busy || !pendingFiles.length}>
                Preview
              </button>
              <button type="button" onClick={onUpload} disabled={busy || !pendingFiles.length}>
                Upload
              </button>
            </div>
            {pendingFiles.length ? <p className="pending-file-list">{pendingFiles.map((file) => file.name).join(", ")}</p> : null}
            {splitPreview ? (
              <div className="split-preview-summary">
                <strong>{splitPreview.split.chunks.length} proposed chunks</strong>
                <span>{splitPreview.split.tags.join(", ") || "no tags"}</span>
              </div>
            ) : null}
          </section>
        </div>

        <div className="explorer-detail" aria-label="File detail">
          <SourcePreview
            busy={busy}
            selectedSource={selectedSource}
            selectedSourceTagChanged={selectedSourceTagChanged}
            selectedSourceTagDraftIdSet={selectedSourceTagDraftIdSet}
            tags={tags}
            uploadGuidance={uploadGuidance}
            onSaveTags={onSaveTags}
            onTagToggle={onTagToggle}
            onUploadGuidanceChange={onUploadGuidanceChange}
            onResplit={onResplit}
          />
        </div>
      </div>
    </aside>
  );
});

const SourceRow = memo(function SourceRow({
  active,
  selected,
  source,
  onDelete,
  onOpen,
  onToggleSelection,
}: {
  active: boolean;
  selected: boolean;
  source: SourceSummary;
  onDelete: (sourceId: string) => void;
  onOpen: (sourceId: string) => void;
  onToggleSelection: (sourceId: string) => void;
}) {
  const rowClassName = [active ? "active-file-row" : "", selected ? "selected-file-row" : ""].filter(Boolean).join(" ");
  return (
    <tr className={rowClassName || undefined} onClick={() => onOpen(source.id)}>
      <td>
        <input
          aria-label={`Select ${source.display_title} for chat`}
          checked={selected}
          className="file-select-checkbox"
          onChange={() => onToggleSelection(source.id)}
          onClick={(event) => event.stopPropagation()}
          type="checkbox"
        />
      </td>
      <td>
        <button
          type="button"
          className="file-name-button"
          onClick={(event) => {
            event.stopPropagation();
            onOpen(source.id);
          }}
        >
          <span className="file-type-badge">{sourceExtension(source)}</span>
          <span>
            <strong>{source.display_title}</strong>
            <small>{source.original_filename}</small>
          </span>
        </button>
      </td>
      <td>
        <div className="file-tag-list">
          {source.tags.slice(0, 2).map((tag) => (
            <span key={tag.id}>{tag.name}</span>
          ))}
          {source.tags.length > 2 ? <span>+{source.tags.length - 2}</span> : null}
          {!source.tags.length ? <span>untagged</span> : null}
        </div>
      </td>
      <td>
        <span className={`status-badge status-${source.status}`}>{source.status}</span>
        <small className="file-size">
          {formatBytes(source.byte_size)} | {formatDate(source.created_at)}
        </small>
      </td>
      <td>
        <button
          type="button"
          className="icon-text-button danger-button"
          onClick={(event) => {
            event.stopPropagation();
            onDelete(source.id);
          }}
        >
          Delete
        </button>
      </td>
    </tr>
  );
});

function SourcePreview({
  busy,
  selectedSource,
  selectedSourceTagChanged,
  selectedSourceTagDraftIdSet,
  tags,
  uploadGuidance,
  onSaveTags,
  onTagToggle,
  onUploadGuidanceChange,
  onResplit,
}: {
  busy: boolean;
  selectedSource: SourceDetail | null;
  selectedSourceTagChanged: boolean;
  selectedSourceTagDraftIdSet: Set<string>;
  tags: TagSummary[];
  uploadGuidance: string;
  onSaveTags: () => void;
  onTagToggle: (tagId: string) => void;
  onUploadGuidanceChange: (value: string) => void;
  onResplit: () => void;
}) {
  const [previewResource, setPreviewResource] = useState<PreviewResource>({ state: "idle" });
  const previewSourceId = selectedSource?.id ?? null;
  const previewSourceKind = selectedSource?.source_kind ?? null;
  const previewMediaType = selectedSource?.media_type ?? null;

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;

    async function loadPreview(source: SourceDetail): Promise<void> {
      if (!canPreviewSource(source)) {
        setPreviewResource({ state: "idle" });
        return;
      }
      setPreviewResource({ state: "loading" });
      try {
        const response = await readSourceContentBlob(source.id);
        const mediaType = response.mediaType ?? source.media_type;
        if (isTextPreview(source, mediaType)) {
          const rawText = await response.blob.text();
          if (!cancelled) {
            setPreviewResource({
              state: "text",
              mediaType,
              text: rawText.slice(0, TEXT_PREVIEW_LIMIT),
              truncated: rawText.length > TEXT_PREVIEW_LIMIT,
            });
          }
          return;
        }
        const nextObjectUrl = URL.createObjectURL(response.blob);
        if (cancelled) {
          URL.revokeObjectURL(nextObjectUrl);
          return;
        }
        objectUrl = nextObjectUrl;
        setPreviewResource({ state: "file", url: objectUrl, mediaType });
      } catch (error) {
        if (!cancelled) {
          setPreviewResource({ state: "error", message: error instanceof Error ? error.message : "Preview failed." });
        }
      }
    }

    if (!selectedSource) {
      setPreviewResource({ state: "idle" });
      return undefined;
    }

    void loadPreview(selectedSource);
    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [previewMediaType, previewSourceId, previewSourceKind]);

  if (!selectedSource) {
    return (
      <section className="source-preview empty-preview">
        <h2>Select a file to preview it.</h2>
        <p>Selected ready files become the ChatKit file scope.</p>
      </section>
    );
  }

  const visibleChunks = selectedSource.chunks.slice(0, CHUNK_PREVIEW_LIMIT);

  return (
    <section className="source-preview">
      <div className="preview-layout">
        <div className="preview-main">
          <div className="source-title-row">
            <div>
              <h2>{selectedSource.display_title}</h2>
              <p>{selectedSource.original_filename}</p>
            </div>
            <span className="file-type-large">{sourceExtension(selectedSource)}</span>
          </div>
          <RawPreview source={selectedSource} resource={previewResource} />
          <div className="chunk-section">
            <div className="tool-heading">
              <h3>Optional split map</h3>
              <span>
                {visibleChunks.length}
                {selectedSource.chunks.length > visibleChunks.length ? ` of ${selectedSource.chunks.length}` : ""}
              </span>
            </div>
            <div className="chunk-list">
              {visibleChunks.map((chunk) => (
                <ChunkRow key={chunk.id} chunk={chunk} />
              ))}
              {!visibleChunks.length ? <p className="empty-state">No split records yet.</p> : null}
            </div>
          </div>
        </div>

        <aside className="metadata-panel">
          <dl>
            <div>
              <dt>Kind</dt>
              <dd>{selectedSource.source_kind}</dd>
            </div>
            <div>
              <dt>Size</dt>
              <dd>{formatBytes(selectedSource.byte_size)}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{formatDate(selectedSource.created_at)}</dd>
            </div>
            <div>
              <dt>Index</dt>
              <dd>{selectedSource.openai_vector_file_id ? "ready" : "pending"}</dd>
            </div>
            <div>
              <dt>Split records</dt>
              <dd>{selectedSource.chunk_count}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd>{formatDate(selectedSource.updated_at)}</dd>
            </div>
          </dl>
          {selectedSource.error_message ? <p className="error-message">{selectedSource.error_message}</p> : null}
          <label className="field-label">
            Optional split guidance
            <textarea
              className="compact-textarea"
              value={uploadGuidance}
              onChange={(event) => onUploadGuidanceChange(event.currentTarget.value)}
            />
          </label>
          <button type="button" className="secondary-button" onClick={onResplit} disabled={busy}>
            Re-split
          </button>
          <div className="tag-editor">
            <strong>Tags {selectedSourceTagDraftIdSet.size}/{SOURCE_TAG_LIMIT}</strong>
            <div className="tag-picker-list">
              {tags.map((tag) => {
                const checked = selectedSourceTagDraftIdSet.has(tag.id);
                return (
                  <label key={tag.id} className="tag-checkbox">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => onTagToggle(tag.id)}
                      disabled={busy || selectedSource.status === "processing" || (!checked && selectedSourceTagDraftIdSet.size >= SOURCE_TAG_LIMIT)}
                    />
                    <span>{tag.name}</span>
                  </label>
                );
              })}
              {!tags.length ? <span className="subtle">No tags yet</span> : null}
            </div>
            <button
              type="button"
              className="secondary-button"
              onClick={onSaveTags}
              disabled={busy || selectedSource.status === "processing" || !selectedSourceTagChanged}
            >
              Save Tags
            </button>
          </div>
        </aside>
      </div>
    </section>
  );
}

function RawPreview({ resource, source }: { resource: PreviewResource; source: SourceDetail }) {
  if (resource.state === "loading") {
    return <div className="raw-preview preview-loading">Loading preview...</div>;
  }
  if (resource.state === "error") {
    return (
      <div className="raw-preview preview-unavailable">
        <strong>Preview unavailable</strong>
        <span>{resource.message}</span>
      </div>
    );
  }
  if (resource.state === "text") {
    return (
      <div className="raw-preview text-preview">
        <pre>{resource.text}</pre>
        {resource.truncated ? <p className="subtle">Showing the first {formatNumber(TEXT_PREVIEW_LIMIT)} characters.</p> : null}
      </div>
    );
  }
  if (resource.state === "file") {
    const mediaType = resource.mediaType.toLowerCase();
    if (source.source_kind === "pdf" || mediaType.includes("pdf")) {
      return (
        <div className="raw-preview document-preview">
          <object data={resource.url} type="application/pdf">
            <a href={resource.url} target="_blank" rel="noreferrer">
              Open PDF preview
            </a>
          </object>
        </div>
      );
    }
    if (source.source_kind === "image" || mediaType.startsWith("image/")) {
      return (
        <div className="raw-preview image-preview">
          <img src={resource.url} alt={source.display_title} />
        </div>
      );
    }
    if (source.source_kind === "audio" || mediaType.startsWith("audio/")) {
      return (
        <div className="raw-preview media-preview">
          <audio src={resource.url} controls />
        </div>
      );
    }
    if (source.source_kind === "video" || mediaType.startsWith("video/")) {
      return (
        <div className="raw-preview media-preview">
          <video src={resource.url} controls />
        </div>
      );
    }
  }
  return (
    <div className="raw-preview preview-unavailable">
      <strong>{source.source_kind} source</strong>
      <span>Optional split preview is available below.</span>
    </div>
  );
}

const ChunkRow = memo(function ChunkRow({ chunk }: { chunk: ChunkSummary }) {
  return (
    <article className="chunk-row">
      <span>{chunk.sequence + 1}</span>
      <div>
        <strong>{chunk.title}</strong>
        <p>{chunk.summary}</p>
      </div>
      <small>{formatLocator(chunk)}</small>
    </article>
  );
});

function entryTypeLabel(entry: FilesystemEntrySummary): string {
  if (entry.kind === "folder") {
    return "";
  }
  const extension = entry.name.split(".").pop()?.slice(0, 4).toUpperCase();
  return extension && extension !== entry.name.toUpperCase() ? extension : (entry.source_kind ?? "file").slice(0, 4).toUpperCase();
}

function safeJsonStringArray(value: string): string[] {
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function sameStringSet(left: string[], right: string[]): boolean {
  if (left.length !== right.length) {
    return false;
  }
  const rightSet = new Set(right);
  return left.every((item) => rightSet.has(item));
}

function isActiveTask(task: TaskSummary): boolean {
  return task.status === "queued" || task.status === "running";
}

function canPreviewSource(source: SourceDetail): boolean {
  return ["pdf", "text", "conversation", "image", "audio", "video"].includes(source.source_kind);
}

function isTextPreview(source: SourceDetail, mediaType: string): boolean {
  const normalized = mediaType.toLowerCase();
  return (
    source.source_kind === "text" ||
    source.source_kind === "conversation" ||
    normalized.startsWith("text/") ||
    normalized.includes("json") ||
    normalized.includes("csv") ||
    normalized.includes("xml")
  );
}

function sourceExtension(source: Pick<SourceSummary, "original_filename" | "source_kind">): string {
  const extension = source.original_filename.split(".").pop()?.slice(0, 4).toUpperCase();
  return extension && extension !== source.original_filename.toUpperCase() ? extension : source.source_kind.slice(0, 4).toUpperCase();
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; value >= 1024 && index < units.length; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${unit}`;
}

function formatDate(value: string): string {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? value : dateFormatter.format(parsed);
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function formatLocator(chunk: ChunkSummary): string {
  const locator = chunk.locator;
  if (locator.type === "page_range" && locator.start_page !== null) {
    return locator.end_page && locator.end_page !== locator.start_page
      ? `pp. ${locator.start_page}-${locator.end_page}`
      : `p. ${locator.start_page}`;
  }
  if (locator.type === "line_range" && locator.start_line !== null) {
    return locator.end_line && locator.end_line !== locator.start_line
      ? `lines ${locator.start_line}-${locator.end_line}`
      : `line ${locator.start_line}`;
  }
  if (locator.type === "time_range" && locator.start_seconds !== null) {
    return locator.end_seconds && locator.end_seconds !== locator.start_seconds
      ? `${Math.round(locator.start_seconds)}-${Math.round(locator.end_seconds)}s`
      : `${Math.round(locator.start_seconds)}s`;
  }
  return chunk.strategy_label;
}

function readStoredWorkspaceSplit(): number {
  if (typeof window === "undefined") {
    return 64;
  }
  const stored = Number(window.localStorage.getItem(WORKSPACE_SPLIT_STORAGE_KEY));
  return Number.isFinite(stored) ? clamp(stored, 46, 76) : 64;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

const ChatPane = memo(function ChatPane({
  onClientTool,
  selectedSourceIds,
}: {
  onClientTool: (toolCall: { name: string; params: Record<string, unknown> }) => Promise<Record<string, unknown>>;
  selectedSourceIds: string[];
}) {
  const chatKitConfig = getChatKitConfig();
  const selectedFileScopeLabel =
    selectedSourceIds.length === 1 ? "One file is in scope. Ask, search, or generate from it." : `${selectedSourceIds.length} files are in scope. Ask, search, or generate from them.`;
  const options = useMemo<UseChatKitOptions>(
    () => ({
      api: {
        url: chatKitConfig.url,
        domainKey: chatKitConfig.domainKey,
        fetch: authenticatedFetch,
      },
      theme: {
        colorScheme: "light",
        radius: "round",
        density: "compact",
      },
      history: {
        enabled: true,
        showDelete: false,
        showRename: false,
      },
      header: {
        enabled: true,
        title: { enabled: true, text: "Chat" },
      },
      startScreen: {
        greeting: selectedSourceIds.length
          ? selectedFileScopeLabel
          : "Select indexed files, then ask me to search, answer, synthesize, image, or narrate from them.",
        prompts: [
          { label: "Answer from files", prompt: "Answer my question using indexed file matches and cite the source titles.", icon: "check-circle" },
          { label: "Search trails", prompt: "Search the indexed files around this topic and explain the useful trails.", icon: "sparkle" },
          { label: "Generate from evidence", prompt: "Use retrieved indexed file matches as evidence, and separate facts from speculation.", icon: "bolt" },
        ],
      },
      composer: {
        placeholder: "Ask the indexed files...",
        attachments: {
          enabled: false,
        },
        dictation: { enabled: false },
        models: MODEL_CHOICES.map((choice) => ({ ...choice, default: choice.id === "balanced" })),
      },
      threadItemActions: {
        feedback: false,
      },
      onClientTool,
    }),
    [chatKitConfig.domainKey, chatKitConfig.url, onClientTool, selectedFileScopeLabel, selectedSourceIds.length],
  );
  const chatKit = useChatKit(options);
  return <ChatKit control={chatKit.control} className="chatkit-element" />;
});

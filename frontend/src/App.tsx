import type { Entity } from "@openai/chatkit-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
  RefObject,
} from "react";

import { ChatPane } from "./components/ChatPane";
import { DeleteEntriesDialog, ExplorerShortcutDialog } from "./components/ExplorerDialogs";
import { FileEntryRow } from "./components/FileEntryRow";
import { LibrarySearchView } from "./components/LibrarySearchView";
import { ResearchBuilderPanel } from "./components/ResearchBuilderPanel";
import { SourcePreview } from "./components/SourcePreview";
import { WorkspaceHeader } from "./components/WorkspaceHeader";
import {
  buildResearchLibrary,
  createFolder,
  createTag,
  deleteFilesystemEntries,
  getAuthenticatedUser,
  getSource,
  listFilesystem,
  listTags,
  listTasks,
  previewSemanticSplit,
  resplitSource,
  searchChunks,
  searchFilesystem,
  setChatKitMetadataGetter,
  updateFilesystemEntry,
  updateSourceTags,
  uploadSource,
} from "./lib/api";
import {
  ACTIVE_TASK_REFRESH_INTERVAL_MS,
  DEFAULT_LIBRARY_QUERY,
  DEFAULT_SPLIT_GUIDANCE,
  EXPLORER_RENDER_LIMIT,
  PREVIEW_SPLIT_STORAGE_KEY,
  SELECTED_FILE_LIMIT,
  WORKSPACE_SPLIT_STORAGE_KEY,
} from "./lib/appConstants";
import type {
  AppProps,
  ChatKitClientToolCall,
  DeleteDialogState,
  LibrarySearchResult,
  ResearchBuilderSeedKind,
  RevealTarget,
  WorkspaceFileView,
} from "./lib/appTypes";
import {
  asResearchBuildResponse,
  asResearchCandidates,
  asResearchIngested,
  mergeResearchCandidates,
  mergeResearchIngested,
} from "./lib/researchUi";
import { filterFilesystemEntries, fuzzyRankFilesystemEntries } from "./lib/search";
import type {
  AuthUser,
  FilesystemBreadcrumb,
  FilesystemEntrySummary,
  FilesystemListResponse,
  ResearchLibraryBuildResponse,
  SourceDetail,
  SplitPreviewResponse,
  TagSummary,
  TaskSummary,
} from "./lib/types";
import { isActiveTask } from "./lib/uiFormat";
import {
  clamp,
  isEditableShortcutTarget,
  readStoredPreviewSplit,
  readStoredWorkspaceSplit,
  sameStringArray,
  sameStringSet,
} from "./lib/uiState";

export function App({ authMode }: AppProps) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [filesystem, setFilesystem] = useState<FilesystemListResponse | null>(null);
  const [knownEntries, setKnownEntries] = useState<Record<string, FilesystemEntrySummary>>({});
  const [tags, setTags] = useState<TagSummary[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [currentFolderId, setCurrentFolderId] = useState<string | null>(null);
  const [activeFileView, setActiveFileView] = useState<WorkspaceFileView>("explorer");
  const [sourceQuery, setSourceQuery] = useState("");
  const [selectedExplorerTagIds, setSelectedExplorerTagIds] = useState<string[]>([]);
  const [libraryQuery, setLibraryQuery] = useState("");
  const [libraryTagMatchMode, setLibraryTagMatchMode] = useState<"all" | "any">("all");
  const [libraryResults, setLibraryResults] = useState<LibrarySearchResult[]>([]);
  const [libraryResultCount, setLibraryResultCount] = useState(0);
  const [librarySearching, setLibrarySearching] = useState(false);
  const [selectedEntryIds, setSelectedEntryIds] = useState<string[]>([]);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [focusedEntryId, setFocusedEntryId] = useState<string | null>(null);
  const [selectionAnchorEntryId, setSelectionAnchorEntryId] = useState<string | null>(null);
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
  const [researchResult, setResearchResult] = useState<ResearchLibraryBuildResponse | null>(null);
  const [deleteDialog, setDeleteDialog] = useState<DeleteDialogState | null>(null);
  const [shortcutDialogOpen, setShortcutDialogOpen] = useState(false);
  const [status, setStatus] = useState("Opening files.");
  const [busy, setBusy] = useState(false);
  const workspaceGridRef = useRef<HTMLElement | null>(null);
  const previewGridRef = useRef<HTMLDivElement | null>(null);
  const knownEntriesRef = useRef<Record<string, FilesystemEntrySummary>>({});
  const tagsRef = useRef<TagSummary[]>([]);
  const clientToolUiQueueRef = useRef<Array<() => void | Promise<void>>>([]);
  const clientToolUiFlushRef = useRef<number | null>(null);
  const [workspaceSplitPercent, setWorkspaceSplitPercent] = useState(() => readStoredWorkspaceSplit());
  const [previewSplitPercent, setPreviewSplitPercent] = useState(() => readStoredPreviewSplit());

  const selectedExplorerTagIdSet = useMemo(() => new Set(selectedExplorerTagIds), [selectedExplorerTagIds]);
  const selectedEntryIdSet = useMemo(() => new Set(selectedEntryIds), [selectedEntryIds]);
  const selectedSourceIdSet = useMemo(() => new Set(selectedSourceIds), [selectedSourceIds]);
  const selectedSourceTagDraftIdSet = useMemo(() => new Set(selectedSourceTagDraftIds), [selectedSourceTagDraftIds]);
  const searching = Boolean(sourceQuery.trim());
  const folderEntries = filesystem?.entries ?? [];
  const visibleEntries = useMemo(
    () => filterFilesystemEntries(folderEntries, sourceQuery),
    [folderEntries, sourceQuery],
  );
  const selectedFileEntries = useMemo(
    () =>
      selectedSourceIds.flatMap((sourceId) => {
        const entry = Object.values(knownEntries).find((item) => item.source_id === sourceId);
        return entry ? [entry] : [];
      }),
    [knownEntries, selectedSourceIds],
  );
  const sourceEntriesById = useMemo(() => {
    const entriesBySourceId: Record<string, FilesystemEntrySummary> = {};
    for (const entry of Object.values(knownEntries)) {
      if (entry.source_id) {
        entriesBySourceId[entry.source_id] = entry;
      }
    }
    return entriesBySourceId;
  }, [knownEntries]);
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
  const previewLayoutStyle = useMemo(
    () =>
      ({
        "--preview-list-width": `${previewSplitPercent}%`,
      }) as CSSProperties & Record<"--preview-list-width", string>,
    [previewSplitPercent],
  );

  const scheduleClientToolUiUpdate = useCallback((run: () => void | Promise<void>): void => {
    clientToolUiQueueRef.current.push(run);
    if (clientToolUiFlushRef.current !== null) {
      return;
    }
    clientToolUiFlushRef.current = window.setTimeout(() => {
      clientToolUiFlushRef.current = null;
      const queuedRuns = clientToolUiQueueRef.current.splice(0);
      void (async () => {
        for (const queuedRun of queuedRuns) {
          try {
            await queuedRun();
          } catch (error) {
            setStatus(error instanceof Error ? error.message : "Could not apply the ChatKit UI update.");
          }
        }
      })();
    }, 0);
  }, []);

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
      return response;
    },
    [cacheEntries],
  );

  const refreshExplorer = useCallback(async (): Promise<void> => {
    const response = await loadFolder(currentFolderId);
    setStatus(`${response.current.path} has ${response.entries.length} entr${response.entries.length === 1 ? "y" : "ies"}.`);
  }, [currentFolderId, loadFolder]);

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
    knownEntriesRef.current = knownEntries;
  }, [knownEntries]);

  useEffect(() => {
    tagsRef.current = tags;
  }, [tags]);

  useEffect(
    () => () => {
      if (clientToolUiFlushRef.current !== null) {
        window.clearTimeout(clientToolUiFlushRef.current);
      }
      clientToolUiQueueRef.current = [];
    },
    [],
  );

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
    }, ACTIVE_TASK_REFRESH_INTERVAL_MS);
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

  const applyExplorerSelection = useCallback(
    (entryIds: string[], focusedEntryId: string, anchorEntryId: string | null): void => {
      const nextEntryIds = Array.from(new Set(entryIds));
      setSelectedEntryIds(nextEntryIds);
      setFocusedEntryId(focusedEntryId);
      setSelectionAnchorEntryId(anchorEntryId ?? focusedEntryId);
      syncChatSelection(nextEntryIds);
      const focusedEntry = knownEntries[focusedEntryId] ?? visibleEntries.find((entry) => entry.id === focusedEntryId);
      if (focusedEntry?.source_id) {
        void openSource(focusedEntry.source_id);
        return;
      }
      setSelectedSource(null);
    },
    [knownEntries, openSource, syncChatSelection, visibleEntries],
  );

  const chooseEntries = useCallback(
    (entry: FilesystemEntrySummary, event: ReactMouseEvent): void => {
      const currentVisibleIds = visibleEntries.map((item) => item.id);
      let nextEntryIds: string[];
      let nextAnchorEntryId = entry.id;
      if (event.shiftKey) {
        const anchorEntryId = selectionAnchorEntryId ?? focusedEntryId ?? selectedEntryIds[0] ?? entry.id;
        const anchorIndex = currentVisibleIds.indexOf(anchorEntryId);
        const targetIndex = currentVisibleIds.indexOf(entry.id);
        nextAnchorEntryId = anchorEntryId;
        if (anchorIndex >= 0 && targetIndex >= 0) {
          const [start, end] = [anchorIndex, targetIndex].sort((left, right) => left - right);
          nextEntryIds = currentVisibleIds.slice(start, end + 1);
        } else {
          nextEntryIds = [entry.id];
          nextAnchorEntryId = entry.id;
        }
      } else if (event.metaKey || event.ctrlKey) {
        nextEntryIds = selectedEntryIds.includes(entry.id)
          ? selectedEntryIds.filter((id) => id !== entry.id)
          : [...selectedEntryIds, entry.id];
      } else {
        nextEntryIds = [entry.id];
      }
      applyExplorerSelection(nextEntryIds, entry.id, nextAnchorEntryId);
    },
    [applyExplorerSelection, focusedEntryId, selectedEntryIds, selectionAnchorEntryId, visibleEntries],
  );

  const openEntry = useCallback(
    (entry: FilesystemEntrySummary): void => {
      if (entry.kind === "folder") {
        setSourceQuery("");
        setSelectedEntryIds([]);
        setSelectedSourceIds([]);
        setFocusedEntryId(null);
        setSelectionAnchorEntryId(null);
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
      setSelectedEntryIds([]);
      setSelectedSourceIds([]);
      setFocusedEntryId(null);
      setSelectionAnchorEntryId(null);
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

  const requestDeleteSelectedEntries = useCallback((): void => {
    if (!selectedEntryIds.length) {
      return;
    }
    const entries = selectedEntryIds.flatMap((entryId) => {
      const entry = knownEntries[entryId];
      return entry ? [entry] : [];
    });
    if (!entries.length) {
      return;
    }
    setDeleteDialog({ entries, phase: "confirming" });
  }, [knownEntries, selectedEntryIds]);

  const confirmDeleteSelectedEntries = useCallback(async (): Promise<void> => {
    if (!deleteDialog) {
      return;
    }
    const entryIds = deleteDialog.entries.map((entry) => entry.id);
    setBusy(true);
    setDeleteDialog((current) => current ? { ...current, phase: "deleting" } : current);
    setStatus(
      `Deleting ${entryIds.length} selected item${entryIds.length === 1 ? "" : "s"}${
        deleteDialog.entries.some((entry) => entry.kind === "folder") ? " and nested folder contents" : ""
      }.`,
    );
    try {
      const result = await deleteFilesystemEntries({ entry_ids: entryIds, confirm: true });
      setSelectedEntryIds([]);
      setSelectedSourceIds([]);
      setFocusedEntryId(null);
      setSelectionAnchorEntryId(null);
      setSelectedSource(null);
      setDeleteDialog(null);
      await refreshExplorer();
      setStatus(
        `Deleted ${result.deleted_entry_ids.length} item${result.deleted_entry_ids.length === 1 ? "" : "s"}${
          result.deleted_source_ids.length ? ` and ${result.deleted_source_ids.length} indexed file${result.deleted_source_ids.length === 1 ? "" : "s"}` : ""
        }.`,
      );
    } catch (error) {
      setDeleteDialog((current) => current ? { ...current, phase: "confirming" } : current);
      setStatus(error instanceof Error ? error.message : "Delete failed.");
    } finally {
      setBusy(false);
    }
  }, [deleteDialog, refreshExplorer]);

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
        setFocusedEntryId(null);
        setSelectionAnchorEntryId(null);
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
        auto_ingest: true,
        discover_references: true,
        max_depth: maxDepth,
        max_sources: maxSources,
        max_candidates_per_source: Math.min(20, Math.max(4, maxSources)),
        max_pending_candidates: Math.max(50, maxSources * Math.max(1, maxDepth + 1)),
      });
      setResearchResult(response);
      setTasks((await listTasks()).tasks);
      setSourceQuery("");
      setSelectedEntryIds([]);
      setSelectedSourceIds([]);
      setFocusedEntryId(null);
      setSelectionAnchorEntryId(null);
      setSelectedSource(null);
      if (response.target_folder_id) {
        await loadFolder(response.target_folder_id);
      } else {
        await refreshExplorer();
      }
      const queuedLabel = response.ingested.length
        ? `, ${response.ingested.length} queued for indexing`
        : ", no public items queued";
      setStatus(`Research library build found ${response.candidates.length} candidate${response.candidates.length === 1 ? "" : "s"}${queuedLabel}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Research library build failed.");
    } finally {
      setBusy(false);
    }
  }, [
    loadFolder,
    refreshExplorer,
    researchMaxDepth,
    researchMaxSources,
    researchQuery,
    researchSeedType,
  ]);

  const toggleExplorerTag = useCallback((tagId: string): void => {
    setSelectedExplorerTagIds((current) =>
      current.includes(tagId) ? current.filter((id) => id !== tagId) : [...current, tagId],
    );
  }, []);

  const clearExplorerFilters = useCallback((): void => {
    setSourceQuery("");
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

  const runLibrarySearch = useCallback(
    async (mode: "replace" | "append"): Promise<void> => {
      const query = libraryQuery.trim() || DEFAULT_LIBRARY_QUERY;
      setLibrarySearching(true);
      setStatus(`${mode === "append" ? "Appending" : "Searching"} semantic results for "${query}".`);
      try {
        const response = await searchChunks({
          query,
          tagIds: selectedExplorerTagIds,
          tagMatchMode: libraryTagMatchMode,
          maxResults: 24,
        });
        const nextResults: LibrarySearchResult[] = [];
        const seenSourceIds = new Set<string>();
        for (const hit of response.hits) {
          if (seenSourceIds.has(hit.source_file_id)) {
            continue;
          }
          seenSourceIds.add(hit.source_file_id);
          const entry = Object.values(knownEntriesRef.current).find((item) => item.source_id === hit.source_file_id) ?? null;
          nextResults.push({ hit, entry });
        }
        setLibraryQuery(query);
        setLibraryResultCount(response.hits.length);
        setLibraryResults((current) => {
          if (mode === "replace") {
            return nextResults;
          }
          const bySourceId = new Map(current.map((result) => [result.hit.source_file_id, result]));
          for (const result of nextResults) {
            bySourceId.set(result.hit.source_file_id, result);
          }
          return Array.from(bySourceId.values());
        });
        setActiveFileView("library");
        setStatus(
          `${mode === "append" ? "Added" : "Found"} ${nextResults.length} semantic source${
            nextResults.length === 1 ? "" : "s"
          } from ${response.hits.length} hit${response.hits.length === 1 ? "" : "s"}.`,
        );
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Semantic search failed.");
      } finally {
        setLibrarySearching(false);
      }
    },
    [libraryQuery, libraryTagMatchMode, selectedExplorerTagIds],
  );

  const toggleLibrarySourceSelection = useCallback((sourceId: string): void => {
    const entry = Object.values(knownEntriesRef.current).find((item) => item.source_id === sourceId) ?? null;
    setSelectedSourceIds((current) =>
      current.includes(sourceId)
        ? current.filter((id) => id !== sourceId)
        : Array.from(new Set([...current, sourceId])).slice(0, SELECTED_FILE_LIMIT),
    );
    if (entry) {
      setSelectedEntryIds((current) =>
        current.includes(entry.id) ? current.filter((id) => id !== entry.id) : Array.from(new Set([...current, entry.id])),
      );
      setFocusedEntryId(entry.id);
      setSelectionAnchorEntryId(entry.id);
    }
  }, []);

  const selectLibraryResultsForChat = useCallback((): void => {
    const sourceIds = libraryResults.map((result) => result.hit.source_file_id).slice(0, SELECTED_FILE_LIMIT);
    const entryIds = libraryResults.flatMap((result) => (result.entry ? [result.entry.id] : [])).slice(0, SELECTED_FILE_LIMIT);
    setSelectedSourceIds(sourceIds);
    setSelectedEntryIds(entryIds);
    setFocusedEntryId(entryIds[0] ?? null);
    setSelectionAnchorEntryId(entryIds[0] ?? null);
    setStatus(
      `Selected ${sourceIds.length} semantic result${sourceIds.length === 1 ? "" : "s"} for ChatKit${
        sourceIds.length > entryIds.length ? "; some files are not loaded in Explorer yet" : ""
      }.`,
    );
  }, [libraryResults]);

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

  const revealFileInExplorer = useCallback(
    async ({ sourceId, entryId }: RevealTarget): Promise<Record<string, unknown>> => {
      const entriesById = knownEntriesRef.current;
      let searchedEntries: FilesystemEntrySummary[] = [];
      let entry = entryId ? entriesById[entryId] : null;
      if (!entry && sourceId) {
        entry = Object.values(entriesById).find((item) => item.source_id === sourceId) ?? null;
      }
      if (!entry && sourceId) {
        const search = await searchFilesystem({ query: sourceId, pageSize: 1 });
        searchedEntries = search.entries;
        entry = search.entries[0] ?? null;
      }
      if (!entry) {
        setStatus("File was not found in the explorer.");
        return { ok: false, message: "File was not found in the explorer." };
      }
      const revealedEntry = entry;
      scheduleClientToolUiUpdate(async () => {
        if (searchedEntries.length) {
          cacheEntries(searchedEntries);
        }
        setActiveFileView("explorer");
        setSourceQuery("");
        await loadFolder(revealedEntry.parent_id);
        setSelectedEntryIds((current) => (sameStringArray(current, [revealedEntry.id]) ? current : [revealedEntry.id]));
        setFocusedEntryId((current) => current === revealedEntry.id ? current : revealedEntry.id);
        setSelectionAnchorEntryId((current) => current === revealedEntry.id ? current : revealedEntry.id);
        if (revealedEntry.source_id) {
          setSelectedSourceIds((current) =>
            sameStringArray(current, [revealedEntry.source_id as string]) ? current : [revealedEntry.source_id as string],
          );
          await openSource(revealedEntry.source_id);
        } else {
          setSelectedSourceIds((current) => (current.length ? [] : current));
          setSelectedSource(null);
          setStatus(`Opened ${revealedEntry.path}.`);
        }
      });
      return { ok: true, entry_id: revealedEntry.id, source_id: revealedEntry.source_id, path: revealedEntry.path };
    },
    [cacheEntries, loadFolder, openSource, scheduleClientToolUiUpdate],
  );

  const searchChatEntities = useCallback(
    async (query: string): Promise<Entity[]> => {
      const entries = fuzzyRankFilesystemEntries(Object.values(knownEntriesRef.current), query).slice(0, 12);
      return entries.map((entry) => {
        const data: Record<string, string> = {
          entry_id: entry.id,
          kind: entry.kind,
          path: entry.path,
        };
        if (entry.source_id) {
          data.source_id = entry.source_id;
        }
        return {
          id: entry.source_id ?? entry.id,
          title: entry.path,
          icon: entry.kind === "folder" ? "lucide:folder" : "lucide:file-text",
          interactive: true,
          group: entry.kind === "folder" ? "Folders" : "Files",
          data,
        };
      });
    },
    [],
  );

  const revealChatEntity = useCallback(
    (entity: Entity): void => {
      void revealFileInExplorer({
        sourceId: entity.data?.source_id ?? null,
        entryId: entity.data?.entry_id ?? (!entity.data?.source_id ? entity.id : null),
      });
    },
    [revealFileInExplorer],
  );

  const handleClientTool = useCallback(
    async (toolCall: ChatKitClientToolCall): Promise<Record<string, unknown>> => {
      if (toolCall.name === "set_file_selection") {
        const rawIds = Array.isArray(toolCall.params.source_ids) ? toolCall.params.source_ids : [];
        const sourceIds = rawIds.filter((id): id is string => typeof id === "string").slice(0, SELECTED_FILE_LIMIT);
        const mode = typeof toolCall.params.mode === "string" ? toolCall.params.mode : "replace";
        const entryIds = Object.values(knownEntriesRef.current)
          .filter((entry) => entry.source_id && sourceIds.includes(entry.source_id))
          .map((entry) => entry.id);
        scheduleClientToolUiUpdate(() => {
          setSelectedSourceIds((current) => {
            const next =
              mode === "add"
                ? Array.from(new Set([...current, ...sourceIds])).slice(0, SELECTED_FILE_LIMIT)
                : mode === "remove"
                  ? current.filter((id) => !sourceIds.includes(id))
                  : sourceIds;
            return sameStringArray(current, next) ? current : next;
          });
          if (entryIds.length) {
            setSelectedEntryIds((current) => (sameStringArray(current, entryIds) ? current : entryIds));
            setFocusedEntryId((current) => current === entryIds[0] ? current : entryIds[0]);
            setSelectionAnchorEntryId((current) => current === entryIds[0] ? current : entryIds[0]);
          }
        });
        return { ok: true, selected_source_ids: sourceIds };
      }
      if (toolCall.name === "set_file_search") {
        const query = typeof toolCall.params.query === "string" ? toolCall.params.query : "";
        const rawTagIds = Array.isArray(toolCall.params.tag_ids)
          ? toolCall.params.tag_ids.filter((id): id is string => typeof id === "string")
          : [];
        const tagIds = rawTagIds.map((tagId) => {
          const tag = tagsRef.current.find((candidate) => candidate.id === tagId || candidate.slug === tagId);
          return tag?.id ?? tagId;
        });
        scheduleClientToolUiUpdate(() => {
          setActiveFileView("library");
          setLibraryQuery((current) => current === query ? current : query);
          setSelectedExplorerTagIds((current) => (sameStringArray(current, tagIds) ? current : tagIds));
        });
        return { ok: true, query, tag_ids: tagIds, requested_tags: rawTagIds };
      }
      if (toolCall.name === "reveal_file") {
        const sourceId = typeof toolCall.params.source_id === "string" ? toolCall.params.source_id : null;
        const entryId = typeof toolCall.params.entry_id === "string" ? toolCall.params.entry_id : null;
        return await revealFileInExplorer({ sourceId, entryId });
      }
      if (toolCall.name === "show_research_builder") {
        const query = typeof toolCall.params.query === "string" ? toolCall.params.query : null;
        const seedType = toolCall.params.seed_type === "paper" ? "paper" : toolCall.params.seed_type === "topic" ? "topic" : null;
        const maxSources = typeof toolCall.params.max_sources === "number" ? toolCall.params.max_sources : null;
        const maxDepth = typeof toolCall.params.max_depth === "number" ? toolCall.params.max_depth : null;
        const result = asResearchBuildResponse(toolCall.params.result);
        const candidates = asResearchCandidates(toolCall.params.candidates);
        const ingested = asResearchIngested(toolCall.params.ingested);
        const targetFolderId =
          result?.target_folder_id ??
          (typeof toolCall.params.target_folder_id === "string"
            ? toolCall.params.target_folder_id
            : typeof toolCall.params.folder_id === "string"
              ? toolCall.params.folder_id
              : null);
        scheduleClientToolUiUpdate(async () => {
          if (query) {
            setResearchQuery((current) => current === query ? current : query);
          }
          if (seedType) {
            setResearchSeedType(seedType);
          }
          if (maxSources !== null) {
            setResearchMaxSources((current) => {
              const next = clamp(Math.round(maxSources), 1, 50);
              return current === next ? current : next;
            });
          }
          if (maxDepth !== null) {
            setResearchMaxDepth((current) => {
              const next = clamp(Math.round(maxDepth), 0, 4);
              return current === next ? current : next;
            });
          }
          if (result) {
            setResearchResult(result);
          } else if (candidates.length || ingested.length) {
            setResearchResult((current) =>
              current
                ? {
                    ...current,
                    candidates: mergeResearchCandidates(current.candidates, candidates),
                    ingested: mergeResearchIngested(current.ingested, ingested),
                  }
                : current,
            );
          }
          if (targetFolderId) {
            setSourceQuery((current) => current === "" ? current : "");
            setSelectedExplorerTagIds((current) => current.length ? [] : current);
            setSelectedEntryIds((current) => current.length ? [] : current);
            setSelectedSourceIds((current) => current.length ? [] : current);
            setFocusedEntryId(null);
            setSelectionAnchorEntryId(null);
            setSelectedSource(null);
            await loadFolder(targetFolderId);
          }
          if (candidates.length || result) {
            const candidateCount = result?.candidates.length ?? candidates.length;
            setStatus(`Research builder is showing ${candidateCount} candidate${candidateCount === 1 ? "" : "s"}.`);
          }
        });
        return { ok: true, candidate_count: result?.candidates.length ?? candidates.length, target_folder_id: targetFolderId };
      }
      return { ok: false, message: `Unknown client tool: ${toolCall.name}` };
    },
    [loadFolder, revealFileInExplorer, scheduleClientToolUiUpdate],
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

  const beginPreviewResize = useCallback((event: ReactPointerEvent<HTMLButtonElement>): void => {
    const grid = previewGridRef.current;
    if (!grid) {
      return;
    }
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    document.body.classList.add("resizing-preview");
    const updateFromPointer = (clientX: number): void => {
      const rect = grid.getBoundingClientRect();
      const nextPercent = clamp(((clientX - rect.left) / rect.width) * 100, 34, 70);
      setPreviewSplitPercent(nextPercent);
      window.localStorage.setItem(PREVIEW_SPLIT_STORAGE_KEY, String(Math.round(nextPercent)));
    };
    updateFromPointer(event.clientX);
    const handlePointerMove = (moveEvent: PointerEvent): void => {
      updateFromPointer(moveEvent.clientX);
    };
    const stopResize = (): void => {
      document.body.classList.remove("resizing-preview");
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
          activeFileView={activeFileView}
          breadcrumbs={filesystem?.breadcrumbs ?? []}
          busy={busy}
          currentFolder={filesystem?.current ?? null}
          entries={visibleEntries}
          focusedEntryId={focusedEntryId}
          libraryQuery={libraryQuery}
          libraryResultCount={libraryResultCount}
          libraryResults={libraryResults}
          librarySearching={librarySearching}
          libraryTagMatchMode={libraryTagMatchMode}
          newTagName={newTagName}
          pendingFiles={pendingFiles}
          previewGridRef={previewGridRef}
          previewLayoutStyle={previewLayoutStyle}
          previewSplitPercent={previewSplitPercent}
          researchMaxDepth={researchMaxDepth}
          researchMaxSources={researchMaxSources}
          researchQuery={researchQuery}
          researchResult={researchResult}
          researchSeedType={researchSeedType}
          searching={searching}
          selectedEntryIds={selectedEntryIds}
          selectedEntryIdSet={selectedEntryIdSet}
          selectedExplorerTagIdSet={selectedExplorerTagIdSet}
          selectedFileEntries={selectedFileEntries}
          selectedSource={selectedSource}
          selectedSourceIdSet={selectedSourceIdSet}
          selectedSourceTagChanged={selectedSourceTagChanged}
          selectedSourceTagDraftIdSet={selectedSourceTagDraftIdSet}
          selectionAnchorEntryId={selectionAnchorEntryId}
          sourceEntriesById={sourceEntriesById}
          sourceQuery={sourceQuery}
          splitPreview={splitPreview}
          tags={tags}
          uploadGuidance={uploadGuidance}
          onActiveFileViewChange={setActiveFileView}
          onChooseEntries={chooseEntries}
          onChooseFiles={chooseFiles}
          onClearFilters={clearExplorerFilters}
          onCreateFolder={() => void createFolderInCurrentFolder()}
          onCreateTag={() => void createExplorerTag()}
          onDeleteSelected={requestDeleteSelectedEntries}
          onDropEntries={(entryIds, folderId) => void moveEntriesToFolder(entryIds, folderId)}
          onGoToFolder={goToFolder}
          onNewTagNameChange={setNewTagName}
          onClosePreview={() => setSelectedSource(null)}
          onOpenEntry={openEntry}
          onOpenSource={(sourceId) => void revealFileInExplorer({ sourceId })}
          onPreviewSplit={() => void previewPendingSplit()}
          onPreviewResize={beginPreviewResize}
          onResearchBuild={() => void buildResearchLibraryFromPanel()}
          onResearchMaxDepthChange={setResearchMaxDepth}
          onResearchMaxSourcesChange={setResearchMaxSources}
          onResearchQueryChange={setResearchQuery}
          onResearchSeedTypeChange={setResearchSeedType}
          onRenameSelected={() => void renameFocusedEntry()}
          onResplit={() => void resplitSelectedSource()}
          onRunLibrarySearch={(mode) => void runLibrarySearch(mode)}
          onSaveTags={() => void saveSelectedSourceTags()}
          onSelectEntries={applyExplorerSelection}
          onShowShortcuts={() => setShortcutDialogOpen(true)}
          onSourceQueryChange={setSourceQuery}
          onTagToggle={toggleSelectedSourceTagDraft}
          onLibraryQueryChange={setLibraryQuery}
          onLibraryTagMatchModeChange={setLibraryTagMatchMode}
          onSelectLibraryResults={selectLibraryResultsForChat}
          onToggleLibrarySourceSelection={toggleLibrarySourceSelection}
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
          <ChatPane
            onClientTool={handleClientTool}
            onEntityClick={revealChatEntity}
            onEntitySearch={searchChatEntities}
            onRevealFile={revealFileInExplorer}
          />
        </aside>
      </section>
      {deleteDialog ? (
        <DeleteEntriesDialog
          busy={busy}
          entries={deleteDialog.entries}
          phase={deleteDialog.phase}
          onCancel={() => setDeleteDialog(null)}
          onConfirm={() => void confirmDeleteSelectedEntries()}
        />
      ) : null}
      {shortcutDialogOpen ? <ExplorerShortcutDialog onClose={() => setShortcutDialogOpen(false)} /> : null}
    </main>
  );
}

const FileExplorer = memo(function FileExplorer({
  activeFileView,
  breadcrumbs,
  busy,
  currentFolder,
  entries,
  focusedEntryId,
  libraryQuery,
  libraryResultCount,
  libraryResults,
  librarySearching,
  libraryTagMatchMode,
  newTagName,
  pendingFiles,
  previewGridRef,
  previewLayoutStyle,
  previewSplitPercent,
  researchMaxDepth,
  researchMaxSources,
  researchQuery,
  researchResult,
  researchSeedType,
  searching,
  selectedEntryIds,
  selectedEntryIdSet,
  selectedExplorerTagIdSet,
  selectedFileEntries,
  selectedSource,
  selectedSourceIdSet,
  selectedSourceTagChanged,
  selectedSourceTagDraftIdSet,
  selectionAnchorEntryId,
  sourceEntriesById,
  sourceQuery,
  splitPreview,
  tags,
  uploadGuidance,
  onActiveFileViewChange,
  onChooseEntries,
  onChooseFiles,
  onClearFilters,
  onCreateFolder,
  onCreateTag,
  onDeleteSelected,
  onDropEntries,
  onGoToFolder,
  onNewTagNameChange,
  onClosePreview,
  onOpenEntry,
  onOpenSource,
  onPreviewSplit,
  onPreviewResize,
  onResearchBuild,
  onResearchMaxDepthChange,
  onResearchMaxSourcesChange,
  onResearchQueryChange,
  onResearchSeedTypeChange,
  onRenameSelected,
  onResplit,
  onRunLibrarySearch,
  onSaveTags,
  onSelectEntries,
  onShowShortcuts,
  onSourceQueryChange,
  onTagToggle,
  onLibraryQueryChange,
  onLibraryTagMatchModeChange,
  onSelectLibraryResults,
  onToggleLibrarySourceSelection,
  onToggleExplorerTag,
  onUpload,
  onUploadGuidanceChange,
}: {
  activeFileView: WorkspaceFileView;
  breadcrumbs: FilesystemBreadcrumb[];
  busy: boolean;
  currentFolder: FilesystemEntrySummary | null;
  entries: FilesystemEntrySummary[];
  focusedEntryId: string | null;
  libraryQuery: string;
  libraryResultCount: number;
  libraryResults: LibrarySearchResult[];
  librarySearching: boolean;
  libraryTagMatchMode: "all" | "any";
  newTagName: string;
  pendingFiles: File[];
  previewGridRef: RefObject<HTMLDivElement | null>;
  previewLayoutStyle: CSSProperties & Record<"--preview-list-width", string>;
  previewSplitPercent: number;
  researchMaxDepth: number;
  researchMaxSources: number;
  researchQuery: string;
  researchResult: ResearchLibraryBuildResponse | null;
  researchSeedType: ResearchBuilderSeedKind;
  searching: boolean;
  selectedEntryIds: string[];
  selectedEntryIdSet: Set<string>;
  selectedExplorerTagIdSet: Set<string>;
  selectedFileEntries: FilesystemEntrySummary[];
  selectedSource: SourceDetail | null;
  selectedSourceIdSet: Set<string>;
  selectedSourceTagChanged: boolean;
  selectedSourceTagDraftIdSet: Set<string>;
  selectionAnchorEntryId: string | null;
  sourceEntriesById: Record<string, FilesystemEntrySummary>;
  sourceQuery: string;
  splitPreview: SplitPreviewResponse | null;
  tags: TagSummary[];
  uploadGuidance: string;
  onActiveFileViewChange: (view: WorkspaceFileView) => void;
  onChooseEntries: (entry: FilesystemEntrySummary, event: ReactMouseEvent) => void;
  onChooseFiles: (files: FileList | null) => void;
  onClearFilters: () => void;
  onCreateFolder: () => void;
  onCreateTag: () => void;
  onDeleteSelected: () => void;
  onDropEntries: (entryIds: string[], folderId: string) => void;
  onGoToFolder: (folderId: string | null) => void;
  onNewTagNameChange: (value: string) => void;
  onClosePreview: () => void;
  onOpenEntry: (entry: FilesystemEntrySummary) => void;
  onOpenSource: (sourceId: string) => void;
  onPreviewSplit: () => void;
  onPreviewResize: (event: ReactPointerEvent<HTMLButtonElement>) => void;
  onResearchBuild: () => void;
  onResearchMaxDepthChange: (value: number) => void;
  onResearchMaxSourcesChange: (value: number) => void;
  onResearchQueryChange: (value: string) => void;
  onResearchSeedTypeChange: (value: ResearchBuilderSeedKind) => void;
  onRenameSelected: () => void;
  onResplit: () => void;
  onRunLibrarySearch: (mode: "replace" | "append") => void;
  onSaveTags: () => void;
  onSelectEntries: (entryIds: string[], focusedEntryId: string, anchorEntryId: string | null) => void;
  onShowShortcuts: () => void;
  onSourceQueryChange: (value: string) => void;
  onTagToggle: (tagId: string) => void;
  onLibraryQueryChange: (value: string) => void;
  onLibraryTagMatchModeChange: (value: "all" | "any") => void;
  onSelectLibraryResults: () => void;
  onToggleLibrarySourceSelection: (sourceId: string) => void;
  onToggleExplorerTag: (tagId: string) => void;
  onUpload: () => void;
  onUploadGuidanceChange: (value: string) => void;
}) {
  const selectedCount = selectedEntryIdSet.size;
  const selectedFileLabel =
    selectedFileEntries.length === 1 ? "1 indexed file selected" : `${selectedFileEntries.length} indexed files selected`;
  const dragEntryIds = useMemo(() => Array.from(selectedEntryIdSet), [selectedEntryIdSet]);
  const entryIds = useMemo(() => entries.map((entry) => entry.id), [entries]);
  const handleExplorerKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>): void => {
      if (busy || isEditableShortcutTarget(event.target)) {
        return;
      }
      const focusRow = (entryId: string): void => {
        window.requestAnimationFrame(() => {
          document.querySelector<HTMLElement>(`[data-entry-id="${entryId}"]`)?.focus();
        });
      };
      const moveFocus = (targetIndex: number, extendSelection: boolean): void => {
        if (!entryIds.length) {
          return;
        }
        const boundedIndex = clamp(targetIndex, 0, entryIds.length - 1);
        const targetEntryId = entryIds[boundedIndex];
        if (extendSelection) {
          const anchorEntryId = selectionAnchorEntryId ?? focusedEntryId ?? selectedEntryIds[0] ?? targetEntryId;
          const anchorIndex = entryIds.indexOf(anchorEntryId);
          if (anchorIndex >= 0) {
            const [start, end] = [anchorIndex, boundedIndex].sort((left, right) => left - right);
            onSelectEntries(entryIds.slice(start, end + 1), targetEntryId, anchorEntryId);
          } else {
            onSelectEntries([targetEntryId], targetEntryId, targetEntryId);
          }
        } else {
          onSelectEntries([targetEntryId], targetEntryId, targetEntryId);
        }
        focusRow(targetEntryId);
      };
      const currentIndex = focusedEntryId ? entryIds.indexOf(focusedEntryId) : -1;
      if (event.key === "F2" && selectedCount === 1) {
        event.preventDefault();
        onRenameSelected();
        return;
      }
      if ((event.key === "Backspace" || (event.altKey && event.key === "ArrowLeft")) && currentFolder?.parent_id) {
        event.preventDefault();
        onGoToFolder(currentFolder.parent_id);
        return;
      }
      if (event.key === "?" || (event.shiftKey && event.key === "/")) {
        event.preventDefault();
        onShowShortcuts();
        return;
      }
      if (event.key === "Escape" && selectedSource) {
        event.preventDefault();
        onClosePreview();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        moveFocus(currentIndex < 0 ? 0 : currentIndex + 1, event.shiftKey);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        moveFocus(currentIndex < 0 ? entryIds.length - 1 : currentIndex - 1, event.shiftKey);
        return;
      }
      if (event.key === "Home") {
        event.preventDefault();
        moveFocus(0, event.shiftKey);
        return;
      }
      if (event.key === "End") {
        event.preventDefault();
        moveFocus(entryIds.length - 1, event.shiftKey);
        return;
      }
      if (event.key === "Enter" && focusedEntryId) {
        const focusedEntry = entries.find((entry) => entry.id === focusedEntryId);
        if (focusedEntry) {
          event.preventDefault();
          onOpenEntry(focusedEntry);
          return;
        }
      }
      if (event.key === "Delete" && selectedCount > 0) {
        event.preventDefault();
        onDeleteSelected();
      }
    },
    [
      busy,
      currentFolder?.parent_id,
      entries,
      entryIds,
      focusedEntryId,
      onClosePreview,
      onDeleteSelected,
      onGoToFolder,
      onOpenEntry,
      onRenameSelected,
      onSelectEntries,
      onShowShortcuts,
      selectedCount,
      selectedEntryIds,
      selectedSource,
      selectionAnchorEntryId,
    ],
  );
  return (
    <aside className="explorer-pane filesystem-pane" aria-label="Files" onKeyDown={handleExplorerKeyDown}>
      <div className="file-view-tabs" role="tablist" aria-label="File views">
        <button
          type="button"
          role="tab"
          aria-selected={activeFileView === "explorer"}
          className={activeFileView === "explorer" ? "view-tab active" : "view-tab"}
          onClick={() => onActiveFileViewChange("explorer")}
        >
          Explorer
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeFileView === "library"}
          className={activeFileView === "library" ? "view-tab active" : "view-tab"}
          onClick={() => onActiveFileViewChange("library")}
        >
          Library
        </button>
      </div>
      <div className="explorer-commandbar">
        <button type="button" className="secondary-button" onClick={onCreateFolder} disabled={busy || searching}>
          New Folder
        </button>
        <div
          className="explorer-selection-summary"
          title={selectedFileEntries.map((entry) => entry.path).join(", ") || "Arrow keys move, Shift extends, F2 renames, Alt+Left goes up, Delete removes"}
        >
          <strong>{selectedFileLabel}</strong>
          <span>{selectedFileEntries.slice(0, 3).map((entry) => entry.name).join(", ") || "No ready files"}</span>
        </div>
        <button type="button" className="icon-button" onClick={onShowShortcuts} aria-label="Keyboard shortcuts" title="Keyboard shortcuts">
          ?
        </button>
      </div>

      <div className="explorer-filterbar">
        <label className="filesystem-query">
          <span>Query</span>
          <input
            value={sourceQuery}
            onChange={(event) => onSourceQueryChange(event.currentTarget.value)}
            placeholder="Find in this folder"
          />
        </label>
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
      {activeFileView === "library" ? (
        <LibrarySearchView
          busy={busy}
          libraryQuery={libraryQuery}
          libraryResultCount={libraryResultCount}
          libraryResults={libraryResults}
          librarySearching={librarySearching}
          libraryTagMatchMode={libraryTagMatchMode}
          selectedSourceIdSet={selectedSourceIdSet}
          selectedTagIdSet={selectedExplorerTagIdSet}
          tags={tags}
          onSelectResults={onSelectLibraryResults}
          onOpenSource={(sourceId) => {
            const entry = Object.values(sourceEntriesById).find((item) => item.source_id === sourceId) ?? null;
            if (entry) {
              onSelectEntries([entry.id], entry.id, entry.id);
            }
            onActiveFileViewChange("explorer");
            onSourceQueryChange("");
            onOpenSource(sourceId);
          }}
          onQueryChange={onLibraryQueryChange}
          onRunSearch={onRunLibrarySearch}
          onTagMatchModeChange={onLibraryTagMatchModeChange}
          onToggleSourceSelection={onToggleLibrarySourceSelection}
          onToggleTag={onToggleExplorerTag}
        />
      ) : (
        <>

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

      <div
        ref={previewGridRef}
        className={selectedSource ? "filesystem-layout has-preview" : "filesystem-layout"}
        style={selectedSource ? previewLayoutStyle : undefined}
      >
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
            busy={busy}
            maxDepth={researchMaxDepth}
            maxSources={researchMaxSources}
            query={researchQuery}
            result={researchResult}
            seedType={researchSeedType}
            sourceEntriesById={sourceEntriesById}
            onBuild={onResearchBuild}
            onMaxDepthChange={onResearchMaxDepthChange}
            onMaxSourcesChange={onResearchMaxSourcesChange}
            onQueryChange={onResearchQueryChange}
            onSeedTypeChange={onResearchSeedTypeChange}
          />
        </section>

        {selectedSource ? (
          <>
            <button
              type="button"
              className="preview-splitter"
              role="separator"
              aria-label="Resize preview"
              aria-orientation="vertical"
              aria-valuemin={34}
              aria-valuemax={70}
              aria-valuenow={Math.round(previewSplitPercent)}
              onPointerDown={onPreviewResize}
            />
          <div className="explorer-detail" aria-label="File detail">
            <div className="explorer-detail-header">
              <strong>{selectedSource.display_title}</strong>
              <button type="button" className="icon-button" onClick={onClosePreview} aria-label="Close preview" title="Close preview">
                X
              </button>
            </div>
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
          </>
        ) : null}
      </div>
        </>
      )}
    </aside>
  );
});

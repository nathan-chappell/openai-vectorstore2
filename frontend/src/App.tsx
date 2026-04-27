import type { Entity } from "@openai/chatkit-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CSSProperties,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
} from "react";

import { ChatPane } from "./components/ChatPane";
import { AdminWorkspacePanel } from "./components/AdminWorkspacePanel";
import { DeleteEntriesDialog, ExplorerShortcutDialog } from "./components/ExplorerDialogs";
import { FileExplorer } from "./components/FileExplorer";
import { WorkspaceHeader } from "./components/WorkspaceHeader";
import {
  createFolder,
  createTag,
  deleteFilesystemEntries,
  getAuthenticatedUser,
  getSource,
  listFilesystem,
  listTags,
  listTasks,
  resplitSource,
  searchChunks,
  searchFilesystem,
  setChatKitMetadataGetter,
  updateFilesystemEntry,
  updateSourceTags,
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
  ChatKitClientToolResult,
  DeleteDialogState,
  LibrarySearchResult,
  RevealTarget,
  WorkspaceFileView,
} from "./lib/appTypes";
import {
  asResearchBuildResponse,
  asResearchCandidates,
  asResearchIngested,
} from "./lib/researchUi";
import { fuzzyRankFilesystemEntries } from "./lib/search";
import type {
  AuthUser,
  FilesystemBreadcrumb,
  FilesystemEntrySummary,
  FilesystemListResponse,
  SourceDetail,
  TagMatchMode,
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
  const [folderBackStack, setFolderBackStack] = useState<Array<string | null>>([]);
  const [folderForwardStack, setFolderForwardStack] = useState<Array<string | null>>([]);
  const [activeFileView, setActiveFileView] = useState<WorkspaceFileView>("explorer");
  const [selectedExplorerTagIds, setSelectedExplorerTagIds] = useState<string[]>([]);
  const [libraryQuery, setLibraryQuery] = useState("");
  const [libraryTagMatchMode, setLibraryTagMatchMode] = useState<TagMatchMode>("all");
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
  const [deleteDialog, setDeleteDialog] = useState<DeleteDialogState | null>(null);
  const [shortcutDialogOpen, setShortcutDialogOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
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
  const canGoBackFolder = folderBackStack.length > 0;
  const canGoForwardFolder = folderForwardStack.length > 0;

  const selectedExplorerTagIdSet = useMemo(() => new Set(selectedExplorerTagIds), [selectedExplorerTagIds]);
  const selectedEntryIdSet = useMemo(() => new Set(selectedEntryIds), [selectedEntryIds]);
  const selectedSourceIdSet = useMemo(() => new Set(selectedSourceIds), [selectedSourceIds]);
  const selectedSourceTagDraftIdSet = useMemo(() => new Set(selectedSourceTagDraftIds), [selectedSourceTagDraftIds]);
  const folderEntries = filesystem?.entries ?? [];
  const visibleEntries = folderEntries;
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

  const focusFirstExplorerRow = useCallback((): void => {
    window.requestAnimationFrame(() => {
      document.querySelector<HTMLElement>(".file-rows [role='row']")?.focus();
    });
  }, []);

  const clearExplorerSelection = useCallback((): void => {
    setSelectedEntryIds([]);
    setSelectedSourceIds([]);
    setFocusedEntryId(null);
    setSelectionAnchorEntryId(null);
    setSelectedSource(null);
  }, []);

  const navigateToFolder = useCallback(
    (folderId: string | null): void => {
      clearExplorerSelection();
      if (folderId !== currentFolderId) {
        setFolderBackStack((current) => [...current, currentFolderId]);
        setFolderForwardStack([]);
      }
      void loadFolder(folderId).then(focusFirstExplorerRow);
    },
    [clearExplorerSelection, currentFolderId, focusFirstExplorerRow, loadFolder],
  );

  const goBackFolder = useCallback((): void => {
    const targetFolderId = folderBackStack.at(-1);
    if (targetFolderId === undefined) {
      return;
    }
    clearExplorerSelection();
    setFolderBackStack((current) => current.slice(0, -1));
    setFolderForwardStack((current) => [currentFolderId, ...current]);
    void loadFolder(targetFolderId).then(focusFirstExplorerRow);
  }, [clearExplorerSelection, currentFolderId, focusFirstExplorerRow, folderBackStack, loadFolder]);

  const goForwardFolder = useCallback((): void => {
    const targetFolderId = folderForwardStack[0];
    if (targetFolderId === undefined) {
      return;
    }
    clearExplorerSelection();
    setFolderForwardStack((current) => current.slice(1));
    setFolderBackStack((current) => [...current, currentFolderId]);
    void loadFolder(targetFolderId).then(focusFirstExplorerRow);
  }, [clearExplorerSelection, currentFolderId, focusFirstExplorerRow, folderForwardStack, loadFolder]);

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
        navigateToFolder(entry.id);
        return;
      }
      if (entry.source_id) {
        void openSource(entry.source_id);
      }
    },
    [navigateToFolder, openSource],
  );

  const goToFolder = useCallback(
    (folderId: string | null): void => {
      navigateToFolder(folderId);
    },
    [navigateToFolder],
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
    async (
      mode: "replace" | "append",
      options: { query?: string; tagIds?: string[]; tagMatchMode?: TagMatchMode } = {},
    ): Promise<void> => {
      const query = (options.query ?? libraryQuery).trim() || DEFAULT_LIBRARY_QUERY;
      const tagIds = options.tagIds ?? selectedExplorerTagIds;
      const tagMatchMode = options.tagMatchMode ?? libraryTagMatchMode;
      setLibrarySearching(true);
      setStatus(`${mode === "append" ? "Appending" : "Searching"} semantic results for "${query}".`);
      try {
        const response = await searchChunks({
          query,
          tagIds,
          tagMatchMode,
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

  const toggleExplorerTag = useCallback(
    (tagId: string): void => {
      const nextTagIds = selectedExplorerTagIds.includes(tagId)
        ? selectedExplorerTagIds.filter((id) => id !== tagId)
        : [...selectedExplorerTagIds, tagId];
      setSelectedExplorerTagIds(nextTagIds);
      void runLibrarySearch("replace", { tagIds: nextTagIds });
    },
    [runLibrarySearch, selectedExplorerTagIds],
  );

  const changeLibraryTagMatchMode = useCallback(
    (value: TagMatchMode): void => {
      setLibraryTagMatchMode(value);
      if (selectedExplorerTagIds.length) {
        void runLibrarySearch("replace", { tagMatchMode: value });
      }
    },
    [runLibrarySearch, selectedExplorerTagIds],
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
    async ({ sourceId, entryId }: RevealTarget): Promise<ChatKitClientToolResult> => {
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
        if (revealedEntry.parent_id !== currentFolderId) {
          setFolderBackStack((current) => [...current, currentFolderId]);
          setFolderForwardStack([]);
        }
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
    [cacheEntries, currentFolderId, loadFolder, openSource, scheduleClientToolUiUpdate],
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
    async (toolCall: ChatKitClientToolCall): Promise<ChatKitClientToolResult> => {
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
          if (targetFolderId) {
            setSelectedExplorerTagIds((current) => current.length ? [] : current);
            setSelectedEntryIds((current) => current.length ? [] : current);
            setSelectedSourceIds((current) => current.length ? [] : current);
            setFocusedEntryId(null);
            setSelectionAnchorEntryId(null);
            setSelectedSource(null);
            if (targetFolderId !== currentFolderId) {
              setFolderBackStack((current) => [...current, currentFolderId]);
              setFolderForwardStack([]);
            }
            await loadFolder(targetFolderId);
          }
          if (candidates.length || result) {
            const candidateCount = result?.candidates.length ?? candidates.length;
            const indexedCount = result?.ingested.length ?? ingested.length;
            setStatus(
              `Research library${query ? ` for "${query}"` : ""} found ${candidateCount} candidate${
                candidateCount === 1 ? "" : "s"
              }${indexedCount ? ` and queued ${indexedCount} for indexing` : ""}.`,
            );
          } else if (ingested.length) {
            setStatus(`Queued ${ingested.length} research source${ingested.length === 1 ? "" : "s"} for indexing.`);
          }
        });
        return { ok: true, candidate_count: result?.candidates.length ?? candidates.length, target_folder_id: targetFolderId };
      }
      return { ok: false, message: `Unknown client tool: ${toolCall.name}` };
    },
    [currentFolderId, loadFolder, revealFileInExplorer, scheduleClientToolUiUpdate],
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
        adminOpen={adminOpen}
        onRefresh={() => void refreshAll()}
        onToggleAdmin={() => setAdminOpen((current) => !current)}
      />

      {adminOpen && user ? <AdminWorkspacePanel user={user} /> : null}

      <section
        ref={workspaceGridRef}
        className={`workspace-grid${adminOpen && user ? " admin-workspace-hidden" : ""}`}
        style={workspaceStyle}
        aria-label="Indexed file workspace"
      >
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
          previewGridRef={previewGridRef}
          previewLayoutStyle={previewLayoutStyle}
          previewSplitPercent={previewSplitPercent}
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
          tags={tags}
          uploadGuidance={uploadGuidance}
          onActiveFileViewChange={setActiveFileView}
          onChooseEntries={chooseEntries}
          onCreateFolder={() => void createFolderInCurrentFolder()}
          onCreateTag={() => void createExplorerTag()}
          onDeleteSelected={requestDeleteSelectedEntries}
          onDropEntries={(entryIds, folderId) => void moveEntriesToFolder(entryIds, folderId)}
          onGoBackFolder={goBackFolder}
          onGoForwardFolder={goForwardFolder}
          onGoToFolder={goToFolder}
          onNewTagNameChange={setNewTagName}
          onClosePreview={() => setSelectedSource(null)}
          onOpenEntry={openEntry}
          onOpenSource={(sourceId) => void revealFileInExplorer({ sourceId })}
          onPreviewResize={beginPreviewResize}
          onRenameSelected={() => void renameFocusedEntry()}
          onResplit={() => void resplitSelectedSource()}
          onRunLibrarySearch={(mode) => void runLibrarySearch(mode)}
          onSaveTags={() => void saveSelectedSourceTags()}
          onSelectEntries={applyExplorerSelection}
          onShowShortcuts={() => setShortcutDialogOpen(true)}
          onTagToggle={toggleSelectedSourceTagDraft}
          onLibraryQueryChange={setLibraryQuery}
          onLibraryTagMatchModeChange={changeLibraryTagMatchMode}
          onSelectLibraryResults={selectLibraryResultsForChat}
          onToggleLibrarySourceSelection={toggleLibrarySourceSelection}
          onToggleExplorerTag={toggleExplorerTag}
          onUploadGuidanceChange={setUploadGuidance}
          canGoBackFolder={canGoBackFolder}
          canGoForwardFolder={canGoForwardFolder}
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

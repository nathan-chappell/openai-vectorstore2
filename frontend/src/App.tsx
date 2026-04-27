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
} from "./lib/api";
import {
  DEFAULT_LIBRARY_QUERY,
  DEFAULT_SPLIT_GUIDANCE,
  ENTITY_FILE_HISTORY_LIMIT,
  EXPLORER_RENDER_LIMIT,
  PREVIEW_SPLIT_STORAGE_KEY,
  WORKSPACE_SPLIT_STORAGE_KEY,
} from "./lib/appConstants";
import type {
  AppProps,
  ChatResultItem,
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
import { isActiveTask, stringAttribute } from "./lib/uiFormat";
import {
  clamp,
  isEditableShortcutTarget,
  readStoredPreviewSplit,
  readStoredWorkspaceSplit,
  sameStringArray,
  stringFromUnknown,
} from "./lib/uiState";

type EntitySearchItem = {
  key: string;
  title: string;
  path: string;
  sourceId: string;
  entryId: string | null;
  icon: string;
  searchableText: string;
};

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
  const [chatResults, setChatResults] = useState<ChatResultItem[]>([]);
  const [selectedEntryIds, setSelectedEntryIds] = useState<string[]>([]);
  const [focusedEntryId, setFocusedEntryId] = useState<string | null>(null);
  const [selectionAnchorEntryId, setSelectionAnchorEntryId] = useState<string | null>(null);
  const [selectedSource, setSelectedSource] = useState<SourceDetail | null>(null);
  const [uploadGuidance, setUploadGuidance] = useState(DEFAULT_SPLIT_GUIDANCE);
  const [deleteDialog, setDeleteDialog] = useState<DeleteDialogState | null>(null);
  const [shortcutDialogOpen, setShortcutDialogOpen] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [status, setStatus] = useState("Opening files.");
  const [busy, setBusy] = useState(false);
  const workspaceGridRef = useRef<HTMLElement | null>(null);
  const previewGridRef = useRef<HTMLDivElement | null>(null);
  const currentFolderIdRef = useRef<string | null>(null);
  const selectedSourceIdRef = useRef<string | null>(null);
  const knownEntriesRef = useRef<Record<string, FilesystemEntrySummary>>({});
  const entitySearchItemsRef = useRef<EntitySearchItem[]>([]);
  const tagsRef = useRef<TagSummary[]>([]);
  const clientToolUiQueueRef = useRef<Array<() => void | Promise<void>>>([]);
  const clientToolUiFlushRef = useRef<number | null>(null);
  const [workspaceSplitPercent, setWorkspaceSplitPercent] = useState(() => readStoredWorkspaceSplit());
  const [previewSplitPercent, setPreviewSplitPercent] = useState(() => readStoredPreviewSplit());
  const canGoBackFolder = folderBackStack.length > 0;
  const canGoForwardFolder = folderForwardStack.length > 0;

  const selectedExplorerTagIdSet = useMemo(() => new Set(selectedExplorerTagIds), [selectedExplorerTagIds]);
  const selectedEntryIdSet = useMemo(() => new Set(selectedEntryIds), [selectedEntryIds]);
  const folderEntries = filesystem?.entries ?? [];
  const visibleEntries = folderEntries;
  const selectedSourceId = selectedSource?.id ?? null;
  currentFolderIdRef.current = currentFolderId;
  selectedSourceIdRef.current = selectedSourceId;
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

  const rememberEntitySearchItems = useCallback((items: EntitySearchItem[]): void => {
    if (!items.length) {
      return;
    }
    const seenKeys = new Set<string>();
    const nextItems: EntitySearchItem[] = [];
    for (const item of [...items, ...entitySearchItemsRef.current]) {
      if (seenKeys.has(item.key)) {
        continue;
      }
      seenKeys.add(item.key);
      nextItems.push(item);
      if (nextItems.length >= ENTITY_FILE_HISTORY_LIMIT) {
        break;
      }
    }
    entitySearchItemsRef.current = nextItems;
  }, []);

  const cacheEntries = useCallback((entries: FilesystemEntrySummary[]): void => {
    setKnownEntries((current) => {
      const next = { ...current };
      for (const entry of entries) {
        next[entry.id] = entry;
      }
      return next;
    });
    rememberEntitySearchItems(entries.flatMap((entry) => entitySearchItemFromEntry(entry) ?? []));
  }, [rememberEntitySearchItems]);

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
      const activeSourceId = selectedSourceIdRef.current;
      const activeFolderId = currentFolderIdRef.current;
      const detailPromise = activeSourceId
        ? getSource(activeSourceId).catch(() => null)
        : Promise.resolve<SourceDetail | null>(null);
      const [me, tagList, taskList, detail] = await Promise.all([getAuthenticatedUser(), listTags(), listTasks(), detailPromise]);
      setUser(me);
      setTags(tagList);
      setTasks(taskList.tasks);
      if (detail) {
        setSelectedSource(detail);
      }
      const response = await loadFolder(activeFolderId);
      const activeTask = taskList.tasks.find(isActiveTask);
      setStatus(activeTask ? `${activeTask.kind} ${activeTask.status}: ${activeTask.title}.` : `Ready at ${response.current.path}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not load the workspace.");
    } finally {
      setBusy(false);
    }
  }, [loadFolder]);

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
    }));
    return () => setChatKitMetadataGetter(null);
  }, []);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key.toLowerCase() !== "r" || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
        return;
      }
      if (busy || isEditableShortcutTarget(event.target)) {
        return;
      }
      event.preventDefault();
      void refreshAll();
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, refreshAll]);

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

  const applyExplorerSelection = useCallback(
    (entryIds: string[], focusedEntryId: string, anchorEntryId: string | null): void => {
      const nextEntryIds = Array.from(new Set(entryIds));
      const nextAnchorEntryId = anchorEntryId ?? focusedEntryId;
      setSelectedEntryIds((current) => (sameStringArray(current, nextEntryIds) ? current : nextEntryIds));
      setFocusedEntryId((current) => current === focusedEntryId ? current : focusedEntryId);
      setSelectionAnchorEntryId((current) => current === nextAnchorEntryId ? current : nextAnchorEntryId);
      const focusedEntry = knownEntries[focusedEntryId] ?? visibleEntries.find((entry) => entry.id === focusedEntryId);
      if (focusedEntry?.source_id) {
        void openSource(focusedEntry.source_id);
        return;
      }
      setSelectedSource(null);
    },
    [knownEntries, openSource, visibleEntries],
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
        rememberEntitySearchItems(nextResults.map(entitySearchItemFromLibraryResult));
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
    [libraryQuery, libraryTagMatchMode, rememberEntitySearchItems, selectedExplorerTagIds],
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
          await openSource(revealedEntry.source_id);
        } else {
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
      const entries = fuzzyRankEntitySearchItems(entitySearchItemsRef.current, query).slice(0, 12);
      return entries.map((entry) => {
        const data: Record<string, string> = {
          kind: "file",
          path: entry.path,
          source_id: entry.sourceId,
        };
        if (entry.entryId) {
          data.entry_id = entry.entryId;
        }
        return {
          id: entry.sourceId,
          title: entry.title,
          icon: entry.icon,
          interactive: true,
          group: "Files",
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
      if (toolCall.name === "show_results") {
        const nextResults = chatResultItemsFromClientTool(toolCall.params, knownEntriesRef.current);
        scheduleClientToolUiUpdate(() => {
          if (nextResults.length) {
            setChatResults((current) => mergeChatResults(nextResults, current));
            rememberEntitySearchItems(nextResults.map(entitySearchItemFromChatResult));
            setActiveFileView("results");
            setStatus(
              `Added ${nextResults.length} chat result${nextResults.length === 1 ? "" : "s"} to the Results view.`,
            );
          }
        });
        return { ok: true, result_count: nextResults.length };
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
    [currentFolderId, loadFolder, rememberEntitySearchItems, revealFileInExplorer, scheduleClientToolUiUpdate],
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
          chatResults={chatResults}
          previewGridRef={previewGridRef}
          previewLayoutStyle={previewLayoutStyle}
          previewSplitPercent={previewSplitPercent}
          selectedEntryIds={selectedEntryIds}
          selectedEntryIdSet={selectedEntryIdSet}
          selectedExplorerTagIdSet={selectedExplorerTagIdSet}
          selectedSource={selectedSource}
          selectionAnchorEntryId={selectionAnchorEntryId}
          tags={tags}
          uploadGuidance={uploadGuidance}
          onActiveFileViewChange={setActiveFileView}
          onChooseEntries={chooseEntries}
          onCreateFolder={() => void createFolderInCurrentFolder()}
          onDeleteSelected={requestDeleteSelectedEntries}
          onDropEntries={(entryIds, folderId) => void moveEntriesToFolder(entryIds, folderId)}
          onGoBackFolder={goBackFolder}
          onGoForwardFolder={goForwardFolder}
          onGoToFolder={goToFolder}
          onClosePreview={() => setSelectedSource(null)}
          onOpenEntry={openEntry}
          onOpenSource={(sourceId) => void openSource(sourceId)}
          onPreviewResize={beginPreviewResize}
          onRenameSelected={() => void renameFocusedEntry()}
          onResplit={() => void resplitSelectedSource()}
          onRunLibrarySearch={(mode) => void runLibrarySearch(mode)}
          onClearChatResults={() => setChatResults([])}
          onSelectEntries={applyExplorerSelection}
          onShowShortcuts={() => setShortcutDialogOpen(true)}
          onLibraryQueryChange={setLibraryQuery}
          onLibraryTagMatchModeChange={changeLibraryTagMatchMode}
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

function entitySearchItemFromEntry(entry: FilesystemEntrySummary): EntitySearchItem | null {
  if (entry.kind !== "file" || !entry.source_id) {
    return null;
  }
  const title = entry.path || entry.name;
  return {
    key: entry.source_id,
    title,
    path: entry.path,
    sourceId: entry.source_id,
    entryId: entry.id,
    icon: "lucide:file-text",
    searchableText: [
      entry.name,
      entry.path,
      entry.description,
      entry.summary,
      entry.source_kind,
      entry.media_type,
      ...entry.suggested_tags,
      ...entry.tags.map((tag) => tag.name),
    ]
      .filter(Boolean)
      .join(" "),
  };
}

function entitySearchItemFromLibraryResult(result: LibrarySearchResult): EntitySearchItem {
  const { hit, entry } = result;
  const title = entry?.path ?? stringAttribute(hit.attributes, "virtual_path") ?? hit.source_title;
  const path = entry?.path ?? stringAttribute(hit.attributes, "virtual_path") ?? hit.original_filename;
  return {
    key: hit.source_file_id,
    title,
    path,
    sourceId: hit.source_file_id,
    entryId: entry?.id ?? null,
    icon: "lucide:file-text",
    searchableText: [
      title,
      path,
      hit.source_title,
      hit.original_filename,
      hit.title,
      hit.summary,
      hit.text,
      ...hit.tags,
      entry?.description,
      entry?.summary,
    ]
      .filter(Boolean)
      .join(" "),
  };
}

function entitySearchItemFromChatResult(result: ChatResultItem): EntitySearchItem {
  const title = result.path ?? result.name;
  return {
    key: result.sourceId,
    title,
    path: result.path ?? result.name,
    sourceId: result.sourceId,
    entryId: result.entryId,
    icon: "lucide:file-text",
    searchableText: [
      result.name,
      result.path,
      result.sourceType,
      result.title,
      result.summary,
      result.text,
      result.locator,
      result.origin,
      result.query,
    ]
      .filter(Boolean)
      .join(" "),
  };
}

function mergeChatResults(incoming: ChatResultItem[], current: ChatResultItem[]): ChatResultItem[] {
  const byKey = new Map<string, ChatResultItem>();
  for (const result of current) {
    byKey.set(result.key, result);
  }
  for (const result of incoming) {
    const existing = byKey.get(result.key);
    byKey.set(result.key, existing ? { ...existing, ...result, seenCount: existing.seenCount + 1 } : result);
  }
  return [...incoming.map((result) => byKey.get(result.key) ?? result), ...current.filter((result) => !incoming.some((item) => item.key === result.key))].slice(0, 100);
}

function chatResultItemsFromClientTool(
  params: Record<string, unknown>,
  knownEntries: Record<string, FilesystemEntrySummary>,
): ChatResultItem[] {
  const origin = stringFromUnknown(params.origin) ?? "chat";
  const query = stringFromUnknown(params.query);
  const rawResults = firstArray(params.results, params.sources, params.hits);
  const entriesBySourceId = new Map(
    Object.values(knownEntries)
      .filter((entry) => entry.source_id)
      .map((entry) => [entry.source_id as string, entry]),
  );
  return rawResults.flatMap((rawResult) => {
    if (!isRecord(rawResult)) {
      return [];
    }
    const sourceId = stringFromUnknown(rawResult.id) ?? stringFromUnknown(rawResult.source_id) ?? stringFromUnknown(rawResult.source_file_id);
    if (!sourceId) {
      return [];
    }
    const entry = entriesBySourceId.get(sourceId) ?? null;
    const chunkId = stringFromUnknown(rawResult.chunk_id);
    const title = stringFromUnknown(rawResult.title);
    const locator = stringFromUnknown(rawResult.locator);
    const key = [sourceId, chunkId, title, locator].filter(Boolean).join(":");
    const name =
      entry?.name ??
      stringFromUnknown(rawResult.name) ??
      stringFromUnknown(rawResult.source_title) ??
      title ??
      stringFromUnknown(rawResult.original_filename) ??
      "Source";
    const score = numberFromUnknown(rawResult.score);
    return [
      {
        key,
        sourceId,
        entryId: entry?.id ?? null,
        name,
        path: entry?.path ?? stringFromUnknown(rawResult.path) ?? stringFromUnknown(rawResult.virtual_path),
        sourceType: entry?.source_kind ?? stringFromUnknown(rawResult.type) ?? stringFromUnknown(rawResult.source_kind),
        score: score === null ? null : Math.max(0, Math.min(1, score)),
        title,
        summary: stringFromUnknown(rawResult.summary),
        text: stringFromUnknown(rawResult.text),
        locator,
        origin,
        query,
        seenCount: 1,
      },
    ];
  });
}

function firstArray(...values: unknown[]): unknown[] {
  for (const value of values) {
    if (Array.isArray(value)) {
      return value;
    }
  }
  return [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numberFromUnknown(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function fuzzyRankEntitySearchItems(items: EntitySearchItem[], query: string): EntitySearchItem[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  if (!normalizedQuery) {
    return items;
  }
  return items
    .map((item) => ({ item, score: fuzzyEntityScore(item, normalizedQuery) }))
    .filter((result) => result.score > 0)
    .sort((left, right) => right.score - left.score || left.item.title.localeCompare(right.item.title))
    .map((result) => result.item);
}

function fuzzyEntityScore(item: EntitySearchItem, normalizedQuery: string): number {
  const candidate = item.searchableText.toLocaleLowerCase();
  if (!candidate) {
    return 0;
  }
  if (candidate === normalizedQuery) {
    return 100;
  }
  if (candidate.startsWith(normalizedQuery)) {
    return 80;
  }
  if (candidate.includes(normalizedQuery)) {
    return 60;
  }
  return isOrderedSubsequence(normalizedQuery, candidate) ? 30 + Math.min(20, normalizedQuery.length) : 0;
}

function isOrderedSubsequence(needle: string, haystack: string): boolean {
  let needleIndex = 0;
  for (const character of haystack) {
    if (character === needle[needleIndex]) {
      needleIndex += 1;
      if (needleIndex === needle.length) {
        return true;
      }
    }
  }
  return false;
}

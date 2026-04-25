import { ChatKit, type UseChatKitOptions, useChatKit } from "@openai/chatkit-react";
import { memo, useCallback, useEffect, useMemo, useState } from "react";

import {
  authenticatedFetch,
  createTag,
  deleteSource,
  getAuthenticatedUser,
  getChatKitConfig,
  getSource,
  listSources,
  listTags,
  listTasks,
  previewSemanticSplit,
  readSourceContentBlob,
  resplitSource,
  setChatKitMetadataGetter,
  updateSourceTags,
  uploadSource,
} from "./lib/api";
import type {
  AuthUser,
  ChunkSummary,
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
const SOURCE_PAGE_SIZE = 100;
const EXPLORER_RENDER_LIMIT = 250;
const SOURCE_TAG_LIMIT = 8;

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

export function App({ authMode }: AppProps) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [tags, setTags] = useState<TagSummary[]>([]);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [selectedSource, setSelectedSource] = useState<SourceDetail | null>(null);
  const [sourceQuery, setSourceQuery] = useState("");
  const [selectedExplorerTagIds, setSelectedExplorerTagIds] = useState<string[]>([]);
  const [newTagName, setNewTagName] = useState("");
  const [uploadGuidance, setUploadGuidance] = useState("Split by complete ideas and preserve page, line, or speaker boundaries.");
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [splitPreview, setSplitPreview] = useState<SplitPreviewResponse | null>(null);
  const [selectedSourceTagDraftIds, setSelectedSourceTagDraftIds] = useState<string[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [status, setStatus] = useState("Booting the semantic library.");
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
      setStatus("Upload queued. Semantic chunks will publish in the background.");
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

      <section className="workspace-grid" aria-label="Semantic workspace">
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

        <aside className="chat-panel" aria-label="Semantic copilot">
          <ChatPane selectedSourceIds={selectedSourceIds} />
        </aside>
      </section>
    </main>
  );
}

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
              placeholder="Semantic splitting guidance"
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
        <p>Open files here, then use the file checkboxes to scope ChatKit.</p>
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
              <h3>Chunk Map</h3>
              <span>
                {visibleChunks.length}
                {selectedSource.chunks.length > visibleChunks.length ? ` of ${selectedSource.chunks.length}` : ""}
              </span>
            </div>
            <div className="chunk-list">
              {visibleChunks.map((chunk) => (
                <ChunkRow key={chunk.id} chunk={chunk} />
              ))}
              {!visibleChunks.length ? <p className="empty-state">No semantic chunks yet.</p> : null}
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
              <dt>Chunks</dt>
              <dd>{selectedSource.chunk_count}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd>{formatDate(selectedSource.updated_at)}</dd>
            </div>
          </dl>
          {selectedSource.error_message ? <p className="error-message">{selectedSource.error_message}</p> : null}
          <label className="field-label">
            Split guidance
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
      <span>Semantic chunk preview is available below.</span>
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

const ChatPane = memo(function ChatPane({ selectedSourceIds }: { selectedSourceIds: string[] }) {
  const chatKitConfig = getChatKitConfig();
  const selectedFileScopeLabel =
    selectedSourceIds.length === 1 ? "Ask about the selected file." : `Ask about the ${selectedSourceIds.length} selected files.`;
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
          : "Select files in the explorer, then ask me to search, branch, answer, image, or narrate from them.",
        prompts: [
          { label: "Grounded QA", prompt: "Answer my question using semantic chunks and cite the source titles.", icon: "check-circle" },
          { label: "Branch search", prompt: "Run a branch search around this topic and explain the interesting trails.", icon: "sparkle" },
          { label: "Creative synthesis", prompt: "Use the retrieved chunks as inspiration, but separate evidence from speculation.", icon: "bolt" },
        ],
      },
      composer: {
        placeholder: "Ask the semantic library...",
        attachments: {
          enabled: false,
        },
        dictation: { enabled: false },
        models: MODEL_CHOICES.map((choice) => ({ ...choice, default: choice.id === "balanced" })),
      },
      threadItemActions: {
        feedback: false,
      },
    }),
    [chatKitConfig.domainKey, chatKitConfig.url, selectedFileScopeLabel, selectedSourceIds.length],
  );
  const chatKit = useChatKit(options);
  return <ChatKit control={chatKit.control} className="chatkit-element" />;
});

import { memo, useCallback, useMemo } from "react";
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
  RefObject,
} from "react";

import type { LibrarySearchResult, ResearchBuilderSeedKind, WorkspaceFileView } from "../lib/appTypes";
import type {
  FilesystemBreadcrumb,
  FilesystemEntrySummary,
  ResearchLibraryBuildResponse,
  SourceDetail,
  SplitPreviewResponse,
  TagMatchMode,
  TagSummary,
} from "../lib/types";
import { clamp, isEditableShortcutTarget } from "../lib/uiState";
import { FileEntryRow } from "./FileEntryRow";
import { LibrarySearchView } from "./LibrarySearchView";
import { ResearchBuilderPanel } from "./ResearchBuilderPanel";
import { SourcePreview } from "./SourcePreview";

export const FileExplorer = memo(function FileExplorer({
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
  libraryTagMatchMode: TagMatchMode;
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
  onLibraryTagMatchModeChange: (value: TagMatchMode) => void;
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

import { memo, useCallback, useMemo } from "react";
import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
  RefObject,
} from "react";

import type { LibrarySearchResult, WorkspaceFileView } from "../lib/appTypes";
import type {
  FilesystemBreadcrumb,
  FilesystemEntrySummary,
  SourceDetail,
  TagMatchMode,
  TagSummary,
} from "../lib/types";
import { clamp, isEditableShortcutTarget } from "../lib/uiState";
import { FileEntryRow } from "./FileEntryRow";
import { LibrarySearchView } from "./LibrarySearchView";
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
  previewGridRef,
  previewLayoutStyle,
  previewSplitPercent,
  selectedEntryIds,
  selectedEntryIdSet,
  selectedExplorerTagIdSet,
  selectedFileEntries,
  selectedSource,
  selectedSourceIdSet,
  selectedSourceTagChanged,
  selectedSourceTagDraftIdSet,
  selectionAnchorEntryId,
  tags,
  uploadGuidance,
  onActiveFileViewChange,
  onChooseEntries,
  onCreateFolder,
  onCreateTag,
  onDeleteSelected,
  onDropEntries,
  onGoBackFolder,
  onGoForwardFolder,
  onGoToFolder,
  onNewTagNameChange,
  onClosePreview,
  onOpenEntry,
  onOpenSource,
  onPreviewResize,
  onRenameSelected,
  onResplit,
  onRunLibrarySearch,
  onSaveTags,
  onSelectEntries,
  onShowShortcuts,
  onTagToggle,
  onLibraryQueryChange,
  onLibraryTagMatchModeChange,
  onSelectLibraryResults,
  onToggleLibrarySourceSelection,
  onToggleExplorerTag,
  onUploadGuidanceChange,
  canGoBackFolder,
  canGoForwardFolder,
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
  previewGridRef: RefObject<HTMLDivElement | null>;
  previewLayoutStyle: CSSProperties & Record<"--preview-list-width", string>;
  previewSplitPercent: number;
  selectedEntryIds: string[];
  selectedEntryIdSet: Set<string>;
  selectedExplorerTagIdSet: Set<string>;
  selectedFileEntries: FilesystemEntrySummary[];
  selectedSource: SourceDetail | null;
  selectedSourceIdSet: Set<string>;
  selectedSourceTagChanged: boolean;
  selectedSourceTagDraftIdSet: Set<string>;
  selectionAnchorEntryId: string | null;
  tags: TagSummary[];
  uploadGuidance: string;
  onActiveFileViewChange: (view: WorkspaceFileView) => void;
  onChooseEntries: (entry: FilesystemEntrySummary, event: ReactMouseEvent) => void;
  onCreateFolder: () => void;
  onCreateTag: () => void;
  onDeleteSelected: () => void;
  onDropEntries: (entryIds: string[], folderId: string) => void;
  onGoBackFolder: () => void;
  onGoForwardFolder: () => void;
  onGoToFolder: (folderId: string | null) => void;
  onNewTagNameChange: (value: string) => void;
  onClosePreview: () => void;
  onOpenEntry: (entry: FilesystemEntrySummary) => void;
  onOpenSource: (sourceId: string) => void;
  onPreviewResize: (event: ReactPointerEvent<HTMLButtonElement>) => void;
  onRenameSelected: () => void;
  onResplit: () => void;
  onRunLibrarySearch: (mode: "replace" | "append") => void;
  onSaveTags: () => void;
  onSelectEntries: (entryIds: string[], focusedEntryId: string, anchorEntryId: string | null) => void;
  onShowShortcuts: () => void;
  onTagToggle: (tagId: string) => void;
  onLibraryQueryChange: (value: string) => void;
  onLibraryTagMatchModeChange: (value: TagMatchMode) => void;
  onSelectLibraryResults: () => void;
  onToggleLibrarySourceSelection: (sourceId: string) => void;
  onToggleExplorerTag: (tagId: string) => void;
  onUploadGuidanceChange: (value: string) => void;
  canGoBackFolder: boolean;
  canGoForwardFolder: boolean;
}) {
  const selectedCount = selectedEntryIdSet.size;
  const selectedFileLabel =
    selectedFileEntries.length === 1 ? "1 indexed file selected" : `${selectedFileEntries.length} indexed files selected`;
  const dragEntryIds = useMemo(() => Array.from(selectedEntryIdSet), [selectedEntryIdSet]);
  const entryIds = useMemo(() => entries.map((entry) => entry.id), [entries]);
  const previewPane = selectedSource ? (
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
          newTagName={newTagName}
          onCreateTag={onCreateTag}
          onNewTagNameChange={onNewTagNameChange}
          onSaveTags={onSaveTags}
          onTagToggle={onTagToggle}
          onUploadGuidanceChange={onUploadGuidanceChange}
          onResplit={onResplit}
        />
      </div>
    </>
  ) : null;
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
      if (event.key === "ArrowLeft" && !event.shiftKey && !event.altKey && canGoBackFolder) {
        event.preventDefault();
        onGoBackFolder();
        return;
      }
      if (event.key === "ArrowRight" && !event.shiftKey && !event.altKey && canGoForwardFolder) {
        event.preventDefault();
        onGoForwardFolder();
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
      onGoBackFolder,
      onGoForwardFolder,
      onGoToFolder,
      onOpenEntry,
      onRenameSelected,
      onSelectEntries,
      onShowShortcuts,
      selectedCount,
      selectedEntryIds,
      selectedSource,
      selectionAnchorEntryId,
      canGoBackFolder,
      canGoForwardFolder,
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
        <button type="button" className="secondary-button" onClick={onCreateFolder} disabled={busy}>
          New Folder
        </button>
        <div
          className="explorer-selection-summary"
          title={selectedFileEntries.map((entry) => entry.path).join(", ") || "Arrow keys move, Left/Right navigate folder history, F2 renames, Delete removes"}
        >
          <strong>{selectedFileLabel}</strong>
          <span>{selectedFileEntries.slice(0, 3).map((entry) => entry.name).join(", ") || "No ready files"}</span>
        </div>
        <button type="button" className="icon-button" onClick={onShowShortcuts} aria-label="Keyboard shortcuts" title="Keyboard shortcuts">
          ?
        </button>
      </div>
      {activeFileView === "library" ? (
        <div
          ref={previewGridRef}
          className={selectedSource ? "filesystem-layout has-preview" : "filesystem-layout"}
          style={selectedSource ? previewLayoutStyle : undefined}
        >
          <LibrarySearchView
            busy={busy}
            libraryQuery={libraryQuery}
            libraryResultCount={libraryResultCount}
            libraryResults={libraryResults}
            librarySearching={librarySearching}
            libraryTagMatchMode={libraryTagMatchMode}
            previewedSourceId={selectedSource?.id ?? null}
            selectedSourceIdSet={selectedSourceIdSet}
            selectedTagIdSet={selectedExplorerTagIdSet}
            tags={tags}
            onSelectResults={onSelectLibraryResults}
            onOpenSource={onOpenSource}
            onQueryChange={onLibraryQueryChange}
            onRunSearch={onRunLibrarySearch}
            onTagMatchModeChange={onLibraryTagMatchModeChange}
            onToggleSourceSelection={onToggleLibrarySourceSelection}
            onToggleTag={onToggleExplorerTag}
          />
          {previewPane}
        </div>
      ) : (
        <div className="explorer-view-body">
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
                {!entries.length ? <div className="empty-file-list">Folder is empty.</div> : null}
              </div>
            </section>

            {selectedSource ? (
              previewPane
            ) : null}
          </div>
        </div>
      )}
    </aside>
  );
});

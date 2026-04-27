import { useMemo, useState } from "react";

import { DEFAULT_LIBRARY_QUERY } from "../lib/appConstants";
import type { LibrarySearchResult } from "../lib/appTypes";
import type { TagMatchMode, TagSummary } from "../lib/types";
import { formatDate, stringAttribute } from "../lib/uiFormat";

const TAG_PREVIEW_LIMIT = 36;

export function LibrarySearchView({
  busy,
  libraryQuery,
  libraryResultCount,
  libraryResults,
  librarySearching,
  libraryTagMatchMode,
  previewedSourceId,
  selectedTagIdSet,
  tags,
  onOpenSource,
  onQueryChange,
  onRunSearch,
  onTagMatchModeChange,
  onToggleTag,
}: {
  busy: boolean;
  libraryQuery: string;
  libraryResultCount: number;
  libraryResults: LibrarySearchResult[];
  librarySearching: boolean;
  libraryTagMatchMode: TagMatchMode;
  previewedSourceId: string | null;
  selectedTagIdSet: Set<string>;
  tags: TagSummary[];
  onOpenSource: (sourceId: string) => void;
  onQueryChange: (value: string) => void;
  onRunSearch: (mode: "replace" | "append") => void;
  onTagMatchModeChange: (value: TagMatchMode) => void;
  onToggleTag: (tagId: string) => void;
}) {
  const disabled = busy || librarySearching;
  const [showAllTags, setShowAllTags] = useState(false);
  const visibleTags = useMemo(() => {
    const sortedTags = [...tags].sort((left, right) => {
      const leftSelected = selectedTagIdSet.has(left.id);
      const rightSelected = selectedTagIdSet.has(right.id);
      if (leftSelected !== rightSelected) {
        return leftSelected ? -1 : 1;
      }
      if (left.source !== right.source) {
        return left.source === "manual" ? -1 : 1;
      }
      if (left.source_count !== right.source_count) {
        return right.source_count - left.source_count;
      }
      return left.name.localeCompare(right.name);
    });
    if (showAllTags || sortedTags.length <= TAG_PREVIEW_LIMIT) {
      return sortedTags;
    }
    const selectedTags = sortedTags.filter((tag) => selectedTagIdSet.has(tag.id));
    const unselectedLimit = Math.max(TAG_PREVIEW_LIMIT, selectedTags.length) - selectedTags.length;
    return [...selectedTags, ...sortedTags.filter((tag) => !selectedTagIdSet.has(tag.id)).slice(0, unselectedLimit)];
  }, [selectedTagIdSet, showAllTags, tags]);
  const hiddenTagCount = Math.max(0, tags.length - visibleTags.length);
  return (
    <section className="library-search-view" aria-label="Tag and semantic search">
      <div className="library-searchbar">
        <label className="library-query-field">
          <span>Semantic query</span>
          <input
            value={libraryQuery}
            onChange={(event) => onQueryChange(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter") {
                return;
              }
              event.preventDefault();
              onRunSearch(event.ctrlKey || event.metaKey ? "append" : "replace");
            }}
            placeholder={DEFAULT_LIBRARY_QUERY}
          />
        </label>
        <div className="segmented-control" aria-label="Tag match mode">
          <button
            type="button"
            className={libraryTagMatchMode === "all" ? "active" : undefined}
            aria-pressed={libraryTagMatchMode === "all"}
            onClick={() => onTagMatchModeChange("all")}
          >
            All
          </button>
          <button
            type="button"
            className={libraryTagMatchMode === "any" ? "active" : undefined}
            aria-pressed={libraryTagMatchMode === "any"}
            onClick={() => onTagMatchModeChange("any")}
          >
            Any
          </button>
        </div>
        <div className="button-row">
          <button type="button" onClick={() => onRunSearch("replace")} disabled={disabled}>
            Search
          </button>
          <button type="button" className="secondary-button" onClick={() => onRunSearch("append")} disabled={disabled}>
            Append
          </button>
        </div>
      </div>

      <div className={showAllTags ? "library-tag-panel expanded" : "library-tag-panel"} aria-label="Tag filters">
        <div className="library-tag-list">
          {visibleTags.map((tag) => (
            <button
              key={tag.id}
              type="button"
              aria-pressed={selectedTagIdSet.has(tag.id)}
              className={selectedTagIdSet.has(tag.id) ? "tag-chip selected" : "tag-chip"}
              onClick={() => onToggleTag(tag.id)}
              disabled={disabled}
            >
              {tag.name}
            </button>
          ))}
          {hiddenTagCount ? (
            <button type="button" className="tag-chip tag-chip-more" onClick={() => setShowAllTags(true)} disabled={disabled}>
              +{hiddenTagCount} more
            </button>
          ) : null}
          {showAllTags && tags.length > TAG_PREVIEW_LIMIT ? (
            <button type="button" className="tag-chip tag-chip-more" onClick={() => setShowAllTags(false)} disabled={disabled}>
              Less
            </button>
          ) : null}
          {!tags.length ? <span>No tags</span> : null}
        </div>
      </div>

      <div className="library-result-summary">
        <strong>{libraryResults.length} source{libraryResults.length === 1 ? "" : "s"}</strong>
        <span>{libraryResultCount ? `${libraryResultCount} vector hit${libraryResultCount === 1 ? "" : "s"}` : "Press Enter to search."}</span>
      </div>

      <div className="file-list-header library-file-list-header">
        <span>Name</span>
        <span>Relevance</span>
        <span>Match</span>
        <span>Modified</span>
      </div>

      <div className="file-rows library-file-rows" role="treegrid" aria-label="Library search results">
        {libraryResults.map(({ hit, entry }) => {
          const resultName = entry?.name ?? stringAttribute(hit.attributes, "virtual_name") ?? hit.source_title;
          const resultPath = entry?.path ?? stringAttribute(hit.attributes, "virtual_path") ?? hit.original_filename;
          const active = previewedSourceId === hit.source_file_id;
          const rowClassName = [
            active ? "active-file-row" : "",
          ]
            .filter(Boolean)
            .join(" ");
          const scorePercent = Math.round(hit.score * 100);
          return (
            <div
              key={hit.source_file_id}
              role="row"
              tabIndex={0}
              aria-selected={active}
              className={rowClassName || undefined}
              data-source-id={hit.source_file_id}
              onClick={() => onOpenSource(hit.source_file_id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onOpenSource(hit.source_file_id);
                }
              }}
            >
              <span role="cell" className="filesystem-name-cell">
                <span className="entry-icon file-icon">{entry?.source_kind?.toUpperCase().slice(0, 4) || "FILE"}</span>
                <span>
                  <strong>{resultName}</strong>
                  <small>{resultPath}</small>
                </span>
              </span>
              <span role="cell" className="status-cell">
                <span className="status-badge status-ready">{scorePercent}%</span>
                <span className="status-progress-track" aria-label={`${scorePercent}% relevance`}>
                  <span style={{ width: `${scorePercent}%` }} />
                </span>
              </span>
              <span role="cell" className="muted-cell library-hit-text">{hit.text || hit.summary}</span>
              <span role="cell" className="muted-cell">{entry ? formatDate(entry.updated_at) : ""}</span>
            </div>
          );
        })}
        {!libraryResults.length ? <div className="empty-file-list">No semantic results yet.</div> : null}
      </div>
    </section>
  );
}

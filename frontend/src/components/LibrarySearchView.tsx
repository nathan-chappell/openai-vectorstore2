import { DEFAULT_LIBRARY_QUERY } from "../lib/appConstants";
import type { LibrarySearchResult } from "../lib/appTypes";
import type { TagSummary } from "../lib/types";
import { stringAttribute } from "../lib/uiFormat";

export function LibrarySearchView({
  busy,
  libraryQuery,
  libraryResultCount,
  libraryResults,
  librarySearching,
  previewedSourceId,
  selectedTagId,
  tags,
  onOpenSource,
  onQueryChange,
  onRunSearch,
  onTagChange,
}: {
  busy: boolean;
  libraryQuery: string;
  libraryResultCount: number;
  libraryResults: LibrarySearchResult[];
  librarySearching: boolean;
  previewedSourceId: string | null;
  selectedTagId: string | null;
  tags: TagSummary[];
  onOpenSource: (sourceId: string) => void;
  onQueryChange: (value: string) => void;
  onRunSearch: (mode: "replace" | "append") => void;
  onTagChange: (tagId: string | null) => void;
}) {
  const disabled = busy || librarySearching;
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
        <label className="tag-select-field">
          <span>Tag</span>
          <select
            value={selectedTagId ?? ""}
            onChange={(event) => onTagChange(event.currentTarget.value || null)}
            disabled={disabled}
          >
            <option value="">All tags</option>
            {tags.map((tag) => (
              <option key={tag.id} value={tag.id}>
                {tag.name}
              </option>
            ))}
          </select>
        </label>
        <div className="button-row">
          <button type="button" onClick={() => onRunSearch("replace")} disabled={disabled}>
            Search
          </button>
          <button type="button" className="secondary-button" onClick={() => onRunSearch("append")} disabled={disabled}>
            Append
          </button>
        </div>
      </div>

      <div className="library-result-summary">
        <strong>{libraryResults.length} source{libraryResults.length === 1 ? "" : "s"}</strong>
        <span>{libraryResultCount ? `${libraryResultCount} vector hit${libraryResultCount === 1 ? "" : "s"}` : "Press Enter to search."}</span>
      </div>

      <div className="file-list-header library-file-list-header">
        <span>Name</span>
        <span>Type</span>
        <span>Relevance</span>
        <span>Match</span>
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
                <strong title={resultPath}>{resultName}</strong>
              </span>
              <span role="cell" className="filesystem-type-cell">
                <span className="entry-icon file-icon">{entry?.source_kind?.toUpperCase().slice(0, 4) || "FILE"}</span>
              </span>
              <span role="cell" className="status-cell">
                <span className="status-badge status-ready">{scorePercent}%</span>
                <span className="status-progress-track" aria-label={`${scorePercent}% relevance`}>
                  <span style={{ width: `${scorePercent}%` }} />
                </span>
              </span>
              <span role="cell" className="muted-cell library-hit-text">{hit.text || hit.summary}</span>
            </div>
          );
        })}
        {!libraryResults.length ? <div className="empty-file-list">No semantic results yet.</div> : null}
      </div>
    </section>
  );
}

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
  libraryTagMatchMode,
  selectedSourceIdSet,
  selectedTagIdSet,
  tags,
  onSelectResults,
  onOpenSource,
  onQueryChange,
  onRunSearch,
  onTagMatchModeChange,
  onToggleSourceSelection,
  onToggleTag,
}: {
  busy: boolean;
  libraryQuery: string;
  libraryResultCount: number;
  libraryResults: LibrarySearchResult[];
  librarySearching: boolean;
  libraryTagMatchMode: "all" | "any";
  selectedSourceIdSet: Set<string>;
  selectedTagIdSet: Set<string>;
  tags: TagSummary[];
  onSelectResults: () => void;
  onOpenSource: (sourceId: string) => void;
  onQueryChange: (value: string) => void;
  onRunSearch: (mode: "replace" | "append") => void;
  onTagMatchModeChange: (value: "all" | "any") => void;
  onToggleSourceSelection: (sourceId: string) => void;
  onToggleTag: (tagId: string) => void;
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

      <div className="library-tag-panel" aria-label="Tag filters">
        {tags.map((tag) => (
          <button
            key={tag.id}
            type="button"
            aria-pressed={selectedTagIdSet.has(tag.id)}
            className={selectedTagIdSet.has(tag.id) ? "tag-chip selected" : "tag-chip"}
            onClick={() => onToggleTag(tag.id)}
          >
            {tag.name}
          </button>
        ))}
        {!tags.length ? <span>No tags</span> : null}
      </div>

      <div className="library-result-summary">
        <strong>{libraryResults.length} source{libraryResults.length === 1 ? "" : "s"}</strong>
        <span>{libraryResultCount ? `${libraryResultCount} vector hit${libraryResultCount === 1 ? "" : "s"}` : "Press Enter to search."}</span>
        <button type="button" className="secondary-button" onClick={onSelectResults} disabled={!libraryResults.length}>
          Select results
        </button>
      </div>

      <div className="library-result-list">
        {libraryResults.map(({ hit, entry }) => {
          const resultName = entry?.name ?? stringAttribute(hit.attributes, "virtual_name") ?? hit.source_title;
          const resultPath = entry?.path ?? stringAttribute(hit.attributes, "virtual_path") ?? hit.original_filename;
          const selected = selectedSourceIdSet.has(hit.source_file_id);
          return (
            <div key={hit.source_file_id} className={selected ? "library-result-row selected-file-row" : "library-result-row"}>
              <input
                aria-label={`Select ${resultName} for chat`}
                checked={selected}
                className="file-select-checkbox"
                onChange={() => onToggleSourceSelection(hit.source_file_id)}
                type="checkbox"
              />
              <button type="button" className="library-result-open" onClick={() => onOpenSource(hit.source_file_id)}>
                <span>
                  <strong>{resultName}</strong>
                  <small>{resultPath}</small>
                </span>
                <span className="library-hit-score">{Math.round(hit.score * 100)}%</span>
                <span className="library-hit-text">{hit.text || hit.summary}</span>
              </button>
            </div>
          );
        })}
        {!libraryResults.length ? <div className="empty-file-list">No semantic results yet.</div> : null}
      </div>
    </section>
  );
}

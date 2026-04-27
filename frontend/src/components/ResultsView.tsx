import type { ChatResultItem } from "../lib/appTypes";

export function ResultsView({
  results,
  previewedSourceId,
  onClear,
  onOpenSource,
}: {
  results: ChatResultItem[];
  previewedSourceId: string | null;
  onClear: () => void;
  onOpenSource: (sourceId: string) => void;
}) {
  return (
    <section className="results-view" aria-label="Chat results">
      <div className="library-result-summary">
        <strong>{results.length} result{results.length === 1 ? "" : "s"}</strong>
        <span>{results.length ? "References from recent chat search and answer tools." : "Chat searches and cited answers will appear here."}</span>
        <button type="button" className="secondary-button" onClick={onClear} disabled={!results.length}>
          Clear
        </button>
      </div>

      <div className="file-list-header results-file-list-header">
        <span>Name</span>
        <span>Type</span>
        <span>Score</span>
        <span>Reference</span>
      </div>

      <div className="file-rows results-file-rows" role="treegrid" aria-label="Chat search results and citations">
        {results.map((result) => {
          const active = previewedSourceId === result.sourceId;
          const scorePercent = result.score === null ? null : Math.round(result.score * 100);
          const reference = result.text || result.summary || result.title || result.locator || result.query || result.origin;
          return (
            <div
              key={result.key}
              role="row"
              tabIndex={0}
              aria-selected={active}
              className={active ? "active-file-row" : undefined}
              data-source-id={result.sourceId}
              onClick={() => onOpenSource(result.sourceId)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onOpenSource(result.sourceId);
                }
              }}
            >
              <span role="cell" className="filesystem-name-cell">
                <strong title={result.path ?? result.name}>{result.name}</strong>
              </span>
              <span role="cell" className="filesystem-type-cell">
                <span className="entry-icon file-icon">{(result.sourceType ?? "file").toUpperCase().slice(0, 4)}</span>
              </span>
              <span role="cell" className="status-cell">
                {scorePercent === null ? <span className="muted-cell">{result.origin}</span> : <span className="status-badge status-ready">{scorePercent}%</span>}
                {scorePercent === null ? null : (
                  <span className="status-progress-track" aria-label={`${scorePercent}% relevance`}>
                    <span style={{ width: `${scorePercent}%` }} />
                  </span>
                )}
              </span>
              <span role="cell" className="muted-cell library-hit-text" title={reference ?? undefined}>
                {result.seenCount > 1 ? `${result.seenCount}x ` : ""}
                {result.locator ? `${result.locator}: ` : ""}
                {reference}
              </span>
            </div>
          );
        })}
        {!results.length ? <div className="empty-file-list">No chat results yet.</div> : null}
      </div>
    </section>
  );
}

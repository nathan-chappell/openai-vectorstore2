import { memo } from "react";

import {
  RESEARCH_SEED_CHOICES,
  RESEARCH_STATUS_LABELS,
  RESEARCH_STATUS_PROGRESS,
} from "../lib/appConstants";
import type { ResearchBuilderSeedKind } from "../lib/appTypes";
import { displayResearchCandidateStatus } from "../lib/researchUi";
import type { FilesystemEntrySummary, ResearchLibraryBuildResponse } from "../lib/types";
import { clamp } from "../lib/uiState";

export const ResearchBuilderPanel = memo(function ResearchBuilderPanel({
  busy,
  maxDepth,
  maxSources,
  query,
  result,
  seedType,
  sourceEntriesById,
  onBuild,
  onMaxDepthChange,
  onMaxSourcesChange,
  onQueryChange,
  onSeedTypeChange,
}: {
  busy: boolean;
  maxDepth: number;
  maxSources: number;
  query: string;
  result: ResearchLibraryBuildResponse | null;
  seedType: ResearchBuilderSeedKind;
  sourceEntriesById: Record<string, FilesystemEntrySummary>;
  onBuild: () => void;
  onMaxDepthChange: (value: number) => void;
  onMaxSourcesChange: (value: number) => void;
  onQueryChange: (value: string) => void;
  onSeedTypeChange: (value: ResearchBuilderSeedKind) => void;
}) {
  const candidates = result?.candidates ?? [];
  const displayCandidates = candidates.map((candidate) => {
    const linkedEntry = candidate.linked_source_file_id ? sourceEntriesById[candidate.linked_source_file_id] : undefined;
    return {
      candidate,
      linkedEntry,
      status: displayResearchCandidateStatus(candidate, linkedEntry),
    };
  });
  const pendingCount = displayCandidates.filter(({ status }) => status === "pending" || status === "approved").length;
  const activeCount = displayCandidates.filter(({ status }) => status === "ingesting").length;
  const ingestedCount = displayCandidates.filter(({ status }) => status === "ingested").length;
  const duplicateCount = Math.max(result?.duplicate_count ?? 0, displayCandidates.filter(({ status }) => status === "duplicate").length);
  const failedCount = displayCandidates.filter(({ status }) => status === "failed").length;
  const hiddenCandidateCount = Math.max(0, candidates.length - 6);
  return (
    <section className="research-builder-strip" aria-label="Research library builder">
      <div className="research-builder-controls">
        <label className="research-query-field">
          <span>Research</span>
          <input
            value={query}
            onChange={(event) => onQueryChange(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && query.trim()) {
                event.preventDefault();
                onBuild();
              }
            }}
            placeholder="Topic or paper title"
          />
        </label>
        <label>
          <span>Seed</span>
          <select
            value={seedType}
            onChange={(event) => onSeedTypeChange(event.currentTarget.value as ResearchBuilderSeedKind)}
          >
            {RESEARCH_SEED_CHOICES.map((choice) => (
              <option key={choice.id} value={choice.id}>
                {choice.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Max</span>
          <input
            type="number"
            min={1}
            max={50}
            value={maxSources}
            onChange={(event) => {
              const nextValue = Number(event.currentTarget.value);
              onMaxSourcesChange(Number.isFinite(nextValue) ? clamp(nextValue, 1, 50) : 1);
            }}
          />
        </label>
        <label>
          <span>Depth</span>
          <input
            type="number"
            min={0}
            max={4}
            value={maxDepth}
            onChange={(event) => {
              const nextValue = Number(event.currentTarget.value);
              onMaxDepthChange(Number.isFinite(nextValue) ? clamp(nextValue, 0, 4) : 0);
            }}
          />
        </label>
        <button type="button" onClick={onBuild} disabled={busy || !query.trim()}>
          Build
        </button>
      </div>

      {result ? (
        <div className="research-builder-results">
          <div className="research-result-summary">
            <strong>
              {candidates.length} candidate{candidates.length === 1 ? "" : "s"}
            </strong>
            <span>
              {result.task.status} | {pendingCount} queued | {activeCount} indexing | {ingestedCount} indexed | {duplicateCount} duplicate
              {duplicateCount === 1 ? "" : "s"} | {failedCount} failed
            </span>
          </div>
          <div className="research-candidate-list" aria-label="Research candidates">
            {displayCandidates.slice(0, 6).map(({ candidate, status }) => {
              const progressPercent = RESEARCH_STATUS_PROGRESS[status];
              const metaParts = [
                candidate.source_type.toUpperCase(),
                candidate.published_at?.slice(0, 10),
                candidate.authors.slice(0, 2).join(", "),
              ].filter(Boolean);
              const detailText = status === "failed" ? (candidate.error_message ?? "Source ingest failed.") : (candidate.summary ?? candidate.description);
              return (
                <article key={candidate.id} className="research-candidate-row">
                  <div>
                    <strong>{candidate.title}</strong>
                    <span>{metaParts.join(" | ") || candidate.normalized_url || candidate.url || "Candidate"}</span>
                    {detailText ? <p>{detailText}</p> : null}
                    {candidate.suggested_tags.length ? <small>{candidate.suggested_tags.slice(0, 4).join(", ")}</small> : null}
                  </div>
                  <div className="research-candidate-actions">
                    <span className={`status-badge status-${status}`}>{RESEARCH_STATUS_LABELS[status]}</span>
                    <span className="research-progress-track" aria-label={`${RESEARCH_STATUS_LABELS[status]} progress`}>
                      <span style={{ width: `${progressPercent}%` }} />
                    </span>
                  </div>
                </article>
              );
            })}
            {hiddenCandidateCount ? <p className="research-candidate-more">+{hiddenCandidateCount} more candidates</p> : null}
            {!candidates.length ? <p className="research-candidate-more">No candidates returned.</p> : null}
          </div>
        </div>
      ) : null}
    </section>
  );
});

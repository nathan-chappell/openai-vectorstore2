import { memo } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

import type { FilesystemEntrySummary } from "../lib/types";
import { entryTypeLabel, formatBytes, formatDate } from "../lib/uiFormat";
import { safeJsonStringArray } from "../lib/uiState";

export const FileEntryRow = memo(function FileEntryRow({
  dragEntryIds,
  entry,
  focused,
  selected,
  onChoose,
  onDropEntries,
  onOpen,
}: {
  dragEntryIds: string[];
  entry: FilesystemEntrySummary;
  focused: boolean;
  selected: boolean;
  onChoose: (entry: FilesystemEntrySummary, event: ReactMouseEvent) => void;
  onDropEntries: (entryIds: string[], folderId: string) => void;
  onOpen: (entry: FilesystemEntrySummary) => void;
}) {
  const rowClassName = [
    selected ? "selected-file-row" : "",
    focused ? "active-file-row" : "",
    entry.kind === "folder" ? "folder-row" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const statusProgressPercent = entry.status === "ready" ? 100 : entry.status === "processing" ? 58 : entry.status === "failed" ? 100 : 0;
  return (
    <div
      role="row"
      tabIndex={0}
      aria-selected={selected}
      className={rowClassName || undefined}
      data-entry-id={entry.id}
      draggable
      onClick={(event) => {
        event.currentTarget.focus();
        onChoose(entry, event);
      }}
      onDoubleClick={() => onOpen(entry)}
      onDragStart={(event) => {
        event.dataTransfer.setData("application/x-entry-ids", JSON.stringify(dragEntryIds.includes(entry.id) ? dragEntryIds : [entry.id]));
        event.dataTransfer.effectAllowed = "move";
      }}
      onDragOver={(event) => {
        if (entry.kind === "folder") {
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
        }
      }}
      onDrop={(event) => {
        if (entry.kind !== "folder") {
          return;
        }
        event.preventDefault();
        const payload = event.dataTransfer.getData("application/x-entry-ids");
        const parsed = safeJsonStringArray(payload);
        if (parsed.length) {
          onDropEntries(parsed, entry.id);
        }
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          onOpen(entry);
        }
      }}
    >
      <span role="cell" className="filesystem-name-cell">
        <strong title={entry.path}>{entry.name || "Files"}</strong>
      </span>
      <span role="cell" className="filesystem-type-cell">
        <span className={entry.kind === "folder" ? "entry-icon folder-icon" : "entry-icon file-icon"}>{entry.kind === "folder" ? "" : entryTypeLabel(entry)}</span>
      </span>
      <span role="cell" className="muted-cell">{entry.byte_size === null ? "" : formatBytes(entry.byte_size)}</span>
      <span role="cell" className="muted-cell">{formatDate(entry.updated_at)}</span>
      <span role="cell" className="status-cell">
        {entry.status ? <span className={`status-badge status-${entry.status}`}>{entry.status}</span> : ""}
        {entry.status ? (
          <span className="status-progress-track" aria-label={`${entry.status} progress`}>
            <span style={{ width: `${statusProgressPercent}%` }} />
          </span>
        ) : null}
      </span>
    </div>
  );
});

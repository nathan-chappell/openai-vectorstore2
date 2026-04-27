import type {
  ChunkHit,
  ChunkSummary,
  FilesystemEntrySummary,
  SourceDetail,
  SourceSummary,
  TaskSummary,
} from "./types";

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function entryTypeLabel(entry: FilesystemEntrySummary): string {
  if (entry.kind === "folder") {
    return "";
  }
  const extension = entry.name.split(".").pop()?.slice(0, 4).toUpperCase();
  return extension && extension !== entry.name.toUpperCase() ? extension : (entry.source_kind ?? "file").slice(0, 4).toUpperCase();
}

export function isActiveTask(task: TaskSummary): boolean {
  return task.status === "queued" || task.status === "running";
}

export function canPreviewSource(source: SourceDetail): boolean {
  return ["pdf", "text", "conversation", "image", "audio", "video"].includes(source.source_kind);
}

export function isTextPreview(source: SourceDetail, mediaType: string): boolean {
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

export function sourceExtension(source: Pick<SourceSummary, "original_filename" | "source_kind">): string {
  const extension = source.original_filename.split(".").pop()?.slice(0, 4).toUpperCase();
  return extension && extension !== source.original_filename.toUpperCase() ? extension : source.source_kind.slice(0, 4).toUpperCase();
}

export function formatBytes(bytes: number): string {
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

export function formatDate(value: string): string {
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? value : dateFormatter.format(parsed);
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

export function formatLocator(chunk: ChunkSummary): string {
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

export function stringAttribute(attributes: ChunkHit["attributes"], key: string): string | null {
  const value = attributes?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

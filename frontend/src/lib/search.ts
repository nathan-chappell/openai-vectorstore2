import type { FilesystemEntrySummary } from "./types";

export function filterFilesystemEntries(entries: FilesystemEntrySummary[], query: string): FilesystemEntrySummary[] {
  return fuzzyRankFilesystemEntries(entries, query);
}

export function fuzzyRankFilesystemEntries(entries: FilesystemEntrySummary[], query: string): FilesystemEntrySummary[] {
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedQuery) {
    return entries;
  }
  return entries
    .map((entry) => ({ entry, score: fuzzyEntryScore(entry, normalizedQuery) }))
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score || left.entry.name.localeCompare(right.entry.name))
    .map((item) => item.entry);
}

function fuzzyEntryScore(entry: FilesystemEntrySummary, normalizedQuery: string): number {
  const fields = [
    entry.name,
    entry.path,
    entry.description,
    entry.summary,
    entry.source_kind,
    entry.media_type,
    ...entry.suggested_tags,
    ...entry.tags.map((tag) => tag.name),
  ];
  let bestScore = 0;
  for (const field of fields) {
    const candidate = normalizeSearchText(field ?? "");
    if (!candidate) {
      continue;
    }
    if (candidate === normalizedQuery) {
      bestScore = Math.max(bestScore, 100);
    } else if (candidate.startsWith(normalizedQuery)) {
      bestScore = Math.max(bestScore, 80);
    } else if (candidate.includes(normalizedQuery)) {
      bestScore = Math.max(bestScore, 60);
    } else if (isOrderedSubsequence(normalizedQuery, candidate)) {
      bestScore = Math.max(bestScore, 30 + Math.min(20, normalizedQuery.length));
    }
  }
  return bestScore;
}

function normalizeSearchText(value: string): string {
  return value.trim().toLocaleLowerCase();
}

function isOrderedSubsequence(needle: string, haystack: string): boolean {
  let needleIndex = 0;
  for (const character of haystack) {
    if (character === needle[needleIndex]) {
      needleIndex += 1;
      if (needleIndex === needle.length) {
        return true;
      }
    }
  }
  return false;
}

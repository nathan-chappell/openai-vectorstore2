import { PREVIEW_SPLIT_STORAGE_KEY, WORKSPACE_SPLIT_STORAGE_KEY } from "./appConstants";

export function safeJsonStringArray(value: string): string[] {
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

export function sameStringSet(left: string[], right: string[]): boolean {
  if (left.length !== right.length) {
    return false;
  }
  const rightSet = new Set(right);
  return left.every((item) => rightSet.has(item));
}

export function sameStringArray(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}

export function readStoredWorkspaceSplit(): number {
  if (typeof window === "undefined") {
    return 64;
  }
  const stored = Number(window.localStorage.getItem(WORKSPACE_SPLIT_STORAGE_KEY));
  return Number.isFinite(stored) ? clamp(stored, 46, 76) : 64;
}

export function readStoredPreviewSplit(): number {
  if (typeof window === "undefined") {
    return 46;
  }
  const stored = Number(window.localStorage.getItem(PREVIEW_SPLIT_STORAGE_KEY));
  return Number.isFinite(stored) ? clamp(stored, 34, 70) : 46;
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function isEditableShortcutTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tagName = target.tagName;
  return target.isContentEditable || tagName === "INPUT" || tagName === "TEXTAREA" || tagName === "SELECT" || tagName === "BUTTON";
}

export function stringFromUnknown(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

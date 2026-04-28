import type { AppProps } from "../lib/appTypes";
import type { AuthUser, LibrarySummary, TaskSummary } from "../lib/types";

export function WorkspaceHeader({
  authMode,
  busy,
  status,
  tasks,
  user,
  libraries,
  selectedLibraryId,
  adminOpen,
  onLibraryChange,
  onRefresh,
  onSignOut,
  onToggleAdmin,
}: {
  authMode: AppProps["authMode"];
  busy: boolean;
  status: string;
  tasks: TaskSummary[];
  user: AuthUser | null;
  libraries: LibrarySummary[];
  selectedLibraryId: string | null;
  adminOpen: boolean;
  onLibraryChange: (libraryId: string) => void;
  onRefresh: () => void;
  onSignOut?: () => void;
  onToggleAdmin: () => void;
}) {
  const latestTask = tasks[0] ?? null;
  const canOpenSettings = user !== null;
  return (
    <header className="app-bar">
      <div className="app-identity">
        <strong>AI Files</strong>
        <span>{authMode === "local-dev" ? "Local dev auth" : "Clerk auth"}</span>
      </div>
      <div className="app-status" title={status}>
        <span>{user?.display_name ?? "Connecting"}</span>
        <strong>{status}</strong>
      </div>
      <div className="task-summary" title={latestTask ? `${latestTask.kind}: ${latestTask.status}` : "No tasks yet"}>
        <span>Recent Tasks</span>
        <strong>{latestTask ? `${latestTask.kind} | ${latestTask.status}` : "No tasks yet"}</strong>
      </div>
      <label className="library-switcher">
        <span>Library</span>
        <select
          value={selectedLibraryId ?? ""}
          onChange={(event) => onLibraryChange(event.currentTarget.value)}
          disabled={!libraries.length || busy}
        >
          {libraries.map((library) => (
            <option key={library.id} value={library.id}>
              {library.personal ? "Personal" : library.title}
              {library.visibility === "public" ? " (public)" : ""}
            </option>
          ))}
        </select>
      </label>
      {canOpenSettings ? (
        <button
          type="button"
          className="icon-button settings-button"
          onClick={onToggleAdmin}
          aria-label={adminOpen ? "Close settings" : "Open settings"}
          aria-pressed={adminOpen}
          title={adminOpen ? "Close settings" : "Settings"}
        >
          ⚙
        </button>
      ) : null}
      <button type="button" className="icon-button" onClick={onRefresh} disabled={busy} aria-label="Refresh" title="Refresh">
        ↻
      </button>
      {authMode === "clerk" && user ? (
        <button type="button" className="icon-button" onClick={onSignOut} aria-label="Sign out" title="Sign out">
          ⇥
        </button>
      ) : null}
    </header>
  );
}

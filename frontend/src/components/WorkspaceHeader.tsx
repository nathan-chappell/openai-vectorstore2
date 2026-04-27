import type { AppProps } from "../lib/appTypes";
import type { AuthUser, TaskSummary } from "../lib/types";

export function WorkspaceHeader({
  authMode,
  busy,
  status,
  tasks,
  user,
  adminOpen,
  onRefresh,
  onToggleAdmin,
}: {
  authMode: AppProps["authMode"];
  busy: boolean;
  status: string;
  tasks: TaskSummary[];
  user: AuthUser | null;
  adminOpen: boolean;
  onRefresh: () => void;
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
      <button type="button" className="secondary-button" onClick={onRefresh} disabled={busy}>
        Refresh
      </button>
    </header>
  );
}

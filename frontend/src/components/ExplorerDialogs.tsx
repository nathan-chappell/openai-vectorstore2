import type { DeleteDialogState } from "../lib/appTypes";
import type { FilesystemEntrySummary } from "../lib/types";

export function DeleteEntriesDialog({
  busy,
  entries,
  phase,
  onCancel,
  onConfirm,
}: {
  busy: boolean;
  entries: FilesystemEntrySummary[];
  phase: DeleteDialogState["phase"];
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const folderCount = entries.filter((entry) => entry.kind === "folder").length;
  const fileCount = entries.length - folderCount;
  const sampleNames = entries.slice(0, 5).map((entry) => entry.name || entry.path);
  const deleting = phase === "deleting";
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={deleting ? undefined : onCancel}>
      <section
        className="confirmation-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-dialog-title"
        onKeyDown={(event) => {
          if (event.key === "Escape" && !deleting) {
            event.preventDefault();
            onCancel();
          }
        }}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <div>
            <h2 id="delete-dialog-title">Delete selected items?</h2>
            <p>
              {entries.length} selected: {fileCount} file{fileCount === 1 ? "" : "s"} and {folderCount} folder
              {folderCount === 1 ? "" : "s"}.
            </p>
          </div>
        </div>
        <p className="danger-note">Folders are deleted recursively, including every nested indexed file and subfolder.</p>
        <ul className="delete-entry-list">
          {sampleNames.map((name) => (
            <li key={name}>{name}</li>
          ))}
          {entries.length > sampleNames.length ? <li>{entries.length - sampleNames.length} more...</li> : null}
        </ul>
        {deleting ? (
          <div className="delete-progress" role="status" aria-live="polite">
            <span>
              Deleting {entries.length} selected item{entries.length === 1 ? "" : "s"}...
            </span>
            <span className="delete-progress-track" aria-hidden="true">
              <span />
            </span>
          </div>
        ) : null}
        <div className="dialog-actions">
          <button type="button" className="secondary-button" onClick={onCancel} disabled={busy || deleting} autoFocus>
            Cancel
          </button>
          <button type="button" className="danger-solid-button" onClick={onConfirm} disabled={busy || deleting}>
            {deleting ? "Deleting" : "Delete"}
          </button>
        </div>
      </section>
    </div>
  );
}

export function ExplorerShortcutDialog({ onClose }: { onClose: () => void }) {
  const shortcuts = [
    ["Up / Down", "Move focus"],
    ["Shift + Up / Down", "Extend selection"],
    ["Home / End", "Jump to first or last item"],
    ["Shift + Home / End", "Extend to first or last item"],
    ["Enter", "Open the focused file or folder"],
    ["F2", "Rename the selected item"],
    ["Left / Right", "Move through folder history"],
    ["Alt + Left", "Go to the parent folder"],
    ["Backspace", "Go to the parent folder"],
    ["Delete", "Delete selected items"],
    ["Esc", "Close preview"],
    ["?", "Show this sheet"],
  ];
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="shortcut-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcut-dialog-title"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onClose();
          }
        }}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="dialog-heading">
          <h2 id="shortcut-dialog-title">Keyboard Shortcuts</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close shortcuts" autoFocus>
            X
          </button>
        </div>
        <dl className="shortcut-list">
          {shortcuts.map(([keys, action]) => (
            <div key={keys}>
              <dt>
                {keys.split(" / ").map((key, index) => (
                  <span key={key}>
                    {index > 0 ? " / " : ""}
                    <kbd>{key}</kbd>
                  </span>
                ))}
              </dt>
              <dd>{action}</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  );
}

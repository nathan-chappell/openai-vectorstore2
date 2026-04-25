import { ChatKit, type UseChatKitOptions, useChatKit } from "@openai/chatkit-react";
import { useEffect, useMemo, useState } from "react";

import {
  branchSearch,
  deleteSource,
  freeformAction,
  getAuthenticatedUser,
  getChatKitConfig,
  getSource,
  imageAction,
  listTasks,
  listSources,
  listTags,
  qaAction,
  previewSemanticSplit,
  resplitSource,
  searchChunks,
  setChatKitMetadataGetter,
  updateSourceTags,
  uploadSource,
  voiceAction,
  authenticatedFetch,
} from "./lib/api";
import type {
  ActionResponse,
  AuthUser,
  BranchSearchResponse,
  ChunkHit,
  SourceDetail,
  SourceSummary,
  SplitPreviewResponse,
  TagSummary,
  TaskSummary,
} from "./lib/types";

type AppProps = {
  authMode: "clerk" | "local-dev";
};

const MODEL_CHOICES = [
  { id: "balanced", label: "Balanced", description: "Everyday retrieval and synthesis" },
  { id: "powerful", label: "Powerful", description: "Best reasoning pass" },
  { id: "lightweight", label: "Lightweight", description: "Fast exploratory pass" },
] as const;

export function App({ authMode }: AppProps) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [tags, setTags] = useState<TagSummary[]>([]);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [selectedSource, setSelectedSource] = useState<SourceDetail | null>(null);
  const [searchQuery, setSearchQuery] = useState("What matters most in this library?");
  const [actionPrompt, setActionPrompt] = useState("Answer from the selected sources with citations.");
  const [uploadGuidance, setUploadGuidance] = useState("Split by complete ideas and preserve page, line, or speaker boundaries.");
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [splitPreview, setSplitPreview] = useState<SplitPreviewResponse | null>(null);
  const [selectedSourceTagDraftIds, setSelectedSourceTagDraftIds] = useState<string[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [hits, setHits] = useState<ChunkHit[]>([]);
  const [branchResult, setBranchResult] = useState<BranchSearchResponse | null>(null);
  const [actionResult, setActionResult] = useState<ActionResponse | null>(null);
  const [status, setStatus] = useState("Booting the semantic library.");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setChatKitMetadataGetter(() => ({
      origin: "web",
      selected_source_ids: selectedSourceIds,
    }));
    return () => setChatKitMetadataGetter(null);
  }, [selectedSourceIds]);

  useEffect(() => {
    void refreshAll();
  }, []);

  useEffect(() => {
    setSelectedSourceTagDraftIds(selectedSource?.tags.map((tag) => tag.id) ?? []);
  }, [selectedSource]);

  async function refreshAll(): Promise<void> {
    setBusy(true);
    try {
      const [me, sourceList, tagList, taskList] = await Promise.all([
        getAuthenticatedUser(),
        listSources({ pageSize: 50 }),
        listTags(),
        listTasks(),
      ]);
      setUser(me);
      setSources(sourceList.sources);
      setTags(tagList);
      setTasks(taskList.tasks);
      setStatus(`Ready with ${sourceList.total_count} source${sourceList.total_count === 1 ? "" : "s"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not load the workspace.");
    } finally {
      setBusy(false);
    }
  }

  function chooseFiles(files: FileList | null): void {
    const nextFiles = Array.from(files ?? []);
    setPendingFiles(nextFiles);
    setSplitPreview(null);
    if (nextFiles.length) {
      setStatus(`Selected ${nextFiles.length} file${nextFiles.length === 1 ? "" : "s"} for preview or upload.`);
    }
  }

  async function previewPendingSplit(): Promise<void> {
    const [file] = pendingFiles;
    if (!file) {
      return;
    }
    setBusy(true);
    try {
      const response = await previewSemanticSplit(file, uploadGuidance);
      setSplitPreview(response);
      setStatus(`Previewed ${response.split.chunks.length} proposed chunk${response.split.chunks.length === 1 ? "" : "s"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Split preview failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleUpload(): Promise<void> {
    if (!pendingFiles.length) {
      return;
    }
    setBusy(true);
    try {
      for (const file of pendingFiles) {
        const response = await uploadSource(file, uploadGuidance, []);
        setStatus(`Uploaded ${response.source.display_title} as task ${response.task?.id.slice(0, 8) ?? "complete"}.`);
      }
      setPendingFiles([]);
      setSplitPreview(null);
      const [sourceList, tagList, taskList] = await Promise.all([listSources({ pageSize: 50 }), listTags(), listTasks()]);
      setSources(sourceList.sources);
      setTags(tagList);
      setTasks(taskList.tasks);
      setStatus("Upload queued. Semantic chunks will publish in the background.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Upload failed.");
    } finally {
      setBusy(false);
    }
  }

  async function resplitSelectedSource(): Promise<void> {
    if (!selectedSource) {
      return;
    }
    setBusy(true);
    try {
      const response = await resplitSource(selectedSource.id, { user_guidance: uploadGuidance });
      const [sourceList, detail, taskList] = await Promise.all([
        listSources({ pageSize: 50 }),
        getSource(selectedSource.id),
        listTasks(),
      ]);
      setSources(sourceList.sources);
      setSelectedSource(detail);
      setTasks(taskList.tasks);
      setStatus(`Queued re-split for ${response.source.display_title} as task ${response.task?.id.slice(0, 8) ?? "pending"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Re-split failed.");
    } finally {
      setBusy(false);
    }
  }

  async function saveSelectedSourceTags(): Promise<void> {
    if (!selectedSource) {
      return;
    }
    setBusy(true);
    try {
      const response = await updateSourceTags(selectedSource.id, { tag_ids: selectedSourceTagDraftIds });
      const [sourceList, detail, tagList, taskList] = await Promise.all([
        listSources({ pageSize: 50 }),
        getSource(selectedSource.id),
        listTags(),
        listTasks(),
      ]);
      setSources(sourceList.sources);
      setSelectedSource(detail);
      setTags(tagList);
      setTasks(taskList.tasks);
      setStatus(`Queued tag reindex for ${response.source.display_title} as task ${response.task?.id.slice(0, 8) ?? "pending"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Tag update failed.");
    } finally {
      setBusy(false);
    }
  }

  function toggleSelectedSourceTagDraft(tagId: string): void {
    setSelectedSourceTagDraftIds((current) =>
      current.includes(tagId) ? current.filter((id) => id !== tagId) : [...current, tagId],
    );
  }

  async function toggleSource(sourceId: string): Promise<void> {
    const next = selectedSourceIds.includes(sourceId)
      ? selectedSourceIds.filter((id) => id !== sourceId)
      : [...selectedSourceIds, sourceId];
    setSelectedSourceIds(next);
    if (!selectedSource || selectedSource.id !== sourceId) {
      try {
        setSelectedSource(await getSource(sourceId));
      } catch {
        setSelectedSource(null);
      }
    }
  }

  async function runSearch(): Promise<void> {
    if (!searchQuery.trim()) {
      return;
    }
    setBusy(true);
    try {
      const response = await searchChunks({ query: searchQuery, selectedSourceIds, maxResults: 8 });
      setHits(response.hits);
      setBranchResult(null);
      setStatus(`Search returned ${response.hits.length} full semantic chunk${response.hits.length === 1 ? "" : "s"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Search failed.");
    } finally {
      setBusy(false);
    }
  }

  async function runBranchSearch(): Promise<void> {
    if (!searchQuery.trim()) {
      return;
    }
    setBusy(true);
    try {
      const response = await branchSearch({ query: searchQuery, selectedSourceIds, descend: 2, maxWidth: 3 });
      setBranchResult(response);
      setHits(response.levels.flatMap((level) => level.hits));
      setStatus(`Branch search explored ${response.levels.length} level${response.levels.length === 1 ? "" : "s"}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Branch search failed.");
    } finally {
      setBusy(false);
    }
  }

  async function runAction(kind: "qa" | "freeform" | "image" | "voice"): Promise<void> {
    if (!actionPrompt.trim()) {
      return;
    }
    setBusy(true);
    try {
      const response =
        kind === "qa"
          ? await qaAction({ prompt: actionPrompt, selectedSourceIds })
          : kind === "freeform"
            ? await freeformAction({ prompt: actionPrompt, mode: "grounded", selectedSourceIds })
            : kind === "image"
              ? await imageAction({ prompt: actionPrompt, selectedSourceIds })
              : await voiceAction({ prompt: actionPrompt, selectedSourceIds });
      setActionResult(response);
      setHits(response.hits);
      setTasks((await listTasks()).tasks);
      setStatus(`${response.kind} completed as task ${response.task_id.slice(0, 8)}.`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  }

  async function removeSource(sourceId: string): Promise<void> {
    setBusy(true);
    try {
      await deleteSource(sourceId);
      setSelectedSourceIds((current) => current.filter((id) => id !== sourceId));
      setSelectedSource((current) => (current?.id === sourceId ? null : current));
      await refreshAll();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Delete failed.");
    } finally {
      setBusy(false);
    }
  }

  const selectedSourceTagChanged = selectedSource
    ? !sameStringSet(selectedSourceTagDraftIds, selectedSource.tags.map((tag) => tag.id))
    : false;

  return (
    <main className="app-shell">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">App-first semantic RAG</p>
          <h1>Vector stores with a memory palace instead of a junk drawer.</h1>
          <p className="hero-copy">
            Upload PDFs, text, and conversations; split them semantically; tag them automatically; then search, branch,
            answer, image, voice, or chat over full retrieved chunks.
          </p>
        </div>
        <div className="status-card">
          <span>{authMode === "local-dev" ? "Local dev auth" : "Clerk auth"}</span>
          <strong>{user?.display_name ?? "Connecting"}</strong>
          <p>{status}</p>
          <div className="task-strip">
            <span>Recent Tasks</span>
            {tasks.slice(0, 4).map((task) => (
              <p key={task.id}>
                {task.kind} · {task.status}
              </p>
            ))}
            {!tasks.length ? <p>No tasks yet</p> : null}
          </div>
          <button type="button" onClick={refreshAll} disabled={busy}>
            Refresh
          </button>
        </div>
      </section>

      <section className="workspace-grid">
        <aside className="library-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Sources</p>
              <h2>Semantic Library</h2>
            </div>
            <span>{selectedSourceIds.length} selected</span>
          </div>

          <label className="drop-card">
            <input type="file" multiple onChange={(event) => chooseFiles(event.currentTarget.files)} />
            <strong>{pendingFiles.length ? `${pendingFiles.length} file${pendingFiles.length === 1 ? "" : "s"} selected` : "Choose raw material"}</strong>
            <span>{pendingFiles.length ? pendingFiles.map((file) => file.name).join(", ") : "PDF, text, transcript, audio, or video"}</span>
          </label>
          <textarea
            value={uploadGuidance}
            onChange={(event) => setUploadGuidance(event.currentTarget.value)}
            placeholder="Semantic splitting guidance"
          />
          <div className="button-row compact">
            <button type="button" onClick={() => void previewPendingSplit()} disabled={busy || !pendingFiles.length}>
              Preview Split
            </button>
            <button type="button" onClick={() => void handleUpload()} disabled={busy || !pendingFiles.length}>
              Upload
            </button>
          </div>
          {splitPreview ? (
            <div className="preview-card">
              <p className="eyebrow">Split Preview</p>
              <strong>
                {splitPreview.source_kind} · {splitPreview.split.chunks.length} chunks · {splitPreview.split.tags.join(", ") || "no tags"}
              </strong>
              <div className="mini-list">
                {splitPreview.split.chunks.slice(0, 6).map((chunk) => (
                  <span key={chunk.sequence}>{chunk.title}</span>
                ))}
              </div>
            </div>
          ) : null}

          <div className="tag-row">
            {tags.slice(0, 10).map((tag) => (
              <span key={tag.id}>{tag.name}</span>
            ))}
          </div>

          <div className="source-list">
            {sources.map((source) => (
              <article key={source.id} className={selectedSourceIds.includes(source.id) ? "source-card selected" : "source-card"}>
                <button type="button" onClick={() => void toggleSource(source.id)}>
                  <strong>{source.display_title}</strong>
                  <span>
                    {source.source_kind} · {source.chunk_count} chunks · {source.status}
                  </span>
                </button>
                <button type="button" className="ghost danger" onClick={() => void removeSource(source.id)}>
                  Delete
                </button>
              </article>
            ))}
          </div>
        </aside>

        <section className="workbench-panel">
          <div className="tool-card search-card">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Retrieve</p>
                <h2>Search And Branch</h2>
              </div>
              <button type="button" onClick={() => void runBranchSearch()} disabled={busy}>
                Branch
              </button>
            </div>
            <div className="input-row">
              <input value={searchQuery} onChange={(event) => setSearchQuery(event.currentTarget.value)} />
              <button type="button" onClick={() => void runSearch()} disabled={busy}>
                Search
              </button>
            </div>
            {branchResult ? (
              <p className="subtle">Branch levels: {branchResult.levels.map((level) => `${level.depth}:${level.hits.length}`).join(" / ")}</p>
            ) : null}
          </div>

          <div className="results-grid">
            <div className="chunk-stack">
              {hits.slice(0, 8).map((hit) => (
                <article key={hit.chunk_id} className="hit-card">
                  <p className="eyebrow">{hit.source_title}</p>
                  <h3>{hit.title}</h3>
                  <p>{hit.summary}</p>
                  <details>
                    <summary>Full chunk</summary>
                    <pre>{hit.text}</pre>
                  </details>
                </article>
              ))}
            </div>
            <div className="detail-card">
              <p className="eyebrow">Selected Source</p>
              {selectedSource ? (
                <>
                  <h3>{selectedSource.display_title}</h3>
                  <p>{selectedSource.original_filename}</p>
                  <button type="button" className="ghost" onClick={() => void resplitSelectedSource()} disabled={busy}>
                    Re-split
                  </button>
                  <div className="source-tag-editor">
                    <div className="tag-picker-list">
                      {tags.map((tag) => (
                        <label key={tag.id} className="tag-checkbox">
                          <input
                            type="checkbox"
                            checked={selectedSourceTagDraftIds.includes(tag.id)}
                            onChange={() => toggleSelectedSourceTagDraft(tag.id)}
                            disabled={busy || selectedSource.status === "processing"}
                          />
                          <span>{tag.name}</span>
                        </label>
                      ))}
                    </div>
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => void saveSelectedSourceTags()}
                      disabled={busy || selectedSource.status === "processing" || !selectedSourceTagChanged}
                    >
                      Save Tags
                    </button>
                  </div>
                  <div className="mini-list">
                    {selectedSource.chunks.slice(0, 6).map((chunk) => (
                      <span key={chunk.id}>{chunk.title}</span>
                    ))}
                  </div>
                </>
              ) : (
                <p>Select a source to inspect its semantic chunk map.</p>
              )}
            </div>
          </div>

          <div className="tool-card">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Act</p>
                <h2>QA, Freeform, Image, Voice</h2>
              </div>
            </div>
            <textarea value={actionPrompt} onChange={(event) => setActionPrompt(event.currentTarget.value)} />
            <div className="button-row">
              <button type="button" onClick={() => void runAction("qa")} disabled={busy}>
                QA
              </button>
              <button type="button" onClick={() => void runAction("freeform")} disabled={busy}>
                Freeform
              </button>
              <button type="button" onClick={() => void runAction("image")} disabled={busy}>
                Image
              </button>
              <button type="button" onClick={() => void runAction("voice")} disabled={busy}>
                Voice
              </button>
            </div>
            {actionResult ? (
              <div className="answer-card">
                {actionResult.answer ? <p>{actionResult.answer}</p> : null}
                {actionResult.asset?.download_url ? (
                  <a href={actionResult.asset.download_url} target="_blank" rel="noreferrer">
                    Open {actionResult.asset.filename}
                  </a>
                ) : null}
              </div>
            ) : null}
          </div>
        </section>

        <aside className="chat-panel">
          <ChatPane selectedSourceIds={selectedSourceIds} />
        </aside>
      </section>
    </main>
  );
}

function sameStringSet(left: string[], right: string[]): boolean {
  if (left.length !== right.length) {
    return false;
  }
  const rightSet = new Set(right);
  return left.every((item) => rightSet.has(item));
}

function ChatPane({ selectedSourceIds }: { selectedSourceIds: string[] }) {
  const chatKitConfig = getChatKitConfig();
  const options = useMemo<UseChatKitOptions>(
    () => ({
      api: {
        url: chatKitConfig.url,
        domainKey: chatKitConfig.domainKey,
        fetch: authenticatedFetch,
        uploadStrategy: { type: "direct", uploadUrl: chatKitConfig.uploadUrl },
      },
      theme: {
        colorScheme: "light",
        radius: "round",
        density: "compact",
      },
      history: {
        enabled: true,
        showDelete: false,
        showRename: false,
      },
      header: {
        enabled: true,
        title: { enabled: true, text: "Semantic Copilot" },
      },
      startScreen: {
        greeting: selectedSourceIds.length
          ? "Ask about the selected sources or widen the search."
          : "Ask me to search, branch, answer, image, or narrate from your semantic library.",
        prompts: [
          { label: "Grounded QA", prompt: "Answer my question using semantic chunks and cite the source titles.", icon: "check-circle" },
          { label: "Branch search", prompt: "Run a branch search around this topic and explain the interesting trails.", icon: "sparkle" },
          { label: "Creative synthesis", prompt: "Use the retrieved chunks as inspiration, but separate evidence from speculation.", icon: "bolt" },
        ],
      },
      composer: {
        placeholder: "Ask the semantic library...",
        attachments: {
          enabled: true,
          maxSize: 20 * 1024 * 1024,
          maxCount: 3,
          accept: {
            "application/pdf": [".pdf"],
            "text/*": [".txt", ".md", ".csv", ".json"],
            "image/*": [".png", ".jpg", ".jpeg", ".webp"],
            "audio/*": [".mp3", ".m4a", ".wav"],
            "video/*": [".mp4", ".mov", ".webm"],
          },
        },
        dictation: { enabled: false },
        models: MODEL_CHOICES.map((choice) => ({ ...choice, default: choice.id === "balanced" })),
      },
      threadItemActions: {
        feedback: false,
      },
    }),
    [chatKitConfig.domainKey, chatKitConfig.uploadUrl, chatKitConfig.url, selectedSourceIds.length],
  );
  const chatKit = useChatKit(options);
  return <ChatKit control={chatKit.control} className="chatkit-element" />;
}

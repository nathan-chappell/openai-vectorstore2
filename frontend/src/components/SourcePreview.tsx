import { memo, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { readSourceContentBlob } from "../lib/api";
import { CHUNK_PREVIEW_LIMIT, TEXT_PREVIEW_LIMIT } from "../lib/appConstants";
import type { PreviewResource } from "../lib/appTypes";
import type { ChunkSummary, SourceDetail, TagSummary } from "../lib/types";
import {
  canPreviewSource,
  formatBytes,
  formatDate,
  formatLocator,
  formatNumber,
  isTextPreview,
  sourceExtension,
} from "../lib/uiFormat";

export function SourcePreview({
  busy,
  selectedSource,
  uploadGuidance,
  tags,
  onUploadGuidanceChange,
  onResplit,
  onSaveSourceTag,
}: {
  busy: boolean;
  selectedSource: SourceDetail | null;
  uploadGuidance: string;
  tags: TagSummary[];
  onUploadGuidanceChange: (value: string) => void;
  onResplit: () => void;
  onSaveSourceTag: (tagSlug: string | null) => void;
}) {
  const [previewResource, setPreviewResource] = useState<PreviewResource>({ state: "idle" });
  const [tagDraft, setTagDraft] = useState("");
  const previewSourceId = selectedSource?.id ?? null;
  const previewSourceKind = selectedSource?.source_kind ?? null;
  const previewMediaType = selectedSource?.media_type ?? null;

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;

    async function loadPreview(source: SourceDetail): Promise<void> {
      if (!canPreviewSource(source)) {
        setPreviewResource({ state: "idle" });
        return;
      }
      setPreviewResource({ state: "loading" });
      try {
        const response = await readSourceContentBlob(source.id);
        const mediaType = response.mediaType ?? source.media_type;
        if (isTextPreview(source, mediaType)) {
          const rawText = await response.blob.text();
          if (!cancelled) {
            setPreviewResource({
              state: "text",
              mediaType,
              text: rawText.slice(0, TEXT_PREVIEW_LIMIT),
              truncated: rawText.length > TEXT_PREVIEW_LIMIT,
            });
          }
          return;
        }
        const nextObjectUrl = URL.createObjectURL(response.blob);
        if (cancelled) {
          URL.revokeObjectURL(nextObjectUrl);
          return;
        }
        objectUrl = nextObjectUrl;
        setPreviewResource({ state: "file", url: objectUrl, mediaType });
      } catch (error) {
        if (!cancelled) {
          setPreviewResource({ state: "error", message: error instanceof Error ? error.message : "Preview failed." });
        }
      }
    }

    if (!selectedSource) {
      setPreviewResource({ state: "idle" });
      return undefined;
    }

    void loadPreview(selectedSource);
    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [previewMediaType, previewSourceId, previewSourceKind]);

  useEffect(() => {
    setTagDraft(selectedSource?.tags[0]?.slug ?? "");
  }, [previewSourceId, selectedSource?.tags]);

  if (!selectedSource) {
    return (
      <section className="source-preview empty-preview">
        <h2>Select a file to preview it.</h2>
        <p>Use @ in ChatKit to reference files in conversation.</p>
      </section>
    );
  }

  const visibleChunks = selectedSource.chunks.slice(0, CHUNK_PREVIEW_LIMIT);

  return (
    <section className="source-preview">
      <div className="preview-layout">
        <div className="preview-main">
          <div className="source-title-row">
            <div>
              <h2>{selectedSource.display_title}</h2>
              <p>{selectedSource.virtual_path ?? selectedSource.original_filename}</p>
            </div>
            <span className="file-type-large">{sourceExtension(selectedSource)}</span>
          </div>
          <RawPreview source={selectedSource} resource={previewResource} />
          <div className="chunk-section">
            <div className="tool-heading">
              <h3>Optional split map</h3>
              <span>
                {visibleChunks.length}
                {selectedSource.chunks.length > visibleChunks.length ? ` of ${selectedSource.chunks.length}` : ""}
              </span>
            </div>
            <div className="chunk-list">
              {visibleChunks.map((chunk) => (
                <ChunkRow key={chunk.id} chunk={chunk} />
              ))}
              {!visibleChunks.length ? <p className="empty-state">No split records yet.</p> : null}
            </div>
          </div>
        </div>

        <aside className="metadata-panel">
          <dl>
            <div className="metadata-path-row">
              <dt>Path</dt>
              <dd title={selectedSource.virtual_path ?? selectedSource.original_filename}>
                {selectedSource.virtual_path ?? selectedSource.original_filename}
              </dd>
            </div>
            <div>
              <dt>Kind</dt>
              <dd>{selectedSource.source_kind}</dd>
            </div>
            <div>
              <dt>Size</dt>
              <dd>{formatBytes(selectedSource.byte_size)}</dd>
            </div>
            <div>
              <dt>Created</dt>
              <dd>{formatDate(selectedSource.created_at)}</dd>
            </div>
            <div>
              <dt>Index</dt>
              <dd>{selectedSource.openai_vector_file_id ? "ready" : "pending"}</dd>
            </div>
            <div>
              <dt>Split records</dt>
              <dd>{selectedSource.chunk_count}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd>{formatDate(selectedSource.updated_at)}</dd>
            </div>
          </dl>
          {selectedSource.error_message ? <p className="error-message">{selectedSource.error_message}</p> : null}
          <label className="field-label">
            Optional split guidance
            <textarea
              className="compact-textarea"
              value={uploadGuidance}
              onChange={(event) => onUploadGuidanceChange(event.currentTarget.value)}
            />
          </label>
          <button type="button" className="secondary-button" onClick={onResplit} disabled={busy}>
            Re-split
          </button>
          <div className="tag-inspection">
            <strong>File tag</strong>
            <label className="field-label compact-field-label">
              <input
                list="source-tag-options"
                value={tagDraft}
                onChange={(event) => setTagDraft(event.currentTarget.value)}
                placeholder="untagged"
                disabled={busy}
              />
            </label>
            <datalist id="source-tag-options">
              {tags.map((tag) => (
                <option key={tag.id} value={tag.slug}>
                  {tag.name}
                </option>
              ))}
            </datalist>
            <button
              type="button"
              className="secondary-button"
              onClick={() => onSaveSourceTag(tagDraft.trim() || null)}
              disabled={busy || tagDraft.trim() === (selectedSource.tags[0]?.slug ?? "")}
            >
              Save tag
            </button>
          </div>
        </aside>
      </div>
    </section>
  );
}

function RawPreview({ resource, source }: { resource: PreviewResource; source: SourceDetail }) {
  if (resource.state === "loading") {
    return <div className="raw-preview preview-loading">Loading preview...</div>;
  }
  if (resource.state === "error") {
    return (
      <div className="raw-preview preview-unavailable">
        <strong>Preview unavailable</strong>
        <span>{resource.message}</span>
      </div>
    );
  }
  if (resource.state === "text") {
    if (isMarkdownSource(source, resource.mediaType)) {
      return (
        <div className="raw-preview markdown-preview">
          <MarkdownPreview text={resource.text} />
          {resource.truncated ? <p className="subtle">Showing the first {formatNumber(TEXT_PREVIEW_LIMIT)} characters.</p> : null}
        </div>
      );
    }
    return (
      <div className="raw-preview text-preview">
        <pre>{resource.text}</pre>
        {resource.truncated ? <p className="subtle">Showing the first {formatNumber(TEXT_PREVIEW_LIMIT)} characters.</p> : null}
      </div>
    );
  }
  if (resource.state === "file") {
    const mediaType = resource.mediaType.toLowerCase();
    if (source.source_kind === "pdf" || mediaType.includes("pdf")) {
      return (
        <div className="raw-preview document-preview">
          <object data={resource.url} type="application/pdf">
            <a href={resource.url} target="_blank" rel="noreferrer">
              Open PDF preview
            </a>
          </object>
        </div>
      );
    }
    if (source.source_kind === "image" || mediaType.startsWith("image/")) {
      return (
        <div className="raw-preview image-preview">
          <img src={resource.url} alt={source.display_title} />
        </div>
      );
    }
    if (source.source_kind === "audio" || mediaType.startsWith("audio/")) {
      return (
        <div className="raw-preview media-preview">
          <audio src={resource.url} controls />
        </div>
      );
    }
    if (source.source_kind === "video" || mediaType.startsWith("video/")) {
      return (
        <div className="raw-preview media-preview">
          <video src={resource.url} controls />
        </div>
      );
    }
  }
  return (
    <div className="raw-preview preview-unavailable">
      <strong>{source.source_kind} source</strong>
      <span>Optional split preview is available below.</span>
    </div>
  );
}

function MarkdownPreview({ text }: { text: string }) {
  return (
    <div className="markdown-preview-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target={href?.startsWith("#") ? undefined : "_blank"} rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

function isMarkdownSource(source: SourceDetail, mediaType: string): boolean {
  const normalizedMediaType = mediaType.toLowerCase();
  const filename = (source.virtual_path ?? source.original_filename).toLowerCase();
  return normalizedMediaType.includes("markdown") || filename.endsWith(".md") || filename.endsWith(".markdown");
}

const ChunkRow = memo(function ChunkRow({ chunk }: { chunk: ChunkSummary }) {
  return (
    <article className="chunk-row">
      <span>{chunk.sequence + 1}</span>
      <div>
        <strong>{chunk.title}</strong>
        <p>{chunk.summary}</p>
      </div>
      <small>{formatLocator(chunk)}</small>
    </article>
  );
});

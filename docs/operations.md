# Operations

## Storage

Local storage is the default. S3-compatible storage is selected with:

```bash
STORAGE_BACKEND=s3
S3_ENDPOINT=
S3_BUCKET=
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
S3_REGION=auto
S3_URL_STYLE=path
```

Keep bucket secrets in `.env` or deployment secret storage. `.env.example` intentionally leaves bucket credentials empty.

## OpenAI File Cleanup

Source delete is responsible for app storage and tracked OpenAI files.

- Original uploaded OpenAI files are tracked on `SourceFile.openai_original_file_id`.
- Chunk vector files are tracked on `SemanticChunk.openai_file_id`.
- Delete detaches chunk files from the user vector store, deletes chunk files, deletes the original OpenAI file when present, and then removes app records.
- Failed ingest attempts clean up any OpenAI files that were already created before failure.

If a process dies mid-operation, use task history and source status to find incomplete work. Reconcile manually before deleting app rows directly.

## Reindexing

Tag changes queue `AppTask(kind="reindex")`.

- Source tag updates re-publish existing chunks with refreshed vector attributes.
- Tag rename/delete queues affected source reindex tasks.
- Old chunk vector files are detached and deleted after replacement files are published.
- Search still applies app-owned DB post-filtering, so stale vector attributes should not leak logically filtered results while reindexing catches up.

## Background Tasks

Ingest, re-split, and reindex currently run in an in-process asyncio worker.

- Queue state lives on `AppTask`.
- Source records use `processing`, `ready`, and `failed`.
- `task_runner_max_concurrency` bounds concurrent background work.

Durable queue/restart recovery is still future hardening.

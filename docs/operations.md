# Operations

## Logging

Backend logs are written to `.local/logs/openai-vectorstore2.log` by default. Override the path with:

```bash
LOG_FILE_PATH=.local/logs/openai-vectorstore2.log
LOG_FILE_MAX_BYTES=5000000
LOG_FILE_BACKUP_COUNT=3
```

Set `LOG_FILE_PATH=` to disable file logging.

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

For a persistent beta deployment, use S3-compatible storage or a mounted volume. Container-local storage is only appropriate for resettable demos because source payloads, generated assets, and saved report artifacts must remain reachable after restarts.

## Migrations

Alembic is the production schema path:

```bash
alembic upgrade head
```

In Docker/Railway, run migrations before the HTTP process starts:

```bash
sh -lc 'alembic upgrade head && openai-vectorstore2-http'
```

`DATABASE_SCHEMA_MODE=create_all` is only for empty throwaway databases and should not be used to evolve a deployed database.

## OpenAI File Cleanup

Source delete is responsible for app storage and tracked OpenAI files.

- Original uploaded OpenAI files are tracked on `SourceFile.openai_original_file_id`.
- Source-level vector index files are tracked on `SourceFile.openai_vector_file_id`.
- Legacy or explicit split vector files, when present, are tracked on `SemanticChunk.openai_file_id`.
- Delete detaches tracked vector files from the user vector store, deletes vector/original OpenAI files, and then removes app records.
- Failed ingest attempts clean up any OpenAI files that were already created before failure.

If a process dies mid-operation, use task history and source status to find incomplete work. Reconcile manually before deleting app rows directly.

## Reindexing

Tag changes queue `AppTask(kind="reindex")`.

- Source tag updates re-publish the source-level vector file with refreshed vector attributes.
- Tag rename/delete queues affected source reindex tasks.
- Old vector files are detached and deleted after replacement files are published.
- Search still applies app-owned DB post-filtering, so stale vector attributes should not leak logically filtered results while reindexing catches up.

## Background Tasks

Ingest, re-split, and reindex currently run in an in-process asyncio worker.

- Queue state lives on `AppTask`.
- Source records use `processing`, `ready`, and `failed`.
- `task_runner_max_concurrency` bounds concurrent background work.

Durable queue/restart recovery is still future hardening.

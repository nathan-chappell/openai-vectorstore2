# RAG Library Smoke Eval: 2026-05-12

This smoke eval was run after adding the installable `openai_vectorstore2`
library facade and generic JSONL eval CLI.

## Setup

- App: local FastAPI server from the current checkout
- Database: temporary local SQLite database
- Storage: temporary local storage directory
- Auth: `local-dev`
- Indexing/search: real OpenAI vector-store path
- Dataset: 2 tiny text sources and 2 JSONL eval queries

The run intentionally avoided the repository `.env` database target by setting a
temporary `DATABASE_URL`.

## Commands

```bash
env ALLOW_LOCAL_DEV_AUTH=true \
  DATABASE_URL='sqlite+aiosqlite:///./.local/eval-smoke-20260512.db' \
  LOCAL_STORAGE_DIR='.local/eval-smoke-storage-20260512' \
  BILLING_ENABLED=false \
  PORT=8015 \
  .venv/bin/openai-vectorstore2-http
```

Two text sources were uploaded through `POST /api/sources`, then evaluated with:

```bash
.venv/bin/python -m openai_vectorstore2.cli \
  --eval .local/eval-smoke-20260512/dataset.jsonl \
  --base-url http://127.0.0.1:8015 \
  --auth-token local-dev \
  --max-results 2 \
  --concurrency 2 \
  --output .local/eval-smoke-20260512/results.json
```

The editable install was then refreshed and the generated console entry point was
validated:

```bash
.venv/bin/pip install -e .
.venv/bin/openai-vectorstore2 --eval .local/eval-smoke-20260512/dataset.jsonl --validate-only
```

## Results

| Metric | Value |
|---|---:|
| Queries | 2 |
| Recall@1 | 1.000 |
| Recall@2 | 1.000 |
| Mean latency | 4929.4 ms |

| Query ID | Expected Rank | Latency | Top Hit |
|---|---:|---:|---|
| `alpha-token` | 1 | 1813.4 ms | `alpha-notes` |
| `bravo-token` | 1 | 8045.4 ms | `bravo-notes` |

The eval result was decent for a smoke run: both source-specific queries
retrieved the expected source at rank 1 through the new JSONL eval path.

---
name: open-ragbench-eval
description: Run this repo's Open RAGBench PDF retrieval evaluation, progressively setting up local benchmark data, uploading missing PDFs to the app, and writing retrieval plus five-question answer-evaluation reports.
---

# Open RAGBench Eval

Use this skill when the user asks to run, refresh, inspect, or report on the Open RAGBench evaluation for this repo.

## Workflow

1. Confirm the backend is running with live OpenAI credentials and local-dev auth, or start it in another terminal/session:

```bash
./.venv/bin/openai-vectorstore2-http
```

2. Prefer the combined progressive setup/upload command. It reuses `subset.json`, downloads each missing PDF into `.local/`, uploads it immediately, and reuses completed `doc_id` records in `uploads.json`.

```bash
./.venv/bin/openai-vectorstore2-open-ragbench-eval setup-upload
```

Use a stable run when continuing a previous eval:

```bash
./.venv/bin/openai-vectorstore2-open-ragbench-eval setup-upload --run-id <run-id>
```

3. If you need to separate setup from upload, prepare the dataset first. This is progressive by default: if `subset.json` already exists for the run, setup reuses it; PDFs are downloaded only when missing.

```bash
./.venv/bin/openai-vectorstore2-open-ragbench-eval setup
```

Then upload the PDFs to the running app:

```bash
./.venv/bin/openai-vectorstore2-open-ragbench-eval upload .local/evals/open_ragbench/<run-id>
```

4. Run the eval and write the report files. The run scores retrieval and, by default, samples five questions for grounded QA answer evaluation against reference answers.

```bash
./.venv/bin/openai-vectorstore2-open-ragbench-eval run .local/evals/open_ragbench/<run-id>
```

Retrieval queries run with bounded parallelism; the default is `--retrieval-concurrency 50`. Lower it if the app or provider starts returning rate-limit errors.

For a long live run, run the command in a background session and check `.local/logs/openai-vectorstore2.log` periodically while the eval CLI emits `INFO` progress lines. Use `--log-level DEBUG` only when inspecting JSON writes or detailed local behavior.

To reduce scope during a smoke run, use smaller counts on `setup-upload` or `--max-queries` on `run`.

```bash
./.venv/bin/openai-vectorstore2-open-ragbench-eval setup-upload --run-id smoke --positive-docs 2 --negative-docs 3
./.venv/bin/openai-vectorstore2-open-ragbench-eval run .local/evals/open_ragbench/smoke --max-queries 5
```

Outputs:

- `.local/evals/open_ragbench/<run-id>/subset.json`
- `.local/evals/open_ragbench/<run-id>/uploads.json`
- `.local/evals/open_ragbench/<run-id>/results.json`
- `.local/evals/open_ragbench/<run-id>/summary.md`
- `.local/evals/open_ragbench/<run-id>/demo_queries.md`
- `.local/evals/open_ragbench/<run-id>/detailed_metrics.md`
- `evals/open_ragbench/latest/` with the latest committed report artifacts, excluding downloaded PDFs

## Reporting Notes

The summary is intentionally human-readable: it includes eval version, run timestamp, dataset id, dataset revision when Hugging Face exposes it, document/query counts, key retrieval metrics, metric definitions, comparison reference points, and links to raw results, query review, and detailed metric tables.

`demo_queries.md` is the inspection surface for individual queries. It shows the query text, expected document id/title/description, retrieved document ids/titles/descriptions, the expected answer, the actual generated answer when QA was evaluated, and QA evidence document hits.

The answer score is a lightweight heuristic: it records whether the expected document appeared in retrieved QA evidence and what fraction of content terms from the benchmark reference answer appeared in the generated answer. Treat `review` and `fail` rows as inspection targets, not a full model-judge result.

Open RAGBench is CC-BY-NC-4.0. Keep downloaded PDFs under `.local/`; only commit the lightweight latest report artifacts under `evals/open_ragbench/latest/`.

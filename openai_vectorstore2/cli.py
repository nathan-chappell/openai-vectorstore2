from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .evals.jsonl import run_jsonl_eval_sync, validate_jsonl_dataset


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openai-vectorstore2")
    parser.add_argument("--eval", dest="eval_path", type=Path, help="Validate and run a JSONL retrieval eval dataset.")
    parser.add_argument("--validate-only", action="store_true", help="Only validate the eval JSONL dataset.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="App base URL for eval requests.")
    parser.add_argument("--auth-token", default="local-dev", help="Bearer token for eval requests.")
    parser.add_argument("--max-results", type=int, default=10, help="Maximum search results per eval query.")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent eval search requests.")
    parser.add_argument("--output", type=Path, default=None, help="Optional path to write eval results JSON.")
    args = parser.parse_args(argv)

    if args.eval_path is None:
        parser.print_help()
        return 2

    records = validate_jsonl_dataset(args.eval_path)
    if args.validate_only:
        print(f"Validated {len(records)} eval records from {args.eval_path}")
        return 0

    report = run_jsonl_eval_sync(
        dataset_path=args.eval_path,
        base_url=args.base_url,
        auth_token=args.auth_token,
        max_results=args.max_results,
        concurrency=args.concurrency,
        output_path=args.output,
    )
    print(
        "Eval completed: "
        f"queries={report.query_count} "
        f"recall@1={report.recall_at_1:.3f} "
        f"recall@{report.max_results}={report.recall_at_k:.3f} "
        f"mean_latency_ms={report.mean_latency_ms:.1f}"
    )
    if args.output is not None:
        print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

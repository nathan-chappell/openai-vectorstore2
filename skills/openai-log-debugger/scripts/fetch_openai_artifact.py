#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

OpenAIArtifactKind = Literal["response", "conversation"]

API_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LOG_FILE = Path(".local/logs/openai-vectorstore2.log")
DEFAULT_OUT_DIR = Path(".local/openai-debug")
ID_PATTERN = re.compile(r"\b(?P<id>(?:resp|conv)_[A-Za-z0-9]+)\b")
PLATFORM_LOG_BASE_URL = "https://platform.openai.com/logs"


@dataclass(frozen=True, slots=True)
class FetchTarget:
    openai_id: str
    kind: OpenAIArtifactKind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch logged OpenAI response or conversation artifacts for local troubleshooting."
    )
    parser.add_argument("targets", nargs="*", help="OpenAI resp_/conv_ IDs or platform log URLs.")
    parser.add_argument("--log-file", type=Path, default=DEFAULT_LOG_FILE, help="Log file to scan when no target is given.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Directory for downloaded JSON artifacts.")
    parser.add_argument("--limit", type=int, default=5, help="Maximum IDs to fetch when scanning the log file.")
    parser.add_argument(
        "--conversation-items-limit",
        type=int,
        default=100,
        help="Maximum conversation items to retrieve for conv_ IDs.",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Optional .env file containing OPENAI_API_KEY.")
    parser.add_argument("--api-base-url", default=os.environ.get("OPENAI_API_BASE_URL", API_BASE_URL))
    args = parser.parse_args(argv)

    load_env_file(args.env_file)
    api_key = os.environ.get("OPENAI_API_KEY")
    target_ids = ids_from_targets(args.targets)
    if not target_ids:
        target_ids = ids_from_log(args.log_file)
        if args.limit > 0:
            target_ids = target_ids[: args.limit]

    if not target_ids:
        print(f"No resp_ or conv_ IDs found in targets or {args.log_file}.", file=sys.stderr)
        return 1

    print(f"Found {len(target_ids)} OpenAI ID(s):")
    for openai_id in target_ids:
        print(f"- {openai_id} {platform_log_url(openai_id)}")

    if not api_key:
        print("OPENAI_API_KEY is not set in the environment or .env; cannot fetch API artifacts.", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for openai_id in target_ids:
        target = target_from_id(openai_id)
        output_path = args.out_dir / f"{target.openai_id}.json"
        try:
            artifact = fetch_artifact(
                target=target,
                api_key=api_key,
                api_base_url=str(args.api_base_url).rstrip("/"),
                conversation_items_limit=max(1, min(100, args.conversation_items_limit)),
            )
        except (HTTPError, URLError, RuntimeError, ValueError) as error:
            failures += 1
            artifact = error_artifact(target=target, error=error)
            print(f"Fetch failed for {target.openai_id}: {error}", file=sys.stderr)

        output_path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote {output_path} {platform_log_url(target.openai_id)}")

    return 1 if failures else 0


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key or normalized_key in os.environ:
            continue
        os.environ[normalized_key] = raw_value.strip().strip("'\"")


def ids_from_targets(targets: list[str]) -> list[str]:
    return dedupe_ids(match.group("id") for target in targets for match in ID_PATTERN.finditer(target))


def ids_from_log(log_file: Path) -> list[str]:
    if not log_file.exists():
        return []
    return dedupe_ids(match.group("id") for match in ID_PATTERN.finditer(log_file.read_text(encoding="utf-8")))


def dedupe_ids(openai_ids: Iterable[str]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for item in openai_ids:
        openai_id = str(item)
        if openai_id not in seen:
            ids.append(openai_id)
            seen.add(openai_id)
    return ids


def target_from_id(openai_id: str) -> FetchTarget:
    if openai_id.startswith("resp_"):
        return FetchTarget(openai_id=openai_id, kind="response")
    if openai_id.startswith("conv_"):
        return FetchTarget(openai_id=openai_id, kind="conversation")
    raise ValueError(f"Unsupported OpenAI ID: {openai_id}")


def fetch_artifact(
    *,
    target: FetchTarget,
    api_key: str,
    api_base_url: str,
    conversation_items_limit: int,
) -> dict[str, object]:
    if target.kind == "response":
        response = request_json(f"{api_base_url}/responses/{quote(target.openai_id, safe='')}", api_key=api_key)
        return base_artifact(target) | {"response": response}

    conversation = request_json(f"{api_base_url}/conversations/{quote(target.openai_id, safe='')}", api_key=api_key)
    query = urlencode({"limit": conversation_items_limit, "order": "asc"})
    items = request_json(
        f"{api_base_url}/conversations/{quote(target.openai_id, safe='')}/items?{query}",
        api_key=api_key,
    )
    return base_artifact(target) | {"conversation": conversation, "items": items}


def request_json(url: str, *, api_key: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read()
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {body[:1000]}") from error
    parsed = json.loads(payload.decode("utf-8"))
    if isinstance(parsed, dict):
        return parsed
    return {"data": parsed}


def base_artifact(target: FetchTarget) -> dict[str, object]:
    return {
        "id": target.openai_id,
        "kind": target.kind,
        "platform_log_url": platform_log_url(target.openai_id),
        "retrieved_at": datetime.now(UTC).isoformat(),
    }


def error_artifact(*, target: FetchTarget, error: BaseException) -> dict[str, object]:
    return base_artifact(target) | {
        "error": {
            "type": type(error).__name__,
            "message": str(error),
        }
    }


def platform_log_url(openai_id: str) -> str:
    return f"{PLATFORM_LOG_BASE_URL}/{quote(openai_id, safe='')}"


if __name__ == "__main__":
    raise SystemExit(main())

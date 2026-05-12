from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from time import monotonic
from typing import Any, cast

import httpx
from pydantic import BaseModel, Field, ValidationError


class JsonlEvalRecord(BaseModel):
    id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    expected_source_id: str | None = None
    expected_doc_id: str | None = None
    reference_answer: str | None = None
    tag_ids: list[str] = Field(default_factory=list, max_length=1)
    selected_source_ids: list[str] = Field(default_factory=list)
    library_id: str | None = None


class JsonlEvalHit(BaseModel):
    source_id: str
    doc_id: str | None = None
    title: str
    score: float
    rank: int


class JsonlEvalResult(BaseModel):
    id: str
    query: str
    expected_source_id: str | None = None
    expected_doc_id: str | None = None
    expected_rank: int | None = None
    latency_ms: float
    hits: list[JsonlEvalHit] = Field(default_factory=list)


class JsonlEvalReport(BaseModel):
    dataset_path: str
    query_count: int
    max_results: int
    recall_at_1: float
    recall_at_k: float
    mean_latency_ms: float
    results: list[JsonlEvalResult]


def validate_jsonl_dataset(path: Path) -> list[JsonlEvalRecord]:
    records: list[JsonlEvalRecord] = []
    errors: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
            continue
        try:
            record = JsonlEvalRecord.model_validate(payload)
        except ValidationError as exc:
            errors.append(f"line {line_number}: {exc.errors(include_url=False)!r}")
            continue
        if record.expected_source_id is None and record.expected_doc_id is None:
            errors.append(f"line {line_number}: expected_source_id or expected_doc_id is required")
            continue
        records.append(record)
    if errors:
        raise ValueError("Invalid eval dataset:\n" + "\n".join(errors))
    if not records:
        raise ValueError("Invalid eval dataset: no records found.")
    return records


async def run_jsonl_eval(
    *,
    dataset_path: Path,
    base_url: str,
    auth_token: str,
    max_results: int = 10,
    concurrency: int = 10,
    output_path: Path | None = None,
) -> JsonlEvalReport:
    records = validate_jsonl_dataset(dataset_path)
    headers = {"Authorization": f"Bearer {auth_token}"}
    semaphore = asyncio.Semaphore(max(1, concurrency))
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=60.0) as client:

        async def score(record: JsonlEvalRecord) -> JsonlEvalResult:
            async with semaphore:
                started_at = monotonic()
                response = await client.post(
                    "/api/search",
                    headers=headers,
                    json={
                        "query": record.query,
                        "library_id": record.library_id,
                        "tag_ids": record.tag_ids,
                        "selected_source_ids": record.selected_source_ids,
                        "max_results": max_results,
                    },
                )
                response.raise_for_status()
                latency_ms = (monotonic() - started_at) * 1000
            payload = cast(dict[str, object], response.json())
            raw_hits = payload.get("hits")
            hits: list[JsonlEvalHit] = []
            expected_rank: int | None = None
            for rank, raw_hit in enumerate(raw_hits if isinstance(raw_hits, list) else [], start=1):
                if not isinstance(raw_hit, dict):
                    continue
                source_id = raw_hit.get("source_file_id")
                if not isinstance(source_id, str):
                    continue
                attributes = raw_hit.get("attributes")
                doc_id = _doc_id_from_hit(raw_hit, attributes=attributes if isinstance(attributes, dict) else {})
                title = raw_hit.get("source_title")
                score_value = raw_hit.get("score")
                hit = JsonlEvalHit(
                    source_id=source_id,
                    doc_id=doc_id,
                    title=title if isinstance(title, str) else source_id,
                    score=float(score_value) if isinstance(score_value, int | float) else 0.0,
                    rank=rank,
                )
                hits.append(hit)
                source_matches = record.expected_source_id is not None and source_id == record.expected_source_id
                doc_matches = record.expected_doc_id is not None and doc_id == record.expected_doc_id
                if expected_rank is None and (source_matches or doc_matches):
                    expected_rank = rank
            return JsonlEvalResult(
                id=record.id,
                query=record.query,
                expected_source_id=record.expected_source_id,
                expected_doc_id=record.expected_doc_id,
                expected_rank=expected_rank,
                latency_ms=latency_ms,
                hits=hits,
            )

        results = await asyncio.gather(*(score(record) for record in records))

    report = build_jsonl_eval_report(dataset_path=dataset_path, max_results=max_results, results=list(results))
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report


def run_jsonl_eval_sync(
    *,
    dataset_path: Path,
    base_url: str,
    auth_token: str,
    max_results: int = 10,
    concurrency: int = 10,
    output_path: Path | None = None,
) -> JsonlEvalReport:
    return asyncio.run(
        run_jsonl_eval(
            dataset_path=dataset_path,
            base_url=base_url,
            auth_token=auth_token,
            max_results=max_results,
            concurrency=concurrency,
            output_path=output_path,
        )
    )


def _doc_id_from_hit(hit: dict[str, object], *, attributes: dict[Any, Any]) -> str | None:
    for key in ("doc_id", "document_id", "open_ragbench_doc_id"):
        value = attributes.get(key) or hit.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def build_jsonl_eval_report(
    *, dataset_path: Path, max_results: int, results: Sequence[JsonlEvalResult]
) -> JsonlEvalReport:
    hits_at_1 = len([result for result in results if result.expected_rank == 1])
    hits_at_k = len(
        [result for result in results if result.expected_rank is not None and result.expected_rank <= max_results]
    )
    denominator = max(len(results), 1)
    return JsonlEvalReport(
        dataset_path=str(dataset_path),
        query_count=len(results),
        max_results=max_results,
        recall_at_1=hits_at_1 / denominator,
        recall_at_k=hits_at_k / denominator,
        mean_latency_ms=sum(result.latency_ms for result in results) / denominator,
        results=list(results),
    )

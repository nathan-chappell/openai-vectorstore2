from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import logging
from math import ceil, log2
from pathlib import Path
import random
import re
import shutil
import sys
from time import monotonic
from typing import Literal, cast

import httpx
from pydantic import BaseModel, Field

DATASET_ID = "vectara/open_ragbench"
EVAL_VERSION = "2026-04-28.4"
DATASET_BASE_URL = "https://huggingface.co/datasets/vectara/open_ragbench/resolve/main/pdf/arxiv"
CORPUS_TREE_URL = "https://huggingface.co/api/datasets/vectara/open_ragbench/tree/main/pdf/arxiv/corpus?recursive=false"
DEFAULT_CATEGORIES: tuple[str, ...] = (
    "cs.LG",
    "cs.AI",
    "cs.CL",
    "cs.CV",
    "cs.RO",
    "stat.ML",
    "eess.IV",
    "eess.AS",
    "cs.SD",
)
DEFAULT_RUN_ID_PREFIX = "open-ragbench"
DEFAULT_SEED = 20260428
DEFAULT_OUTPUT_ROOT = Path(".local/evals/open_ragbench")
DEFAULT_LATEST_ARTIFACT_DIR = Path("evals/open_ragbench/latest")
DEFAULT_AUTH_TOKEN = "local-dev"
DEFAULT_TIMEOUT_SECONDS = 60.0
OPEN_RAGBENCH_REPO_URL = "https://github.com/vectara/open-rag-bench"
BEIR_PAPER_URL = "https://datasets-benchmarks-proceedings.neurips.cc/paper_files/paper/2021/file/65b9eea6e1cc6bb9f0cd2a47751a186f-Paper-round2.pdf"
RAGBENCH_PAPER_URL = "https://arxiv.org/abs/2407.11005"

logger = logging.getLogger(__name__)


class OpenRagbenchQuery(BaseModel):
    query: str
    type: str
    source: str


class OpenRagbenchQrel(BaseModel):
    doc_id: str
    section_id: int


class CorpusDoc(BaseModel):
    doc_id: str
    categories: list[str] = Field(default_factory=list)
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    published: str | None = None
    updated: str | None = None
    corpus_path: str | None = None
    corpus_size: int | None = None


class OpenRagbenchDataset(BaseModel):
    dataset_id: str = DATASET_ID
    dataset_revision: str | None = None
    queries: dict[str, OpenRagbenchQuery]
    qrels: dict[str, OpenRagbenchQrel]
    answers: dict[str, str]
    pdf_urls: dict[str, str]
    corpus_docs: dict[str, CorpusDoc]


class SubsetDoc(BaseModel):
    doc_id: str
    title: str
    abstract: str | None = None
    categories: list[str]
    assigned_category: str
    split: Literal["positive", "negative"]
    pdf_url: str
    query_count: int = 0


class SubsetQuery(BaseModel):
    query_id: str
    query: str
    query_type: str
    query_source: str
    expected_doc_id: str
    section_id: int
    reference_answer: str | None = None
    category: str


class SubsetManifest(BaseModel):
    run_id: str
    dataset_id: str = DATASET_ID
    dataset_revision: str | None = None
    eval_version: str = EVAL_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    seed: int = DEFAULT_SEED
    categories: list[str]
    positive_doc_target: int
    negative_doc_target: int
    documents: list[SubsetDoc]
    queries: list[SubsetQuery]


class UploadRecord(BaseModel):
    doc_id: str
    source_id: str | None = None
    task_id: str | None = None
    status: str
    filename: str
    virtual_name: str
    category: str
    folder_id: str | None = None
    error_message: str | None = None


class UploadManifest(BaseModel):
    run_id: str
    tag_id: str
    root_folder_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    uploads: list[UploadRecord] = Field(default_factory=list)


class SearchHitRecord(BaseModel):
    source_id: str
    doc_id: str | None = None
    source_title: str
    score: float
    rank: int


class QueryEvalResult(BaseModel):
    query_id: str
    query: str
    expected_doc_id: str
    category: str
    query_type: str
    query_source: str
    expected_rank: int | None
    hits: list[SearchHitRecord]
    latency_ms: float = 0.0


class AnswerEvalResult(BaseModel):
    query_id: str
    query: str
    expected_doc_id: str
    reference_answer: str | None = None
    generated_answer: str
    expected_doc_rank: int | None
    retrieved_expected_doc: bool
    used_hits: list[SearchHitRecord] = Field(default_factory=list)
    reference_coverage: float
    covered_reference_terms: list[str] = Field(default_factory=list)
    verdict: Literal["pass", "review", "fail"]
    latency_ms: float = 0.0


class MetricAtK(BaseModel):
    k: int
    recall: float
    mrr: float
    ndcg: float


class LatencySummary(BaseModel):
    count: int
    total_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float


class EvalReport(BaseModel):
    run_id: str
    eval_version: str = EVAL_VERSION
    dataset_id: str = DATASET_ID
    dataset_revision: str | None = None
    tag_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    max_results: int
    query_count: int
    metrics: list[MetricAtK]
    by_category: dict[str, list[MetricAtK]]
    by_query_type: dict[str, list[MetricAtK]]
    by_query_source: dict[str, list[MetricAtK]]
    results: list[QueryEvalResult]
    answer_evaluations: list[AnswerEvalResult] = Field(default_factory=list)
    retrieval_duration_ms: float | None = None
    retrieval_latency: LatencySummary | None = None
    answer_eval_duration_ms: float | None = None
    answer_latency: LatencySummary | None = None


class EvalAppClient:
    def __init__(self, *, client: httpx.AsyncClient, auth_token: str, library_id: str | None = None) -> None:
        self._client = client
        self._headers = {"Authorization": f"Bearer {auth_token}"}
        self._library_id = library_id

    async def create_library(
        self,
        *,
        title: str,
        description: str | None = None,
        slug: str | None = None,
    ) -> str:
        response = await self._client.post(
            "/api/libraries",
            headers=self._headers,
            json={"title": title, "description": description, "visibility": "public", "slug": slug},
        )
        response.raise_for_status()
        payload = cast(dict[str, object], response.json())
        library_id = payload.get("id")
        if not isinstance(library_id, str) or not library_id:
            raise RuntimeError(f"Library creation did not return an id: {payload!r}")
        self._library_id = library_id
        return library_id

    async def create_folder(self, *, name: str, parent_id: str | None = None) -> str:
        response = await self._client.post(
            "/api/filesystem/folders",
            headers=self._headers,
            json={"name": name, "parent_id": parent_id, "library_id": self._library_id},
        )
        response.raise_for_status()
        payload = cast(dict[str, object], response.json())
        folder_id = payload.get("id")
        if not isinstance(folder_id, str) or not folder_id:
            raise RuntimeError(f"Folder creation did not return an id: {payload!r}")
        return folder_id

    async def upload_pdf(
        self,
        *,
        path: Path,
        tag_id: str,
        folder_id: str,
        virtual_name: str,
        user_guidance: str,
    ) -> tuple[str, str | None]:
        form_data = {
            "tag_ids": tag_id,
            "folder_id": folder_id,
            "virtual_name": virtual_name,
            "user_guidance": user_guidance,
        }
        if self._library_id is not None:
            form_data["library_id"] = self._library_id
        response = await self._client.post(
            "/api/sources",
            headers=self._headers,
            files={"file": (path.name, path.read_bytes(), "application/pdf")},
            data=form_data,
        )
        response.raise_for_status()
        payload = cast(dict[str, object], response.json())
        source = cast(dict[str, object], payload.get("source"))
        task = payload.get("task")
        source_id = source.get("id")
        if not isinstance(source_id, str) or not source_id:
            raise RuntimeError(f"Upload did not return a source id: {payload!r}")
        task_id: str | None = None
        if isinstance(task, dict) and isinstance(task.get("id"), str):
            task_id = cast(str, task["id"])
        return source_id, task_id

    async def wait_for_task(self, *, task_id: str, timeout_seconds: float) -> str:
        deadline = monotonic() + timeout_seconds
        last_payload: dict[str, object] | None = None
        while monotonic() < deadline:
            response = await self._client.get(f"/api/tasks/{task_id}", headers=self._headers)
            response.raise_for_status()
            payload = cast(dict[str, object], response.json())
            last_payload = payload
            status = payload.get("status")
            if status in {"completed", "failed", "cancelled"}:
                return cast(str, status)
            await asyncio.sleep(1.0)
        raise TimeoutError(f"Timed out waiting for task {task_id}; last payload={last_payload!r}")

    async def search(self, *, query: str, tag_id: str, max_results: int) -> list[dict[str, object]]:
        response = await self._client.post(
            "/api/search",
            headers=self._headers,
            json={"query": query, "library_id": self._library_id, "tag_ids": [tag_id], "max_results": max_results},
        )
        response.raise_for_status()
        payload = cast(dict[str, object], response.json())
        hits = payload.get("hits")
        if not isinstance(hits, list):
            raise RuntimeError(f"Search response did not include hits: {payload!r}")
        return [cast(dict[str, object], hit) for hit in hits if isinstance(hit, dict)]

    async def qa(self, *, query: str, tag_id: str, max_results: int) -> tuple[str, list[dict[str, object]]]:
        response = await self._client.post(
            "/api/actions/qa",
            headers=self._headers,
            json={"prompt": query, "library_id": self._library_id, "tag_ids": [tag_id], "max_results": max_results},
        )
        response.raise_for_status()
        payload = cast(dict[str, object], response.json())
        answer = payload.get("answer")
        hits = payload.get("hits")
        if not isinstance(answer, str):
            raise RuntimeError(f"QA response did not include answer text: {payload!r}")
        if not isinstance(hits, list):
            raise RuntimeError(f"QA response did not include hits: {payload!r}")
        return answer, [cast(dict[str, object], hit) for hit in hits if isinstance(hit, dict)]


def select_subset(
    *,
    dataset: OpenRagbenchDataset,
    run_id: str,
    categories: Sequence[str] = DEFAULT_CATEGORIES,
    positive_doc_target: int = 40,
    negative_doc_target: int = 60,
    seed: int = DEFAULT_SEED,
) -> SubsetManifest:
    category_order = list(dict.fromkeys(categories))
    positive_doc_ids = {qrel.doc_id for qrel in dataset.qrels.values()}
    query_counts = Counter(qrel.doc_id for qrel in dataset.qrels.values())
    assigned_docs: dict[str, tuple[CorpusDoc, str]] = {}
    for doc_id, doc in dataset.corpus_docs.items():
        assigned_category = _assigned_category(doc.categories, category_order)
        if assigned_category is None or doc_id not in dataset.pdf_urls:
            continue
        assigned_docs[doc_id] = (doc, assigned_category)

    positive_buckets: dict[str, list[str]] = defaultdict(list)
    negative_buckets: dict[str, list[str]] = defaultdict(list)
    for doc_id, (_doc, assigned_category) in assigned_docs.items():
        if doc_id in positive_doc_ids:
            positive_buckets[assigned_category].append(doc_id)
        else:
            negative_buckets[assigned_category].append(doc_id)

    positive_selection = _select_weighted_doc_ids(
        buckets=positive_buckets,
        category_order=category_order,
        target_count=positive_doc_target,
        seed=seed,
    )
    negative_selection = _select_weighted_doc_ids(
        buckets=negative_buckets,
        category_order=category_order,
        target_count=negative_doc_target,
        seed=seed + 1,
    )

    documents: list[SubsetDoc] = []
    for split, selected_doc_ids in (("positive", positive_selection), ("negative", negative_selection)):
        for doc_id in selected_doc_ids:
            doc, assigned_category = assigned_docs[doc_id]
            documents.append(
                SubsetDoc(
                    doc_id=doc_id,
                    title=doc.title or doc_id,
                    abstract=doc.abstract,
                    categories=doc.categories,
                    assigned_category=assigned_category,
                    split=cast(Literal["positive", "negative"], split),
                    pdf_url=dataset.pdf_urls[doc_id],
                    query_count=query_counts[doc_id],
                )
            )
    documents = sorted(documents, key=lambda item: (item.split, item.assigned_category, item.doc_id))
    selected_positive_doc_ids = {doc.doc_id for doc in documents if doc.split == "positive"}
    selected_category_by_doc_id = {doc.doc_id: doc.assigned_category for doc in documents}
    subset_queries: list[SubsetQuery] = []
    for query_id, qrel in sorted(dataset.qrels.items()):
        if qrel.doc_id not in selected_positive_doc_ids:
            continue
        query = dataset.queries.get(query_id)
        if query is None:
            continue
        subset_queries.append(
            SubsetQuery(
                query_id=query_id,
                query=query.query,
                query_type=query.type,
                query_source=query.source,
                expected_doc_id=qrel.doc_id,
                section_id=qrel.section_id,
                reference_answer=dataset.answers.get(query_id),
                category=selected_category_by_doc_id[qrel.doc_id],
            )
        )

    return SubsetManifest(
        run_id=run_id,
        dataset_revision=dataset.dataset_revision,
        seed=seed,
        categories=category_order,
        positive_doc_target=positive_doc_target,
        negative_doc_target=negative_doc_target,
        documents=documents,
        queries=subset_queries,
    )


def grow_subset(
    *,
    dataset: OpenRagbenchDataset,
    manifest: SubsetManifest,
    additional_docs: int = 20,
    seed: int | None = None,
) -> SubsetManifest:
    if additional_docs <= 0:
        return manifest
    existing_doc_ids = {doc.doc_id for doc in manifest.documents}
    existing_positive_count = _split_count(manifest, "positive")
    existing_negative_count = _split_count(manifest, "negative")
    positive_increment = round(additional_docs * existing_positive_count / max(len(manifest.documents), 1))
    positive_increment = min(max(positive_increment, 1), additional_docs)
    negative_increment = additional_docs - positive_increment
    positive_doc_ids = {qrel.doc_id for qrel in dataset.qrels.values()}
    query_counts = Counter(qrel.doc_id for qrel in dataset.qrels.values())
    positive_buckets: dict[str, list[str]] = defaultdict(list)
    negative_buckets: dict[str, list[str]] = defaultdict(list)
    category_order = list(manifest.categories)
    for doc_id, doc in dataset.corpus_docs.items():
        if doc_id in existing_doc_ids or doc_id not in dataset.pdf_urls:
            continue
        assigned_category = _assigned_category(doc.categories, category_order)
        if assigned_category is None:
            continue
        if doc_id in positive_doc_ids:
            positive_buckets[assigned_category].append(doc_id)
        else:
            negative_buckets[assigned_category].append(doc_id)

    growth_seed = seed if seed is not None else manifest.seed + len(manifest.documents)
    selected_positive_doc_ids = _select_weighted_doc_ids(
        buckets=positive_buckets,
        category_order=category_order,
        target_count=positive_increment,
        seed=growth_seed,
    )
    selected_negative_doc_ids = _select_weighted_doc_ids(
        buckets=negative_buckets,
        category_order=category_order,
        target_count=negative_increment,
        seed=growth_seed + 1,
    )
    new_documents: list[SubsetDoc] = []
    for split, selected_doc_ids in (("positive", selected_positive_doc_ids), ("negative", selected_negative_doc_ids)):
        for doc_id in selected_doc_ids:
            doc = dataset.corpus_docs[doc_id]
            assigned_category = _assigned_category(doc.categories, category_order)
            if assigned_category is None:
                continue
            new_documents.append(
                SubsetDoc(
                    doc_id=doc_id,
                    title=doc.title or doc_id,
                    abstract=doc.abstract,
                    categories=doc.categories,
                    assigned_category=assigned_category,
                    split=cast(Literal["positive", "negative"], split),
                    pdf_url=dataset.pdf_urls[doc_id],
                    query_count=query_counts[doc_id],
                )
            )
    documents = sorted(
        [*manifest.documents, *new_documents], key=lambda item: (item.split, item.assigned_category, item.doc_id)
    )
    selected_positive_ids = {doc.doc_id for doc in documents if doc.split == "positive"}
    category_by_doc_id = {doc.doc_id: doc.assigned_category for doc in documents}
    queries: list[SubsetQuery] = []
    for query_id, qrel in sorted(dataset.qrels.items()):
        if qrel.doc_id not in selected_positive_ids:
            continue
        query = dataset.queries.get(query_id)
        if query is None:
            continue
        queries.append(
            SubsetQuery(
                query_id=query_id,
                query=query.query,
                query_type=query.type,
                query_source=query.source,
                expected_doc_id=qrel.doc_id,
                section_id=qrel.section_id,
                reference_answer=dataset.answers.get(query_id),
                category=category_by_doc_id[qrel.doc_id],
            )
        )
    return manifest.model_copy(
        update={
            "dataset_revision": dataset.dataset_revision or manifest.dataset_revision,
            "positive_doc_target": existing_positive_count + positive_increment,
            "negative_doc_target": existing_negative_count + negative_increment,
            "documents": documents,
            "queries": queries,
        }
    )


def aggregate_results(results: Sequence[QueryEvalResult], *, ks: Sequence[int]) -> list[MetricAtK]:
    return [_metric_at_k(results, k=k) for k in ks]


def build_eval_report(
    *,
    run_id: str,
    dataset_id: str,
    dataset_revision: str | None,
    tag_id: str,
    max_results: int,
    results: list[QueryEvalResult],
    answer_evaluations: list[AnswerEvalResult] | None = None,
    retrieval_duration_ms: float | None = None,
    answer_eval_duration_ms: float | None = None,
    ks: Sequence[int] = (1, 3, 5, 10),
) -> EvalReport:
    answer_items = answer_evaluations or []
    return EvalReport(
        run_id=run_id,
        dataset_id=dataset_id,
        dataset_revision=dataset_revision,
        tag_id=tag_id,
        max_results=max_results,
        query_count=len(results),
        metrics=aggregate_results(results, ks=ks),
        by_category=_grouped_metrics(results, key="category", ks=ks),
        by_query_type=_grouped_metrics(results, key="query_type", ks=ks),
        by_query_source=_grouped_metrics(results, key="query_source", ks=ks),
        results=results,
        answer_evaluations=answer_items,
        retrieval_duration_ms=retrieval_duration_ms,
        retrieval_latency=_latency_summary([result.latency_ms for result in results]),
        answer_eval_duration_ms=answer_eval_duration_ms,
        answer_latency=_latency_summary([item.latency_ms for item in answer_items]),
    )


async def upload_subset_to_app(
    *,
    api: EvalAppClient,
    manifest: SubsetManifest,
    pdf_dir: Path,
    tag_id: str,
    timeout_seconds: float = 900.0,
    existing_uploads: UploadManifest | None = None,
) -> UploadManifest:
    prior_by_doc_id = {
        upload.doc_id: upload
        for upload in (existing_uploads.uploads if existing_uploads is not None else [])
        if upload.source_id is not None and upload.status == "completed"
    }
    root_folder_id = existing_uploads.root_folder_id if existing_uploads is not None else None
    if root_folder_id is None:
        root_folder_id = await api.create_folder(name=f"Open RAGBench {manifest.run_id}")
    folder_by_category: dict[str, str] = {
        upload.category: upload.folder_id
        for upload in (existing_uploads.uploads if existing_uploads is not None else [])
        if upload.folder_id is not None
    }
    for category in sorted({doc.assigned_category for doc in manifest.documents}):
        if category not in folder_by_category:
            folder_by_category[category] = await api.create_folder(name=category, parent_id=root_folder_id)

    upload_manifest = UploadManifest(run_id=manifest.run_id, tag_id=tag_id, root_folder_id=root_folder_id)
    for doc in manifest.documents:
        filename = pdf_filename(doc)
        path = pdf_dir / filename
        virtual_name = f"{doc.doc_id} - {_safe_filename_part(doc.title)}.pdf"
        prior = prior_by_doc_id.get(doc.doc_id)
        if prior is not None:
            upload_manifest.uploads.append(prior)
            continue
        if not path.exists():
            upload_manifest.uploads.append(
                UploadRecord(
                    doc_id=doc.doc_id,
                    status="failed",
                    filename=filename,
                    virtual_name=virtual_name,
                    category=doc.assigned_category,
                    folder_id=folder_by_category[doc.assigned_category],
                    error_message=f"Missing PDF: {path}",
                )
            )
            continue
        try:
            source_id, task_id = await api.upload_pdf(
                path=path,
                tag_id=tag_id,
                folder_id=folder_by_category[doc.assigned_category],
                virtual_name=virtual_name,
                user_guidance="Open RAGBench PDF retrieval evaluation source. Preserve scientific terminology.",
            )
            status = (
                "completed"
                if task_id is None
                else await api.wait_for_task(
                    task_id=task_id,
                    timeout_seconds=timeout_seconds,
                )
            )
            upload_manifest.uploads.append(
                UploadRecord(
                    doc_id=doc.doc_id,
                    source_id=source_id,
                    task_id=task_id,
                    status=status,
                    filename=filename,
                    virtual_name=virtual_name,
                    category=doc.assigned_category,
                    folder_id=folder_by_category[doc.assigned_category],
                )
            )
        except Exception as exc:
            upload_manifest.uploads.append(
                UploadRecord(
                    doc_id=doc.doc_id,
                    status="failed",
                    filename=filename,
                    virtual_name=virtual_name,
                    category=doc.assigned_category,
                    folder_id=folder_by_category[doc.assigned_category],
                    error_message=str(exc),
                )
            )
            raise
    return upload_manifest


async def run_retrieval_eval(
    *,
    api: EvalAppClient,
    manifest: SubsetManifest,
    uploads: UploadManifest,
    max_results: int = 10,
    max_queries: int | None = None,
    concurrency: int = 50,
) -> EvalReport:
    source_to_doc_id = {
        upload.source_id: upload.doc_id
        for upload in uploads.uploads
        if upload.source_id is not None and upload.status == "completed"
    }
    selected_queries = manifest.queries[:max_queries] if max_queries is not None else manifest.queries
    logger.info(
        "open_ragbench_retrieval_eval_started run_id=%s queries=%s max_results=%s concurrency=%s tag_id=%s",
        manifest.run_id,
        len(selected_queries),
        max_results,
        max(1, concurrency),
        uploads.tag_id,
    )
    semaphore = asyncio.Semaphore(max(1, concurrency))
    eval_started_at = monotonic()

    async def score_query(query_index: int, query: SubsetQuery) -> tuple[int, QueryEvalResult]:
        async with semaphore:
            query_started_at = monotonic()
            hits_payload = await api.search(query=query.query, tag_id=uploads.tag_id, max_results=max_results)
            latency_ms = (monotonic() - query_started_at) * 1000
        hits: list[SearchHitRecord] = []
        expected_rank: int | None = None
        for rank, hit in enumerate(hits_payload, start=1):
            source_id = hit.get("source_file_id")
            if not isinstance(source_id, str):
                continue
            doc_id = source_to_doc_id.get(source_id)
            score = hit.get("score")
            source_title = hit.get("source_title")
            hits.append(
                SearchHitRecord(
                    source_id=source_id,
                    doc_id=doc_id,
                    source_title=source_title if isinstance(source_title, str) else source_id,
                    score=float(score) if isinstance(score, int | float) else 0.0,
                    rank=rank,
                )
            )
            if doc_id == query.expected_doc_id and expected_rank is None:
                expected_rank = rank
        return (
            query_index,
            QueryEvalResult(
                query_id=query.query_id,
                query=query.query,
                expected_doc_id=query.expected_doc_id,
                category=query.category,
                query_type=query.query_type,
                query_source=query.query_source,
                expected_rank=expected_rank,
                hits=hits,
                latency_ms=latency_ms,
            ),
        )

    tasks = [asyncio.create_task(score_query(index, query)) for index, query in enumerate(selected_queries)]
    results_by_index: list[QueryEvalResult | None] = [None] * len(selected_queries)
    completed_count = 0
    hit_count = 0
    for task in asyncio.as_completed(tasks):
        index, result = await task
        results_by_index[index] = result
        completed_count += 1
        if result.expected_rank is not None:
            hit_count += 1
        if completed_count % 25 == 0 or completed_count == len(selected_queries):
            logger.info(
                "open_ragbench_retrieval_eval_progress run_id=%s scored=%s total=%s hits=%s",
                manifest.run_id,
                completed_count,
                len(selected_queries),
                hit_count,
            )
    results = [result for result in results_by_index if result is not None]
    misses = len([result for result in results if result.expected_rank is None])
    retrieval_duration_ms = (monotonic() - eval_started_at) * 1000
    report = build_eval_report(
        run_id=manifest.run_id,
        dataset_id=manifest.dataset_id,
        dataset_revision=manifest.dataset_revision,
        tag_id=uploads.tag_id,
        max_results=max_results,
        results=results,
        retrieval_duration_ms=retrieval_duration_ms,
    )
    logger.info(
        "open_ragbench_retrieval_eval_completed run_id=%s queries=%s misses=%s duration_ms=%.1f mean_latency_ms=%s p95_latency_ms=%s",
        manifest.run_id,
        len(results),
        misses,
        retrieval_duration_ms,
        _format_optional_latency(report.retrieval_latency.mean_ms if report.retrieval_latency is not None else None),
        _format_optional_latency(report.retrieval_latency.p95_ms if report.retrieval_latency is not None else None),
    )
    return report


async def run_answer_eval(
    *,
    api: EvalAppClient,
    manifest: SubsetManifest,
    uploads: UploadManifest,
    retrieval_report: EvalReport,
    sample_size: int = 5,
    max_results: int = 8,
) -> list[AnswerEvalResult]:
    source_to_doc_id = {
        upload.source_id: upload.doc_id
        for upload in uploads.uploads
        if upload.source_id is not None and upload.status == "completed"
    }
    query_by_id = {query.query_id: query for query in manifest.queries}
    selected_results = _select_answer_sample(retrieval_report.results, sample_size=sample_size)
    logger.info(
        "open_ragbench_answer_eval_started run_id=%s sample_size=%s max_results=%s",
        manifest.run_id,
        len(selected_results),
        max_results,
    )
    evaluations: list[AnswerEvalResult] = []
    for result in selected_results:
        query = query_by_id[result.query_id]
        answer_started_at = monotonic()
        answer, hits = await api.qa(query=result.query, tag_id=uploads.tag_id, max_results=max_results)
        latency_ms = (monotonic() - answer_started_at) * 1000
        used_hits: list[SearchHitRecord] = []
        hit_doc_ids: list[str | None] = []
        for index, hit in enumerate(hits, start=1):
            source_id = hit.get("source_file_id")
            if not isinstance(source_id, str):
                continue
            doc_id = source_to_doc_id.get(source_id)
            hit_doc_ids.append(doc_id)
            score = hit.get("score")
            source_title = hit.get("source_title")
            used_hits.append(
                SearchHitRecord(
                    source_id=source_id,
                    doc_id=doc_id,
                    source_title=source_title if isinstance(source_title, str) else source_id,
                    score=float(score) if isinstance(score, int | float) else 0.0,
                    rank=index,
                )
            )
        retrieved_expected_doc = result.expected_doc_id in hit_doc_ids
        coverage, covered_terms = _reference_coverage(answer=answer, reference=query.reference_answer)
        verdict: Literal["pass", "review", "fail"]
        if retrieved_expected_doc and coverage >= 0.25:
            verdict = "pass"
        elif retrieved_expected_doc or coverage >= 0.25:
            verdict = "review"
        else:
            verdict = "fail"
        evaluations.append(
            AnswerEvalResult(
                query_id=result.query_id,
                query=result.query,
                expected_doc_id=result.expected_doc_id,
                reference_answer=query.reference_answer,
                generated_answer=answer,
                expected_doc_rank=result.expected_rank,
                retrieved_expected_doc=retrieved_expected_doc,
                used_hits=used_hits,
                reference_coverage=coverage,
                covered_reference_terms=covered_terms,
                verdict=verdict,
                latency_ms=latency_ms,
            )
        )
        logger.info(
            "open_ragbench_answer_eval_query run_id=%s query_id=%s verdict=%s expected_doc_retrieved=%s coverage=%.3f latency_ms=%.1f",
            manifest.run_id,
            result.query_id,
            verdict,
            retrieved_expected_doc,
            coverage,
            latency_ms,
        )
    logger.info("open_ragbench_answer_eval_completed run_id=%s samples=%s", manifest.run_id, len(evaluations))
    return evaluations


def render_summary_markdown(*, manifest: SubsetManifest, uploads: UploadManifest, report: EvalReport) -> str:
    upload_statuses = Counter(upload.status for upload in uploads.uploads)
    recall_at_10 = _metric_value(report.metrics, k=10, metric="recall")
    mrr_at_10 = _metric_value(report.metrics, k=10, metric="mrr")
    ndcg_at_10 = _metric_value(report.metrics, k=10, metric="ndcg")
    recall_at_1 = _metric_value(report.metrics, k=1, metric="recall")
    retrieval_latency = report.retrieval_latency
    answer_latency = report.answer_latency
    answer_passes = len([item for item in report.answer_evaluations if item.verdict == "pass"])
    answer_count = len(report.answer_evaluations)
    misses = [result for result in report.results if result.expected_rank is None]
    weakest_categories = [
        item for item in _weakest_metric_groups(report.by_category, metric="recall", k=10, limit=3) if item[1] < 0.9995
    ]
    lines = [
        f"# Open RAGBench Retrieval Eval: {report.run_id}",
        "",
        f"- Eval version: `{report.eval_version}`",
        f"- Created at: `{report.created_at.isoformat()}`",
        f"- Dataset: `{manifest.dataset_id}`",
        f"- Dataset revision: `{manifest.dataset_revision or 'unknown'}`",
        f"- Documents: {len(manifest.documents)} ({_split_count(manifest, 'positive')} positive, {_split_count(manifest, 'negative')} negative)",
        f"- Queries scored: {report.query_count}",
        f"- Eval tag: `{report.tag_id}`",
        f"- Upload statuses: {_counter_text(upload_statuses)}",
        "",
        "## Summary",
        "",
        f"This run retrieved the expected document in the first result for {_format_optional_metric(recall_at_1)} of queries and within the top 10 for {_format_optional_metric(recall_at_10)} of queries.",
        f"The top-10 ranking quality was MRR@10={_format_optional_metric(mrr_at_10)} and nDCG@10={_format_optional_metric(ndcg_at_10)} across {report.query_count} scored queries.",
        f"The five-question answer check produced {answer_passes}/{answer_count} pass verdicts."
        if answer_count
        else "No answer-generation sample was scored.",
        f"Retrieval scoring took {_format_seconds(report.retrieval_duration_ms)} wall-clock with p95 search latency {_format_optional_latency(retrieval_latency.p95_ms if retrieval_latency is not None else None)} ms.",
        f"There were {len(misses)} retrieval misses at top {report.max_results}.",
        "",
        "Detailed artifacts: [full retrieval results](results.json), [query review](demo_queries.md), [detailed metric tables](detailed_metrics.md).",
        "",
        "## Key Metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Recall@1 | {_format_optional_metric(recall_at_1)} |",
        f"| Recall@10 | {_format_optional_metric(recall_at_10)} |",
        f"| MRR@10 | {_format_optional_metric(mrr_at_10)} |",
        f"| nDCG@10 | {_format_optional_metric(ndcg_at_10)} |",
        "",
        "## Latency",
        "",
        "| Stage | Wall Clock | Count | Mean | P50 | P95 | Max |",
        "|---|---:|---:|---:|---:|---:|---:|",
        _latency_table_row(
            "Retrieval search requests",
            duration_ms=report.retrieval_duration_ms,
            summary=retrieval_latency,
        ),
        _latency_table_row(
            "Answer-generation checks",
            duration_ms=report.answer_eval_duration_ms,
            summary=answer_latency,
        ),
    ]
    if weakest_categories:
        lines.extend(
            [
                "",
                "## Notes",
                "",
                "Lowest category Recall@10 values: "
                + ", ".join(f"`{group}`={value:.3f}" for group, value in weakest_categories)
                + ".",
            ]
        )
    lines.extend(
        [
            "",
            "## Baseline Context",
            "",
            "This table is deliberately conservative. The only row directly produced by this harness is this app run. The other rows are scale and sanity-check references from adjacent benchmarks, not leaderboard claims against the same corpus, chunking, relevance labels, or serving path.",
            "",
            "| Reference | What It Measures | Reported Signal | Why It Is Not Directly Comparable | Source |",
            "|---|---|---|---|---|",
            f"| This app run | Document-level retrieval on a topic-filtered Open RAGBench PDF subset, {len(manifest.documents)} docs / {report.query_count} queries | Recall@10={_format_optional_metric(recall_at_10)}, MRR@10={_format_optional_metric(mrr_at_10)}, nDCG@10={_format_optional_metric(ndcg_at_10)} | Direct result, but on a deliberately small curated subset | this report |",
            f"| Open RAGBench draft corpus | Multimodal arXiv PDF RAG dataset | 1000 PDFs, 3045 QA pairs, 400 positive docs, 600 hard negatives | Dataset scale reference, not a published retrieval-system score | [Vectara Open RAG Bench]({OPEN_RAGBENCH_REPO_URL}) |",
            f"| BEIR SciFact BM25 | Scientific abstract retrieval | nDCG@10=0.665 | Different corpus, abstract-level documents, and BEIR relevance labels | [BEIR paper, Table 2]({BEIR_PAPER_URL}) |",
            f"| BEIR SciFact BM25 + cross-encoder | Scientific abstract retrieval with reranking | nDCG@10=0.688 | Different corpus and reranker setup; useful as a mature retrieval baseline sanity check | [BEIR paper, Table 2]({BEIR_PAPER_URL}) |",
            f"| RAGBench / TRACe | End-to-end RAG answer evaluation across industry domains | 100k examples and explainable RAG labels | Evaluates generated-answer behavior rather than document-level top-k retrieval | [RAGBench paper]({RAGBENCH_PAPER_URL}) |",
        ]
    )
    if report.answer_evaluations:
        lines.extend(["", "## Five-Question Answer Evaluation", ""])
        lines.append(
            "Detailed document traces and retrieved/used sources: [query review](demo_queries.md#five-answer-checks)."
        )
        for item in report.answer_evaluations:
            lines.extend(
                [
                    "",
                    f"### `{item.query_id}`",
                    "",
                    f"- Verdict: `{item.verdict}`",
                    f"- Expected doc retrieved: `{str(item.retrieved_expected_doc).lower()}`",
                    f"- Reference coverage: `{item.reference_coverage:.3f}`",
                    f"- QA latency: `{_format_optional_latency(item.latency_ms)} ms`",
                    "",
                    "**Question**",
                    "",
                    item.query,
                    "",
                    "**Expected Answer**",
                    "",
                    item.reference_answer or "(none)",
                    "",
                    "**System Answer**",
                    "",
                    item.generated_answer,
                ]
            )
    lines.extend(
        [
            "",
            "## Metric Definitions",
            "",
            "- Recall@k: fraction of scored queries where the expected Open RAGBench document appears anywhere in the top k search results.",
            "- MRR@k: mean reciprocal rank of the expected document when it appears in the top k; a rank-1 hit contributes 1.0, rank 3 contributes 0.333, and a miss contributes 0.",
            "- nDCG@k: rank-discounted gain for the expected document in the top k. This run has one relevant document per query, so nDCG is 1.0 at rank 1, 1/log2(rank+1) at lower ranks, and 0 for a miss.",
            "- Search latency: elapsed time for an individual `/api/search` request after it starts under the eval runner's concurrency gate; retrieval wall clock is the elapsed time for the full batch.",
            "- QA latency: elapsed time for an individual `/api/actions/qa` request, including answer generation and any retrieval performed by that API path.",
            "- Reference coverage: lightweight answer-eval heuristic measuring how many non-stopword terms from the Open RAGBench reference answer appear in the generated answer.",
            "- Verdict: pass when the expected document is retrieved and reference coverage is at least 0.25, review when only one of those checks passes, and fail otherwise.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def render_detailed_metrics_markdown(*, report: EvalReport) -> str:
    lines = [
        f"# Detailed Metrics: {report.run_id}",
        "",
        "## Overall Metrics",
        "",
        "| k | Recall | MRR | nDCG |",
        "|---:|---:|---:|---:|",
    ]
    lines.extend(f"| {item.k} | {item.recall:.3f} | {item.mrr:.3f} | {item.ndcg:.3f} |" for item in report.metrics)
    lines.extend(["", "## Latency", ""])
    lines.extend(
        [
            "| Stage | Wall Clock | Count | Mean | P50 | P95 | Max |",
            "|---|---:|---:|---:|---:|---:|---:|",
            _latency_table_row(
                "Retrieval search requests",
                duration_ms=report.retrieval_duration_ms,
                summary=report.retrieval_latency,
            ),
            _latency_table_row(
                "Answer-generation checks",
                duration_ms=report.answer_eval_duration_ms,
                summary=report.answer_latency,
            ),
        ]
    )
    lines.extend(["", "## Category Breakdown", ""])
    lines.extend(_metric_group_table(report.by_category))
    lines.extend(["", "## Query Type Breakdown", ""])
    lines.extend(_metric_group_table(report.by_query_type))
    lines.extend(["", "## Query Source Breakdown", ""])
    lines.extend(_metric_group_table(report.by_query_source))
    return "\n".join(lines).strip() + "\n"


def render_demo_queries_markdown(
    *,
    manifest: SubsetManifest,
    uploads: UploadManifest,
    report: EvalReport,
    limit_each: int = 5,
) -> str:
    del uploads
    docs_by_id = {doc.doc_id: doc for doc in manifest.documents}
    queries_by_id = {query.query_id: query for query in manifest.queries}
    answer_by_query_id = {item.query_id: item for item in report.answer_evaluations}
    successes = [result for result in report.results if result.expected_rank is not None]
    misses = [result for result in report.results if result.expected_rank is None]
    answer_results = [result for result in report.results if result.query_id in answer_by_query_id]
    lines = [f"# Query Review: {report.run_id}", "", "## Five Answer Checks", ""]
    for result in answer_results[:limit_each]:
        lines.extend(
            [
                _render_demo_result(
                    result,
                    docs_by_id=docs_by_id,
                    query=queries_by_id.get(result.query_id),
                    answer_eval=answer_by_query_id.get(result.query_id),
                ),
                "",
            ]
        )
    lines.extend(["", "## Successful Retrievals", ""])
    for result in successes[:limit_each]:
        lines.extend(
            [
                _render_demo_result(
                    result,
                    docs_by_id=docs_by_id,
                    query=queries_by_id.get(result.query_id),
                    answer_eval=answer_by_query_id.get(result.query_id),
                ),
                "",
            ]
        )
    lines.extend(["", "## Misses To Inspect", ""])
    for result in misses[:limit_each]:
        lines.extend(
            [
                _render_demo_result(
                    result,
                    docs_by_id=docs_by_id,
                    query=queries_by_id.get(result.query_id),
                    answer_eval=answer_by_query_id.get(result.query_id),
                ),
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def pdf_filename(doc: SubsetDoc) -> str:
    return f"{doc.doc_id}.pdf"


def write_json(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    logger.debug("open_ragbench_json_written path=%s", path)


def write_report_artifacts(
    *, output_dir: Path, manifest: SubsetManifest, uploads: UploadManifest, report: EvalReport
) -> None:
    write_json(output_dir / "results.json", report)
    (output_dir / "summary.md").write_text(
        render_summary_markdown(manifest=manifest, uploads=uploads, report=report),
        encoding="utf-8",
    )
    (output_dir / "detailed_metrics.md").write_text(render_detailed_metrics_markdown(report=report), encoding="utf-8")
    (output_dir / "demo_queries.md").write_text(
        render_demo_queries_markdown(manifest=manifest, uploads=uploads, report=report),
        encoding="utf-8",
    )
    sync_latest_artifacts(output_dir=output_dir)


def sync_latest_artifacts(*, output_dir: Path, latest_dir: Path = DEFAULT_LATEST_ARTIFACT_DIR) -> None:
    latest_dir.mkdir(parents=True, exist_ok=True)
    for filename in (
        "subset.json",
        "uploads.json",
        "results.json",
        "summary.md",
        "demo_queries.md",
        "detailed_metrics.md",
    ):
        source = output_dir / filename
        if source.exists():
            shutil.copy2(source, latest_dir / filename)
    logger.info("open_ragbench_latest_artifacts_synced source_dir=%s latest_dir=%s", output_dir, latest_dir)


def read_subset_manifest(path: Path) -> SubsetManifest:
    return SubsetManifest.model_validate_json(path.read_text(encoding="utf-8"))


def read_upload_manifest(path: Path) -> UploadManifest:
    return UploadManifest.model_validate_json(path.read_text(encoding="utf-8"))


def read_eval_report(path: Path) -> EvalReport:
    return EvalReport.model_validate_json(path.read_text(encoding="utf-8"))


def load_remote_dataset(
    *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS, max_workers: int = 8
) -> OpenRagbenchDataset:
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        queries = {
            key: OpenRagbenchQuery.model_validate(value)
            for key, value in cast(
                dict[str, object], client.get(f"{DATASET_BASE_URL}/queries.json").raise_for_status().json()
            ).items()
            if isinstance(value, dict)
        }
        qrels = {
            key: OpenRagbenchQrel.model_validate(value)
            for key, value in cast(
                dict[str, object], client.get(f"{DATASET_BASE_URL}/qrels.json").raise_for_status().json()
            ).items()
            if isinstance(value, dict)
        }
        answers = {
            key: str(value)
            for key, value in cast(
                dict[str, object], client.get(f"{DATASET_BASE_URL}/answers.json").raise_for_status().json()
            ).items()
        }
        pdf_urls = {
            key: str(value)
            for key, value in cast(
                dict[str, object], client.get(f"{DATASET_BASE_URL}/pdf_urls.json").raise_for_status().json()
            ).items()
        }
        tree_rows = cast(list[object], client.get(CORPUS_TREE_URL).raise_for_status().json())
        dataset_payload = cast(
            dict[str, object],
            client.get("https://huggingface.co/api/datasets/vectara/open_ragbench").raise_for_status().json(),
        )
    corpus_entries = [
        cast(dict[str, object], row)
        for row in tree_rows
        if isinstance(row, dict)
        and row.get("type") == "file"
        and isinstance(row.get("path"), str)
        and cast(str, row["path"]).endswith(".json")
    ]
    corpus_docs = _load_corpus_metadata(corpus_entries, timeout_seconds=timeout_seconds, max_workers=max_workers)
    dataset_revision = dataset_payload.get("sha")
    return OpenRagbenchDataset(
        dataset_revision=dataset_revision if isinstance(dataset_revision, str) else None,
        queries=queries,
        qrels=qrels,
        answers=answers,
        pdf_urls=pdf_urls,
        corpus_docs=corpus_docs,
    )


def enrich_manifest_titles(
    *, manifest: SubsetManifest, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> SubsetManifest:
    docs: list[SubsetDoc] = []
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        for doc in manifest.documents:
            try:
                payload = cast(
                    dict[str, object],
                    client.get(f"{DATASET_BASE_URL}/corpus/{doc.doc_id}.json").raise_for_status().json(),
                )
            except httpx.HTTPError:
                docs.append(doc)
                continue
            title = payload.get("title")
            abstract = payload.get("abstract")
            categories = payload.get("categories")
            docs.append(
                doc.model_copy(
                    update={
                        "title": title if isinstance(title, str) and title.strip() else doc.title,
                        "abstract": abstract if isinstance(abstract, str) and abstract.strip() else doc.abstract,
                        "categories": [item for item in categories if isinstance(item, str)]
                        if isinstance(categories, list)
                        else doc.categories,
                    }
                )
            )
    return manifest.model_copy(update={"documents": docs})


def download_subset_pdfs(
    *,
    manifest: SubsetManifest,
    pdf_dir: Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    reused_count = 0
    downloaded_count = 0
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        for doc in manifest.documents:
            path = pdf_dir / pdf_filename(doc)
            if path.exists() and path.stat().st_size > 0:
                reused_count += 1
                continue
            response = client.get(doc.pdf_url)
            response.raise_for_status()
            media_type = response.headers.get("content-type", "").casefold()
            if "pdf" not in media_type and not response.content.startswith(b"%PDF"):
                raise RuntimeError(f"Download for {doc.doc_id} did not look like a PDF: {doc.pdf_url}")
            path.write_bytes(response.content)
            downloaded_count += 1
    logger.info(
        "open_ragbench_pdf_setup_complete run_id=%s pdf_reused=%s pdf_downloaded=%s pdf_dir=%s",
        manifest.run_id,
        reused_count,
        downloaded_count,
        pdf_dir,
    )


async def download_pdf_if_missing(
    *,
    client: httpx.AsyncClient,
    doc: SubsetDoc,
    pdf_dir: Path,
) -> tuple[Path, bool]:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    path = pdf_dir / pdf_filename(doc)
    if path.exists() and path.stat().st_size > 0:
        return path, False
    response = await client.get(doc.pdf_url)
    response.raise_for_status()
    media_type = response.headers.get("content-type", "").casefold()
    if "pdf" not in media_type and not response.content.startswith(b"%PDF"):
        raise RuntimeError(f"Download for {doc.doc_id} did not look like a PDF: {doc.pdf_url}")
    path.write_bytes(response.content)
    return path, True


def setup_subset(
    *,
    run_id: str,
    output_root: Path,
    categories: Sequence[str],
    positive_docs: int,
    negative_docs: int,
    seed: int,
    max_workers: int,
) -> SubsetManifest:
    output_dir = output_root / run_id
    subset_path = output_dir / "subset.json"
    if subset_path.exists():
        manifest = read_subset_manifest(subset_path)
        logger.info("open_ragbench_subset_reused run_id=%s subset_path=%s", run_id, subset_path)
    else:
        logger.info("open_ragbench_subset_build_started run_id=%s subset_path=%s", run_id, subset_path)
        dataset = load_remote_dataset(max_workers=max_workers)
        manifest = select_subset(
            dataset=dataset,
            run_id=run_id,
            categories=categories,
            positive_doc_target=positive_docs,
            negative_doc_target=negative_docs,
            seed=seed,
        )
        manifest = enrich_manifest_titles(manifest=manifest)
        write_json(subset_path, manifest)
        logger.info(
            "open_ragbench_subset_built run_id=%s documents=%s queries=%s",
            run_id,
            len(manifest.documents),
            len(manifest.queries),
        )
    download_subset_pdfs(manifest=manifest, pdf_dir=output_dir / "pdfs")
    return manifest


async def setup_and_upload_progressively(
    *,
    api: EvalAppClient,
    run_id: str,
    output_root: Path,
    tag_id: str,
    categories: Sequence[str],
    positive_docs: int,
    negative_docs: int,
    grow_by: int,
    seed: int,
    max_workers: int,
    task_timeout_seconds: float,
    download_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> UploadManifest:
    output_dir = output_root / run_id
    subset_path = output_dir / "subset.json"
    uploads_path = output_dir / "uploads.json"
    if subset_path.exists():
        manifest = read_subset_manifest(subset_path)
        logger.info("open_ragbench_subset_reused run_id=%s subset_path=%s", run_id, subset_path)
        if grow_by > 0:
            dataset = load_remote_dataset(max_workers=max_workers)
            before_count = len(manifest.documents)
            manifest = grow_subset(dataset=dataset, manifest=manifest, additional_docs=grow_by)
            manifest = enrich_manifest_titles(manifest=manifest)
            write_json(subset_path, manifest)
            logger.info(
                "open_ragbench_subset_grown run_id=%s previous_documents=%s documents=%s grow_by=%s",
                run_id,
                before_count,
                len(manifest.documents),
                grow_by,
            )
    else:
        logger.info("open_ragbench_subset_build_started run_id=%s subset_path=%s", run_id, subset_path)
        dataset = load_remote_dataset(max_workers=max_workers)
        manifest = select_subset(
            dataset=dataset,
            run_id=run_id,
            categories=categories,
            positive_doc_target=positive_docs,
            negative_doc_target=negative_docs,
            seed=seed,
        )
        manifest = enrich_manifest_titles(manifest=manifest)
        write_json(subset_path, manifest)
        logger.info(
            "open_ragbench_subset_built run_id=%s documents=%s queries=%s",
            run_id,
            len(manifest.documents),
            len(manifest.queries),
        )

    existing_uploads = read_upload_manifest(uploads_path) if uploads_path.exists() else None
    prior_by_doc_id = {
        upload.doc_id: upload
        for upload in (existing_uploads.uploads if existing_uploads is not None else [])
        if upload.source_id is not None and upload.status == "completed"
    }
    root_folder_id = existing_uploads.root_folder_id if existing_uploads is not None else None
    if root_folder_id is None:
        root_folder_id = await api.create_folder(name=f"Open RAGBench {manifest.run_id}")
    folder_by_category: dict[str, str] = {
        upload.category: upload.folder_id
        for upload in (existing_uploads.uploads if existing_uploads is not None else [])
        if upload.folder_id is not None
    }
    for category in sorted({doc.assigned_category for doc in manifest.documents}):
        if category not in folder_by_category:
            folder_by_category[category] = await api.create_folder(name=category, parent_id=root_folder_id)

    upload_manifest = UploadManifest(run_id=manifest.run_id, tag_id=tag_id, root_folder_id=root_folder_id)
    reused_upload_count = 0
    reused_pdf_count = 0
    downloaded_pdf_count = 0
    uploaded_count = 0
    logger.info(
        "open_ragbench_setup_upload_started run_id=%s documents=%s prior_completed_uploads=%s tag_id=%s",
        run_id,
        len(manifest.documents),
        len(prior_by_doc_id),
        tag_id,
    )
    async with httpx.AsyncClient(timeout=download_timeout_seconds, follow_redirects=True) as download_client:
        for doc in manifest.documents:
            filename = pdf_filename(doc)
            virtual_name = f"{doc.doc_id} - {_safe_filename_part(doc.title)}.pdf"
            prior = prior_by_doc_id.get(doc.doc_id)
            if prior is not None:
                reused_upload_count += 1
                upload_manifest.uploads.append(prior)
                write_json(uploads_path, upload_manifest)
                continue
            try:
                path, downloaded = await download_pdf_if_missing(
                    client=download_client,
                    doc=doc,
                    pdf_dir=output_dir / "pdfs",
                )
                if downloaded:
                    downloaded_pdf_count += 1
                else:
                    reused_pdf_count += 1
                source_id, task_id = await api.upload_pdf(
                    path=path,
                    tag_id=tag_id,
                    folder_id=folder_by_category[doc.assigned_category],
                    virtual_name=virtual_name,
                    user_guidance="Open RAGBench PDF retrieval evaluation source. Preserve scientific terminology.",
                )
                status = (
                    "completed"
                    if task_id is None
                    else await api.wait_for_task(
                        task_id=task_id,
                        timeout_seconds=task_timeout_seconds,
                    )
                )
                upload_manifest.uploads.append(
                    UploadRecord(
                        doc_id=doc.doc_id,
                        source_id=source_id,
                        task_id=task_id,
                        status=status,
                        filename=filename,
                        virtual_name=virtual_name,
                        category=doc.assigned_category,
                        folder_id=folder_by_category[doc.assigned_category],
                    )
                )
                uploaded_count += 1
                write_json(uploads_path, upload_manifest)
            except Exception as exc:
                upload_manifest.uploads.append(
                    UploadRecord(
                        doc_id=doc.doc_id,
                        status="failed",
                        filename=filename,
                        virtual_name=virtual_name,
                        category=doc.assigned_category,
                        folder_id=folder_by_category[doc.assigned_category],
                        error_message=str(exc),
                    )
                )
                write_json(uploads_path, upload_manifest)
                raise
    logger.info(
        "open_ragbench_setup_upload_completed run_id=%s reused_uploads=%s uploaded=%s pdf_reused=%s pdf_downloaded=%s uploads_path=%s",
        run_id,
        reused_upload_count,
        uploaded_count,
        reused_pdf_count,
        downloaded_pdf_count,
        uploads_path,
    )
    return upload_manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    if args.command == "setup":
        run_id = args.run_id or f"{DEFAULT_RUN_ID_PREFIX}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        output_root = Path(args.output_root)
        manifest = setup_subset(
            run_id=run_id,
            output_root=output_root,
            categories=args.categories,
            positive_docs=args.positive_docs,
            negative_docs=args.negative_docs,
            seed=args.seed,
            max_workers=args.max_workers,
        )
        print(f"Prepared {output_root / manifest.run_id}")
        return 0
    if args.command == "setup-upload":
        run_id = args.run_id or f"{DEFAULT_RUN_ID_PREFIX}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        output_root = Path(args.output_root)
        tag_id = args.tag_id or f"open-ragbench-eval-{run_id}"
        uploads = asyncio.run(
            _setup_upload_from_cli(
                base_url=args.base_url,
                auth_token=args.auth_token,
                library_id=args.library_id,
                create_public_library=args.create_public_library,
                public_library_title=args.public_library_title,
                public_library_slug=args.public_library_slug,
                run_id=run_id,
                output_root=output_root,
                tag_id=tag_id,
                categories=args.categories,
                positive_docs=args.positive_docs,
                negative_docs=args.negative_docs,
                grow_by=args.grow_by,
                seed=args.seed,
                max_workers=args.max_workers,
                task_timeout_seconds=args.task_timeout,
            )
        )
        print(f"Wrote {output_root / run_id / 'uploads.json'} ({len(uploads.uploads)} records)")
        return 0
    if args.command == "build-subset":
        run_id = args.run_id or f"{DEFAULT_RUN_ID_PREFIX}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        output_dir = Path(args.output_root) / run_id
        dataset = load_remote_dataset(max_workers=args.max_workers)
        manifest = select_subset(
            dataset=dataset,
            run_id=run_id,
            categories=args.categories,
            positive_doc_target=args.positive_docs,
            negative_doc_target=args.negative_docs,
            seed=args.seed,
        )
        manifest = enrich_manifest_titles(manifest=manifest)
        download_subset_pdfs(manifest=manifest, pdf_dir=output_dir / "pdfs")
        write_json(output_dir / "subset.json", manifest)
        print(f"Wrote {output_dir / 'subset.json'}")
        return 0
    if args.command == "upload":
        output_dir = Path(args.run_dir)
        manifest = read_subset_manifest(output_dir / "subset.json")
        existing_uploads = (
            read_upload_manifest(output_dir / "uploads.json") if (output_dir / "uploads.json").exists() else None
        )
        tag_id = args.tag_id or (
            existing_uploads.tag_id if existing_uploads is not None else f"open-ragbench-eval-{manifest.run_id}"
        )
        uploads = asyncio.run(
            _upload_from_cli(
                base_url=args.base_url,
                auth_token=args.auth_token,
                library_id=args.library_id,
                manifest=manifest,
                pdf_dir=output_dir / "pdfs",
                tag_id=tag_id,
                timeout_seconds=args.task_timeout,
                existing_uploads=existing_uploads,
            )
        )
        write_json(output_dir / "uploads.json", uploads)
        print(f"Wrote {output_dir / 'uploads.json'}")
        return 0
    if args.command == "report":
        output_dir = Path(args.run_dir)
        manifest = read_subset_manifest(output_dir / "subset.json")
        uploads = read_upload_manifest(output_dir / "uploads.json")
        report = read_eval_report(output_dir / "results.json")
        write_report_artifacts(output_dir=output_dir, manifest=manifest, uploads=uploads, report=report)
        _log_report_written(manifest=manifest, output_dir=output_dir)
        print(f"Wrote {output_dir / 'summary.md'}")
        return 0
    if args.command == "run":
        output_dir = Path(args.run_dir)
        manifest = read_subset_manifest(output_dir / "subset.json")
        uploads = read_upload_manifest(output_dir / "uploads.json")
        report = asyncio.run(
            _run_eval_from_cli(
                base_url=args.base_url,
                auth_token=args.auth_token,
                library_id=args.library_id,
                manifest=manifest,
                uploads=uploads,
                max_results=args.max_results,
                max_queries=args.max_queries,
                retrieval_concurrency=args.retrieval_concurrency,
                answer_sample_size=args.answer_sample_size,
                answer_max_results=args.answer_max_results,
            )
        )
        write_report_artifacts(output_dir=output_dir, manifest=manifest, uploads=uploads, report=report)
        _enforce_threshold(report=report, min_recall_at_10=args.min_recall_at_10)
        _log_report_written(manifest=manifest, output_dir=output_dir)
        print(f"Wrote {output_dir / 'results.json'}")
        return 0
    parser.print_help()
    return 2


def _load_corpus_metadata(
    entries: Sequence[Mapping[str, object]],
    *,
    timeout_seconds: float,
    max_workers: int,
) -> dict[str, CorpusDoc]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    docs: dict[str, CorpusDoc] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = [executor.submit(_fetch_corpus_doc_tail, entry, timeout_seconds) for entry in entries]
        for future in as_completed(futures):
            doc = future.result()
            docs[doc.doc_id] = doc
    return docs


def _fetch_corpus_doc_tail(entry: Mapping[str, object], timeout_seconds: float) -> CorpusDoc:
    path = cast(str, entry["path"])
    raw_size = entry.get("size")
    size = raw_size if isinstance(raw_size, int) else int(raw_size) if isinstance(raw_size, str) else 0
    doc_id = Path(path).stem
    start = max(0, size - 65_536)
    headers = {"Range": f"bytes={start}-{max(size - 1, 0)}"} if size > 0 else {}
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.get(
            f"https://huggingface.co/datasets/vectara/open_ragbench/resolve/main/{path}", headers=headers
        )
        response.raise_for_status()
        text = response.text
        categories = _extract_json_string_list(text, "categories")
        if not categories:
            full_response = client.get(f"https://huggingface.co/datasets/vectara/open_ragbench/resolve/main/{path}")
            full_response.raise_for_status()
            payload = cast(dict[str, object], full_response.json())
            categories_payload = payload.get("categories")
            categories = (
                [item for item in categories_payload if isinstance(item, str)]
                if isinstance(categories_payload, list)
                else []
            )
            title = payload.get("title")
            return CorpusDoc(
                doc_id=doc_id,
                title=title if isinstance(title, str) else None,
                categories=categories,
                corpus_path=path,
                corpus_size=size,
            )
    return CorpusDoc(doc_id=doc_id, categories=categories, corpus_path=path, corpus_size=size)


def _extract_json_string_list(text: str, key: str) -> list[str]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[(?P<body>.*?)\]', text, flags=re.DOTALL)
    if match is None:
        return []
    values = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group("body"))
    return [bytes(value, "utf-8").decode("unicode_escape") for value in values]


def _assigned_category(categories: Sequence[str], category_order: Sequence[str]) -> str | None:
    category_set = set(category_order)
    for category in categories:
        if category in category_set:
            return category
    return None


def _select_weighted_doc_ids(
    *,
    buckets: Mapping[str, Sequence[str]],
    category_order: Sequence[str],
    target_count: int,
    seed: int,
) -> list[str]:
    available = {category: len(buckets.get(category, ())) for category in category_order}
    if sum(available.values()) < target_count:
        raise ValueError(f"Not enough matching documents for target {target_count}: {available!r}")
    counts = _weighted_counts(available=available, target_count=target_count, category_order=category_order)
    rng = random.Random(seed)
    selected: list[str] = []
    for category in category_order:
        doc_ids = sorted(buckets.get(category, ()))
        rng.shuffle(doc_ids)
        selected.extend(sorted(doc_ids[: counts[category]]))
    return selected


def _weighted_counts(
    *,
    available: Mapping[str, int],
    target_count: int,
    category_order: Sequence[str],
) -> dict[str, int]:
    total_available = sum(available.get(category, 0) for category in category_order)
    if target_count < 0:
        raise ValueError("target_count must be non-negative.")
    if total_available < target_count:
        raise ValueError("target_count exceeds available documents.")
    raw_counts = {
        category: (target_count * available.get(category, 0) / total_available if total_available else 0.0)
        for category in category_order
    }
    counts = {category: min(int(raw_counts[category]), available.get(category, 0)) for category in category_order}
    remaining = target_count - sum(counts.values())
    while remaining > 0:
        candidates = [category for category in category_order if counts[category] < available.get(category, 0)]
        if not candidates:
            raise ValueError("Could not allocate weighted sample counts.")
        candidates.sort(
            key=lambda category: (
                raw_counts[category] - int(raw_counts[category]),
                available.get(category, 0) - counts[category],
                category,
            ),
            reverse=True,
        )
        for category in candidates:
            if remaining <= 0:
                break
            counts[category] += 1
            remaining -= 1
    return counts


def _metric_at_k(results: Sequence[QueryEvalResult], *, k: int) -> MetricAtK:
    if not results:
        return MetricAtK(k=k, recall=0.0, mrr=0.0, ndcg=0.0)
    recall_values: list[float] = []
    mrr_values: list[float] = []
    ndcg_values: list[float] = []
    for result in results:
        rank = result.expected_rank
        hit = rank is not None and rank <= k
        recall_values.append(1.0 if hit else 0.0)
        mrr_values.append((1.0 / rank) if hit and rank is not None else 0.0)
        ndcg_values.append((1.0 / log2(rank + 1)) if hit and rank is not None else 0.0)
    denominator = float(len(results))
    return MetricAtK(
        k=k,
        recall=sum(recall_values) / denominator,
        mrr=sum(mrr_values) / denominator,
        ndcg=sum(ndcg_values) / denominator,
    )


def _latency_summary(latencies_ms: Sequence[float]) -> LatencySummary | None:
    values = sorted(value for value in latencies_ms if value >= 0)
    if not values:
        return None
    total = sum(values)
    return LatencySummary(
        count=len(values),
        total_ms=total,
        mean_ms=total / len(values),
        p50_ms=_nearest_rank_percentile(values, percentile=0.50),
        p95_ms=_nearest_rank_percentile(values, percentile=0.95),
        max_ms=values[-1],
    )


def _nearest_rank_percentile(sorted_values: Sequence[float], *, percentile: float) -> float:
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, ceil(percentile * len(sorted_values)) - 1))
    return sorted_values[index]


def _grouped_metrics(
    results: Sequence[QueryEvalResult],
    *,
    key: Literal["category", "query_type", "query_source"],
    ks: Sequence[int],
) -> dict[str, list[MetricAtK]]:
    grouped: dict[str, list[QueryEvalResult]] = defaultdict(list)
    for result in results:
        grouped[str(getattr(result, key))].append(result)
    return {group: aggregate_results(group_results, ks=ks) for group, group_results in sorted(grouped.items())}


def _safe_filename_part(value: str, *, max_length: int = 96) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", value.replace("\n", " ")).strip(" .-_")
    return (cleaned or "paper")[:max_length].strip() or "paper"


def _split_count(manifest: SubsetManifest, split: Literal["positive", "negative"]) -> int:
    return len([doc for doc in manifest.documents if doc.split == split])


def _counter_text(counter: Counter[str]) -> str:
    if not counter:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(counter.items()))


def _latency_table_row(label: str, *, duration_ms: float | None, summary: LatencySummary | None) -> str:
    if summary is None:
        return f"| {label} | {_format_seconds(duration_ms)} | 0 | n/a | n/a | n/a | n/a |"
    return (
        f"| {label} | {_format_seconds(duration_ms)} | {summary.count} | "
        f"{_format_optional_latency(summary.mean_ms)} ms | "
        f"{_format_optional_latency(summary.p50_ms)} ms | "
        f"{_format_optional_latency(summary.p95_ms)} ms | "
        f"{_format_optional_latency(summary.max_ms)} ms |"
    )


def _metric_group_table(groups: Mapping[str, Sequence[MetricAtK]]) -> list[str]:
    lines = ["| Group | k | Recall | MRR | nDCG |", "|---|---:|---:|---:|---:|"]
    for group, metrics in groups.items():
        for metric in metrics:
            lines.append(f"| `{group}` | {metric.k} | {metric.recall:.3f} | {metric.mrr:.3f} | {metric.ndcg:.3f} |")
    return lines


def _metric_value(metrics: Sequence[MetricAtK], *, k: int, metric: Literal["recall", "mrr", "ndcg"]) -> float | None:
    for item in metrics:
        if item.k == k:
            return float(getattr(item, metric))
    return None


def _weakest_metric_groups(
    groups: Mapping[str, Sequence[MetricAtK]],
    *,
    metric: Literal["recall", "mrr", "ndcg"],
    k: int,
    limit: int,
) -> list[tuple[str, float]]:
    values = [
        (group, value)
        for group, metrics in groups.items()
        if (value := _metric_value(metrics, k=k, metric=metric)) is not None
    ]
    return sorted(values, key=lambda item: (item[1], item[0]))[:limit]


def _format_optional_metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _format_optional_latency(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _format_seconds(value_ms: float | None) -> str:
    return "n/a" if value_ms is None else f"{value_ms / 1000:.1f}s"


def _render_demo_result(
    result: QueryEvalResult,
    *,
    docs_by_id: Mapping[str, SubsetDoc],
    query: SubsetQuery | None,
    answer_eval: AnswerEvalResult | None,
) -> str:
    rank = result.expected_rank if result.expected_rank is not None else "miss"
    expected_doc = docs_by_id.get(result.expected_doc_id)
    expected_doc_rows = [
        _render_doc_table_row(rank="expected", doc_id=result.expected_doc_id, doc=expected_doc, score=None)
    ]
    hit_lines = [_render_hit_table_row(hit=hit, docs_by_id=docs_by_id) for hit in result.hits[:5]]
    reference_answer = (
        query.reference_answer
        if query is not None
        else answer_eval.reference_answer
        if answer_eval is not None
        else None
    )
    lines = [
        f"### {result.query_id}",
        "",
        f"- Rank: `{rank}`",
        f"- Category: `{result.category}`",
        f"- Type/source: `{result.query_type}` / `{result.query_source}`",
        f"- Search latency: `{_format_optional_latency(result.latency_ms)} ms`",
        "",
        "**Query**",
        "",
        result.query,
        "",
        "**Expected Document**",
        "",
        "| Role | Doc ID | Title | Description | Score |",
        "|---|---|---|---|---:|",
        "\n".join(expected_doc_rows),
        "",
        "**Expected Answer**",
        "",
        reference_answer or "(none)",
        "",
        "**Retrieved Documents**",
        "",
        "| Rank | Doc ID | Title | Description | Score |",
        "|---:|---|---|---|---:|",
        "\n".join(hit_lines) if hit_lines else "| - | - | - | - | - |",
    ]
    if answer_eval is not None:
        used_hit_lines = [_render_hit_table_row(hit=hit, docs_by_id=docs_by_id) for hit in answer_eval.used_hits[:5]]
        lines.extend(
            [
                "",
                "**Used Documents**",
                "",
                "| Rank | Doc ID | Title | Description | Score |",
                "|---:|---|---|---|---:|",
                "\n".join(used_hit_lines) if used_hit_lines else "| - | - | - | - | - |",
                "",
                "**Actual Answer**",
                "",
                answer_eval.generated_answer,
                "",
                f"Verdict: `{answer_eval.verdict}`; expected doc used: `{str(answer_eval.retrieved_expected_doc).lower()}`; reference coverage: `{answer_eval.reference_coverage:.3f}`.",
                f"QA latency: `{_format_optional_latency(answer_eval.latency_ms)} ms`.",
            ]
        )
    else:
        lines.extend(["", "**Actual Answer**", "", "(not generated for this retrieval-only sample)"])
    return "\n".join(lines)


def _render_hit_table_row(*, hit: SearchHitRecord, docs_by_id: Mapping[str, SubsetDoc]) -> str:
    doc = docs_by_id.get(hit.doc_id or "")
    return _render_doc_table_row(
        rank=str(hit.rank), doc_id=hit.doc_id or "unmapped", doc=doc, score=hit.score, fallback_title=hit.source_title
    )


def _render_doc_table_row(
    *,
    rank: str,
    doc_id: str,
    doc: SubsetDoc | None,
    score: float | None,
    fallback_title: str | None = None,
) -> str:
    title = _one_line(doc.title if doc is not None else fallback_title or doc_id)
    score_text = "" if score is None else f"{score:.4f}"
    return (
        f"| {_markdown_cell(rank)} | `{_markdown_cell(doc_id)}` | {_markdown_cell(title)} | "
        f"{_markdown_cell(_doc_description(doc))} | {_markdown_cell(score_text)} |"
    )


def _doc_description(doc: SubsetDoc | None) -> str:
    if doc is None:
        return "No manifest metadata available."
    if doc.abstract:
        abstract = _one_line(doc.abstract)
        if len(abstract) > 280:
            return f"{abstract[:277]}..."
        return abstract
    categories = ", ".join(doc.categories) or "unknown"
    return f"{doc.assigned_category}; {doc.split}; {doc.query_count} associated eval queries; categories: {categories}."


def _one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _markdown_cell(value: str) -> str:
    return _one_line(value).replace("|", "\\|")


def _render_answer_eval_result(result: AnswerEvalResult) -> str:
    return "\n".join(
        [
            f"### {result.query_id}",
            "",
            f"- Verdict: `{result.verdict}`",
            f"- Expected doc retrieved: `{str(result.retrieved_expected_doc).lower()}`",
            f"- Expected doc rank from retrieval run: `{result.expected_doc_rank if result.expected_doc_rank is not None else 'miss'}`",
            f"- Reference coverage: `{result.reference_coverage:.3f}`",
            f"- QA latency: `{_format_optional_latency(result.latency_ms)} ms`",
            f"- Covered reference terms: `{', '.join(result.covered_reference_terms) or 'none'}`",
            "",
            "**Question**",
            "",
            result.query,
            "",
            "**Reference Answer**",
            "",
            result.reference_answer or "(none)",
            "",
            "**Generated Answer**",
            "",
            result.generated_answer,
        ]
    )


def _select_answer_sample(results: Sequence[QueryEvalResult], *, sample_size: int) -> list[QueryEvalResult]:
    if sample_size <= 0:
        return []
    grouped: dict[tuple[str, str, str], list[QueryEvalResult]] = defaultdict(list)
    for result in sorted(results, key=lambda item: (item.category, item.query_type, item.query_source, item.query_id)):
        grouped[(result.category, result.query_type, result.query_source)].append(result)
    selected: list[QueryEvalResult] = []
    while len(selected) < sample_size:
        made_progress = False
        for key in sorted(grouped):
            candidates = grouped[key]
            if not candidates:
                continue
            selected.append(candidates.pop(0))
            made_progress = True
            if len(selected) >= sample_size:
                break
        if not made_progress:
            break
    return selected


def _reference_coverage(*, answer: str, reference: str | None) -> tuple[float, list[str]]:
    reference_terms = sorted(set(_content_terms(reference or "")))
    if not reference_terms:
        return 0.0, []
    answer_terms = set(_content_terms(answer))
    covered = [term for term in reference_terms if term in answer_terms]
    return len(covered) / len(reference_terms), covered


def _content_terms(value: str) -> list[str]:
    stopwords = {
        "about",
        "above",
        "after",
        "also",
        "because",
        "been",
        "being",
        "between",
        "does",
        "from",
        "have",
        "into",
        "that",
        "their",
        "these",
        "this",
        "through",
        "using",
        "what",
        "when",
        "where",
        "which",
        "while",
        "with",
    }
    return [term for term in re.findall(r"[a-z0-9][a-z0-9-]{2,}", value.casefold()) if term not in stopwords]


async def _upload_from_cli(
    *,
    base_url: str,
    auth_token: str,
    library_id: str | None,
    manifest: SubsetManifest,
    pdf_dir: Path,
    tag_id: str,
    timeout_seconds: float,
    existing_uploads: UploadManifest | None,
) -> UploadManifest:
    async with httpx.AsyncClient(base_url=base_url, timeout=None, follow_redirects=True) as client:
        return await upload_subset_to_app(
            api=EvalAppClient(client=client, auth_token=auth_token, library_id=library_id),
            manifest=manifest,
            pdf_dir=pdf_dir,
            tag_id=tag_id,
            timeout_seconds=timeout_seconds,
            existing_uploads=existing_uploads,
        )


async def _setup_upload_from_cli(
    *,
    base_url: str,
    auth_token: str,
    library_id: str | None,
    create_public_library: bool,
    public_library_title: str,
    public_library_slug: str,
    run_id: str,
    output_root: Path,
    tag_id: str,
    categories: Sequence[str],
    positive_docs: int,
    negative_docs: int,
    grow_by: int,
    seed: int,
    max_workers: int,
    task_timeout_seconds: float,
) -> UploadManifest:
    async with httpx.AsyncClient(base_url=base_url, timeout=None, follow_redirects=True) as client:
        api = EvalAppClient(client=client, auth_token=auth_token, library_id=library_id)
        if create_public_library:
            await api.create_library(
                title=public_library_title,
                description="Public read-only Open RAGBench arXiv PDF set for demos, testing, and retrieval evaluation.",
                slug=public_library_slug,
            )
        return await setup_and_upload_progressively(
            api=api,
            run_id=run_id,
            output_root=output_root,
            tag_id=tag_id,
            categories=categories,
            positive_docs=positive_docs,
            negative_docs=negative_docs,
            grow_by=grow_by,
            seed=seed,
            max_workers=max_workers,
            task_timeout_seconds=task_timeout_seconds,
        )


async def _run_eval_from_cli(
    *,
    base_url: str,
    auth_token: str,
    library_id: str | None,
    manifest: SubsetManifest,
    uploads: UploadManifest,
    max_results: int,
    max_queries: int | None,
    retrieval_concurrency: int,
    answer_sample_size: int,
    answer_max_results: int,
) -> EvalReport:
    async with httpx.AsyncClient(base_url=base_url, timeout=None, follow_redirects=True) as client:
        api = EvalAppClient(client=client, auth_token=auth_token, library_id=library_id)
        report = await run_retrieval_eval(
            api=api,
            manifest=manifest,
            uploads=uploads,
            max_results=max_results,
            max_queries=max_queries,
            concurrency=retrieval_concurrency,
        )
        answer_started_at = monotonic()
        answer_evaluations = await run_answer_eval(
            api=api,
            manifest=manifest,
            uploads=uploads,
            retrieval_report=report,
            sample_size=answer_sample_size,
            max_results=answer_max_results,
        )
        answer_eval_duration_ms = (monotonic() - answer_started_at) * 1000
        return report.model_copy(
            update={
                "answer_evaluations": answer_evaluations,
                "answer_eval_duration_ms": answer_eval_duration_ms,
                "answer_latency": _latency_summary([item.latency_ms for item in answer_evaluations]),
            }
        )


def _enforce_threshold(*, report: EvalReport, min_recall_at_10: float | None) -> None:
    if min_recall_at_10 is None:
        return
    recall_at_10 = next((metric.recall for metric in report.metrics if metric.k == 10), None)
    if recall_at_10 is None or recall_at_10 < min_recall_at_10:
        raise SystemExit(f"Recall@10 {recall_at_10 or 0.0:.3f} is below required {min_recall_at_10:.3f}.")


def _configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _log_report_written(*, manifest: SubsetManifest, output_dir: Path) -> None:
    logger.info(
        "open_ragbench_report_written run_id=%s summary=%s detailed_metrics=%s query_review=%s results=%s",
        manifest.run_id,
        output_dir / "summary.md",
        output_dir / "detailed_metrics.md",
        output_dir / "demo_queries.md",
        output_dir / "results.json",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and run the Open RAGBench PDF retrieval eval.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Create or reuse a subset and download missing PDFs.")
    setup.add_argument("--run-id", default=None)
    setup.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    setup.add_argument("--positive-docs", type=int, default=40)
    setup.add_argument("--negative-docs", type=int, default=60)
    setup.add_argument("--seed", type=int, default=DEFAULT_SEED)
    setup.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    setup.add_argument("--max-workers", type=int, default=8)

    setup_upload = subparsers.add_parser(
        "setup-upload",
        help="Create or reuse a subset, download each missing PDF, and upload it immediately.",
    )
    setup_upload.add_argument("--run-id", default=None)
    setup_upload.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    setup_upload.add_argument("--base-url", default="http://localhost:8000")
    setup_upload.add_argument("--auth-token", default=DEFAULT_AUTH_TOKEN)
    setup_upload.add_argument("--tag-id", default=None)
    setup_upload.add_argument("--library-id", default=None)
    setup_upload.add_argument("--create-public-library", action="store_true")
    setup_upload.add_argument("--public-library-title", default="Open RAGBench arXiv demo library")
    setup_upload.add_argument("--public-library-slug", default="open-ragbench-arxiv")
    setup_upload.add_argument("--grow-by", type=int, default=20)
    setup_upload.add_argument("--positive-docs", type=int, default=40)
    setup_upload.add_argument("--negative-docs", type=int, default=60)
    setup_upload.add_argument("--seed", type=int, default=DEFAULT_SEED)
    setup_upload.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    setup_upload.add_argument("--max-workers", type=int, default=8)
    setup_upload.add_argument("--task-timeout", type=float, default=900.0)

    build = subparsers.add_parser("build-subset", help="Select 100 documents and download their PDFs.")
    build.add_argument("--run-id", default=None)
    build.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    build.add_argument("--positive-docs", type=int, default=40)
    build.add_argument("--negative-docs", type=int, default=60)
    build.add_argument("--seed", type=int, default=DEFAULT_SEED)
    build.add_argument("--categories", nargs="+", default=list(DEFAULT_CATEGORIES))
    build.add_argument("--max-workers", type=int, default=8)

    upload = subparsers.add_parser("upload", help="Upload the selected PDFs to the running app.")
    upload.add_argument("run_dir")
    upload.add_argument("--base-url", default="http://localhost:8000")
    upload.add_argument("--auth-token", default=DEFAULT_AUTH_TOKEN)
    upload.add_argument("--library-id", default=None)
    upload.add_argument("--tag-id", default=None)
    upload.add_argument("--task-timeout", type=float, default=900.0)

    report = subparsers.add_parser(
        "report", help="Regenerate summary.md and demo_queries.md from existing JSON results."
    )
    report.add_argument("run_dir")

    run = subparsers.add_parser("run", help="Run retrieval scoring through the app API.")
    run.add_argument("run_dir")
    run.add_argument("--base-url", default="http://localhost:8000")
    run.add_argument("--auth-token", default=DEFAULT_AUTH_TOKEN)
    run.add_argument("--library-id", default=None)
    run.add_argument("--max-results", type=int, default=10)
    run.add_argument("--max-queries", type=int, default=None)
    run.add_argument("--retrieval-concurrency", type=int, default=50)
    run.add_argument("--answer-sample-size", type=int, default=5)
    run.add_argument("--answer-max-results", type=int, default=8)
    run.add_argument("--min-recall-at-10", type=float, default=None)
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import httpx
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject
import pytest

from backend import create_fastapi_app
from backend.app.core.config import AppSettings
from backend.app.evals.open_ragbench import (
    AnswerEvalResult,
    CorpusDoc,
    EvalReport,
    EvalAppClient,
    MetricAtK,
    OpenRagbenchDataset,
    OpenRagbenchQrel,
    OpenRagbenchQuery,
    QueryEvalResult,
    SearchHitRecord,
    SubsetDoc,
    SubsetManifest,
    SubsetQuery,
    UploadManifest,
    UploadRecord,
    aggregate_results,
    build_eval_report,
    render_demo_queries_markdown,
    render_summary_markdown,
    run_answer_eval,
    run_retrieval_eval,
    select_subset,
    grow_subset,
    upload_subset_to_app,
)


def test_open_ragbench_subset_is_category_restricted_and_deterministic() -> None:
    dataset = _dataset_fixture()

    first = select_subset(
        dataset=dataset,
        run_id="eval-test",
        categories=["cs.LG", "cs.AI", "cs.CV"],
        positive_doc_target=4,
        negative_doc_target=6,
        seed=123,
    )
    second = select_subset(
        dataset=dataset,
        run_id="eval-test",
        categories=["cs.LG", "cs.AI", "cs.CV"],
        positive_doc_target=4,
        negative_doc_target=6,
        seed=123,
    )

    assert first.model_dump(exclude={"created_at"}) == second.model_dump(exclude={"created_at"})
    assert len(first.documents) == 10
    assert len([doc for doc in first.documents if doc.split == "positive"]) == 4
    assert len([doc for doc in first.documents if doc.split == "negative"]) == 6
    assert {doc.assigned_category for doc in first.documents} <= {"cs.LG", "cs.AI", "cs.CV"}
    assert {query.expected_doc_id for query in first.queries} <= {
        doc.doc_id for doc in first.documents if doc.split == "positive"
    }
    assert all(query.reference_answer is not None for query in first.queries)


def test_open_ragbench_metrics_use_single_relevant_doc_rank() -> None:
    results = [
        _result("q1", rank=1),
        _result("q2", rank=3),
        _result("q3", rank=None),
    ]

    metrics = aggregate_results(results, ks=[1, 3])

    assert metrics[0].k == 1
    assert metrics[0].recall == pytest.approx(1 / 3)
    assert metrics[0].mrr == pytest.approx(1 / 3)
    assert metrics[0].ndcg == pytest.approx(1 / 3)
    assert metrics[1].k == 3
    assert metrics[1].recall == pytest.approx(2 / 3)
    assert metrics[1].mrr == pytest.approx((1 + 1 / 3) / 3)


def test_open_ragbench_growth_preserves_existing_docs_and_adds_twenty() -> None:
    dataset = _dataset_fixture()
    manifest = select_subset(
        dataset=dataset,
        run_id="growth-test",
        categories=["cs.LG", "cs.AI", "cs.CV"],
        positive_doc_target=3,
        negative_doc_target=3,
        seed=11,
    )

    grown = grow_subset(dataset=dataset, manifest=manifest, additional_docs=6, seed=22)

    original_doc_ids = {doc.doc_id for doc in manifest.documents}
    assert len(grown.documents) == len(manifest.documents) + 6
    assert original_doc_ids <= {doc.doc_id for doc in grown.documents}
    assert len({doc.doc_id for doc in grown.documents}) == len(grown.documents)
    assert grown.positive_doc_target == len([doc for doc in grown.documents if doc.split == "positive"])
    assert grown.negative_doc_target == len([doc for doc in grown.documents if doc.split == "negative"])
    assert {query.expected_doc_id for query in grown.queries} <= {
        doc.doc_id for doc in grown.documents if doc.split == "positive"
    }


def test_open_ragbench_query_review_includes_question_docs_and_answers() -> None:
    manifest = SubsetManifest(
        run_id="review-test",
        categories=["cs.LG"],
        positive_doc_target=1,
        negative_doc_target=0,
        documents=[
            SubsetDoc(
                doc_id="2401.00001v1",
                title="Expected Paper",
                abstract="Expected paper abstract.",
                categories=["cs.LG"],
                assigned_category="cs.LG",
                split="positive",
                pdf_url="https://example.test/expected.pdf",
                query_count=1,
            ),
            SubsetDoc(
                doc_id="2401.00002v1",
                title="Retrieved Paper",
                abstract="Retrieved paper abstract.",
                categories=["cs.AI"],
                assigned_category="cs.AI",
                split="negative",
                pdf_url="https://example.test/retrieved.pdf",
                query_count=0,
            ),
        ],
        queries=[
            _subset_query(
                query_id="q-review",
                query="What does the expected paper prove?",
                expected_doc_id="2401.00001v1",
                reference_answer="It proves the expected result.",
            )
        ],
    )
    result = QueryEvalResult(
        query_id="q-review",
        query="What does the expected paper prove?",
        expected_doc_id="2401.00001v1",
        category="cs.LG",
        query_type="extractive",
        query_source="text",
        expected_rank=2,
        latency_ms=123.4,
        hits=[
            SearchHitRecord(
                source_id="source-2", doc_id="2401.00002v1", source_title="Retrieved Paper", score=0.8, rank=1
            ),
            SearchHitRecord(
                source_id="source-1", doc_id="2401.00001v1", source_title="Expected Paper", score=0.7, rank=2
            ),
        ],
    )
    report = _report_fixture(
        run_id="review-test",
        result=result,
        answer=AnswerEvalResult(
            query_id="q-review",
            query=result.query,
            expected_doc_id=result.expected_doc_id,
            reference_answer="It proves the expected result.",
            generated_answer="The expected paper proves the expected result.",
            expected_doc_rank=2,
            retrieved_expected_doc=True,
            used_hits=result.hits,
            reference_coverage=1.0,
            covered_reference_terms=["proves", "expected", "result"],
            verdict="pass",
            latency_ms=456.7,
        ),
    )

    markdown = render_demo_queries_markdown(manifest=manifest, uploads=_uploads_fixture(), report=report, limit_each=1)

    assert "What does the expected paper prove?" in markdown
    assert "Expected Paper" in markdown
    assert "Expected paper abstract." in markdown
    assert "Retrieved Paper" in markdown
    assert "Retrieved paper abstract." in markdown
    assert "It proves the expected result." in markdown
    assert "The expected paper proves the expected result." in markdown
    assert "Search latency: `123.4 ms`" in markdown
    assert "QA latency: `456.7 ms`" in markdown


def test_open_ragbench_summary_includes_latency_and_answer_details_in_order() -> None:
    manifest = _summary_manifest_fixture()
    result = QueryEvalResult(
        query_id="q-summary",
        query="What does the summary paper prove?",
        expected_doc_id="2401.00001v1",
        category="cs.LG",
        query_type="extractive",
        query_source="text",
        expected_rank=1,
        latency_ms=125.0,
        hits=[
            SearchHitRecord(
                source_id="source-1", doc_id="2401.00001v1", source_title="Summary Paper", score=0.9, rank=1
            )
        ],
    )
    answer = AnswerEvalResult(
        query_id="q-summary",
        query=result.query,
        expected_doc_id=result.expected_doc_id,
        reference_answer="It proves the summary result.",
        generated_answer="The system says the summary result is proven.",
        expected_doc_rank=1,
        retrieved_expected_doc=True,
        used_hits=result.hits,
        reference_coverage=0.75,
        covered_reference_terms=["summary", "result"],
        verdict="pass",
        latency_ms=875.0,
    )
    report = build_eval_report(
        run_id="summary-test",
        dataset_id=manifest.dataset_id,
        dataset_revision=manifest.dataset_revision,
        tag_id="open-ragbench-eval-summary-test",
        max_results=10,
        results=[result],
        answer_evaluations=[answer],
        retrieval_duration_ms=250.0,
        answer_eval_duration_ms=900.0,
    )

    markdown = render_summary_markdown(manifest=manifest, uploads=_uploads_fixture(), report=report)

    assert "## Latency" in markdown
    assert "Retrieval search requests" in markdown
    assert "125.0 ms" in markdown
    assert "## Baseline Context" in markdown
    assert "## Five-Question Answer Evaluation" in markdown
    assert (
        "Detailed document traces and retrieved/used sources: [query review](demo_queries.md#five-answer-checks)."
        in markdown
    )
    assert "What does the summary paper prove?" in markdown
    assert "It proves the summary result." in markdown
    assert "The system says the summary result is proven." in markdown
    assert markdown.index("## Baseline Context") < markdown.index("## Five-Question Answer Evaluation")
    assert markdown.index("## Five-Question Answer Evaluation") < markdown.index("## Metric Definitions")


@pytest.mark.asyncio
async def test_open_ragbench_upload_reuses_completed_upload_records(tmp_path: Path) -> None:
    dataset = _dataset_fixture()
    manifest = select_subset(
        dataset=dataset,
        run_id="resume-test",
        categories=["cs.LG"],
        positive_doc_target=1,
        negative_doc_target=0,
        seed=7,
    )
    doc = manifest.documents[0]
    existing = UploadManifest(
        run_id="resume-test",
        tag_id="open-ragbench-eval-resume-test",
        root_folder_id="folder-root",
        uploads=[
            UploadRecord(
                doc_id=doc.doc_id,
                source_id="source-existing",
                task_id="task-existing",
                status="completed",
                filename=f"{doc.doc_id}.pdf",
                virtual_name=f"{doc.doc_id}.pdf",
                category=doc.assigned_category,
                folder_id="folder-category",
            )
        ],
    )
    fake_api = _CountingEvalApi()

    uploads = await upload_subset_to_app(
        api=cast(EvalAppClient, fake_api),
        manifest=manifest,
        pdf_dir=tmp_path,
        tag_id=existing.tag_id,
        existing_uploads=existing,
    )

    assert fake_api.upload_count == 0
    assert fake_api.created_folders == []
    assert uploads.uploads[0].source_id == "source-existing"


@pytest.mark.asyncio
async def test_open_ragbench_api_runner_uploads_pdfs_and_scores_search(
    configured_settings: AppSettings,
    fake_openai: None,
    tmp_path: Path,
) -> None:
    del fake_openai
    dataset = OpenRagbenchDataset(
        queries={
            "q-alpha": OpenRagbenchQuery(query="alpha retrieval evidence", type="extractive", source="text"),
            "q-bravo": OpenRagbenchQuery(query="bravo retrieval evidence", type="abstractive", source="text-image"),
        },
        qrels={
            "q-alpha": OpenRagbenchQrel(doc_id="2401.00001v1", section_id=0),
            "q-bravo": OpenRagbenchQrel(doc_id="2401.00002v1", section_id=0),
        },
        answers={"q-alpha": "alpha answer", "q-bravo": "bravo answer"},
        pdf_urls={
            "2401.00001v1": "https://arxiv.org/pdf/2401.00001v1",
            "2401.00002v1": "https://arxiv.org/pdf/2401.00002v1",
            "2401.00003v1": "https://arxiv.org/pdf/2401.00003v1",
        },
        corpus_docs={
            "2401.00001v1": CorpusDoc(doc_id="2401.00001v1", title="Alpha Paper", categories=["cs.LG"]),
            "2401.00002v1": CorpusDoc(doc_id="2401.00002v1", title="Bravo Paper", categories=["cs.LG"]),
            "2401.00003v1": CorpusDoc(doc_id="2401.00003v1", title="Negative Paper", categories=["cs.LG"]),
        },
    )
    manifest = select_subset(
        dataset=dataset,
        run_id="api-test",
        categories=["cs.LG"],
        positive_doc_target=2,
        negative_doc_target=1,
        seed=5,
    )
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    for doc in manifest.documents:
        (pdf_dir / f"{doc.doc_id}.pdf").write_bytes(_pdf_with_pages([f"{doc.title} retrieval evidence"]))

    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
            api = EvalAppClient(client=http_client, auth_token="local-dev")
            uploads = await upload_subset_to_app(
                api=api,
                manifest=manifest,
                pdf_dir=pdf_dir,
                tag_id="open-ragbench-eval-api-test",
                timeout_seconds=5,
            )
            report = await run_retrieval_eval(
                api=api,
                manifest=manifest,
                uploads=uploads,
                max_results=3,
            )
            answer_evaluations = await run_answer_eval(
                api=api,
                manifest=manifest,
                uploads=uploads,
                retrieval_report=report,
                sample_size=2,
                max_results=3,
            )

    assert isinstance(uploads, UploadManifest)
    assert len(uploads.uploads) == 3
    assert all(upload.status == "completed" for upload in uploads.uploads)
    assert report.query_count == 2
    assert all(result.expected_rank is not None for result in report.results)
    assert report.metrics[-1].recall == 1.0
    assert len(answer_evaluations) == 2
    assert {item.verdict for item in answer_evaluations} <= {"pass", "review", "fail"}
    assert all(item.generated_answer for item in answer_evaluations)


def _dataset_fixture() -> OpenRagbenchDataset:
    corpus_docs: dict[str, CorpusDoc] = {}
    pdf_urls: dict[str, str] = {}
    queries: dict[str, OpenRagbenchQuery] = {}
    qrels: dict[str, OpenRagbenchQrel] = {}
    answers: dict[str, str] = {}
    categories = ["cs.LG", "cs.AI", "cs.CV"]
    for category_index, category in enumerate(categories):
        for index in range(1, 7):
            doc_id = f"2401.{category_index}{index:04d}v1"
            corpus_docs[doc_id] = CorpusDoc(doc_id=doc_id, title=f"{category} paper {index}", categories=[category])
            pdf_urls[doc_id] = f"https://arxiv.org/pdf/{doc_id}"
            if index <= 3:
                query_id = f"query-{doc_id}"
                queries[query_id] = OpenRagbenchQuery(
                    query=f"What does {doc_id} discuss?",
                    type="extractive",
                    source="text",
                )
                qrels[query_id] = OpenRagbenchQrel(doc_id=doc_id, section_id=0)
                answers[query_id] = f"Answer for {doc_id}"
    return OpenRagbenchDataset(
        queries=queries,
        qrels=qrels,
        answers=answers,
        pdf_urls=pdf_urls,
        corpus_docs=corpus_docs,
    )


def _subset_query(*, query_id: str, query: str, expected_doc_id: str, reference_answer: str | None) -> SubsetQuery:
    return SubsetQuery(
        query_id=query_id,
        query=query,
        query_type="extractive",
        query_source="text",
        expected_doc_id=expected_doc_id,
        section_id=0,
        reference_answer=reference_answer,
        category="cs.LG",
    )


def _uploads_fixture() -> UploadManifest:
    return UploadManifest(run_id="review-test", tag_id="open-ragbench-eval-review-test")


def _summary_manifest_fixture() -> SubsetManifest:
    return SubsetManifest(
        run_id="summary-test",
        categories=["cs.LG"],
        positive_doc_target=1,
        negative_doc_target=0,
        documents=[
            SubsetDoc(
                doc_id="2401.00001v1",
                title="Summary Paper",
                abstract="Summary paper abstract.",
                categories=["cs.LG"],
                assigned_category="cs.LG",
                split="positive",
                pdf_url="https://example.test/summary.pdf",
                query_count=1,
            )
        ],
        queries=[
            _subset_query(
                query_id="q-summary",
                query="What does the summary paper prove?",
                expected_doc_id="2401.00001v1",
                reference_answer="It proves the summary result.",
            )
        ],
    )


def _report_fixture(*, run_id: str, result: QueryEvalResult, answer: AnswerEvalResult) -> EvalReport:
    metrics = [MetricAtK(k=1, recall=0.0, mrr=0.0, ndcg=0.0), MetricAtK(k=10, recall=1.0, mrr=0.5, ndcg=0.631)]
    return EvalReport(
        run_id=run_id,
        tag_id="open-ragbench-eval-review-test",
        max_results=10,
        query_count=1,
        metrics=metrics,
        by_category={"cs.LG": metrics},
        by_query_type={"extractive": metrics},
        by_query_source={"text": metrics},
        results=[result],
        answer_evaluations=[answer],
    )


def _result(query_id: str, *, rank: int | None) -> QueryEvalResult:
    hits = [
        SearchHitRecord(
            source_id=f"source-{index}", doc_id=f"doc-{index}", source_title=f"Doc {index}", score=1.0, rank=index
        )
        for index in range(1, 4)
    ]
    return QueryEvalResult(
        query_id=query_id,
        query="What happened?",
        expected_doc_id="doc-1",
        category="cs.LG",
        query_type="extractive",
        query_source="text",
        expected_rank=rank,
        hits=hits,
    )


class _CountingEvalApi:
    def __init__(self) -> None:
        self.created_folders: list[str] = []
        self.upload_count = 0

    async def create_folder(self, *, name: str, parent_id: str | None = None) -> str:
        del parent_id
        self.created_folders.append(name)
        return f"folder-{len(self.created_folders)}"

    async def upload_pdf(
        self,
        *,
        path: Path,
        tag_id: str,
        folder_id: str,
        virtual_name: str,
        user_guidance: str,
    ) -> tuple[str, str | None]:
        del path, tag_id, folder_id, virtual_name, user_guidance
        self.upload_count += 1
        return f"source-{self.upload_count}", None

    async def wait_for_task(self, *, task_id: str, timeout_seconds: float) -> str:
        del task_id, timeout_seconds
        return "completed"

    async def search(self, *, query: str, tag_id: str, max_results: int) -> list[dict[str, object]]:
        del query, tag_id, max_results
        return []


def _pdf_with_pages(page_texts: list[str]) -> bytes:
    writer = PdfWriter()
    add_object = cast(Callable[[object], Any], getattr(writer, "_add_object"))
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = add_object(font)
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = StreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()

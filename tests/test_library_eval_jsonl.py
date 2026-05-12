from __future__ import annotations

from pathlib import Path

import pytest

from openai_vectorstore2.evals.jsonl import JsonlEvalResult, build_jsonl_eval_report, validate_jsonl_dataset


def test_validate_jsonl_dataset_accepts_source_or_doc_expectations(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        "\n".join(
            [
                '{"id":"q1","query":"alpha","expected_source_id":"source-1"}',
                '{"id":"q2","query":"beta","expected_doc_id":"doc-2","tag_ids":["eval"]}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    records = validate_jsonl_dataset(dataset)

    assert [record.id for record in records] == ["q1", "q2"]
    assert records[0].expected_source_id == "source-1"
    assert records[1].expected_doc_id == "doc-2"
    assert records[1].tag_ids == ["eval"]


def test_validate_jsonl_dataset_reports_all_line_errors(tmp_path: Path) -> None:
    dataset = tmp_path / "bad.jsonl"
    dataset.write_text(
        "\n".join(
            [
                '{"id":"missing-expectation","query":"alpha"}',
                '{"id":"bad-json",',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        validate_jsonl_dataset(dataset)

    message = str(exc_info.value)
    assert "line 1: expected_source_id or expected_doc_id is required" in message
    assert "line 2: invalid JSON" in message


def test_jsonl_eval_report_calculates_recall_and_latency(tmp_path: Path) -> None:
    report = build_jsonl_eval_report(
        dataset_path=tmp_path / "dataset.jsonl",
        max_results=3,
        results=[
            JsonlEvalResult(id="q1", query="alpha", expected_rank=1, latency_ms=100.0),
            JsonlEvalResult(id="q2", query="beta", expected_rank=3, latency_ms=200.0),
            JsonlEvalResult(id="q3", query="gamma", expected_rank=None, latency_ms=300.0),
        ],
    )

    assert report.query_count == 3
    assert report.recall_at_1 == pytest.approx(1 / 3)
    assert report.recall_at_k == pytest.approx(2 / 3)
    assert report.mean_latency_ms == pytest.approx(200.0)

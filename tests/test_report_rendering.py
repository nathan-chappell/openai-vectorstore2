from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.schemas import (
    ReportCitation,
    ReportDocument,
    ReportFigureBlock,
    ReportListBlock,
    ReportMathBlock,
    ReportParagraphBlock,
    ReportSection,
    ReportTableBlock,
)
from backend.app.services.reports import render_report_markdown


def test_render_report_markdown_preserves_math_tables_and_citations() -> None:
    report = ReportDocument(
        title="Retrieval Quality Report",
        subtitle="April evaluation pass",
        abstract="This report summarizes retrieval quality.",
        sections=[
            ReportSection(
                title="Findings",
                blocks=[
                    ReportParagraphBlock(
                        text="Hybrid retrieval improved grounded answers.",
                        citations=[ReportCitation(label="S1", source_id="source_alpha", note="Alpha source")],
                    ),
                    ReportListBlock(ordered=True, items=["Higher recall", "Lower citation drift"]),
                    ReportMathBlock(expression="F_1 = 2 \\cdot \\frac{P \\cdot R}{P + R}", label="f1"),
                    ReportTableBlock(
                        caption="Scores by run",
                        headers=["Run", "Precision | Recall"],
                        rows=[["baseline", "0.72 | 0.66"], ["hybrid", "0.81 | 0.74"]],
                    ),
                    ReportFigureBlock(
                        alt_text="Precision [chart]",
                        uri="library://assets/figure-precision.png",
                        caption="Precision by run.",
                    ),
                ],
            )
        ],
        citations=[ReportCitation(label="S1", source_id="source_alpha", note="Alpha source")],
    )

    markdown = render_report_markdown(report)

    assert markdown.startswith("# Retrieval Quality Report\n\nApril evaluation pass")
    assert "[S1](chatkit-link://source?source_id=source_alpha)" in markdown
    assert "$$\nF_1 = 2 \\cdot \\frac{P \\cdot R}{P + R}\n$$" in markdown
    assert "<!-- math-label: f1 -->" in markdown
    assert "| Run | Precision \\| Recall |" in markdown
    assert "| baseline | 0.72 \\| 0.66 |" in markdown
    assert "![Precision \\[chart\\]](library://assets/figure-precision.png)" in markdown
    assert markdown.endswith("- [S1](chatkit-link://source?source_id=source_alpha): Alpha source\n")


def test_report_table_rejects_rows_with_wrong_width() -> None:
    with pytest.raises(ValidationError, match="rows must match"):
        ReportTableBlock(headers=["A", "B"], rows=[["only one cell"]])

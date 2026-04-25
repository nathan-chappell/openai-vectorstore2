from __future__ import annotations

from backend.app.services.sources import build_pdf_text_batches


def test_build_pdf_text_batches_preserves_page_markers_and_ranges() -> None:
    extracted_text = "\n\n".join(
        [
            "[page 1]\nAlpha",
            "[page 2]\nBravo",
            "[page 3]\nCharlie",
            "[page 4]\nDelta",
            "[page 5]\nEcho",
        ]
    )

    batches = build_pdf_text_batches(extracted_text, pages_per_batch=2)

    assert [(batch.start_page, batch.end_page, batch.label) for batch in batches] == [
        (1, 2, "pages 1-2"),
        (3, 4, "pages 3-4"),
        (5, 5, "page 5"),
    ]
    assert batches[0].text == "[page 1]\nAlpha\n\n[page 2]\nBravo"
    assert batches[2].text == "[page 5]\nEcho"


def test_build_pdf_text_batches_falls_back_to_single_text_batch_without_markers() -> None:
    batches = build_pdf_text_batches("Loose extracted text", pages_per_batch=2)

    assert len(batches) == 1
    assert batches[0].start_page is None
    assert batches[0].end_page is None
    assert batches[0].label == "PDF text"
    assert batches[0].text == "Loose extracted text"

from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
from typing import Any, cast

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from backend.app.services.sources import build_pdf_text_batches, extract_pdf_text, split_pdf_payload_by_size


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


def test_extract_pdf_text_marks_pages_from_fixture_pdf() -> None:
    extracted_text = extract_pdf_text(
        filename="fixture.pdf", payload=_pdf_with_pages(["Alpha page one", "Bravo page two"])
    )

    assert "[page 1]\nAlpha page one" in extracted_text
    assert "[page 2]\nBravo page two" in extracted_text
    batches = build_pdf_text_batches(extracted_text, pages_per_batch=1)
    assert [(batch.start_page, batch.end_page, batch.label) for batch in batches] == [
        (1, 1, "page 1"),
        (2, 2, "page 2"),
    ]


def test_split_pdf_payload_by_size_preserves_page_ranges_and_limits() -> None:
    page_texts = [
        "Alpha " * 200,
        "Bravo " * 200,
        "Charlie " * 200,
    ]
    payload = _pdf_with_pages(page_texts)
    single_page_limit = max(len(_pdf_with_pages([text])) for text in page_texts) + 256

    parts = split_pdf_payload_by_size(filename="large.pdf", payload=payload, max_part_bytes=single_page_limit)

    assert len(parts) == 3
    assert [(part.filename, part.start_page, part.end_page) for part in parts] == [
        ("large.part-001.pdf", 1, 1),
        ("large.part-002.pdf", 2, 2),
        ("large.part-003.pdf", 3, 3),
    ]
    assert all(len(part.payload) <= single_page_limit for part in parts)
    assert "Alpha" in extract_pdf_text(filename=parts[0].filename, payload=parts[0].payload)
    assert "Charlie" in extract_pdf_text(filename=parts[2].filename, payload=parts[2].payload)


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

# pyright: reportPrivateUsage=false

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest

from backend import create_fastapi_app
from backend.app.core.config import AppSettings
from backend.app.models import ResearchImportCandidate, SourceFile
from backend.app.schemas import ResearchImportCreateRequest, ResearchLibraryBuildRequest
from backend.app.services import research as research_module
from backend.app.services.research import ResearchImportService


def test_research_import_url_normalization_removes_tracking_and_fragments() -> None:
    normalized = research_module._normalize_url(
        "HTTPS://Example.COM:443/Paper/?utm_source=newsletter&b=2&a=1&fbclid=ignored#section"
    )

    assert normalized == "https://example.com/Paper?a=1&b=2"
    assert research_module._normalize_url("http://example.com:80/") == "http://example.com/"
    assert research_module._normalize_url("https://example.com:444/path") == "https://example.com:444/path"
    assert research_module._normalize_url("ftp://example.com/paper") is None
    assert research_module._normalize_url("not a url") is None


def test_research_import_arxiv_and_pdf_detection() -> None:
    assert research_module._arxiv_pdf_url("https://arxiv.org/abs/2401.01234v2") == "https://arxiv.org/pdf/2401.01234v2.pdf"
    assert research_module._arxiv_pdf_url("https://arxiv.org/pdf/2401.01234") == "https://arxiv.org/pdf/2401.01234.pdf"
    assert research_module._source_type_from_url("https://arxiv.org/abs/2401.01234", default="url") == "arxiv"
    assert research_module._source_type_from_url("https://example.com/report.PDF?download=1", default="url") == "pdf"
    assert research_module._source_type_from_url("https://example.com/article", default="url") == "url"


def test_research_import_download_filenames_keep_supported_extensions_and_titles() -> None:
    assert (
        research_module._filename_from_url(
            "https://arxiv.org/abs/1809.04281",
            title="Universal Language Model Fine-tuning for Text Classification",
            extension=".txt",
        )
        == "Universal Language Model Fine-tuning for Text Classification.txt"
    )
    assert (
        research_module._filename_from_url(
            "https://arxiv.org/pdf/1706.03762.pdf",
            title="Attention Is All You Need",
            extension=".pdf",
        )
        == "Attention Is All You Need.pdf"
    )
    assert research_module._filename_from_url("https://example.com/reports/model.PDF?download=1", title=None, extension=".pdf") == "model.pdf"
    assert research_module._filename_from_url("https://example.com/articles/alignment.html", title="Alignment", extension=".txt") == "alignment.txt"


def test_research_import_html_cleanup_removes_boilerplate_and_preserves_public_links() -> None:
    cleaned = research_module._html_to_text(
        """
        <html>
          <head><style>.hidden { display: none; }</style><script>bad()</script></head>
          <body>
            <article>
              <h1>Deep&nbsp;Research Note</h1>
              <p>Read <a href="https://Example.com/Paper?utm_source=x&amp;b=2">the paper</a> next.</p>
            </article>
          </body>
        </html>
        """
    )

    assert "bad()" not in cleaned
    assert ".hidden" not in cleaned
    assert "<article>" not in cleaned
    assert "Deep Research Note" in cleaned
    assert "the paper (https://Example.com/Paper?utm_source=x&b=2)" in cleaned


@pytest.mark.asyncio
async def test_research_import_linkedin_export_seed_is_cleaned(configured_settings: AppSettings) -> None:
    service = ResearchImportService(
        settings=configured_settings,
        database=cast(Any, None),
        sources=cast(Any, None),
        openai=cast(Any, None),
    )

    material = await service._material_from_seed(
        ResearchImportCreateRequest(
            seed_type="linkedin_export",
            filename="linkedin-export.html",
            text="""
            <article>
              <h1>Vector Memory Field Notes</h1>
              <p>Exported LinkedIn article with <a href='https://example.com/reference'>a cited reference</a>.</p>
            </article>
            """,
        )
    )

    text = material.payload.decode("utf-8")
    assert material.source_type == "linkedin_export"
    assert material.media_type == "text/plain"
    assert "Vector Memory Field Notes" in text
    assert "https://example.com/reference" in text
    assert "<article>" not in text


def test_research_candidate_summary_reflects_linked_source_status(configured_settings: AppSettings) -> None:
    service = ResearchImportService(
        settings=configured_settings,
        database=cast(Any, None),
        sources=cast(Any, None),
        openai=cast(Any, None),
    )
    now = datetime.now(UTC)
    source = SourceFile(
        id="source",
        library_id="library",
        uploaded_by_user_id=1,
        display_title="Attention note",
        original_filename="attention.txt",
        media_type="text/plain",
        source_kind="text",
        status="failed",
        byte_size=42,
        storage_provider="local",
        storage_key="sources/attention.txt",
        error_message="Ingest cancelled during shutdown.",
        created_at=now,
        updated_at=now,
    )
    candidate = ResearchImportCandidate(
        id="candidate",
        library_id="library",
        user_id=1,
        task_id="task",
        linked_source_file_id="source",
        status="ingested",
        source_type="url",
        title="Attention note",
        depth=0,
        provenance_json={},
        created_at=now,
        updated_at=now,
    )
    candidate.linked_source_file = source

    summary = service._candidate_summary(candidate)

    assert summary.status == "failed"
    assert summary.error_message == "Ingest cancelled during shutdown."


@pytest.mark.asyncio
async def test_research_import_dedupes_review_candidates_against_ingested_source_provenance(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            first = await client.post(
                "/api/research/imports",
                headers=auth_headers,
                json={
                    "seed_type": "text",
                    "title": "Duplicate seed",
                    "text": "A stable seed body about dedupe and library provenance.",
                    "ingest_seed": True,
                    "discover_references": False,
                },
            )
            assert first.status_code == 200
            first_payload = first.json()
            assert first_payload["seed_source"] is not None
            assert first_payload["candidates"] == []

            second = await client.post(
                "/api/research/imports",
                headers=auth_headers,
                json={
                    "seed_type": "text",
                    "title": "Duplicate seed",
                    "text": "A stable seed body about dedupe and library provenance.",
                    "ingest_seed": False,
                    "discover_references": False,
                },
            )
            assert second.status_code == 200
            payload = second.json()
            assert payload["duplicate_count"] == 1
            assert payload["candidates"] == []


@pytest.mark.asyncio
async def test_research_library_progress_reports_search_depth_and_slots(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        events: list[tuple[str, str]] = []

        async def record_progress(icon: str, text: str) -> None:
            events.append((icon, text))

        services = app.state.services
        response = await services.research.build_library(
            clerk_user_id="local-dev",
            payload=ResearchLibraryBuildRequest(
                seed_type="paper",
                query="Attention Is All You Need",
                auto_ingest=False,
                max_depth=2,
                max_sources=4,
                max_candidates_per_source=2,
                max_pending_candidates=4,
            ),
            origin_surface="web",
            progress_callback=record_progress,
        )

    messages = [text for _, text in events]
    assert len(response.candidates) == 4
    assert any("Searching web for primary references" in message for message in messages)
    assert any("Expanding references at depth 2 from 2 parent candidates with 2 slots open." == message for message in messages)
    assert any("Depth 2: searching references from Example reference 1 (1/2, 2 slots left)." == message for message in messages)
    assert any("Depth 2: Example reference 1 returned 2 candidates; 0 slots left." == message for message in messages)

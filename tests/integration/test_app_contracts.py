from __future__ import annotations

import asyncio
from base64 import b64encode
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from time import monotonic
from typing import cast

from chatkit.types import FileAttachment, ThreadMetadata
import httpx
import pytest

from backend import create_fastapi_app
from backend.app.bootstrap import AppServices, create_services
from backend.app.core.capabilities import chatkit_tool_names, mcp_tool_names, rest_route_names
from backend.app.core.config import AppSettings
from backend.app.mcp.server import create_mcp_server
from backend.app.models import AppChatAttachment
from backend.app.schemas import TaskDetail

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FRONTEND_SCHEMA_CONTRACT: dict[str, tuple[str, set[str]]] = {
    "ActionResponse": ("ActionResponse", {"asset", "answer", "hits", "kind", "task_id"}),
    "AuthUser": ("AuthUser", {"active", "clerk_user_id", "display_name", "primary_email", "role"}),
    "BranchSearchResponse": ("BranchSearchResponse", {"descend", "levels", "max_width", "query"}),
    "ChunkHit": ("ChunkHit", {"attributes", "chunk_id", "locator", "score", "source_file_id", "text"}),
    "ChunkLocator": ("ChunkLocator", {"end_page", "start_page", "type"}),
    "ChunkSummary": ("ChunkSummary", {"id", "keywords", "locator", "source_file_id", "text"}),
    "FileListResponse": ("SourceListResponse", {"has_more", "page", "page_size", "sources", "total_count"}),
    "GeneratedAsset": ("GeneratedAsset", {"byte_size", "download_url", "filename", "id", "kind"}),
    "IngestFinalizeResponse": ("IngestFinalizeResponse", {"source", "task"}),
    "LibrarySourceDetail": (
        "SourceDetail",
        {"chunks", "ingest_strategy", "metadata", "storage_key", "storage_provider"},
    ),
    "LibrarySourceSummary": (
        "SourceSummary",
        {
            "chunk_count",
            "description",
            "display_title",
            "id",
            "openai_vector_file_id",
            "source_kind",
            "status",
            "summary",
            "suggested_tags",
            "tags",
        },
    ),
    "ResearchCandidateIngestRequest": (
        "ResearchCandidateIngestRequest",
        {"candidate_ids", "folder_id", "tag_ids", "task_id"},
    ),
    "ResearchCandidateIngestResponse": ("ResearchCandidateIngestResponse", {"candidates", "ingested"}),
    "ResearchCandidateListResponse": (
        "ResearchCandidateListResponse",
        {"candidates", "has_more", "page", "page_size", "total_count"},
    ),
    "ResearchCandidateStatusUpdateRequest": (
        "ResearchCandidateStatusUpdateRequest",
        {"candidate_ids", "status"},
    ),
    "ResearchCandidateStatusUpdateResponse": ("ResearchCandidateStatusUpdateResponse", {"candidates"}),
    "ResearchImportCandidateSummary": (
        "ResearchImportCandidateSummary",
        {"description", "id", "status", "source_type", "suggested_tags", "summary", "task_id", "title", "url"},
    ),
    "ResearchImportCreateRequest": (
        "ResearchImportCreateRequest",
        {"discover_references", "folder_name", "ingest_seed", "seed_type", "text", "url"},
    ),
    "ResearchImportResponse": (
        "ResearchImportResponse",
        {"candidates", "duplicate_count", "seed_source", "target_folder_id", "task"},
    ),
    "ResearchLibraryBuildRequest": (
        "ResearchLibraryBuildRequest",
        {"auto_ingest", "max_sources", "query", "seed_type"},
    ),
    "ResearchLibraryBuildResponse": (
        "ResearchLibraryBuildResponse",
        {"candidates", "duplicate_count", "ingested", "target_folder_id", "task"},
    ),
    "ResplitSourceRequest": ("ResplitSourceRequest", {"tag_ids", "user_guidance"}),
    "SearchResponse": ("SearchResponse", {"hits", "query"}),
    "SemanticChunkDraft": (
        "SemanticChunkDraft",
        {"keywords", "locator", "sequence", "strategy_label", "summary", "text", "title"},
    ),
    "SemanticSplitResult": ("SemanticSplitResult", {"chunks", "strategy_label", "tags"}),
    "SourceTagsUpdateRequest": ("SourceTagsUpdateRequest", {"tag_ids"}),
    "SplitPreviewResponse": (
        "SplitPreviewResponse",
        {
            "byte_size",
            "extracted_character_count",
            "filename",
            "ingest_strategy",
            "media_type",
            "previewed_at",
            "source_kind",
            "split",
        },
    ),
    "TagCreateRequest": ("TagCreateRequest", {"name", "color"}),
    "TagMutationResponse": ("TagMutationResponse", {"tag", "tasks"}),
    "TagSummary": ("TagSummary", {"id", "name", "slug", "source", "source_count"}),
    "TagUpdateRequest": ("TagUpdateRequest", {"name", "color"}),
    "TaskDetail": ("TaskDetail", {"state_json"}),
    "TaskListResponse": ("TaskListResponse", {"tasks"}),
    "TaskSummary": ("TaskSummary", {"id", "input_json", "kind", "origin_surface", "result_json", "status"}),
}


def test_capability_matrix_matches_current_rest_routes(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    openapi_schema = app.openapi()
    actual_routes = {
        f"{method.upper()} {path}"
        for path, path_spec in openapi_schema["paths"].items()
        for method in path_spec
        if method in {"delete", "get", "patch", "post", "put"}
    }

    assert rest_route_names() <= actual_routes


def test_frontend_types_cover_public_schema_contracts(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    backend_schemas = set(app.openapi()["components"]["schemas"])
    frontend_source = (PROJECT_ROOT / "frontend/src/lib/types.ts").read_text(encoding="utf-8")

    for backend_schema, (frontend_type, expected_fields) in FRONTEND_SCHEMA_CONTRACT.items():
        assert backend_schema in backend_schemas
        assert expected_fields <= _exported_type_fields(frontend_source, frontend_type)
    assert '"resplit"' in frontend_source
    assert '"reindex"' in frontend_source
    assert '"research_import"' in frontend_source


@pytest.mark.asyncio
async def test_http_ingest_search_and_qa_contracts(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}

            me = await client.get("/api/auth/me", headers=auth_headers)
            assert me.status_code == 200
            assert me.json()["clerk_user_id"] == "local-dev"

            upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={
                    "file": (
                        "notes.txt",
                        b"Semantic retrieval likes meaningful chunks.\nFiltering needs tags.",
                        "text/plain",
                    )
                },
                data={"user_guidance": "Split as a concise note."},
            )
            assert upload.status_code == 200
            upload_payload = upload.json()
            source = upload_payload["source"]
            assert source["status"] == "processing"
            assert source["chunk_count"] == 0
            task = upload_payload["task"]
            assert task["kind"] == "ingest"
            assert task["status"] in {"queued", "running", "completed"}
            assert task["origin_surface"] == "web"
            assert task["source_file_id"] == source["id"]
            completed_task = await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=task["id"],
                expected_status="completed",
            )
            result_json = completed_task["result_json"]
            assert isinstance(result_json, dict)
            chunk_count = result_json["chunk_count"]
            assert isinstance(chunk_count, int)
            assert chunk_count == 0
            assert isinstance(result_json["openai_vector_file_id"], str)

            search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "semantic retrieval", "max_results": 4},
            )
            assert search.status_code == 200
            assert search.json()["hits"]

            qa = await client.post(
                "/api/actions/qa",
                headers=auth_headers,
                json={"prompt": "What does retrieval like?", "max_results": 4},
            )
            assert qa.status_code == 200
            payload = qa.json()
            assert payload["kind"] == "qa"
            assert "Fake grounded answer" in payload["answer"]

            tasks = await client.get("/api/tasks", headers=auth_headers)
            assert tasks.status_code == 200
            task_kinds = {task["kind"] for task in tasks.json()["tasks"]}
            assert {"ingest", "qa"} <= task_kinds

            source_detail = await client.get(f"/api/sources/{source['id']}", headers=auth_headers)
            assert source_detail.status_code == 200
            source_detail_payload = source_detail.json()
            assert source_detail_payload["status"] == "ready"
            assert source_detail_payload["chunks"] == []
            original_file_id = source_detail_payload["openai_original_file_id"]
            vector_file_id = source_detail_payload["openai_vector_file_id"]
            assert original_file_id is not None
            assert vector_file_id is not None
            assert source_detail_payload["vector_attributes"]["source_id"] == source["id"]

            delete = await client.delete(f"/api/sources/{source['id']}", headers=auth_headers)
            assert delete.status_code == 200
            openai_gateway = app.state.services.openai
            assert {original_file_id, vector_file_id} <= set(openai_gateway.deleted_file_ids)
            assert ("vs_fake", vector_file_id) in set(openai_gateway.detached_vector_store_file_ids)

            tasks_after_delete = await client.get("/api/tasks", headers=auth_headers)
            assert tasks_after_delete.status_code == 200
            deleted_source_tasks = [
                task
                for task in tasks_after_delete.json()["tasks"]
                if task["kind"] == "ingest" and task["id"] == upload_payload["task"]["id"]
            ]
            assert deleted_source_tasks[0]["source_file_id"] is None


@pytest.mark.asyncio
async def test_http_filesystem_recursive_folder_delete_removes_nested_sources(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            parent = await client.post(
                "/api/filesystem/folders",
                headers=auth_headers,
                json={"name": "Delete Me"},
            )
            assert parent.status_code == 200
            parent_entry = parent.json()
            child = await client.post(
                "/api/filesystem/folders",
                headers=auth_headers,
                json={"name": "Nested", "parent_id": parent_entry["id"]},
            )
            assert child.status_code == 200
            child_entry = child.json()

            upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={"file": ("nested-note.txt", b"Nested source for recursive delete.", "text/plain")},
                data={"folder_id": child_entry["id"]},
            )
            assert upload.status_code == 200
            upload_payload = upload.json()
            source_id = upload_payload["source"]["id"]
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=upload_payload["task"]["id"],
                expected_status="completed",
            )

            child_listing = await client.get(
                "/api/filesystem",
                headers=auth_headers,
                params={"folder_id": child_entry["id"]},
            )
            assert child_listing.status_code == 200
            assert [entry["source_id"] for entry in child_listing.json()["entries"]] == [source_id]

            delete = await client.post(
                "/api/filesystem/delete",
                headers=auth_headers,
                json={"entry_ids": [parent_entry["id"]], "confirm": True},
            )
            assert delete.status_code == 200
            delete_payload = delete.json()
            assert set(delete_payload["deleted_entry_ids"]) == {parent_entry["id"], child_entry["id"]}
            assert delete_payload["deleted_source_ids"] == [source_id]

            deleted_source = await client.get(f"/api/sources/{source_id}", headers=auth_headers)
            assert deleted_source.status_code == 404
            root_listing = await client.get("/api/filesystem", headers=auth_headers)
            assert root_listing.status_code == 200
            assert "Delete Me" not in {entry["name"] for entry in root_listing.json()["entries"]}


@pytest.mark.asyncio
async def test_http_split_preview_is_inspect_only(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            preview = await client.post(
                "/api/sources/split-preview",
                headers=auth_headers,
                files={"file": ("preview-note.txt", b"Preview only. Do not publish vectors.", "text/plain")},
                data={"user_guidance": "Show compact chunks."},
            )
            assert preview.status_code == 200
            payload = preview.json()
            assert payload["source_kind"] == "text"
            assert payload["split"]["tags"]
            assert payload["split"]["chunks"]

            sources = await client.get("/api/sources", headers=auth_headers)
            assert sources.status_code == 200
            assert sources.json()["total_count"] == 0

            tasks = await client.get("/api/tasks", headers=auth_headers)
            assert tasks.status_code == 200
            assert tasks.json()["tasks"] == []

            openai_gateway = app.state.services.openai
            assert openai_gateway.deleted_file_ids == []
            assert openai_gateway.detached_vector_store_file_ids == []


@pytest.mark.asyncio
async def test_http_research_import_ingests_seed_and_queues_url_candidates(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            create = await client.post(
                "/api/research/imports",
                headers=auth_headers,
                json={
                    "seed_type": "text",
                    "title": "Importer seed",
                    "text": "Read this first. Related reference: https://example.com/research-note",
                    "ingest_seed": True,
                    "discover_references": False,
                },
            )
            assert create.status_code == 200
            payload = create.json()
            assert payload["task"]["kind"] == "research_import"
            assert payload["task"]["status"] == "completed"
            assert payload["seed_source"]["status"] == "processing"
            assert len(payload["candidates"]) == 1
            [candidate] = payload["candidates"]
            assert candidate["status"] == "pending"
            assert candidate["normalized_url"] == "https://example.com/research-note"

            tasks = await client.get("/api/tasks", headers=auth_headers)
            assert tasks.status_code == 200
            task_kinds = {task["kind"] for task in tasks.json()["tasks"]}
            assert {"research_import", "ingest"} <= task_kinds

            candidates = await client.get(
                "/api/research/candidates",
                headers=auth_headers,
                params={"task_id": payload["task"]["id"], "status": "pending"},
            )
            assert candidates.status_code == 200
            assert candidates.json()["total_count"] == 1


@pytest.mark.asyncio
async def test_http_research_paper_seed_creates_folder_and_enriched_candidates(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            create = await client.post(
                "/api/research/imports",
                headers=auth_headers,
                json={
                    "seed_type": "paper",
                    "text": "Attention Is All You Need",
                    "discover_references": True,
                    "max_candidates_per_source": 2,
                    "max_pending_candidates": 2,
                },
            )
            assert create.status_code == 200
            payload = create.json()
            assert payload["seed_source"] is None
            assert len(payload["candidates"]) == 2
            candidate = payload["candidates"][0]
            assert candidate["description"] == "Short description for example reference 1."
            assert candidate["summary"] == "Summary for example reference 1 in a research library."
            assert candidate["suggested_tags"] == ["research", "reference-1"]
            assert candidate["authors"] == ["Author 1"]
            assert candidate["published_at"] == "2024"
            assert candidate["provenance"]["target_folder_id"]

            root = await client.get("/api/filesystem", headers=auth_headers)
            assert root.status_code == 200
            research_folder = next(entry for entry in root.json()["entries"] if entry["name"] == "Research")
            research_listing = await client.get(
                "/api/filesystem",
                headers=auth_headers,
                params={"folder_id": research_folder["id"]},
            )
            assert research_listing.status_code == 200
            assert [entry["name"] for entry in research_listing.json()["entries"]] == ["Attention Is All You Need"]


@pytest.mark.asyncio
async def test_http_research_library_build_can_discover_without_auto_ingest_when_requested(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            build = await client.post(
                "/api/research/library-builds",
                headers=auth_headers,
                json={
                    "seed_type": "topic",
                    "query": "transformer interpretability",
                    "folder_name": "Transformer Interpretability",
                    "auto_ingest": False,
                    "max_sources": 3,
                    "max_candidates_per_source": 3,
                },
            )
            assert build.status_code == 200
            payload = build.json()
            assert payload["task"]["kind"] == "research_import"
            assert payload["target_folder_id"]
            assert payload["seed_source"] is None
            assert payload["ingested"] == []
            assert len(payload["candidates"]) == 3
            assert {candidate["status"] for candidate in payload["candidates"]} == {"pending"}

            target_folder = await client.get(
                "/api/filesystem",
                headers=auth_headers,
                params={"folder_id": payload["target_folder_id"]},
            )
            assert target_folder.status_code == 200
            assert target_folder.json()["current"]["path"] == "/Research/Transformer Interpretability"


@pytest.mark.asyncio
async def test_http_research_library_build_expands_followup_candidates(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            build = await client.post(
                "/api/research/library-builds",
                headers=auth_headers,
                json={
                    "seed_type": "paper",
                    "query": "Attention Is All You Need",
                    "auto_ingest": False,
                    "max_depth": 2,
                    "max_sources": 4,
                    "max_candidates_per_source": 2,
                },
            )
            assert build.status_code == 200
            payload = build.json()
            assert len(payload["candidates"]) == 4
            first_hop = [candidate for candidate in payload["candidates"] if candidate["depth"] == 1]
            followups = [candidate for candidate in payload["candidates"] if candidate["depth"] == 2]
            assert len(first_hop) == 2
            assert len(followups) == 2
            first_hop_ids = {candidate["id"] for candidate in first_hop}
            assert {candidate["parent_candidate_id"] for candidate in followups} <= first_hop_ids
            assert followups[0]["title"].startswith("Follow-up reference")
            assert followups[0]["provenance"]["discovery_depth"] == 2


@pytest.mark.asyncio
async def test_http_research_library_build_auto_ingests_public_candidates(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai

    async def handle_reference_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.read(2048)
        request_line = request.splitlines()[0].decode("ascii", errors="ignore")
        path = request_line.split(" ")[1] if " " in request_line else "/reference.txt"
        body = f"Fetched research source from {path}. Alpha retrieval evidence for auto ingestion.".encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle_reference_request, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    app = create_fastapi_app(configured_settings)
    try:
        async with app.router.lifespan_context(app):
            app.state.services.openai.research_candidate_base_url = f"http://127.0.0.1:{port}"
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                build = await client.post(
                    "/api/research/library-builds",
                    headers=auth_headers,
                    json={
                        "seed_type": "topic",
                        "query": "deterministic auto ingest",
                        "folder_name": "Deterministic Auto Ingest",
                        "auto_ingest": True,
                        "max_depth": 1,
                        "max_sources": 2,
                        "max_candidates_per_source": 2,
                    },
                )
                assert build.status_code == 200
                payload = build.json()
                assert len(payload["ingested"]) == 2
                assert {candidate["status"] for candidate in payload["candidates"]} == {"ingesting"}
                assert all(candidate["linked_source_file_id"] for candidate in payload["candidates"])

                for item in payload["ingested"]:
                    await _wait_for_http_task(
                        client,
                        auth_headers=auth_headers,
                        task_id=item["task"]["id"],
                        expected_status="completed",
                    )
                    detail = await client.get(f"/api/sources/{item['source']['id']}", headers=auth_headers)
                    assert detail.status_code == 200
                    metadata = detail.json()["metadata"]
                    assert metadata["research_import_task_id"] == payload["task"]["id"]
                    assert metadata["research_candidate_id"]
                    assert detail.json()["virtual_path"].startswith("/Research/Deterministic Auto Ingest/")

                candidates = await client.get(
                    "/api/research/candidates",
                    headers=auth_headers,
                    params={"task_id": payload["task"]["id"]},
                )
                assert candidates.status_code == 200
                assert {candidate["status"] for candidate in candidates.json()["candidates"]} == {"ingested"}

                target_folder = await client.get(
                    "/api/filesystem",
                    headers=auth_headers,
                    params={"folder_id": payload["target_folder_id"]},
                )
                assert target_folder.status_code == 200
                assert len(target_folder.json()["entries"]) == 2
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_http_research_library_build_skips_duplicate_downloaded_content(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai

    async def handle_reference_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(2048)
        body = b"Same downloaded research content. Alpha retrieval evidence for duplicate suppression."
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle_reference_request, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    app = create_fastapi_app(configured_settings)
    try:
        async with app.router.lifespan_context(app):
            app.state.services.openai.research_candidate_base_url = f"http://127.0.0.1:{port}"
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                build = await client.post(
                    "/api/research/library-builds",
                    headers=auth_headers,
                    json={
                        "seed_type": "topic",
                        "query": "duplicate downloaded content",
                        "folder_name": "Duplicate Downloaded Content",
                        "auto_ingest": True,
                        "max_depth": 1,
                        "max_sources": 2,
                        "max_candidates_per_source": 2,
                    },
                )
                assert build.status_code == 200
                payload = build.json()
                assert len(payload["ingested"]) == 1
                assert payload["duplicate_count"] == 1
                statuses = {candidate["status"] for candidate in payload["candidates"]}
                assert statuses == {"ingesting", "duplicate"}
                duplicate = next(candidate for candidate in payload["candidates"] if candidate["status"] == "duplicate")
                assert duplicate["error_message"] == "Duplicate research candidate content."

                [ingested] = payload["ingested"]
                await _wait_for_http_task(
                    client,
                    auth_headers=auth_headers,
                    task_id=ingested["task"]["id"],
                    expected_status="completed",
                )
                candidates = await client.get(
                    "/api/research/candidates",
                    headers=auth_headers,
                    params={"task_id": payload["task"]["id"]},
                )
                assert candidates.status_code == 200
                assert {candidate["status"] for candidate in candidates.json()["candidates"]} == {"ingested", "duplicate"}

                target_folder = await client.get(
                    "/api/filesystem",
                    headers=auth_headers,
                    params={"folder_id": payload["target_folder_id"]},
                )
                assert target_folder.status_code == 200
                assert len(target_folder.json()["entries"]) == 1
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_http_research_candidate_approval_ingests_through_source_service(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            create = await client.post(
                "/api/research/imports",
                headers=auth_headers,
                json={
                    "seed_type": "text",
                    "title": "Pending seed",
                    "filename": "pending-seed.txt",
                    "text": "Pending research candidate about alpha semantic retrieval.",
                    "ingest_seed": False,
                    "discover_references": False,
                },
            )
            assert create.status_code == 200
            [candidate] = create.json()["candidates"]

            approve = await client.post(
                "/api/research/candidates/status",
                headers=auth_headers,
                json={"candidate_ids": [candidate["id"]], "status": "approved"},
            )
            assert approve.status_code == 200
            assert approve.json()["candidates"][0]["status"] == "approved"

            ingest = await client.post(
                "/api/research/candidates/ingest",
                headers=auth_headers,
                json={"candidate_ids": [candidate["id"]]},
            )
            assert ingest.status_code == 200
            ingest_payload = ingest.json()
            assert len(ingest_payload["ingested"]) == 1
            source = ingest_payload["ingested"][0]["source"]
            task = ingest_payload["ingested"][0]["task"]
            assert task["kind"] == "ingest"
            assert ingest_payload["candidates"][0]["status"] == "ingesting"
            assert ingest_payload["candidates"][0]["linked_source_file_id"] == source["id"]

            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=task["id"],
                expected_status="completed",
            )
            candidates = await client.get(
                "/api/research/candidates",
                headers=auth_headers,
                params={"task_id": create.json()["task"]["id"]},
            )
            assert candidates.status_code == 200
            assert candidates.json()["candidates"][0]["status"] == "ingested"

            detail = await client.get(f"/api/sources/{source['id']}", headers=auth_headers)
            assert detail.status_code == 200
            metadata = detail.json()["metadata"]
            assert metadata["research_candidate_id"] == candidate["id"]
            assert metadata["research_import_task_id"] == create.json()["task"]["id"]


@pytest.mark.asyncio
async def test_http_resplit_replaces_chunks_after_successful_split(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={
                    "file": ("resplit-note.txt", b"First split. Then split again with safer replacement.", "text/plain")
                },
            )
            assert upload.status_code == 200
            upload_payload = upload.json()
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=upload_payload["task"]["id"],
                expected_status="completed",
            )

            source_id = upload_payload["source"]["id"]
            before = await client.get(f"/api/sources/{source_id}", headers=auth_headers)
            assert before.status_code == 200
            before_payload = before.json()
            original_file_id = before_payload["openai_original_file_id"]
            old_vector_file_id = before_payload["openai_vector_file_id"]
            assert original_file_id is not None
            assert old_vector_file_id is not None
            assert before_payload["chunks"] == []

            resplit = await client.post(
                f"/api/sources/{source_id}/resplit",
                headers=auth_headers,
                json={"user_guidance": "Use a fresh compact split."},
            )
            assert resplit.status_code == 200
            resplit_payload = resplit.json()
            assert resplit_payload["source"]["status"] == "processing"
            assert resplit_payload["task"]["kind"] == "resplit"
            assert resplit_payload["task"]["origin_surface"] == "web"
            completed_task = await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=resplit_payload["task"]["id"],
                expected_status="completed",
            )
            result_json = completed_task["result_json"]
            assert isinstance(result_json, dict)
            assert result_json["replaced_chunk_count"] == 0

            after = await client.get(f"/api/sources/{source_id}", headers=auth_headers)
            assert after.status_code == 200
            after_payload = after.json()
            assert after_payload["status"] == "ready"
            assert after_payload["openai_original_file_id"] == original_file_id
            assert after_payload["openai_vector_file_id"] != old_vector_file_id
            assert after_payload["chunks"]
            assert all(chunk["openai_file_id"] is None for chunk in after_payload["chunks"])

            openai_gateway = app.state.services.openai
            assert old_vector_file_id in openai_gateway.deleted_file_ids
            assert original_file_id not in openai_gateway.deleted_file_ids
            assert ("vs_fake", old_vector_file_id) in set(openai_gateway.detached_vector_store_file_ids)


@pytest.mark.asyncio
async def test_failed_resplit_preserves_ready_chunks_before_replacement(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={"file": ("stable-note.txt", b"Keep these chunks when a re-split fails.", "text/plain")},
            )
            assert upload.status_code == 200
            upload_payload = upload.json()
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=upload_payload["task"]["id"],
                expected_status="completed",
            )
            source_id = upload_payload["source"]["id"]
            before = await client.get(f"/api/sources/{source_id}", headers=auth_headers)
            assert before.status_code == 200
            before_payload = before.json()
            old_chunk_ids = [chunk["id"] for chunk in before_payload["chunks"]]
            old_chunk_file_ids = [
                chunk["openai_file_id"] for chunk in before_payload["chunks"] if chunk["openai_file_id"] is not None
            ]
            app.state.services.openai.deleted_file_ids.clear()
            app.state.services.openai.detached_vector_store_file_ids.clear()
            app.state.services.openai.fail_during_split = True

            resplit = await client.post(
                f"/api/sources/{source_id}/resplit",
                headers=auth_headers,
                json={"user_guidance": "This split will fail before replacement."},
            )
            assert resplit.status_code == 200
            failed_task = await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=resplit.json()["task"]["id"],
                expected_status="failed",
            )

            after = await client.get(f"/api/sources/{source_id}", headers=auth_headers)
            assert after.status_code == 200
            after_payload = after.json()
            assert after_payload["status"] == "ready"
            assert [chunk["id"] for chunk in after_payload["chunks"]] == old_chunk_ids
            assert [
                chunk["openai_file_id"] for chunk in after_payload["chunks"] if chunk["openai_file_id"] is not None
            ] == old_chunk_file_ids
            assert app.state.services.openai.deleted_file_ids == []
            assert app.state.services.openai.detached_vector_store_file_ids == []
            state_json = failed_task["state_json"]
            assert isinstance(state_json, dict)
            assert state_json["old_chunks_replaced"] is False


@pytest.mark.asyncio
async def test_http_search_honors_tag_source_and_kind_filters(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            alpha_tag = await client.post("/api/tags", headers=auth_headers, json={"name": "alpha"})
            bravo_tag = await client.post("/api/tags", headers=auth_headers, json={"name": "bravo"})
            assert alpha_tag.status_code == 200
            assert bravo_tag.status_code == 200
            alpha_tag_id = alpha_tag.json()["tag"]["id"]
            bravo_tag_id = bravo_tag.json()["tag"]["id"]
            alpha_upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={"file": ("alpha.txt", b"Alpha topic for semantic retrieval.", "text/plain")},
                data={"tag_ids": alpha_tag_id},
            )
            assert alpha_upload.status_code == 200
            alpha_payload = alpha_upload.json()
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=alpha_payload["task"]["id"],
                expected_status="completed",
            )
            bravo_upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={"file": ("bravo.txt", b"Bravo topic for semantic retrieval.", "text/plain")},
                data={"tag_ids": bravo_tag_id},
            )
            assert bravo_upload.status_code == 200
            bravo_payload = bravo_upload.json()
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=bravo_payload["task"]["id"],
                expected_status="completed",
            )

            alpha_source_id = alpha_payload["source"]["id"]
            bravo_source_id = bravo_payload["source"]["id"]

            alpha_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "retrieval", "tag_ids": [alpha_tag_id], "max_results": 8},
            )
            assert alpha_search.status_code == 200
            assert {hit["source_file_id"] for hit in alpha_search.json()["hits"]} == {alpha_source_id}

            any_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={
                    "query": "retrieval",
                    "tag_ids": [alpha_tag_id, bravo_tag_id],
                    "tag_match_mode": "any",
                    "max_results": 8,
                },
            )
            assert any_search.status_code == 200
            assert {hit["source_file_id"] for hit in any_search.json()["hits"]} == {alpha_source_id, bravo_source_id}

            all_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={
                    "query": "retrieval",
                    "tag_ids": [alpha_tag_id, bravo_tag_id],
                    "tag_match_mode": "all",
                    "max_results": 8,
                },
            )
            assert all_search.status_code == 200
            assert all_search.json()["hits"] == []

            source_scoped_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "retrieval", "selected_source_ids": [bravo_source_id], "max_results": 8},
            )
            assert source_scoped_search.status_code == 200
            assert {hit["source_file_id"] for hit in source_scoped_search.json()["hits"]} == {bravo_source_id}

            kind_scoped_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "retrieval", "source_kinds": ["pdf"], "max_results": 8},
            )
            assert kind_scoped_search.status_code == 200
            assert kind_scoped_search.json()["hits"] == []

            dated_branch_search = await client.post(
                "/api/search/branch",
                headers=auth_headers,
                json={
                    "query": "retrieval",
                    "created_before": "2000-01-01T00:00:00Z",
                    "descend": 1,
                    "max_width": 3,
                },
            )
            assert dated_branch_search.status_code == 200
            assert dated_branch_search.json()["levels"] == []

            app.state.services.openai.ignore_filters = True
            fallback_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "retrieval", "tag_ids": [bravo_tag_id], "max_results": 8},
            )
            assert fallback_search.status_code == 200
            assert {hit["source_file_id"] for hit in fallback_search.json()["hits"]} == {bravo_source_id}


@pytest.mark.asyncio
async def test_http_source_tag_update_reindexes_vector_attributes(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            alpha_tag = await client.post("/api/tags", headers=auth_headers, json={"name": "alpha"})
            bravo_tag = await client.post("/api/tags", headers=auth_headers, json={"name": "bravo"})
            assert alpha_tag.status_code == 200
            assert bravo_tag.status_code == 200
            alpha_tag_id = alpha_tag.json()["tag"]["id"]
            bravo_tag_id = bravo_tag.json()["tag"]["id"]
            alpha_upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={"file": ("alpha.txt", b"Alpha topic for semantic retrieval.", "text/plain")},
                data={"tag_ids": alpha_tag_id},
            )
            assert alpha_upload.status_code == 200
            alpha_payload = alpha_upload.json()
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=alpha_payload["task"]["id"],
                expected_status="completed",
            )
            bravo_upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={"file": ("bravo.txt", b"Bravo topic for semantic retrieval.", "text/plain")},
                data={"tag_ids": bravo_tag_id},
            )
            assert bravo_upload.status_code == 200
            bravo_payload = bravo_upload.json()
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=bravo_payload["task"]["id"],
                expected_status="completed",
            )

            alpha_source_id = alpha_payload["source"]["id"]
            bravo_source_id = bravo_payload["source"]["id"]

            before_detail = await client.get(f"/api/sources/{alpha_source_id}", headers=auth_headers)
            assert before_detail.status_code == 200
            old_vector_file_id = before_detail.json()["openai_vector_file_id"]
            assert old_vector_file_id is not None

            tag_update = await client.post(
                f"/api/sources/{alpha_source_id}/tags",
                headers=auth_headers,
                json={"tag_ids": [bravo_tag_id]},
            )
            assert tag_update.status_code == 200
            tag_update_payload = tag_update.json()
            assert tag_update_payload["task"]["kind"] == "reindex"
            assert tag_update_payload["source"]["status"] == "processing"
            completed_task = await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=tag_update_payload["task"]["id"],
                expected_status="completed",
            )
            result_json = completed_task["result_json"]
            assert isinstance(result_json, dict)
            assert result_json["tag_count"] == 1

            after_detail = await client.get(f"/api/sources/{alpha_source_id}", headers=auth_headers)
            assert after_detail.status_code == 200
            after_payload = after_detail.json()
            assert after_payload["status"] == "ready"
            assert [tag["id"] for tag in after_payload["tags"]] == [bravo_tag_id]
            assert after_payload["openai_vector_file_id"] != old_vector_file_id
            assert old_vector_file_id in app.state.services.openai.deleted_file_ids
            assert ("vs_fake", old_vector_file_id) in set(app.state.services.openai.detached_vector_store_file_ids)

            old_tag_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "retrieval", "tag_ids": [alpha_tag_id], "max_results": 8},
            )
            assert old_tag_search.status_code == 200
            assert alpha_source_id not in {hit["source_file_id"] for hit in old_tag_search.json()["hits"]}

            new_tag_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "retrieval", "tag_ids": [bravo_tag_id], "max_results": 8},
            )
            assert new_tag_search.status_code == 200
            assert {hit["source_file_id"] for hit in new_tag_search.json()["hits"]} == {
                alpha_source_id,
                bravo_source_id,
            }


@pytest.mark.asyncio
async def test_http_manual_tag_crud_reindexes_affected_sources(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            alpha_tag = await client.post("/api/tags", headers=auth_headers, json={"name": "alpha"})
            assert alpha_tag.status_code == 200
            alpha_tag_id = alpha_tag.json()["tag"]["id"]
            upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={"file": ("alpha.txt", b"Alpha topic for semantic retrieval.", "text/plain")},
                data={"tag_ids": alpha_tag_id},
            )
            assert upload.status_code == 200
            upload_payload = upload.json()
            source_id = upload_payload["source"]["id"]
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=upload_payload["task"]["id"],
                expected_status="completed",
            )

            renamed = await client.patch(
                f"/api/tags/{alpha_tag_id}",
                headers=auth_headers,
                json={"name": "alpha-renamed"},
            )
            assert renamed.status_code == 200
            renamed_payload = renamed.json()
            assert renamed_payload["tag"]["slug"] == "alpha-renamed"
            assert [task["kind"] for task in renamed_payload["tasks"]] == ["reindex"]
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=renamed_payload["tasks"][0]["id"],
                expected_status="completed",
            )

            renamed_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "retrieval", "tag_ids": [alpha_tag_id], "max_results": 8},
            )
            assert renamed_search.status_code == 200
            assert {hit["source_file_id"] for hit in renamed_search.json()["hits"]} == {source_id}

            manual_tag = await client.post(
                "/api/tags",
                headers=auth_headers,
                json={"name": "review-needed", "color": "#2563eb"},
            )
            assert manual_tag.status_code == 200
            manual_tag_id = manual_tag.json()["tag"]["id"]
            assert manual_tag.json()["tag"]["source"] == "manual"

            tag_update = await client.post(
                f"/api/sources/{source_id}/tags",
                headers=auth_headers,
                json={"tag_ids": [alpha_tag_id, manual_tag_id]},
            )
            assert tag_update.status_code == 200
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=tag_update.json()["task"]["id"],
                expected_status="completed",
            )

            deleted = await client.delete(f"/api/tags/{manual_tag_id}", headers=auth_headers)
            assert deleted.status_code == 200
            deleted_payload = deleted.json()
            assert deleted_payload["tag"] is None
            assert [task["kind"] for task in deleted_payload["tasks"]] == ["reindex"]
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=deleted_payload["tasks"][0]["id"],
                expected_status="completed",
            )

            after_detail = await client.get(f"/api/sources/{source_id}", headers=auth_headers)
            assert after_detail.status_code == 200
            assert [tag["id"] for tag in after_detail.json()["tags"]] == [alpha_tag_id]


@pytest.mark.asyncio
async def test_failed_ingest_cleans_up_tracked_openai_files(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        app.state.services.openai.fail_during_vector_attach = True
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={
                    "file": ("failing-notes.txt", b"Vector attach failure should clean up OpenAI files.", "text/plain")
                },
            )
            assert upload.status_code == 200
            upload_payload = upload.json()
            assert upload_payload["source"]["status"] == "processing"
            failed_task = await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=upload_payload["task"]["id"],
                expected_status="failed",
            )
            assert app.state.services.openai.deleted_file_ids == ["file_original_2", "file_original_1"]
            source = await client.get(f"/api/sources/{upload_payload['source']['id']}", headers=auth_headers)
            assert source.status_code == 200
            assert source.json()["status"] == "failed"
            assert source.json()["openai_original_file_id"] is None
            assert source.json()["openai_vector_file_id"] is None

            tasks = await client.get("/api/tasks", headers=auth_headers)
            assert tasks.status_code == 200
            [task] = tasks.json()["tasks"]
            assert task["kind"] == "ingest"
            assert task["status"] == "failed"
            assert task["error_message"] == "Fake vector attach failure."
            assert failed_task["error_message"] == "Fake vector attach failure."


@pytest.mark.asyncio
async def test_mcp_server_exposes_app_first_tools(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    server = create_mcp_server(configured_settings, services)
    try:
        tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}
    finally:
        await services.close()

    assert set(tools) == mcp_tool_names()
    assert tools["delete_source"].annotations is not None
    assert tools["delete_source"].annotations.destructiveHint is True
    assert tools["resplit_source"].annotations is not None
    assert tools["resplit_source"].annotations.destructiveHint is True
    assert set(tools["ingest_file_source"].parameters["required"]) == {"filename", "payload_base64"}
    assert set(tools["preview_file_split"].parameters["required"]) == {"filename", "payload_base64"}
    assert tools["search_chunks"].parameters["required"] == ["query"]
    assert tools["sources"].meta is not None
    assert tools["sources"].meta["ui"]["resourceUri"].startswith("ui://")


@pytest.mark.asyncio
async def test_mcp_sources_ui_resource_renders_explorer_sections(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    server = create_mcp_server(configured_settings, services)
    try:
        result = await server.call_tool("sources", {}, run_middleware=False)
    finally:
        await services.close()

    serialized = json.dumps(result.structured_content, sort_keys=True, default=str)
    assert "Indexed Files" in serialized
    assert "Query files, filenames, kinds, status" in serialized
    assert "Research Library Builder" in serialized
    assert "Build library" in serialized
    assert "Research Candidates" in serialized
    assert "Search indexed files with the selected tag scope" in serialized
    assert "Recent Tasks" in serialized
    assert "selectedTagIds" in serialized
    assert "researchCandidates" in serialized


@pytest.mark.asyncio
async def test_mcp_split_preview_is_inspect_only(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    server = create_mcp_server(configured_settings, services)
    try:
        result = await server.call_tool(
            "preview_file_split",
            {
                "filename": "mcp-preview.txt",
                "payload_base64": b64encode(b"MCP preview should not publish a source.").decode("ascii"),
                "media_type": "text/plain",
            },
            run_middleware=False,
        )
        tasks = await services.actions.list_tasks(clerk_user_id="local-dev")
        sources = await services.sources.list_sources(
            clerk_user_id="local-dev",
            query=None,
            tag_ids=[],
            tag_match_mode="all",
            page=1,
            page_size=10,
        )
    finally:
        await services.close()

    payload = result.structured_content
    assert payload is not None
    assert payload["split"]["chunks"]
    assert tasks.tasks == []
    assert sources.total_count == 0


@pytest.mark.asyncio
async def test_mcp_file_ingest_tool_runs_against_app_core(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    server = create_mcp_server(configured_settings, services)
    try:
        result = await server.call_tool(
            "ingest_file_source",
            {
                "filename": "mcp-note.txt",
                "payload_base64": b64encode(b"MCP file ingest should reach the app core.").decode("ascii"),
                "media_type": "text/plain",
            },
            run_middleware=False,
        )
        payload = result.structured_content
        assert payload is not None
        completed_task = await _wait_for_service_task(
            services,
            task_id=payload["task"]["id"],
            expected_status="completed",
        )
        source_detail = await services.sources.get_source(
            clerk_user_id="local-dev",
            source_id=payload["source"]["id"],
        )
    finally:
        await services.close()

    assert payload["source"]["status"] == "processing"
    assert source_detail.status == "ready"
    assert payload["source"]["source_kind"] == "text"
    assert payload["task"]["kind"] == "ingest"
    assert completed_task.origin_surface == "mcp"


@pytest.mark.asyncio
async def test_mcp_answer_research_library_uses_scoped_sources(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    server = create_mcp_server(configured_settings, services)
    try:
        ingest = await server.call_tool(
            "ingest_file_source",
            {
                "filename": "research-answer-note.txt",
                "payload_base64": b64encode(b"Research answer source mentions Cobalt Maple evidence.").decode("ascii"),
                "media_type": "text/plain",
            },
            run_middleware=False,
        )
        ingest_payload = ingest.structured_content
        assert ingest_payload is not None
        await _wait_for_service_task(
            services,
            task_id=ingest_payload["task"]["id"],
            expected_status="completed",
        )

        answer = await server.call_tool(
            "answer_research_library",
            {
                "question": "What evidence is available?",
                "source_ids": [ingest_payload["source"]["id"]],
            },
            run_middleware=False,
        )
    finally:
        await services.close()

    payload = answer.structured_content
    assert payload is not None
    assert payload["kind"] == "qa"
    assert "Fake grounded answer" in payload["answer"]
    assert payload["hits"]
    assert {hit["source_file_id"] for hit in payload["hits"]} == {ingest_payload["source"]["id"]}


@pytest.mark.asyncio
async def test_http_chatkit_attachment_upload_creates_source_task_and_metadata(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            upload = await client.post(
                "/api/chatkit/attachments",
                headers=auth_headers,
                files={
                    "file": (
                        "chatkit-note.txt",
                        b"Attached ChatKit notes should become searchable source material.",
                        "text/plain",
                    )
                },
                data={"user_guidance": "Keep attachment chunks concise."},
            )

            assert upload.status_code == 200
            attachment_payload = upload.json()
            assert attachment_payload["type"] == "file"
            assert attachment_payload["name"] == "chatkit-note.txt"
            assert attachment_payload["mime_type"] == "text/plain"
            metadata = attachment_payload["metadata"]
            source_id = metadata["source_id"]
            task_id = metadata["task_id"]
            assert metadata["attachment_id"] == attachment_payload["id"]
            assert metadata["origin_surface"] == "chatkit"
            assert metadata["source"]["id"] == source_id
            assert metadata["task"]["id"] == task_id

            task = await client.get(f"/api/tasks/{task_id}", headers=auth_headers)
            assert task.status_code == 200
            task_payload = task.json()
            assert task_payload["kind"] == "ingest"
            assert task_payload["origin_surface"] == "chatkit"
            assert task_payload["source_file_id"] == source_id
            assert task_payload["origin_thread_id"] is None

            async with app.state.services.database.session() as session:
                record = await session.get(AppChatAttachment, attachment_payload["id"])
                assert record is not None
                assert record.payload["metadata"]["source_id"] == source_id
                assert record.payload["metadata"]["task_id"] == task_id

            completed_task = await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=task_id,
                expected_status="completed",
            )
            assert completed_task["origin_surface"] == "chatkit"


@pytest.mark.asyncio
async def test_chatkit_attachment_save_backfills_ingest_task_thread_link(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            upload = await client.post(
                "/api/chatkit/attachments",
                headers=auth_headers,
                files={"file": ("thread-link.txt", b"Thread linked attachment payload.", "text/plain")},
            )
            assert upload.status_code == 200
            attachment_payload = upload.json()

        services = app.state.services
        context = services.chatkit_server.build_user_context(
            clerk_user_id="local-dev",
            user_email=None,
            display_name="Local Dev",
            bearer_token="local-dev",
        )
        thread = ThreadMetadata(id="chat_thread_link_test", created_at=datetime.now(UTC))
        await services.chatkit_server.store.save_thread(thread, context=context)
        linked_attachment = FileAttachment.model_validate(attachment_payload).model_copy(
            update={"thread_id": thread.id}
        )
        await services.chatkit_server.store.save_attachment(linked_attachment, context=context)

        task_detail = await services.actions.get_task(
            clerk_user_id="local-dev",
            task_id=attachment_payload["metadata"]["task_id"],
        )
        assert task_detail.origin_thread_id == thread.id

        async with services.database.session() as session:
            record = await session.get(AppChatAttachment, attachment_payload["id"])
            assert record is not None
            assert record.payload["thread_id"] == thread.id
            assert record.payload["metadata"]["thread_id"] == thread.id


@pytest.mark.asyncio
async def test_chatkit_thread_metadata_persists_selected_source_scope(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    try:
        context = services.chatkit_server.build_user_context(
            clerk_user_id="local-dev",
            user_email=None,
            display_name="Local Dev",
            bearer_token="local-dev",
        )
        context.selected_source_ids = ["source_alpha", "source_bravo"]
        context.thread_origin = "web"
        thread = ThreadMetadata(
            id="chat_scope_metadata_test",
            created_at=datetime.now(UTC),
            metadata={"existing": "value"},
        )

        await services.chatkit_server.store.save_thread(thread, context=context)
        loaded = await services.chatkit_server.store.load_thread(thread.id, context=context)

        assert loaded.metadata["existing"] == "value"
        assert loaded.metadata["selected_source_ids"] == ["source_alpha", "source_bravo"]
        assert loaded.metadata["selected_source_count"] == 2
        assert loaded.metadata["scope_origin"] == "web"
        assert isinstance(loaded.metadata["scope_updated_at"], str)
    finally:
        await services.close()


@pytest.mark.asyncio
async def test_chatkit_server_exposes_documented_app_core_tools(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    try:
        tools = services.chatkit_server.tool_names()
    finally:
        await services.close()

    assert tools == chatkit_tool_names()


def _exported_type_fields(source: str, type_name: str) -> set[str]:
    match = re.search(rf"export type {type_name} = (?P<body>.*?\n}};)", source, flags=re.DOTALL)
    assert match is not None, f"Missing frontend type export: {type_name}"
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\??:", match.group("body"), flags=re.MULTILINE))


async def _wait_for_http_task(
    client: httpx.AsyncClient,
    *,
    auth_headers: dict[str, str],
    task_id: str,
    expected_status: str,
) -> dict[str, object]:
    deadline = monotonic() + 5
    last_payload: dict[str, object] | None = None
    terminal_statuses = {"completed", "failed", "cancelled"}
    while monotonic() < deadline:
        response = await client.get(f"/api/tasks/{task_id}", headers=auth_headers)
        assert response.status_code == 200
        payload = cast(dict[str, object], response.json())
        last_payload = payload
        task_status = payload.get("status")
        if task_status == expected_status:
            return payload
        if task_status in terminal_statuses:
            pytest.fail(f"Task {task_id} ended with {task_status} instead of {expected_status}.")
        await asyncio.sleep(0.01)
    pytest.fail(f"Timed out waiting for task {task_id} to reach {expected_status}; last payload: {last_payload!r}")


async def _wait_for_service_task(
    services: AppServices,
    *,
    task_id: str,
    expected_status: str,
) -> TaskDetail:
    deadline = monotonic() + 5
    last_task = None
    terminal_statuses = {"completed", "failed", "cancelled"}
    while monotonic() < deadline:
        last_task = await services.actions.get_task(clerk_user_id="local-dev", task_id=task_id)
        if last_task.status == expected_status:
            return last_task
        if last_task.status in terminal_statuses:
            pytest.fail(f"Task {task_id} ended with {last_task.status} instead of {expected_status}.")
        await asyncio.sleep(0.01)
    pytest.fail(f"Timed out waiting for task {task_id} to reach {expected_status}; last task: {last_task!r}")

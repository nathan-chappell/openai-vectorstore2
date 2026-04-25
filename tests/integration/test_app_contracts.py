from __future__ import annotations

import asyncio
from base64 import b64encode
from pathlib import Path
import re
from time import monotonic
from typing import cast

import httpx
import pytest

from backend import create_fastapi_app
from backend.app.core.capabilities import chatkit_tool_names, mcp_tool_names, rest_route_names
from backend.app.core.config import AppSettings
from backend.app.bootstrap import AppServices, create_services
from backend.app.mcp.server import create_mcp_server
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
    "LibrarySourceDetail": ("SourceDetail", {"chunks", "ingest_strategy", "storage_key", "storage_provider"}),
    "LibrarySourceSummary": ("SourceSummary", {"chunk_count", "display_title", "id", "source_kind", "status", "tags"}),
    "ResplitSourceRequest": ("ResplitSourceRequest", {"tag_ids", "user_guidance"}),
    "SearchResponse": ("SearchResponse", {"hits", "query"}),
    "SemanticChunkDraft": (
        "SemanticChunkDraft",
        {"keywords", "locator", "sequence", "strategy_label", "summary", "text", "title"},
    ),
    "SemanticSplitResult": ("SemanticSplitResult", {"chunks", "strategy_label", "tags"}),
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
    "TagSummary": ("TagSummary", {"id", "name", "slug", "source", "source_count"}),
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
            assert chunk_count >= 1

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
            assert len(source_detail_payload["chunks"]) >= 1
            chunk_file_ids = [
                chunk["openai_file_id"]
                for chunk in source_detail_payload["chunks"]
                if chunk["openai_file_id"] is not None
            ]
            original_file_id = source_detail_payload["openai_original_file_id"]
            assert original_file_id is not None

            delete = await client.delete(f"/api/sources/{source['id']}", headers=auth_headers)
            assert delete.status_code == 200
            openai_gateway = app.state.services.openai
            assert {original_file_id, *chunk_file_ids} <= set(openai_gateway.deleted_file_ids)
            assert {("vs_fake", file_id) for file_id in chunk_file_ids} <= set(
                openai_gateway.detached_vector_store_file_ids
            )

            tasks_after_delete = await client.get("/api/tasks", headers=auth_headers)
            assert tasks_after_delete.status_code == 200
            deleted_source_tasks = [
                task
                for task in tasks_after_delete.json()["tasks"]
                if task["kind"] == "ingest" and task["id"] == upload_payload["task"]["id"]
            ]
            assert deleted_source_tasks[0]["source_file_id"] is None


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
            old_chunk_file_ids = [
                chunk["openai_file_id"] for chunk in before_payload["chunks"] if chunk["openai_file_id"] is not None
            ]
            assert original_file_id is not None
            assert old_chunk_file_ids

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
            assert result_json["replaced_chunk_count"] == len(old_chunk_file_ids)

            after = await client.get(f"/api/sources/{source_id}", headers=auth_headers)
            assert after.status_code == 200
            after_payload = after.json()
            assert after_payload["status"] == "ready"
            assert after_payload["openai_original_file_id"] == original_file_id
            new_chunk_file_ids = [
                chunk["openai_file_id"] for chunk in after_payload["chunks"] if chunk["openai_file_id"] is not None
            ]
            assert new_chunk_file_ids
            assert not set(old_chunk_file_ids) & set(new_chunk_file_ids)

            openai_gateway = app.state.services.openai
            assert set(old_chunk_file_ids) <= set(openai_gateway.deleted_file_ids)
            assert original_file_id not in openai_gateway.deleted_file_ids
            assert {("vs_fake", file_id) for file_id in old_chunk_file_ids} <= set(
                openai_gateway.detached_vector_store_file_ids
            )


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
            alpha_upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={"file": ("alpha.txt", b"Alpha topic for semantic retrieval.", "text/plain")},
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
            )
            assert bravo_upload.status_code == 200
            bravo_payload = bravo_upload.json()
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=bravo_payload["task"]["id"],
                expected_status="completed",
            )

            tags = await client.get("/api/tags", headers=auth_headers)
            assert tags.status_code == 200
            tag_ids_by_name = {tag["name"].casefold(): tag["id"] for tag in tags.json()}
            alpha_tag_id = tag_ids_by_name["alpha"]
            bravo_tag_id = tag_ids_by_name["bravo"]
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

            app.state.services.openai.ignore_filters = True
            fallback_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "retrieval", "tag_ids": [bravo_tag_id], "max_results": 8},
            )
            assert fallback_search.status_code == 200
            assert {hit["source_file_id"] for hit in fallback_search.json()["hits"]} == {bravo_source_id}


@pytest.mark.asyncio
async def test_failed_ingest_cleans_up_tracked_openai_files(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        app.state.services.openai.fail_during_split = True
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={"file": ("failing-notes.txt", b"Split failure should clean up original files.", "text/plain")},
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
            assert app.state.services.openai.deleted_file_ids == ["file_original_1"]
            source = await client.get(f"/api/sources/{upload_payload['source']['id']}", headers=auth_headers)
            assert source.status_code == 200
            assert source.json()["status"] == "failed"

            tasks = await client.get("/api/tasks", headers=auth_headers)
            assert tasks.status_code == 200
            [task] = tasks.json()["tasks"]
            assert task["kind"] == "ingest"
            assert task["status"] == "failed"
            assert task["error_message"] == "Fake semantic split failure."
            assert failed_task["error_message"] == "Fake semantic split failure."


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

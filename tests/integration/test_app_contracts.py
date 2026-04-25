from __future__ import annotations

import httpx
import pytest

from backend import create_fastapi_app
from backend.app.core.config import AppSettings
from backend.app.mcp.server import create_mcp_server
from backend.app.bootstrap import create_services


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
                files={"file": ("notes.txt", b"Semantic retrieval likes meaningful chunks.\nFiltering needs tags.", "text/plain")},
                data={"user_guidance": "Split as a concise note."},
            )
            assert upload.status_code == 200
            source = upload.json()["source"]
            assert source["status"] == "ready"
            assert source["chunk_count"] >= 1

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


@pytest.mark.asyncio
async def test_mcp_server_exposes_app_first_tools(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    services = create_services(configured_settings)
    server = create_mcp_server(configured_settings, services)
    try:
        tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}
    finally:
        await services.close()

    expected = {
        "branch_search",
        "delete_source",
        "freeform",
        "generate_image",
        "generate_voice",
        "get_source_detail",
        "get_task",
        "ingest_text_source",
        "list_sources",
        "list_tags",
        "list_tasks",
        "qa",
        "search_chunks",
        "sources",
    }
    assert expected.issubset(set(tools))
    assert tools["sources"].meta is not None
    assert tools["sources"].meta["ui"]["resourceUri"].startswith("ui://")

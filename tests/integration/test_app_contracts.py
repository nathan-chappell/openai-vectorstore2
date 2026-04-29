from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from importlib import import_module
import json
from pathlib import Path
import re
from types import SimpleNamespace
import sys
from time import monotonic
from typing import Any, cast

from chatkit.types import (
    AssistantMessageContent,
    AssistantMessageItem,
    FileAttachment,
    InferenceOptions,
    ThreadMetadata,
    UserMessageItem,
    UserMessageTextContent,
)
import httpx
import pytest
from sqlalchemy import select

from backend import create_fastapi_app
from backend.app.bootstrap import AppServices, create_services
from backend.app.chatkit.store import VectorstoreChatStore
from backend.app.core.capabilities import chatkit_tool_names, mcp_tool_names, rest_route_names
from backend.app.core.config import AppSettings, get_settings
from backend.app.mcp.server import create_mcp_server
from backend.app.models import AppChatAttachment, AppChatEntry
from backend.app.schemas import QaRequest, TaskDetail

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FRONTEND_SCHEMA_CONTRACT: dict[str, tuple[str, set[str]]] = {
    "ActionResponse": ("ActionResponse", {"asset", "answer", "hits", "kind", "task_id"}),
    "AdminGrantCreditRequest": ("AdminGrantCreditRequest", {"clerk_user_id", "credit_amount_usd", "note"}),
    "AdminGrantCreditResponse": ("AdminGrantCreditResponse", {"balance", "grant"}),
    "AdminFreeCreditDecisionRequest": (
        "AdminFreeCreditDecisionRequest",
        {"credit_amount_usd", "decision_note", "request_id", "status"},
    ),
    "AdminSetUserActiveRequest": ("AdminSetUserActiveRequest", {"active", "clerk_user_id"}),
    "AdminSetUserActiveResponse": (
        "AdminSetUserActiveResponse",
        {"active", "clerk_user_id", "credit_floor_usd", "current_credit_usd"},
    ),
    "AdminUserListResponse": ("AdminUserListResponse", {"has_more", "items", "limit", "offset", "query"}),
    "AdminUserSummary": (
        "AdminUserSummary",
        {"active", "clerk_user_id", "credit_floor_usd", "current_credit_usd", "primary_email", "role"},
    ),
    "AuthUser": (
        "AuthUser",
        {"active", "clerk_user_id", "credit_floor_usd", "current_credit_usd", "display_name", "primary_email", "role"},
    ),
    "BillingStatusResponse": (
        "BillingStatusResponse",
        {"active", "billable", "billing_enabled", "clerk_user_id", "credit_floor_usd", "current_credit_usd", "role"},
    ),
    "PaymentIntegrationResponse": (
        "PaymentIntegrationResponse",
        {
            "checkout_enabled",
            "max_payment_usd",
            "min_payment_usd",
            "paypal_payment_url",
            "paypal_recipient_email",
            "provider",
            "reason",
            "receipt_upload_enabled",
        },
    ),
    "PaymentAttemptSummary": (
        "PaymentAttemptSummary",
        {"clerk_user_id", "credit_grant_id", "expected_amount_usd", "reference_code", "review_reason", "status"},
    ),
    "PaymentAttemptListResponse": (
        "PaymentAttemptListResponse",
        {"attempts"},
    ),
    "PayPalPaymentAttemptCreateRequest": (
        "PayPalPaymentAttemptCreateRequest",
        {"expected_amount_usd"},
    ),
    "AdminPaymentAttemptDecisionRequest": (
        "AdminPaymentAttemptDecisionRequest",
        {"attempt_id", "credit_amount_usd", "decision_note", "provider_reference", "status"},
    ),
    "FreeCreditRequestCreate": (
        "FreeCreditRequestCreate",
        {"idempotency_key", "reason", "requested_amount_usd", "source"},
    ),
    "FreeCreditRequestListResponse": ("FreeCreditRequestListResponse", {"requests"}),
    "FreeCreditRequestSummary": (
        "FreeCreditRequestSummary",
        {"clerk_user_id", "credit_grant_id", "id", "reason", "requested_amount_usd", "status"},
    ),
    "BranchSearchResponse": ("BranchSearchResponse", {"descend", "levels", "max_width", "query"}),
    "ChunkHit": ("ChunkHit", {"attributes", "chunk_id", "locator", "score", "source_file_id", "text"}),
    "ChunkLocator": ("ChunkLocator", {"end_page", "start_page", "type"}),
    "ChunkSummary": ("ChunkSummary", {"id", "keywords", "locator", "source_file_id", "text"}),
    "FileListResponse": ("SourceListResponse", {"has_more", "page", "page_size", "sources", "total_count"}),
    "GeneratedAsset": ("GeneratedAsset", {"byte_size", "download_url", "filename", "id", "kind"}),
    "IngestFinalizeResponse": ("IngestFinalizeResponse", {"source", "task"}),
    "ReportMarkdownSaveRequest": ("ReportMarkdownSaveRequest", {"document", "filename", "folder_id", "tag_ids"}),
    "ReportMarkdownSaveResponse": ("ReportMarkdownSaveResponse", {"markdown", "source", "task"}),
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
            assert me.json()["current_credit_usd"] == 0.0
            assert me.json()["credit_floor_usd"] == -1.0

            billing = await client.get("/api/billing/me", headers=auth_headers)
            assert billing.status_code == 200
            assert billing.json()["billable"] is True

            payment_status = await client.get("/api/billing/payment-status", headers=auth_headers)
            assert payment_status.status_code == 200
            assert payment_status.json()["provider"] == "default"
            assert payment_status.json()["checkout_enabled"] is False

            grant = await client.post(
                "/api/admin/credits/grant",
                headers=auth_headers,
                json={"clerk_user_id": "local-dev", "credit_amount_usd": 2.5, "note": "manual test grant"},
            )
            assert grant.status_code == 200
            assert grant.json()["balance"]["current_credit_usd"] == 2.5
            assert grant.json()["grant"]["admin_clerk_user_id"] == "local-dev"

            admin_users = await client.get("/api/admin/users", headers=auth_headers)
            assert admin_users.status_code == 200
            assert admin_users.json()["items"][0]["clerk_user_id"] == "local-dev"

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

            billing_after_qa = await client.get("/api/billing/me", headers=auth_headers)
            assert billing_after_qa.status_code == 200
            assert billing_after_qa.json()["current_credit_usd"] < 2.5

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
async def test_http_paypal_receipt_upload_grants_temporary_credit(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    settings = configured_settings.model_copy(update={"paypal_recipient_email": "owner@example.com"})
    app = create_fastapi_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            payment_status = await client.get("/api/billing/payment-status", headers=auth_headers)
            assert payment_status.status_code == 200
            assert payment_status.json()["provider"] == "paypal"
            assert payment_status.json()["receipt_upload_enabled"] is True

            created = await client.post(
                "/api/billing/paypal/attempts",
                headers=auth_headers,
                json={"expected_amount_usd": 12.0},
            )
            assert created.status_code == 200
            attempt = created.json()
            reference_code = attempt["reference_code"]

            receipt_text = "\n".join(
                [
                    "PayPal receipt",
                    "Transaction ID: PAYPAL123456789",
                    "Paid with PayPal",
                    "Amount: $12.00 USD",
                    "Recipient: owner@example.com",
                    f"Reference: {reference_code}",
                ]
            )
            reviewed = await client.post(
                f"/api/billing/paypal/attempts/{attempt['id']}/receipt",
                headers=auth_headers,
                files={"file": ("paypal-receipt.txt", receipt_text.encode(), "text/plain")},
            )
            assert reviewed.status_code == 200
            reviewed_payload = reviewed.json()
            assert reviewed_payload["status"] == "temporarily_approved"
            assert reviewed_payload["temporary_access_expires_at"] is None
            assert reviewed_payload["credit_grant_id"]
            assert reviewed_payload["provider_reference"] == "PAYPAL123456789"

            billing = await client.get("/api/billing/me", headers=auth_headers)
            assert billing.status_code == 200
            assert billing.json()["current_credit_usd"] == 12.0

            admin_payments = await client.get(
                "/api/admin/payments?status=temporarily_approved",
                headers=auth_headers,
            )
            assert admin_payments.status_code == 200
            assert admin_payments.json()["attempts"][0]["id"] == attempt["id"]

            confirmed = await client.post(
                "/api/admin/payments/decide",
                headers=auth_headers,
                json={
                    "attempt_id": attempt["id"],
                    "status": "confirmed_paid",
                    "decision_note": "Matched PayPal dashboard manually.",
                },
            )
            assert confirmed.status_code == 200
            assert confirmed.json()["status"] == "confirmed_paid"


@pytest.mark.asyncio
async def test_http_free_credit_request_can_be_approved_by_admin(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/api/billing/free-credit-requests",
                headers=auth_headers,
                json={
                    "requested_amount_usd": 7.0,
                    "source": "general",
                    "reason": "Trying the beta workflows before adding payment details.",
                    "idempotency_key": "free-credit-test-1",
                },
            )
            assert created.status_code == 200
            request_payload = created.json()
            assert request_payload["status"] == "pending"

            listed = await client.get("/api/admin/free-credit-requests?status=pending", headers=auth_headers)
            assert listed.status_code == 200
            assert listed.json()["requests"][0]["id"] == request_payload["id"]

            approved = await client.post(
                "/api/admin/free-credit-requests/decide",
                headers=auth_headers,
                json={
                    "request_id": request_payload["id"],
                    "status": "approved",
                    "credit_amount_usd": 7.0,
                    "decision_note": "Approved for beta test.",
                },
            )
            assert approved.status_code == 200
            approved_payload = approved.json()
            assert approved_payload["status"] == "approved"
            assert approved_payload["credit_grant_id"]
            assert approved_payload["decided_amount_usd"] == 7.0

            billing = await client.get("/api/billing/me", headers=auth_headers)
            assert billing.status_code == 200
            assert billing.json()["current_credit_usd"] == 7.0


@pytest.mark.asyncio
async def test_report_markdown_save_api_persists_report_as_source(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/reports/markdown",
                headers=auth_headers,
                json={
                    "document": {
                        "title": "Retrieval Quality Report",
                        "abstract": "A compact report about retrieval behavior.",
                        "sections": [
                            {
                                "title": "Findings",
                                "blocks": [
                                    {
                                        "kind": "paragraph",
                                        "text": "Source-level retrieval keeps file evidence intact.",
                                        "citations": [{"label": "S1", "source_id": "source-alpha"}],
                                    }
                                ],
                            }
                        ],
                        "citations": [{"label": "S1", "source_id": "source-alpha", "note": "Example source"}],
                    },
                    "filename": "retrieval-quality.md",
                },
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["markdown"].startswith("# Retrieval Quality Report")
            assert "[S1](chatkit-link://source?source_id=source-alpha)" in payload["markdown"]
            assert payload["source"]["original_filename"] == "retrieval-quality.md"
            assert payload["source"]["status"] == "processing"
            task = payload["task"]
            assert task["kind"] == "ingest"
            assert task["origin_surface"] == "web"
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=task["id"],
                expected_status="completed",
            )

            detail = await client.get(f"/api/sources/{payload['source']['id']}", headers=auth_headers)
            assert detail.status_code == 200
            detail_payload = detail.json()
            assert detail_payload["status"] == "ready"
            assert detail_payload["media_type"] == "text/markdown"
            assert detail_payload["metadata"]["artifact_kind"] == "report"
            assert detail_payload["metadata"]["report_format"] == "markdown"


@pytest.mark.asyncio
async def test_http_public_library_can_be_selected_without_polluting_personal_library(
    configured_settings: AppSettings,
    fake_openai: None,
    auth_headers: dict[str, str],
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            created = await client.post(
                "/api/libraries",
                headers=auth_headers,
                json={"title": "Open RAGBench demo", "visibility": "public", "slug": "open-ragbench-demo"},
            )
            assert created.status_code == 200
            public_library_id = created.json()["id"]

            libraries = await client.get("/api/libraries", headers=auth_headers)
            assert libraries.status_code == 200
            library_payload = libraries.json()
            assert public_library_id in {library["id"] for library in library_payload["libraries"]}
            assert library_payload["default_library_id"] != public_library_id

            upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={"file": ("public-note.txt", b"Public demo retrieval evidence.", "text/plain")},
                data={"library_id": public_library_id},
            )
            assert upload.status_code == 200
            source_id = upload.json()["source"]["id"]
            await _wait_for_http_task(
                client,
                auth_headers=auth_headers,
                task_id=upload.json()["task"]["id"],
                expected_status="completed",
            )

            personal_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "public demo retrieval", "max_results": 8},
            )
            assert personal_search.status_code == 200
            assert source_id not in {hit["source_file_id"] for hit in personal_search.json()["hits"]}

            public_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"library_id": public_library_id, "query": "public demo retrieval", "max_results": 8},
            )
            assert public_search.status_code == 200
            assert {hit["source_file_id"] for hit in public_search.json()["hits"]} == {source_id}


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
                assert {candidate["status"] for candidate in candidates.json()["candidates"]} == {
                    "ingested",
                    "duplicate",
                }

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
            alpha_tag_slug = alpha_tag.json()["tag"]["slug"]
            bravo_tag_id = bravo_tag.json()["tag"]["id"]
            alpha_upload = await client.post(
                "/api/sources",
                headers=auth_headers,
                files={"file": ("alpha.txt", b"Alpha topic for semantic retrieval.", "text/plain")},
                data={"tag_ids": alpha_tag_slug},
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
                json={"query": "retrieval", "tag_ids": [alpha_tag_slug], "max_results": 8},
            )
            assert alpha_search.status_code == 200
            assert {hit["source_file_id"] for hit in alpha_search.json()["hits"]} == {alpha_source_id}

            bravo_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "retrieval", "tag_ids": [bravo_tag_id], "max_results": 8},
            )
            assert bravo_search.status_code == 200
            assert {hit["source_file_id"] for hit in bravo_search.json()["hits"]} == {bravo_source_id}

            source_scoped_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={"query": "retrieval", "selected_source_ids": [bravo_source_id], "max_results": 8},
            )
            assert source_scoped_search.status_code == 200
            assert {hit["source_file_id"] for hit in source_scoped_search.json()["hits"]} == {bravo_source_id}

            entry_scoped_search = await client.post(
                "/api/search",
                headers=auth_headers,
                json={
                    "query": "retrieval",
                    "selected_source_ids": [bravo_payload["source"]["filesystem_entry_id"]],
                    "max_results": 8,
                },
            )
            assert entry_scoped_search.status_code == 200
            assert {hit["source_file_id"] for hit in entry_scoped_search.json()["hits"]} == {bravo_source_id}

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
            renamed_tag_id = renamed_payload["tag"]["id"]
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
                json={"query": "retrieval", "tag_ids": [renamed_tag_id], "max_results": 8},
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
                json={"tag_ids": [manual_tag_id]},
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
            assert after_detail.json()["tags"] == []


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
    assert set(tools) == {
        "open_file_search_ui",
        "library_search",
        "research_library",
        "answer_from_library",
        "manage_library",
    }
    assert tools["open_file_search_ui"].meta is not None
    assert tools["open_file_search_ui"].meta["ui"]["resourceUri"].startswith("ui://prefab/tool/")
    assert tools["open_file_search_ui"].meta["ui"]["resourceUri"].endswith("/renderer.html")
    assert tools["open_file_search_ui"].meta["ui"]["visibility"] == ["model", "app"]
    assert tools["open_file_search_ui"].meta["openai/widgetAccessible"] is True
    assert tools["open_file_search_ui"].meta["fastmcp"]["app"] == "Indexed Files"
    assert tools["open_file_search_ui"].title == "Open File Search UI"
    assert "sources" not in tools
    assert "source_search" not in tools
    assert "retrieve_files" not in tools
    assert "ingest_file_source" not in tools


@pytest.mark.asyncio
async def test_mcp_dev_entrypoint_exports_local_tooling_server(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del configured_settings
    del fake_openai
    get_settings.cache_clear()
    sys.modules.pop("backend.app.mcp.dev_server", None)
    module = import_module("backend.app.mcp.dev_server")
    server = getattr(module, "mcp")
    services = cast(AppServices, getattr(module, "services"))
    try:
        tools = {tool.name for tool in await server.list_tools(run_middleware=False)}
    finally:
        await services.close()
        sys.modules.pop("backend.app.mcp.dev_server", None)
        get_settings.cache_clear()

    assert tools == mcp_tool_names()


def test_http_app_uses_noauth_mcp_server_when_mcp_auth_mode_is_none(
    configured_settings: AppSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeMcpServer:
        def http_app(
            self,
            *,
            path: str,
            transport: str,
            json_response: bool | None = None,
            stateless_http: bool | None = None,
        ) -> Any:
            from starlette.applications import Starlette

            calls.append(f"http_app:{path}:{transport}:{json_response}:{stateless_http}")
            return Starlette()

    def fake_create_mcp_server(settings: AppSettings, services: AppServices) -> FakeMcpServer:
        del settings, services
        calls.append("bearer")
        return FakeMcpServer()

    def fake_create_dev_mcp_server(settings: AppSettings, services: AppServices) -> FakeMcpServer:
        del settings, services
        calls.append("none")
        return FakeMcpServer()

    monkeypatch.setattr("backend.app.main.create_mcp_server", fake_create_mcp_server)
    monkeypatch.setattr("backend.app.main.create_dev_mcp_server", fake_create_dev_mcp_server)

    create_fastapi_app(configured_settings.model_copy(update={"mcp_auth_mode": "none"}))

    assert calls == [
        "none",
        "http_app:/:streamable-http:None:None",
        "http_app:/:streamable-http:True:True",
    ]


@pytest.mark.asyncio
async def test_http_mcp_uses_stateless_fallback_for_missing_session_tool_calls(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    settings = configured_settings.model_copy(update={"mcp_auth_mode": "none"})
    app = create_fastapi_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/mcp/",
                headers={"accept": "application/json, text/event-stream"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "open_file_search_ui", "arguments": {}},
                },
            )

    assert response.status_code == 200
    assert "Missing session ID" not in response.text
    assert "File Search" in response.text


@pytest.mark.asyncio
async def test_mcp_protected_resource_metadata_is_json_when_configured(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    settings = configured_settings.model_copy(
        update={
            "app_base_url": "https://vectorstore.example.com",
            "mcp_authorization_servers": ["https://auth.example.com"],
            "mcp_required_scopes": ["profile", "email"],
        }
    )
    app = create_fastapi_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    metadata = response.json()
    assert metadata == {
        "resource": "https://vectorstore.example.com/mcp",
        "authorization_servers": ["https://vectorstore.example.com"],
        "token_types_supported": ["urn:ietf:params:oauth:token-type:access_token"],
        "token_introspection_endpoint": "https://auth.example.com/oauth/token",
        "token_introspection_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
        ],
        "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
        "authorization_data_types_supported": ["oauth_scope"],
        "authorization_data_locations_supported": ["header", "body"],
        "key_challenges_supported": [
            {
                "challenge_type": "urn:ietf:params:oauth:pkce:code_challenge",
                "challenge_algs": ["S256"],
            }
        ],
        "service_documentation": "https://clerk.com/docs",
        "scopes_supported": ["profile", "email"],
        "resource_name": settings.app_name,
    }


@pytest.mark.asyncio
async def test_mcp_oauth_metadata_options_uses_public_cors(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    settings = configured_settings.model_copy(
        update={
            "app_base_url": "https://vectorstore.example.com",
            "mcp_authorization_servers": ["https://auth.example.com"],
        }
    )
    app = create_fastapi_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.options(
                "/.well-known/oauth-protected-resource/mcp/",
                headers={
                    "origin": "https://chatgpt.com",
                    "access-control-request-method": "GET",
                },
            )

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["access-control-allow-methods"] == "GET, OPTIONS"


@pytest.mark.asyncio
async def test_mcp_authorization_server_metadata_proxies_configured_issuer(
    configured_settings: AppSettings,
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_openai
    requested_urls: list[str] = []

    class FakeAuthorizationMetadataClient:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            assert timeout == 10.0
            assert follow_redirects is True

        async def __aenter__(self) -> "FakeAuthorizationMetadataClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            del exc_type, exc, traceback

        async def get(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
            assert headers == {"accept": "application/json"}
            requested_urls.append(url)
            return httpx.Response(
                status_code=200,
                json={
                    "issuer": "https://auth.example.com",
                    "authorization_endpoint": "https://auth.example.com/oauth/authorize",
                    "token_endpoint": "https://auth.example.com/oauth/token",
                    "registration_endpoint": "https://auth.example.com/oauth/register",
                    "code_challenge_methods_supported": ["S256"],
                    "scopes_supported": ["email", "profile"],
                },
            )

    monkeypatch.setattr("backend.app.main.HttpAsyncClient", FakeAuthorizationMetadataClient)
    settings = configured_settings.model_copy(
        update={
            "app_base_url": "https://vectorstore.example.com",
            "mcp_authorization_servers": ["https://auth.example.com"],
        }
    )
    app = create_fastapi_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/.well-known/oauth-authorization-server",
                headers={"origin": "https://chatgpt.com"},
            )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers
    assert requested_urls == ["https://auth.example.com/.well-known/oauth-authorization-server"]
    metadata = response.json()
    assert metadata["issuer"] == "https://auth.example.com"
    assert metadata["registration_endpoint"] == "https://auth.example.com/oauth/register"
    assert metadata["scopes_supported"] == settings.mcp_required_scopes


@pytest.mark.asyncio
async def test_mcp_openid_metadata_aliases_proxy_configured_issuer(
    configured_settings: AppSettings,
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_openai
    requested_urls: list[str] = []

    class FakeAuthorizationMetadataClient:
        def __init__(self, *, timeout: float, follow_redirects: bool) -> None:
            assert timeout == 10.0
            assert follow_redirects is True

        async def __aenter__(self) -> "FakeAuthorizationMetadataClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
            del exc_type, exc, traceback

        async def get(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
            assert headers == {"accept": "application/json"}
            requested_urls.append(url)
            return httpx.Response(
                status_code=200,
                json={
                    "issuer": "https://auth.example.com",
                    "authorization_endpoint": "https://auth.example.com/oauth/authorize",
                    "token_endpoint": "https://auth.example.com/oauth/token",
                    "registration_endpoint": "https://auth.example.com/oauth/register",
                    "code_challenge_methods_supported": ["S256"],
                    "scopes_supported": ["openid", "email", "profile"],
                },
            )

    monkeypatch.setattr("backend.app.main.HttpAsyncClient", FakeAuthorizationMetadataClient)
    settings = configured_settings.model_copy(
        update={
            "app_base_url": "https://vectorstore.example.com",
            "mcp_authorization_servers": ["https://auth.example.com"],
        }
    )
    app = create_fastapi_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            root_response = await client.get("/.well-known/openid-configuration")
            mcp_response = await client.get("/mcp/.well-known/openid-configuration")

    assert root_response.status_code == 200
    assert mcp_response.status_code == 200
    assert root_response.json()["registration_endpoint"] == "https://auth.example.com/oauth/register"
    assert mcp_response.json()["registration_endpoint"] == "https://auth.example.com/oauth/register"
    assert root_response.json()["scopes_supported"] == settings.mcp_required_scopes
    assert mcp_response.json()["scopes_supported"] == settings.mcp_required_scopes
    assert requested_urls == [
        "https://auth.example.com/.well-known/oauth-authorization-server",
        "https://auth.example.com/.well-known/oauth-authorization-server",
    ]

@pytest.mark.asyncio
async def test_unknown_well_known_paths_do_not_return_spa_shell(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    app = create_fastapi_app(configured_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/.well-known/not-a-real-metadata-document")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.asyncio
async def test_mcp_sources_ui_resource_renders_explorer_sections(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    server = create_mcp_server(configured_settings, services)
    try:
        result = await server.call_tool("open_file_search_ui", {}, run_middleware=False)
        serialized = json.dumps(result.structured_content, sort_keys=True, default=str)
        assert "File Search" in serialized
        assert '"tool": "open_file_search_ui"' in serialized
        assert '"action": "search"' in serialized
        assert "run_file_search_for_ui" not in serialized
        assert '"type": "Table"' in serialized
        assert "Score" in serialized
        assert "Match" in serialized
        assert "selectedFiles" in serialized
        assert "selection_action" not in serialized
        assert "Rank" not in serialized
        assert "Summary" not in serialized
        assert "Description" not in serialized
        assert '"label": "[x]"' not in serialized
        assert '"label": "[ ]"' not in serialized
        assert '"type": "Checkbox"' not in serialized
        assert '"selectedSourceIds": []' in serialized
    finally:
        await services.close()


@pytest.mark.asyncio
async def test_mcp_sources_ui_search_respects_search_request_limit(
    configured_settings: AppSettings,
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_openai
    captured_max_results: list[int] = []

    async def fake_search(*, clerk_user_id: str, request: Any, origin_surface: str) -> SimpleNamespace:
        assert clerk_user_id == "local-dev"
        assert origin_surface == "mcp_app"
        captured_max_results.append(request.max_results)
        return SimpleNamespace(query=request.query, hits=[])

    services = create_services(configured_settings)
    monkeypatch.setattr("backend.app.mcp.server.current_mcp_clerk_user_id", lambda: "local-dev")
    monkeypatch.setattr(services.sources, "search", fake_search)
    server = create_mcp_server(configured_settings, services)
    try:
        result = await server.call_tool(
            "open_file_search_ui",
            {"action": "search", "query": "fine tuning"},
            run_middleware=False,
        )
    finally:
        await services.close()

    assert result.structured_content is not None
    assert captured_max_results == [24]
    assert result.structured_content["message"] == "No matching files."


@pytest.mark.asyncio
async def test_mcp_sources_ui_search_keeps_top_ten_by_score(
    configured_settings: AppSettings,
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_openai
    selected_files = [
        {
            "source_id": f"src_{index}",
            "title": f"Source {index}",
            "summary": "Already selected.",
            "preview": "Already selected.",
            "relevance_score": index / 10,
        }
        for index in range(1, 11)
    ]

    async def fake_search(*, clerk_user_id: str, request: Any, origin_surface: str) -> SimpleNamespace:
        assert clerk_user_id == "local-dev"
        assert origin_surface == "mcp_app"
        assert request.max_results == 24
        return SimpleNamespace(
            query=request.query,
            hits=[
                SimpleNamespace(
                    chunk_id="chunk_high",
                    source_file_id="src_high",
                    source_title="High Score Source",
                    original_filename="high-score.pdf",
                    score=0.95,
                    title="Matched section",
                    summary="OpenAI vector-store match from the indexed source file.",
                    text="A useful matched snippet that explains why this file belongs in the kept set.",
                    tags=[],
                )
            ],
        )

    services = create_services(configured_settings)
    monkeypatch.setattr("backend.app.mcp.server.current_mcp_clerk_user_id", lambda: "local-dev")
    monkeypatch.setattr(services.sources, "search", fake_search)
    server = create_mcp_server(configured_settings, services)
    try:
        result = await server.call_tool(
            "open_file_search_ui",
            {
                "action": "search",
                "query": "fine tuning",
                "selected_files": selected_files,
            },
            run_middleware=False,
        )
    finally:
        await services.close()

    assert result.structured_content is not None
    source_ids = result.structured_content["selected_source_ids"]
    assert len(source_ids) == 10
    assert source_ids[:2] == ["src_10", "src_high"]
    assert "src_1" not in source_ids
    assert result.structured_content["results"][1]["preview"].startswith("Matched section:")


@pytest.mark.asyncio
async def test_mcp_agent_facade_tool_runs_through_agents_sdk(
    configured_settings: AppSettings,
    fake_openai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del fake_openai
    async def fake_run(starting_agent: Any, input: Any, **kwargs: Any) -> Any:
        context = kwargs["context"]
        context.operations.append({"operation": "fake_library_search", "summary": {"ok": True}})
        assert starting_agent.name == "library_search_intake"
        assert "hand" in str(input)
        return SimpleNamespace(
            final_output="Fake library-search facade result.",
            last_agent=SimpleNamespace(name="library_search_subagent"),
            last_response_id="resp_fake_facade",
        )

    monkeypatch.setattr("backend.app.mcp.agent_facade.Runner.run", fake_run)
    services = create_services(configured_settings)
    server = create_mcp_server(configured_settings, services)
    try:
        result = await server.call_tool(
            "library_search",
            {"instruction": "Find semantic retrieval notes.", "query": "semantic retrieval"},
            run_middleware=False,
        )
    finally:
        await services.close()

    assert result.structured_content is not None
    assert result.structured_content["facade"] == "library_search"
    assert result.structured_content["last_agent"] == "library_search_subagent"
    assert result.structured_content["operations"] == [
        {"operation": "fake_library_search", "summary": {"ok": True}}
    ]
    assert getattr(result.content[0], "text", None) == "Fake library-search facade result."


@pytest.mark.asyncio
async def test_mcp_split_preview_is_inspect_only(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    try:
        result = await services.sources.preview_semantic_split(
            clerk_user_id="local-dev",
            filename="mcp-preview.txt",
            declared_media_type="text/plain",
            payload=b"MCP preview should not publish a source.",
            user_guidance=None,
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

    assert result.split.chunks
    assert tasks.tasks == []
    assert sources.total_count == 0


@pytest.mark.asyncio
async def test_mcp_file_ingest_tool_runs_against_app_core(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    try:
        payload = await services.sources.ingest_source(
            clerk_user_id="local-dev",
            filename="mcp-note.txt",
            declared_media_type="text/plain",
            payload=b"MCP file ingest should reach the app core.",
            tag_ids=[],
            user_guidance=None,
            origin_surface="mcp",
        )
        assert payload.task is not None
        completed_task = await _wait_for_service_task(
            services,
            task_id=payload.task.id,
            expected_status="completed",
        )
        source_detail = await services.sources.get_source(
            clerk_user_id="local-dev",
            source_id=payload.source.id,
        )
    finally:
        await services.close()

    assert payload.source.status == "processing"
    assert source_detail.status == "ready"
    assert payload.source.source_kind == "text"
    assert payload.task.kind == "ingest"
    assert completed_task.origin_surface == "mcp"


@pytest.mark.asyncio
async def test_mcp_retrieve_files_returns_embedded_file_resources(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    file_payload = b"MCP retrieve_files should return the stored source content."
    try:
        ingest = await services.sources.ingest_source(
            clerk_user_id="local-dev",
            filename="retrievable-note.txt",
            declared_media_type="text/plain",
            payload=file_payload,
            tag_ids=[],
            user_guidance=None,
            origin_surface="mcp",
        )
        assert ingest.task is not None
        await _wait_for_service_task(
            services,
            task_id=ingest.task.id,
            expected_status="completed",
        )
        source_id = ingest.source.id

        detail = await services.sources.get_source(clerk_user_id="local-dev", source_id=source_id)
        from backend.app.mcp.server import _retrieve_files_result  # pyright: ignore[reportPrivateUsage]

        retrieved = await _retrieve_files_result(
            services=services,
            clerk_user_id="local-dev",
            source_ids=[source_id],
            include_extracted_text=True,
            max_bytes_per_file=2_000_000,
            max_extracted_chars_per_file=120_000,
        )
    finally:
        await services.close()

    assert detail.content_retrieval_tool == "retrieve_files"
    assert detail.content_retrieval_source_ids == [source_id]
    assert detail.download_url is not None
    assert detail.download_url.startswith(("http://", "https://"))
    assert retrieved.structured_content is not None
    [file_metadata] = retrieved.structured_content["files"]
    assert file_metadata["source_id"] == source_id
    assert file_metadata["content_kind"] == "text"
    assert file_metadata["original_truncated"] is False
    assert len(retrieved.content) >= 2
    original_resource = next(item for item in retrieved.content if getattr(item, "type", None) == "resource")
    assert getattr(cast(Any, original_resource).resource, "text") == file_payload.decode("utf-8")


@pytest.mark.asyncio
async def test_source_file_inputs_skip_large_selected_files(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    small_payload = b"small selected source."
    oversized_payload = b"x" * 80
    second_payload = b"second selected source that fits."
    try:
        small = await services.sources.ingest_source(
            clerk_user_id="local-dev",
            filename="small-selection.txt",
            declared_media_type="text/plain",
            payload=small_payload,
            tag_ids=[],
            user_guidance=None,
            origin_surface="system",
        )
        oversized = await services.sources.ingest_source(
            clerk_user_id="local-dev",
            filename="oversized-selection.txt",
            declared_media_type="text/plain",
            payload=oversized_payload,
            tag_ids=[],
            user_guidance=None,
            origin_surface="system",
        )
        second = await services.sources.ingest_source(
            clerk_user_id="local-dev",
            filename="second-selection.txt",
            declared_media_type="text/plain",
            payload=second_payload,
            tag_ids=[],
            user_guidance=None,
            origin_surface="system",
        )
        for response in (small, oversized, second):
            assert response.task is not None
            await _wait_for_service_task(services, task_id=response.task.id, expected_status="completed")

        capped_by_file = await services.sources.ensure_source_file_inputs(
            clerk_user_id="local-dev",
            source_ids=[oversized.source.id, small.source.id, second.source.id],
            limit=2,
            max_file_bytes=64,
            max_total_bytes=128,
        )
        capped_by_total = await services.sources.ensure_source_file_inputs(
            clerk_user_id="local-dev",
            source_ids=[small.source.id, second.source.id],
            limit=3,
            max_total_bytes=len(small_payload) + 5,
        )
    finally:
        await services.close()

    assert [item.source_id for item in capped_by_file] == [small.source.id, second.source.id]
    assert [item.byte_size for item in capped_by_file] == [len(small_payload), len(second_payload)]
    assert [item.source_id for item in capped_by_total] == [small.source.id]


@pytest.mark.asyncio
async def test_mcp_answer_research_library_uses_scoped_sources(
    configured_settings: AppSettings,
    fake_openai: None,
) -> None:
    del fake_openai
    services = create_services(configured_settings)
    try:
        ingest = await services.sources.ingest_source(
            clerk_user_id="local-dev",
            filename="research-answer-note.txt",
            declared_media_type="text/plain",
            payload=b"Research answer source mentions Cobalt Maple evidence.",
            tag_ids=[],
            user_guidance=None,
            origin_surface="mcp",
        )
        assert ingest.task is not None
        await _wait_for_service_task(
            services,
            task_id=ingest.task.id,
            expected_status="completed",
        )

        answer = await services.actions.qa(
            clerk_user_id="local-dev",
            payload=QaRequest(prompt="What evidence is available?", selected_source_ids=[ingest.source.id]),
            origin_surface="mcp",
        )
    finally:
        await services.close()

    assert answer.kind == "qa"
    assert answer.answer is not None
    assert "Fake grounded answer" in answer.answer
    assert answer.hits
    assert {hit.source_file_id for hit in answer.hits} == {ingest.source.id}


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
            role="admin",
            credit_floor_usd=-1.0,
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
async def test_chatkit_thread_metadata_persists_origin_without_file_selection_scope(
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
            role="admin",
            credit_floor_usd=-1.0,
            bearer_token="local-dev",
        )
        context.thread_origin = "web"
        thread = ThreadMetadata(
            id="chat_scope_metadata_test",
            created_at=datetime.now(UTC),
            metadata={
                "existing": "value",
                "openai_conversation_id": "conv_scope_test",
                "openai_previous_response_id": "resp_scope_test",
            },
        )

        await services.chatkit_server.store.save_thread(thread, context=context)
        loaded = await services.chatkit_server.store.load_thread(thread.id, context=context)

        assert loaded.metadata["existing"] == "value"
        assert "selected_source_ids" not in loaded.metadata
        assert "selected_source_count" not in loaded.metadata
        assert loaded.metadata["scope_origin"] == "web"
        assert isinstance(loaded.metadata["scope_updated_at"], str)
        assert loaded.metadata["openai_conversation_id"] == "conv_scope_test"
        assert loaded.metadata["openai_previous_response_id"] == "resp_scope_test"
    finally:
        await services.close()


@pytest.mark.asyncio
async def test_chatkit_store_compacts_thread_items_without_deleting_originals(
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
            role="admin",
            credit_floor_usd=-1.0,
            bearer_token="local-dev",
        )
        thread = ThreadMetadata(id="chat_compaction_test", created_at=datetime.now(UTC), metadata={})
        await services.chatkit_server.store.save_thread(thread, context=context)
        user_1 = UserMessageItem(
            id="chat_compact_user_1",
            thread_id=thread.id,
            created_at=datetime.now(UTC),
            content=[UserMessageTextContent(text="First long user turn")],
            inference_options=InferenceOptions(),
        )
        assistant_1 = AssistantMessageItem(
            id="chat_compact_assistant_1",
            thread_id=thread.id,
            created_at=datetime.now(UTC),
            content=[AssistantMessageContent(text="First long assistant answer")],
        )
        user_2 = UserMessageItem(
            id="chat_compact_user_2",
            thread_id=thread.id,
            created_at=datetime.now(UTC),
            content=[UserMessageTextContent(text="Current active question")],
            inference_options=InferenceOptions(),
        )
        summary = AssistantMessageItem(
            id="chat_compact_summary_1",
            thread_id=thread.id,
            created_at=datetime.now(UTC),
            content=[AssistantMessageContent(text="## Data\n- source_a remains relevant")],
        )
        await services.chatkit_server.store.save_item(thread.id, user_1, context=context)
        await services.chatkit_server.store.save_item(thread.id, assistant_1, context=context)
        await services.chatkit_server.store.save_item(thread.id, user_2, context=context)

        chat_store = cast(VectorstoreChatStore, services.chatkit_server.store)
        compacted_count = await chat_store.compact_thread_items(
            thread_id=thread.id,
            item_ids=[user_1.id, assistant_1.id],
            summary_item=summary,
            compaction_group_id="compact_group_1",
            context=context,
        )
        active_page = await services.chatkit_server.store.load_thread_items(
            thread.id,
            after=None,
            limit=10,
            order="asc",
            context=context,
        )

        assert compacted_count == 2
        assert [item.id for item in active_page.data] == [summary.id, user_2.id]
        async with services.database.session() as session:
            compacted_entries = list(
                (
                    await session.execute(
                        select(AppChatEntry)
                        .where(AppChatEntry.thread_id == thread.id)
                        .order_by(AppChatEntry.sequence.asc())
                    )
                )
                .scalars()
                .all()
            )
            assert [entry.id for entry in compacted_entries] == [
                user_1.id,
                assistant_1.id,
                summary.id,
                user_2.id,
            ]
            assert [entry.visibility for entry in compacted_entries] == [
                "compacted",
                "compacted",
                "active",
                "active",
            ]
            assert compacted_entries[0].compaction_group_id == "compact_group_1"
            assert compacted_entries[0].compacted_at is not None
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

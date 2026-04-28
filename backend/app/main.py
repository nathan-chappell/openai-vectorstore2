from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
import logging
import mimetypes
import os
from pathlib import Path
from time import perf_counter
from typing import Literal

from chatkit.server import StreamingResult
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
import uvicorn

from backend.app.admin import payment_integration_status
from backend.app.bootstrap import create_services
from backend.app.core.config import AppSettings, get_settings
from backend.app.mcp.server import create_dev_mcp_server, create_mcp_server
from backend.app.schemas import (
    ActionResponse,
    AdminFreeCreditDecisionRequest,
    AdminGrantCreditRequest,
    AdminGrantCreditResponse,
    AdminSetUserActiveRequest,
    AdminSetUserActiveResponse,
    AdminUserListResponse,
    AdminUserSummary,
    AdminPaymentAttemptDecisionRequest,
    AuthUser,
    BillingStatusResponse,
    BranchSearchRequest,
    BranchSearchResponse,
    FileListResponse,
    FilesystemCreateFolderRequest,
    FilesystemDeleteRequest,
    FilesystemDeleteResponse,
    FilesystemEntrySummary,
    FilesystemListResponse,
    FilesystemSearchResponse,
    FilesystemUpdateEntryRequest,
    FreeCreditRequestCreate,
    FreeCreditRequestListResponse,
    FreeCreditRequestStatus,
    FreeCreditRequestSummary,
    FreeformRequest,
    ImageGenerationRequest,
    IngestFinalizeResponse,
    LibraryCreateRequest,
    LibraryListResponse,
    LibrarySummary,
    LibrarySourceDetail,
    PaymentAttemptListResponse,
    PaymentAttemptStatus,
    PaymentAttemptSummary,
    PaymentIntegrationResponse,
    PayPalPaymentAttemptCreateRequest,
    QaRequest,
    ResearchCandidateIngestRequest,
    ResearchCandidateIngestResponse,
    ResearchCandidateListResponse,
    ResearchCandidateStatus,
    ResearchCandidateStatusUpdateRequest,
    ResearchCandidateStatusUpdateResponse,
    ResearchImportCreateRequest,
    ResearchImportResponse,
    ResearchLibraryBuildRequest,
    ResearchLibraryBuildResponse,
    ResplitSourceRequest,
    ReportMarkdownSaveRequest,
    ReportMarkdownSaveResponse,
    SearchRequest,
    SearchResponse,
    SplitPreviewResponse,
    TagCreateRequest,
    TagMutationResponse,
    TagSummary,
    TagUpdateRequest,
    TaskDetail,
    TaskKind,
    TaskListResponse,
    SourceTagsUpdateRequest,
    VoiceGenerationRequest,
)
from backend.app.services.reports import save_report_markdown_source
from backend.app.services import AuthenticatedUser
from backend.app.web_auth import (
    require_active_web_user,
    require_admin_web_user,
    require_authenticated_web_user,
    require_billable_web_user,
)

logger = logging.getLogger(__name__)


def _app_version() -> str:
    try:
        return version("openai-vectorstore2")
    except PackageNotFoundError:
        return "unknown"


def create_fastapi_app(settings: AppSettings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    services = create_services(resolved_settings)
    mcp_server = (
        create_dev_mcp_server(resolved_settings, services)
        if resolved_settings.mcp_auth_mode == "none"
        else create_mcp_server(resolved_settings, services)
    )
    mcp_http_app = mcp_server.http_app(path="/", transport="streamable-http")
    static_dir = Path(resolved_settings.normalized_static_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.services = services
        app.state.mcp_server = mcp_server
        await services.database.ensure_ready()
        logger.info("app_started name=%s version=%s", resolved_settings.app_name, _app_version())
        if resolved_settings.mcp_auth_mode == "none":
            logger.warning("mcp_auth_disabled mode=none subject=local-dev")
        async with mcp_http_app.lifespan(mcp_http_app):
            try:
                yield
            finally:
                await services.close()

    app = FastAPI(title=resolved_settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )

    @app.middleware("http")
    async def log_http_request(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started_at = perf_counter()
        method = request.method
        path = request.url.path
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "%s %s failed (%.1fms)",
                method,
                path,
                (perf_counter() - started_at) * 1000,
            )
            raise

        duration_ms = (perf_counter() - started_at) * 1000
        log_method = logger.info
        if response.status_code >= 500:
            log_method = logger.error
        elif response.status_code >= 400:
            log_method = logger.warning
        if 200 <= response.status_code < 300:
            log_method("%s %s (%.1fms)", method, path, duration_ms)
        else:
            log_method("%s %s -> %s (%.1fms)", method, path, response.status_code, duration_ms)
        return response

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE", "HEAD", "OPTIONS"], include_in_schema=False)
    async def mcp_root_redirect(request: Request) -> RedirectResponse:
        query_string = request.url.query
        target = "/mcp/"
        if query_string:
            target = f"{target}?{query_string}"
        return RedirectResponse(url=target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    app.mount("/mcp", mcp_http_app)

    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource/", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource/mcp/", include_in_schema=False)
    async def mcp_protected_resource_metadata() -> dict[str, object]:
        if resolved_settings.mcp_auth_mode == "none":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP auth is disabled.")
        if not resolved_settings.mcp_authorization_servers:
            raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="MCP OAuth is not configured.")
        return {
            "resource": f"{resolved_settings.normalized_app_base_url}/mcp",
            "authorization_servers": resolved_settings.mcp_authorization_servers,
            "scopes_supported": resolved_settings.mcp_required_scopes,
            "resource_name": resolved_settings.app_name,
        }

    @app.get("/health")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/client-config")
    async def client_config_api() -> dict[str, str | None]:
        return {
            "chatkit_domain_key": resolved_settings.chatkit_domain_key,
            "clerk_publishable_key": resolved_settings.clerk_publishable_key,
        }

    @app.get("/api/auth/me")
    async def auth_me_api(user: AuthenticatedUser = Depends(require_authenticated_web_user)) -> AuthUser:
        billing = await services.billing.get_status(
            clerk_user_id=user.clerk_user_id,
            credit_floor_usd=user.credit_floor_usd,
            role=user.role,
        )
        return AuthUser(
            clerk_user_id=user.clerk_user_id,
            display_name=user.display_name,
            primary_email=user.email,
            active=user.active,
            role=user.role,
            current_credit_usd=billing.current_credit_usd,
            credit_floor_usd=billing.credit_floor_usd,
        )

    @app.get("/api/billing/me")
    async def billing_me_api(
        user: AuthenticatedUser = Depends(require_authenticated_web_user),
    ) -> BillingStatusResponse:
        billing = await services.billing.get_status(
            clerk_user_id=user.clerk_user_id,
            credit_floor_usd=user.credit_floor_usd,
            role=user.role,
        )
        return BillingStatusResponse(
            **billing.model_dump(),
            active=user.active,
            role=user.role,
            primary_email=user.email,
        )

    @app.get("/api/billing/payment-status")
    async def billing_payment_status_api(
        _: AuthenticatedUser = Depends(require_authenticated_web_user),
    ) -> PaymentIntegrationResponse:
        integration = payment_integration_status(resolved_settings)
        paypal_recipient_email = (
            resolved_settings.paypal_recipient_email.strip() if resolved_settings.paypal_recipient_email else None
        )
        paypal_payment_url = str(resolved_settings.paypal_payment_url) if resolved_settings.paypal_payment_url else None
        return PaymentIntegrationResponse(
            provider="paypal" if paypal_recipient_email else integration.provider,
            checkout_enabled=integration.checkout_enabled,
            receipt_upload_enabled=paypal_recipient_email is not None,
            reason=(
                "Send a PayPal payment, include the generated reference code, then upload the receipt for immediate receipt-backed credit."
                if paypal_recipient_email
                else integration.reason
            ),
            paypal_recipient_email=paypal_recipient_email,
            paypal_payment_url=paypal_payment_url,
            min_payment_usd=resolved_settings.paypal_min_payment_usd,
            max_payment_usd=resolved_settings.paypal_max_payment_usd,
        )

    @app.get("/api/billing/paypal/attempts")
    async def list_paypal_attempts_api(
        user: AuthenticatedUser = Depends(require_authenticated_web_user),
    ) -> PaymentAttemptListResponse:
        attempts = await services.payments.list_user_attempts(clerk_user_id=user.clerk_user_id)
        return PaymentAttemptListResponse(attempts=attempts)

    @app.post("/api/billing/paypal/attempts")
    async def create_paypal_attempt_api(
        payload: PayPalPaymentAttemptCreateRequest,
        user: AuthenticatedUser = Depends(require_authenticated_web_user),
    ) -> PaymentAttemptSummary:
        try:
            return await services.payments.create_paypal_attempt(
                clerk_user_id=user.clerk_user_id,
                expected_amount_usd=payload.expected_amount_usd,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.post("/api/billing/paypal/attempts/{attempt_id}/receipt")
    async def upload_paypal_receipt_api(
        attempt_id: str,
        file: UploadFile = File(...),
        user: AuthenticatedUser = Depends(require_authenticated_web_user),
    ) -> PaymentAttemptSummary:
        payload = await file.read()
        if len(payload) > 5_000_000:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Receipt upload is too large."
            )
        try:
            return await services.payments.review_receipt_upload(
                clerk_user_id=user.clerk_user_id,
                attempt_id=attempt_id,
                filename=file.filename or "receipt",
                media_type=file.content_type,
                payload=payload,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.get("/api/billing/free-credit-requests")
    async def list_free_credit_requests_api(
        user: AuthenticatedUser = Depends(require_authenticated_web_user),
    ) -> FreeCreditRequestListResponse:
        requests = await services.free_credits.list_user_requests(clerk_user_id=user.clerk_user_id)
        return FreeCreditRequestListResponse(requests=requests)

    @app.post("/api/billing/free-credit-requests")
    async def create_free_credit_request_api(
        payload: FreeCreditRequestCreate,
        user: AuthenticatedUser = Depends(require_authenticated_web_user),
    ) -> FreeCreditRequestSummary:
        try:
            return await services.free_credits.create_request(clerk_user_id=user.clerk_user_id, payload=payload)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @app.post("/api/admin/credits/grant")
    async def admin_grant_credit_api(
        payload: AdminGrantCreditRequest,
        admin: AuthenticatedUser = Depends(require_admin_web_user),
    ) -> AdminGrantCreditResponse:
        target_record = await services.auth.get_user_record(payload.clerk_user_id)
        balance, grant = await services.billing.grant_credit(
            clerk_user_id=payload.clerk_user_id,
            credit_amount_usd=payload.credit_amount_usd,
            admin_clerk_user_id=admin.clerk_user_id,
            note=payload.note,
            credit_floor_usd=target_record.credit_floor_usd,
            role=target_record.role,
        )
        return AdminGrantCreditResponse(balance=balance, grant=grant)

    @app.get("/api/admin/payments")
    async def admin_list_payment_attempts_api(
        _: AuthenticatedUser = Depends(require_admin_web_user),
        status_filter: PaymentAttemptStatus | None = Query(default=None, alias="status"),
    ) -> PaymentAttemptListResponse:
        attempts = await services.payments.list_admin_attempts(status=status_filter)
        return PaymentAttemptListResponse(attempts=attempts)

    @app.get("/api/admin/free-credit-requests")
    async def admin_list_free_credit_requests_api(
        _: AuthenticatedUser = Depends(require_admin_web_user),
        status_filter: FreeCreditRequestStatus | None = Query(default=None, alias="status"),
    ) -> FreeCreditRequestListResponse:
        requests = await services.free_credits.list_admin_requests(status=status_filter)
        return FreeCreditRequestListResponse(requests=requests)

    @app.post("/api/admin/free-credit-requests/decide")
    async def admin_decide_free_credit_request_api(
        payload: AdminFreeCreditDecisionRequest,
        admin: AuthenticatedUser = Depends(require_admin_web_user),
    ) -> FreeCreditRequestSummary:
        try:
            return await services.free_credits.decide_admin_request(
                request_id=payload.request_id,
                admin_clerk_user_id=admin.clerk_user_id,
                status=payload.status,
                decision_note=payload.decision_note,
                credit_amount_usd=payload.credit_amount_usd,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post("/api/admin/payments/decide")
    async def admin_decide_payment_attempt_api(
        payload: AdminPaymentAttemptDecisionRequest,
        admin: AuthenticatedUser = Depends(require_admin_web_user),
    ) -> PaymentAttemptSummary:
        try:
            return await services.payments.decide_admin_attempt(
                attempt_id=payload.attempt_id,
                admin_clerk_user_id=admin.clerk_user_id,
                status=payload.status,
                decision_note=payload.decision_note,
                credit_amount_usd=payload.credit_amount_usd,
                provider_reference=payload.provider_reference,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/api/admin/users")
    async def admin_list_users_api(
        admin: AuthenticatedUser = Depends(require_admin_web_user),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        query: str | None = Query(default=None, max_length=200),
    ) -> AdminUserListResponse:
        del admin
        records = await services.auth.list_user_records(limit=limit, offset=offset, query=query)
        balances = await services.billing.list_balance_amounts([record.clerk_user_id for record in records])
        return AdminUserListResponse(
            items=[
                AdminUserSummary(
                    clerk_user_id=record.clerk_user_id,
                    primary_email=record.primary_email,
                    display_name=record.display_name,
                    image_url=record.image_url,
                    active=record.active,
                    role=record.role,
                    current_credit_usd=round(float(balances.get(record.clerk_user_id, 0.0)), 8),
                    credit_floor_usd=record.credit_floor_usd,
                    created_at_ms=record.created_at_ms,
                    last_sign_in_at_ms=record.last_sign_in_at_ms,
                )
                for record in records
            ],
            limit=limit,
            offset=offset,
            has_more=len(records) == limit,
            query=query.strip() if isinstance(query, str) and query.strip() else None,
        )

    @app.post("/api/admin/users/set-active")
    async def admin_set_user_active_api(
        payload: AdminSetUserActiveRequest,
        _: AuthenticatedUser = Depends(require_admin_web_user),
    ) -> AdminSetUserActiveResponse:
        target = await services.auth.set_user_active_state(
            clerk_user_id=payload.clerk_user_id,
            active=payload.active,
        )
        balance = await services.billing.get_status(
            clerk_user_id=payload.clerk_user_id,
            credit_floor_usd=target.credit_floor_usd,
            role=target.role,
        )
        return AdminSetUserActiveResponse(
            clerk_user_id=target.clerk_user_id,
            active=target.active,
            current_credit_usd=balance.current_credit_usd,
            credit_floor_usd=balance.credit_floor_usd,
        )

    @app.get("/api/sources")
    async def list_sources_api(
        user: AuthenticatedUser = Depends(require_active_web_user),
        library_id: str | None = Query(default=None),
        query: str | None = Query(default=None, min_length=1),
        tag_ids: list[str] | None = Query(default=None),
        tag_match_mode: Literal["all", "any"] = Query(default="all"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=24, ge=1, le=100),
    ) -> FileListResponse:
        return await services.sources.list_sources(
            clerk_user_id=user.clerk_user_id,
            library_id=library_id,
            query=query,
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/libraries")
    async def list_libraries_api(
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> LibraryListResponse:
        return await services.sources.list_libraries(clerk_user_id=user.clerk_user_id)

    @app.post("/api/libraries")
    async def create_library_api(
        payload: LibraryCreateRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> LibrarySummary:
        try:
            return await services.sources.create_library(clerk_user_id=user.clerk_user_id, payload=payload)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/filesystem")
    async def list_filesystem_api(
        user: AuthenticatedUser = Depends(require_active_web_user),
        library_id: str | None = Query(default=None),
        folder_id: str | None = Query(default=None),
    ) -> FilesystemListResponse:
        try:
            return await services.sources.list_filesystem(
                clerk_user_id=user.clerk_user_id,
                library_id=library_id,
                folder_id=folder_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/api/filesystem/search")
    async def search_filesystem_api(
        user: AuthenticatedUser = Depends(require_billable_web_user),
        library_id: str | None = Query(default=None),
        query: str | None = Query(default=None),
        tag_ids: list[str] | None = Query(default=None),
        tag_match_mode: Literal["all", "any"] = Query(default="all"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
    ) -> FilesystemSearchResponse:
        try:
            return await services.sources.search_filesystem(
                clerk_user_id=user.clerk_user_id,
                library_id=library_id,
                query=query,
                tag_ids=tag_ids or [],
                tag_match_mode=tag_match_mode,
                page=page,
                page_size=page_size,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/filesystem/folders")
    async def create_folder_api(
        payload: FilesystemCreateFolderRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> FilesystemEntrySummary:
        try:
            return await services.sources.create_folder(
                clerk_user_id=user.clerk_user_id,
                library_id=payload.library_id,
                parent_id=payload.parent_id,
                name=payload.name,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.patch("/api/filesystem/entries/{entry_id}")
    async def update_filesystem_entry_api(
        entry_id: str,
        payload: FilesystemUpdateEntryRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> FilesystemEntrySummary:
        try:
            return await services.sources.update_filesystem_entry(
                clerk_user_id=user.clerk_user_id,
                entry_id=entry_id,
                name=payload.name,
                parent_id=payload.parent_id,
                origin_surface="web",
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/filesystem/delete")
    async def delete_filesystem_entries_api(
        payload: FilesystemDeleteRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> FilesystemDeleteResponse:
        try:
            return await services.sources.delete_filesystem_entries(
                clerk_user_id=user.clerk_user_id,
                entry_ids=payload.entry_ids,
                confirm=payload.confirm,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/sources")
    async def upload_source_api(
        file: UploadFile = File(...),
        tag_ids: list[str] | None = Form(default=None),
        user_guidance: str | None = Form(default=None),
        folder_id: str | None = Form(default=None),
        virtual_name: str | None = Form(default=None),
        library_id: str | None = Form(default=None),
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> IngestFinalizeResponse:
        payload = await file.read()
        await file.close()
        try:
            return await services.sources.ingest_source(
                clerk_user_id=user.clerk_user_id,
                filename=file.filename or "upload",
                declared_media_type=file.content_type,
                payload=payload,
                tag_ids=tag_ids or [],
                user_guidance=user_guidance,
                origin_surface="web",
                folder_id=folder_id,
                virtual_name=virtual_name,
                library_id=library_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/sources/split-preview")
    async def preview_source_split_api(
        file: UploadFile = File(...),
        user_guidance: str | None = Form(default=None),
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> SplitPreviewResponse:
        payload = await file.read()
        await file.close()
        try:
            return await services.sources.preview_semantic_split(
                clerk_user_id=user.clerk_user_id,
                filename=file.filename or "preview",
                declared_media_type=file.content_type,
                payload=payload,
                user_guidance=user_guidance,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/sources/{source_id}/resplit")
    async def resplit_source_api(
        source_id: str,
        payload: ResplitSourceRequest,
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> IngestFinalizeResponse:
        try:
            return await services.sources.resplit_source(
                clerk_user_id=user.clerk_user_id,
                source_id=source_id,
                tag_ids=payload.tag_ids,
                user_guidance=payload.user_guidance,
                origin_surface="web",
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/sources/{source_id}/tags")
    async def update_source_tags_api(
        source_id: str,
        payload: SourceTagsUpdateRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> IngestFinalizeResponse:
        try:
            return await services.sources.update_source_tags(
                clerk_user_id=user.clerk_user_id,
                source_id=source_id,
                tag_ids=payload.tag_ids,
                origin_surface="web",
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/sources/{source_id}")
    async def get_source_api(
        source_id: str,
        user: AuthenticatedUser = Depends(require_active_web_user),
        library_id: str | None = Query(default=None),
    ) -> LibrarySourceDetail:
        try:
            return await services.sources.get_source(
                clerk_user_id=user.clerk_user_id,
                source_id=source_id,
                library_id=library_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.delete("/api/sources/{source_id}")
    async def delete_source_api(
        source_id: str,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> dict[str, str]:
        try:
            deleted_id = await services.sources.delete_source(clerk_user_id=user.clerk_user_id, source_id=source_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"deleted_source_id": deleted_id}

    @app.get("/api/sources/{source_id}/content")
    async def read_source_content_api(
        source_id: str,
        user: AuthenticatedUser = Depends(require_active_web_user),
        library_id: str | None = Query(default=None),
    ) -> Response:
        try:
            detail, payload = await services.sources.read_source_bytes(
                clerk_user_id=user.clerk_user_id,
                source_id=source_id,
                library_id=library_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return Response(
            content=payload,
            media_type=detail.media_type,
            headers={"Content-Disposition": f'attachment; filename="{detail.original_filename}"'},
        )

    @app.get("/api/storage/local/{key:path}")
    async def local_storage_api(key: str, filename: str | None = Query(default=None)) -> Response:
        payload = await services.storage.get_bytes(key=key)
        media_type, encoding = mimetypes.guess_type(filename or key)
        headers: dict[str, str] = {}
        if encoding:
            headers["Content-Encoding"] = encoding
        if filename:
            headers["Content-Disposition"] = f'inline; filename="{filename}"'
        return Response(content=payload, media_type=media_type or "application/octet-stream", headers=headers)

    @app.get("/api/tags")
    async def list_tags_api(
        user: AuthenticatedUser = Depends(require_active_web_user),
        library_id: str | None = Query(default=None),
    ) -> list[TagSummary]:
        return await services.sources.list_tags(clerk_user_id=user.clerk_user_id, library_id=library_id)

    @app.post("/api/research/imports")
    async def create_research_import_api(
        payload: ResearchImportCreateRequest,
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> ResearchImportResponse:
        try:
            return await services.research.create_import(
                clerk_user_id=user.clerk_user_id,
                payload=payload,
                origin_surface="web",
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/research/library-builds")
    async def build_research_library_api(
        payload: ResearchLibraryBuildRequest,
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> ResearchLibraryBuildResponse:
        try:
            return await services.research.build_library(
                clerk_user_id=user.clerk_user_id,
                payload=payload,
                origin_surface="web",
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/research/candidates")
    async def list_research_candidates_api(
        user: AuthenticatedUser = Depends(require_active_web_user),
        task_id: str | None = Query(default=None),
        status_filter: ResearchCandidateStatus | None = Query(default=None, alias="status"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=100),
    ) -> ResearchCandidateListResponse:
        return await services.research.list_candidates(
            clerk_user_id=user.clerk_user_id,
            task_id=task_id,
            status=status_filter,
            page=page,
            page_size=page_size,
        )

    @app.post("/api/research/candidates/status")
    async def update_research_candidate_status_api(
        payload: ResearchCandidateStatusUpdateRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> ResearchCandidateStatusUpdateResponse:
        try:
            return await services.research.update_candidate_status(
                clerk_user_id=user.clerk_user_id,
                candidate_ids=payload.candidate_ids,
                status=payload.status,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/api/research/candidates/ingest")
    async def ingest_research_candidates_api(
        payload: ResearchCandidateIngestRequest,
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> ResearchCandidateIngestResponse:
        try:
            return await services.research.ingest_approved_candidates(
                clerk_user_id=user.clerk_user_id,
                payload=payload,
                origin_surface="web",
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/tags")
    async def create_tag_api(
        payload: TagCreateRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> TagMutationResponse:
        try:
            return await services.sources.create_tag(
                clerk_user_id=user.clerk_user_id,
                name=payload.name,
                color=payload.color,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.patch("/api/tags/{tag_id}")
    async def update_tag_api(
        tag_id: str,
        payload: TagUpdateRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> TagMutationResponse:
        try:
            return await services.sources.update_tag(
                clerk_user_id=user.clerk_user_id,
                tag_id=tag_id,
                name=payload.name,
                color=payload.color,
                origin_surface="web",
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.delete("/api/tags/{tag_id}")
    async def delete_tag_api(
        tag_id: str,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> TagMutationResponse:
        try:
            return await services.sources.delete_tag(
                clerk_user_id=user.clerk_user_id,
                tag_id=tag_id,
                origin_surface="web",
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/search")
    async def search_api(
        payload: SearchRequest,
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> SearchResponse:
        return await services.sources.search(clerk_user_id=user.clerk_user_id, request=payload, origin_surface="web")

    @app.post("/api/search/branch")
    async def branch_search_api(
        payload: BranchSearchRequest,
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> BranchSearchResponse:
        return await services.sources.branch_search(clerk_user_id=user.clerk_user_id, request=payload)

    @app.post("/api/actions/qa")
    async def qa_api(
        payload: QaRequest,
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> ActionResponse:
        return await services.actions.qa(clerk_user_id=user.clerk_user_id, payload=payload, origin_surface="web")

    @app.post("/api/actions/freeform")
    async def freeform_api(
        payload: FreeformRequest,
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> ActionResponse:
        return await services.actions.freeform(clerk_user_id=user.clerk_user_id, payload=payload, origin_surface="web")

    @app.post("/api/reports/markdown")
    async def save_report_markdown_api(
        payload: ReportMarkdownSaveRequest,
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> ReportMarkdownSaveResponse:
        try:
            return await save_report_markdown_source(
                sources=services.sources,
                clerk_user_id=user.clerk_user_id,
                request=payload,
                origin_surface="web",
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/actions/image")
    async def image_api(
        payload: ImageGenerationRequest,
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> ActionResponse:
        return await services.actions.image(clerk_user_id=user.clerk_user_id, payload=payload, origin_surface="web")

    @app.post("/api/actions/voice")
    async def voice_api(
        payload: VoiceGenerationRequest,
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> ActionResponse:
        return await services.actions.voice(clerk_user_id=user.clerk_user_id, payload=payload, origin_surface="web")

    @app.get("/api/tasks")
    async def list_tasks_api(
        user: AuthenticatedUser = Depends(require_active_web_user),
        kind: TaskKind | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> TaskListResponse:
        return await services.actions.list_tasks(clerk_user_id=user.clerk_user_id, kind=kind, limit=limit)

    @app.get("/api/tasks/{task_id}")
    async def get_task_api(
        task_id: str,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> TaskDetail:
        try:
            return await services.actions.get_task(clerk_user_id=user.clerk_user_id, task_id=task_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.post("/api/chatkit")
    async def chatkit_entrypoint(
        request: Request,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> Response:
        started_at = perf_counter()
        raw_request = await request.body()
        request_summary = services.chatkit_server.request_log_summary(raw_request)
        request_label = (
            f"{request_summary.op} thread={request_summary.thread_id}"
            if request_summary.thread_id is not None
            else request_summary.op
        )
        logger.info(
            "chatkit %s received user=%s bytes=%s",
            request_label,
            user.clerk_user_id,
            len(raw_request),
        )
        try:
            context = await services.chatkit_server.build_request_context(
                raw_request,
                clerk_user_id=user.clerk_user_id,
                user_email=user.email,
                display_name=user.display_name,
                role=user.role,
                credit_floor_usd=user.credit_floor_usd,
                bearer_token=user.bearer_token,
                request_app=request.app,
            )
            result = await services.chatkit_server.process(raw_request, context)
        except Exception:
            logger.exception(
                "chatkit %s failed user=%s bytes=%s (%.1fms)",
                request_label,
                user.clerk_user_id,
                len(raw_request),
                (perf_counter() - started_at) * 1000,
            )
            raise
        if isinstance(result, StreamingResult):
            logger.info(
                "chatkit %s streaming user=%s bytes=%s (%.1fms)",
                request_label,
                user.clerk_user_id,
                len(raw_request),
                (perf_counter() - started_at) * 1000,
            )
            return StreamingResponse(result, media_type="text/event-stream")
        logger.info(
            "chatkit %s completed user=%s bytes=%s response_bytes=%s (%.1fms)",
            request_label,
            user.clerk_user_id,
            len(raw_request),
            len(result.json),
            (perf_counter() - started_at) * 1000,
        )
        return Response(content=result.json, media_type="application/json")

    @app.post("/api/chatkit/attachments")
    async def upload_chatkit_attachment_api(
        file: UploadFile = File(...),
        tag_ids: list[str] | None = Form(default=None),
        user_guidance: str | None = Form(default=None),
        thread_id: str | None = Form(default=None),
        user: AuthenticatedUser = Depends(require_billable_web_user),
    ) -> Response:
        payload = await file.read()
        await file.close()
        context = services.chatkit_server.build_user_context(
            clerk_user_id=user.clerk_user_id,
            user_email=user.email,
            display_name=user.display_name,
            role=user.role,
            credit_floor_usd=user.credit_floor_usd,
            bearer_token=user.bearer_token,
        )
        try:
            attachment = await services.chatkit_server.create_uploaded_attachment(
                filename=file.filename or "upload",
                declared_media_type=file.content_type,
                payload=payload,
                tag_ids=tag_ids or [],
                user_guidance=user_guidance,
                thread_id=thread_id.strip() if thread_id is not None and thread_id.strip() else None,
                context=context,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return Response(content=attachment.model_dump_json(exclude_none=True), media_type="application/json")

    @app.get("/{full_path:path}")
    async def spa_entrypoint(full_path: str) -> Response:
        if full_path.startswith(".well-known/"):
            raise HTTPException(status_code=404, detail="Well-known metadata not found.")
        index_path = static_dir / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="Frontend build not found.")
        candidate = static_dir / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return _static_file_response(candidate)
        return _static_file_response(index_path)

    return app


def _static_file_response(path: Path) -> Response:
    media_type, encoding = mimetypes.guess_type(path.name)
    headers: dict[str, str] = {}
    if encoding:
        headers["Content-Encoding"] = encoding
    return Response(content=path.read_bytes(), media_type=media_type or "application/octet-stream", headers=headers)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(create_fastapi_app(), host="0.0.0.0", port=port)

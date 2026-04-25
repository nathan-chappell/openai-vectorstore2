from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import mimetypes
from pathlib import Path
from typing import Literal

from chatkit.server import StreamingResult
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
import uvicorn

from backend.app.bootstrap import create_services
from backend.app.core.config import AppSettings, get_settings
from backend.app.mcp.server import create_mcp_server
from backend.app.schemas import (
    ActionResponse,
    AuthUser,
    BranchSearchRequest,
    BranchSearchResponse,
    FileListResponse,
    FreeformRequest,
    ImageGenerationRequest,
    IngestFinalizeResponse,
    LibrarySourceDetail,
    QaRequest,
    SearchRequest,
    SearchResponse,
    SplitPreviewResponse,
    TagSummary,
    TaskDetail,
    TaskKind,
    TaskListResponse,
    VoiceGenerationRequest,
)
from backend.app.services import AuthenticatedUser
from backend.app.web_auth import require_active_web_user, require_authenticated_web_user


def create_fastapi_app(settings: AppSettings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    services = create_services(resolved_settings)
    mcp_server = create_mcp_server(resolved_settings, services)
    mcp_http_app = mcp_server.http_app(path="/", transport="streamable-http")
    static_dir = Path(resolved_settings.normalized_static_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.services = services
        app.state.mcp_server = mcp_server
        await services.database.ensure_ready()
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

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE", "HEAD", "OPTIONS"], include_in_schema=False)
    async def mcp_root_redirect(request: Request) -> RedirectResponse:
        query_string = request.url.query
        target = "/mcp/"
        if query_string:
            target = f"{target}?{query_string}"
        return RedirectResponse(url=target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    app.mount("/mcp", mcp_http_app)

    @app.get("/health")
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/auth/me")
    async def auth_me_api(user: AuthenticatedUser = Depends(require_authenticated_web_user)) -> AuthUser:
        return AuthUser(
            clerk_user_id=user.clerk_user_id,
            display_name=user.display_name,
            primary_email=user.email,
            active=user.active,
            role=user.role,
        )

    @app.get("/api/sources")
    async def list_sources_api(
        user: AuthenticatedUser = Depends(require_active_web_user),
        query: str | None = Query(default=None, min_length=1),
        tag_ids: list[str] | None = Query(default=None),
        tag_match_mode: Literal["all", "any"] = Query(default="all"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=24, ge=1, le=100),
    ) -> FileListResponse:
        return await services.sources.list_sources(
            clerk_user_id=user.clerk_user_id,
            query=query,
            tag_ids=tag_ids or [],
            tag_match_mode=tag_match_mode,
            page=page,
            page_size=page_size,
        )

    @app.post("/api/sources")
    async def upload_source_api(
        file: UploadFile = File(...),
        tag_ids: list[str] | None = Form(default=None),
        user_guidance: str | None = Form(default=None),
        user: AuthenticatedUser = Depends(require_active_web_user),
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
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/sources/split-preview")
    async def preview_source_split_api(
        file: UploadFile = File(...),
        user_guidance: str | None = Form(default=None),
        user: AuthenticatedUser = Depends(require_active_web_user),
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

    @app.get("/api/sources/{source_id}")
    async def get_source_api(
        source_id: str,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> LibrarySourceDetail:
        try:
            return await services.sources.get_source(clerk_user_id=user.clerk_user_id, source_id=source_id)
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
    ) -> Response:
        try:
            detail, payload = await services.sources.read_source_bytes(
                clerk_user_id=user.clerk_user_id, source_id=source_id
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
    async def list_tags_api(user: AuthenticatedUser = Depends(require_active_web_user)) -> list[TagSummary]:
        return await services.sources.list_tags(clerk_user_id=user.clerk_user_id)

    @app.post("/api/search")
    async def search_api(
        payload: SearchRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> SearchResponse:
        return await services.sources.search(clerk_user_id=user.clerk_user_id, request=payload)

    @app.post("/api/search/branch")
    async def branch_search_api(
        payload: BranchSearchRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> BranchSearchResponse:
        return await services.sources.branch_search(clerk_user_id=user.clerk_user_id, request=payload)

    @app.post("/api/actions/qa")
    async def qa_api(
        payload: QaRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> ActionResponse:
        return await services.actions.qa(clerk_user_id=user.clerk_user_id, payload=payload, origin_surface="web")

    @app.post("/api/actions/freeform")
    async def freeform_api(
        payload: FreeformRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> ActionResponse:
        return await services.actions.freeform(clerk_user_id=user.clerk_user_id, payload=payload, origin_surface="web")

    @app.post("/api/actions/image")
    async def image_api(
        payload: ImageGenerationRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
    ) -> ActionResponse:
        return await services.actions.image(clerk_user_id=user.clerk_user_id, payload=payload, origin_surface="web")

    @app.post("/api/actions/voice")
    async def voice_api(
        payload: VoiceGenerationRequest,
        user: AuthenticatedUser = Depends(require_active_web_user),
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
        raw_request = await request.body()
        context = await services.chatkit_server.build_request_context(
            raw_request,
            clerk_user_id=user.clerk_user_id,
            user_email=user.email,
            display_name=user.display_name,
            bearer_token=user.bearer_token,
            request_app=request.app,
        )
        result = await services.chatkit_server.process(raw_request, context)
        if isinstance(result, StreamingResult):
            return StreamingResponse(result, media_type="text/event-stream")
        return Response(content=result.json, media_type="application/json")

    @app.get("/{full_path:path}")
    async def spa_entrypoint(full_path: str) -> Response:
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
    uvicorn.run(create_fastapi_app(), host="0.0.0.0", port=8000)

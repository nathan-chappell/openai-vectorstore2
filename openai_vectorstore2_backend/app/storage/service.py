from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path, PurePosixPath
import re
from typing import Any, Protocol
from uuid import uuid4

from openai_vectorstore2_backend.app.core.config import AppSettings

SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class StoredObject:
    provider: str
    key: str
    byte_size: int
    media_type: str


class StorageService(Protocol):
    provider: str

    async def put_bytes(
        self,
        *,
        scope: str,
        filename: str,
        media_type: str,
        payload: bytes,
    ) -> StoredObject: ...

    async def get_bytes(self, *, key: str) -> bytes: ...

    async def delete_object(self, *, key: str) -> None: ...

    def build_download_url(
        self,
        *,
        key: str,
        filename: str,
        media_type: str,
        inline: bool,
    ) -> str | None: ...


class LocalStorageService:
    provider = "local"

    def __init__(self, root_dir: Path) -> None:
        self._root_dir = root_dir

    async def put_bytes(
        self,
        *,
        scope: str,
        filename: str,
        media_type: str,
        payload: bytes,
    ) -> StoredObject:
        key = _object_key(scope=scope, filename=filename)
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return StoredObject(provider=self.provider, key=key, byte_size=len(payload), media_type=media_type)

    async def get_bytes(self, *, key: str) -> bytes:
        path = self._path_for_key(key)
        return path.read_bytes()

    async def delete_object(self, *, key: str) -> None:
        self._path_for_key(key).unlink(missing_ok=True)

    def build_download_url(
        self,
        *,
        key: str,
        filename: str,
        media_type: str,
        inline: bool,
    ) -> str | None:
        del media_type, inline
        return f"/api/storage/local/{key}?filename={_safe_filename(filename)}"

    def _path_for_key(self, key: str) -> Path:
        normalized = PurePosixPath(key)
        if normalized.is_absolute() or ".." in normalized.parts:
            raise ValueError("Invalid local storage key.")
        return self._root_dir / Path(*normalized.parts)


class S3StorageService:
    provider = "s3"

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._client: Any | None = None

    async def put_bytes(
        self,
        *,
        scope: str,
        filename: str,
        media_type: str,
        payload: bytes,
    ) -> StoredObject:
        key = _object_key(scope=scope, filename=filename)
        await asyncio.to_thread(self._put_bytes_sync, key, payload, media_type)
        return StoredObject(provider=self.provider, key=key, byte_size=len(payload), media_type=media_type)

    async def get_bytes(self, *, key: str) -> bytes:
        return await asyncio.to_thread(self._get_bytes_sync, key)

    async def delete_object(self, *, key: str) -> None:
        await asyncio.to_thread(self._client_or_raise().remove_object, self._bucket(), key)

    def build_download_url(
        self,
        *,
        key: str,
        filename: str,
        media_type: str,
        inline: bool,
    ) -> str | None:
        disposition = "inline" if inline else "attachment"
        safe_filename = _safe_filename(filename)
        response_headers = {
            "response-content-disposition": f'{disposition}; filename="{safe_filename}"',
            "response-content-type": media_type,
        }
        return self._client_or_raise().presigned_get_object(
            self._bucket(),
            key,
            expires=timedelta(seconds=self._settings.storage_download_url_ttl_seconds),
            response_headers=response_headers,
        )

    def _put_bytes_sync(self, key: str, payload: bytes, media_type: str) -> None:
        from io import BytesIO

        self._client_or_raise().put_object(
            self._bucket(),
            key,
            BytesIO(payload),
            length=len(payload),
            content_type=media_type,
        )

    def _get_bytes_sync(self, key: str) -> bytes:
        response = self._client_or_raise().get_object(self._bucket(), key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def _bucket(self) -> str:
        if self._settings.s3_bucket is None or not self._settings.s3_bucket.strip():
            raise RuntimeError("S3 storage is selected but S3_BUCKET is not configured.")
        return self._settings.s3_bucket.strip()

    def _client_or_raise(self) -> Any:
        from minio import Minio

        if self._client is not None:
            return self._client
        if self._settings.s3_endpoint is None or not self._settings.s3_endpoint.strip():
            raise RuntimeError("S3 storage is selected but S3_ENDPOINT is not configured.")
        if self._settings.s3_access_key_id is None or not self._settings.s3_access_key_id.strip():
            raise RuntimeError("S3 storage is selected but S3_ACCESS_KEY_ID is not configured.")
        if self._settings.s3_secret_access_key is None:
            raise RuntimeError("S3 storage is selected but S3_SECRET_ACCESS_KEY is not configured.")
        raw_endpoint = self._settings.s3_endpoint.strip()
        secure = not raw_endpoint.startswith("http://")
        endpoint = raw_endpoint.removeprefix("https://").removeprefix("http://").strip("/")
        client = Minio(
            endpoint,
            access_key=self._settings.s3_access_key_id.strip(),
            secret_key=self._settings.s3_secret_access_key.get_secret_value(),
            region=self._settings.s3_region,
            secure=secure,
        )
        if self._settings.s3_url_style == "path":
            client.disable_virtual_style_endpoint()
        else:
            client.enable_virtual_style_endpoint()
        self._client = client
        return client


def build_storage_service(settings: AppSettings) -> StorageService:
    if settings.storage_backend == "s3":
        return S3StorageService(settings)
    return LocalStorageService(settings.normalized_local_storage_dir)


def _object_key(*, scope: str, filename: str) -> str:
    cleaned_scope = _safe_segment(scope) or "objects"
    return f"{cleaned_scope}/{uuid4().hex}/{_safe_filename(filename)}"


def _safe_segment(value: str) -> str:
    return SAFE_SEGMENT_RE.sub("-", value.strip()).strip("-._")


def _safe_filename(value: str) -> str:
    name = PurePosixPath(value).name.strip() or "object.bin"
    cleaned = SAFE_SEGMENT_RE.sub("-", name).strip("-._")
    return cleaned or "object.bin"

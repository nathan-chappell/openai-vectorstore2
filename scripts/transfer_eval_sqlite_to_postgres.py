#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path, PurePosixPath
import sys
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Engine

from backend.app.core.config import AppSettings
from backend.app.db.session import postgres_connect_args
from backend.app.models import Base
from backend.app.storage import LocalStorageService

DEFAULT_LOCAL_DB = Path(".local/evals/open_ragbench/live-server.db")
DEFAULT_LOCAL_STORAGE = Path(".local/evals/open_ragbench/app-storage")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = AppSettings()
    if not settings.sync_database_url.startswith("postgresql"):
        raise RuntimeError("Refusing transfer because DATABASE_URL is not PostgreSQL.")
    if settings.database_postgres_schema is None:
        raise RuntimeError("Refusing transfer because DATABASE_POSTGRES_SCHEMA is not configured.")

    source_db = Path(args.source_db)
    if not source_db.exists():
        raise FileNotFoundError(source_db)

    source_engine = create_engine(f"sqlite:///{source_db}", future=True)
    target_engine = create_engine(
        settings.sync_database_url,
        future=True,
        pool_pre_ping=True,
        connect_args=postgres_connect_args(settings.database_postgres_schema, async_driver=False),
    )
    try:
        source_counts = _table_counts(source_engine)
        target_counts = _table_counts(target_engine)
        nonempty_targets = {table: count for table, count in target_counts.items() if count > 0}
        if nonempty_targets and not args.allow_nonempty_target:
            raise RuntimeError(
                "Target schema is not empty; rerun with --allow-nonempty-target if you intentionally want "
                f"ON CONFLICT DO NOTHING behavior. nonempty={nonempty_targets!r}"
            )
        rows_by_table = _read_rows(source_engine)
        if args.dry_run:
            print(f"dry_run source_counts={source_counts}")
            print(f"dry_run target_counts={target_counts}")
            return 0

        asyncio.run(
            _copy_storage_objects(
                rows_by_table=rows_by_table,
                settings=settings,
                source_storage_root=Path(args.source_storage),
            )
        )
        _rewrite_storage_provider(rows_by_table=rows_by_table, provider=settings.storage_backend)
        copied = _copy_database_rows(target_engine=target_engine, rows_by_table=rows_by_table)
        print(f"copied={copied}")
        return 0
    finally:
        source_engine.dispose()
        target_engine.dispose()


def _read_rows(source_engine: Engine) -> dict[str, list[dict[str, Any]]]:
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    with source_engine.connect() as connection:
        for table in Base.metadata.sorted_tables:
            rows = [dict(row) for row in connection.execute(select(table)).mappings()]
            rows_by_table[table.name] = rows
    return rows_by_table


def _copy_database_rows(*, target_engine: Engine, rows_by_table: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    copied: dict[str, int] = {}
    with target_engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            rows = rows_by_table.get(table.name, [])
            if not rows:
                copied[table.name] = 0
                continue
            statement = postgres_insert(table).values(rows).on_conflict_do_nothing()
            result = connection.execute(statement)
            copied[table.name] = result.rowcount
    return copied


async def _copy_storage_objects(
    *,
    rows_by_table: dict[str, list[dict[str, Any]]],
    settings: AppSettings,
    source_storage_root: Path,
) -> None:
    if settings.storage_backend != "s3":
        return
    local_storage = LocalStorageService(source_storage_root)
    s3_client = _s3_client(settings)
    s3_bucket = _s3_bucket(settings)
    object_rows = [
        row
        for table_name in ("source_file", "stored_asset")
        for row in rows_by_table.get(table_name, [])
        if row.get("storage_provider") == "local" and isinstance(row.get("storage_key"), str)
    ]
    for row in object_rows:
        key = str(row["storage_key"])
        if not _local_storage_object_exists(root=source_storage_root, key=key):
            raise FileNotFoundError(source_storage_root / Path(*PurePosixPath(key).parts))
        if not await asyncio.to_thread(_s3_object_exists, s3_client, s3_bucket, key):
            payload = await local_storage.get_bytes(key=key)
            media_type = str(row.get("media_type") or "application/octet-stream")
            await asyncio.to_thread(
                _s3_put_object,
                s3_client,
                s3_bucket,
                key,
                payload,
                media_type,
            )


def _rewrite_storage_provider(*, rows_by_table: dict[str, list[dict[str, Any]]], provider: str) -> None:
    for table_name in ("source_file", "stored_asset"):
        for row in rows_by_table.get(table_name, []):
            if "storage_provider" in row:
                row["storage_provider"] = provider


def _s3_client(settings: AppSettings) -> Any:
    from minio import Minio

    if settings.s3_endpoint is None or not settings.s3_endpoint.strip():
        raise RuntimeError("S3 storage is selected but S3_ENDPOINT is not configured.")
    if settings.s3_access_key_id is None or not settings.s3_access_key_id.strip():
        raise RuntimeError("S3 storage is selected but S3_ACCESS_KEY_ID is not configured.")
    if settings.s3_secret_access_key is None:
        raise RuntimeError("S3 storage is selected but S3_SECRET_ACCESS_KEY is not configured.")
    raw_endpoint = settings.s3_endpoint.strip()
    secure = not raw_endpoint.startswith("http://")
    endpoint = raw_endpoint.removeprefix("https://").removeprefix("http://").strip("/")
    client = Minio(
        endpoint,
        access_key=settings.s3_access_key_id.strip(),
        secret_key=settings.s3_secret_access_key.get_secret_value(),
        region=settings.s3_region,
        secure=secure,
    )
    if settings.s3_url_style == "path":
        client.disable_virtual_style_endpoint()
    else:
        client.enable_virtual_style_endpoint()
    return client


def _s3_bucket(settings: AppSettings) -> str:
    if settings.s3_bucket is None or not settings.s3_bucket.strip():
        raise RuntimeError("S3 storage is selected but S3_BUCKET is not configured.")
    return settings.s3_bucket.strip()


def _s3_object_exists(client: Any, bucket: str, key: str) -> bool:
    try:
        client.stat_object(bucket, key)
    except Exception as exc:
        error_code = getattr(exc, "code", "")
        if error_code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
            return False
        raise
    return True


def _s3_put_object(client: Any, bucket: str, key: str, payload: bytes, media_type: str) -> None:
    client.put_object(bucket, key, BytesIO(payload), length=len(payload), content_type=media_type)


def _local_storage_object_exists(*, root: Path, key: str) -> bool:
    normalized = PurePosixPath(key)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"Invalid local storage key: {key}")
    return (root / Path(*normalized.parts)).exists()


def _table_counts(engine: Engine) -> dict[str, int]:
    counts: dict[str, int] = {}
    with engine.connect() as connection:
        for table_name in sorted(Base.metadata.tables):
            try:
                counts[table_name] = int(connection.execute(text(f'select count(*) from "{table_name}"')).scalar_one())
            except Exception:
                counts[table_name] = -1
    return counts


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy the local Open RAGBench eval SQLite DB into configured Postgres.")
    parser.add_argument("--source-db", default=str(DEFAULT_LOCAL_DB))
    parser.add_argument("--source-storage", default=str(DEFAULT_LOCAL_STORAGE))
    parser.add_argument("--allow-nonempty-target", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

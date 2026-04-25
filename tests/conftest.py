from __future__ import annotations

from collections.abc import Iterator
import base64
from pathlib import Path

import pytest

from backend.app.core.config import AppSettings
from backend.app.integrations.openai_gateway import VectorSearchCandidate
from backend.app.schemas import ChunkHit, ChunkLocator, SemanticChunkDraft, SemanticSplitResult


class FakeOpenAIGateway:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.vector_store_id = "vs_fake"
        self._chunks: dict[str, VectorSearchCandidate] = {}
        self._counter = 0
        self.deleted_file_ids: list[str] = []
        self.detached_vector_store_file_ids: list[tuple[str, str]] = []
        self.fail_during_split = False

    async def close(self) -> None:
        return None

    async def create_vector_store(self, *, name: str, metadata: dict[str, str]) -> str:
        del name, metadata
        return self.vector_store_id

    async def upload_file_bytes(self, *, filename: str, payload: bytes, purpose: object) -> str:
        del filename, payload, purpose
        self._counter += 1
        return f"file_original_{self._counter}"

    async def transcribe_audio_bytes(self, *, filename: str, payload: bytes) -> tuple[str, dict[str, object]]:
        del filename, payload
        return "speaker: transcribed conversation about semantic retrieval", {"segments": []}

    async def split_semantically(
        self,
        *,
        source_title: str,
        source_kind: str,
        text: str,
        user_guidance: str | None,
    ) -> SemanticSplitResult:
        del source_kind, user_guidance
        if self.fail_during_split:
            raise RuntimeError("Fake semantic split failure.")
        midpoint = max(1, len(text) // 2)
        first = text[:midpoint].strip() or text.strip()
        second = text[midpoint:].strip()
        chunks = [
            SemanticChunkDraft(
                sequence=1,
                title=f"{source_title} overview",
                summary="A semantic overview chunk for retrieval.",
                text=first,
                keywords=["semantic", "overview"],
                locator=ChunkLocator(type="line_range", start_line=1, end_line=6),
                strategy_label="fake_semantic",
            )
        ]
        if second:
            chunks.append(
                SemanticChunkDraft(
                    sequence=2,
                    title=f"{source_title} details",
                    summary="A second chunk with implementation details.",
                    text=second,
                    keywords=["details", "retrieval"],
                    locator=ChunkLocator(type="line_range", start_line=7, end_line=14),
                    strategy_label="fake_semantic",
                )
            )
        return SemanticSplitResult(strategy_label="fake_semantic", tags=["semantic", "retrieval"], chunks=chunks)

    async def attach_chunk_to_vector_store(
        self,
        *,
        vector_store_id: str,
        filename: str,
        text_content: str,
        attributes: dict[str, str | float | bool],
    ) -> str:
        del vector_store_id, filename
        self._counter += 1
        file_id = f"file_chunk_{self._counter}"
        self._chunks[file_id] = VectorSearchCandidate(
            openai_file_id=file_id,
            score=0.93,
            text=text_content,
            attributes=attributes,
        )
        return file_id

    async def detach_file_from_vector_store(self, *, vector_store_id: str, file_id: str) -> None:
        self.detached_vector_store_file_ids.append((vector_store_id, file_id))

    async def delete_file(self, *, file_id: str) -> None:
        self.deleted_file_ids.append(file_id)

    async def search_vector_store(
        self,
        *,
        vector_store_id: str,
        query: str,
        max_results: int,
        filters: object,
    ) -> list[VectorSearchCandidate]:
        del vector_store_id, query, filters
        return list(self._chunks.values())[:max_results]

    async def answer_with_chunks(self, *, prompt: str, hits: list[ChunkHit]) -> str:
        return f"Fake grounded answer to '{prompt}' using {len(hits)} chunks."

    async def freeform_with_chunks(self, *, prompt: str, hits: list[ChunkHit], mode: str) -> str:
        return f"Fake {mode} response to '{prompt}' using {len(hits)} chunks."

    async def generate_image_bytes(self, *, prompt: str, size: str) -> tuple[bytes, dict[str, object]]:
        del prompt, size
        return base64.b64decode("iVBORw0KGgo="), {"model": "fake-image"}

    async def generate_voice_bytes(
        self, *, text: str, voice: str, response_format: str
    ) -> tuple[bytes, dict[str, object]]:
        del text
        return b"fake-audio", {"voice": voice, "response_format": response_format}


@pytest.fixture
def configured_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppSettings]:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("APP_SIGNING_SECRET", "test-secret")
    monkeypatch.setenv("ALLOW_LOCAL_DEV_AUTH", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "dist"))
    yield AppSettings()


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.app.bootstrap.OpenAIGateway", FakeOpenAIGateway)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer local-dev"}

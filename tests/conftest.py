from __future__ import annotations

from collections.abc import Iterator
import base64
from pathlib import Path

import pytest

from openai_vectorstore2_backend.app.core.config import AppSettings
from openai_vectorstore2_backend.app.integrations.openai_gateway import OpenAITextResult, VectorSearchCandidate
from openai_vectorstore2_backend.app.schemas import (
    ChunkHit,
    ChunkLocator,
    ResearchDiscoveryCandidateDraft,
    ResearchDiscoveryResult,
    SemanticChunkDraft,
    SemanticSplitResult,
)


class FakeOpenAIGateway:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.vector_store_id = "vs_fake"
        self._chunks: dict[str, VectorSearchCandidate] = {}
        self._uploaded_files: dict[str, tuple[str, bytes, object]] = {}
        self._counter = 0
        self.deleted_file_ids: list[str] = []
        self.detached_vector_store_file_ids: list[tuple[str, str]] = []
        self.fail_during_split = False
        self.fail_during_vector_attach = False
        self.ignore_filters = False
        self.research_candidate_base_url = "https://example.com"

    async def close(self) -> None:
        return None

    async def create_vector_store(self, *, name: str, metadata: dict[str, str]) -> str:
        del name, metadata
        return self.vector_store_id

    async def create_conversation(self, *, metadata: dict[str, str]) -> str:
        del metadata
        self._counter += 1
        return f"conv_fake_{self._counter}"

    async def upload_file_bytes(self, *, filename: str, payload: bytes, purpose: object) -> str:
        self._counter += 1
        file_id = f"file_original_{self._counter}"
        self._uploaded_files[file_id] = (filename, payload, purpose)
        return file_id

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
        tags = ["semantic", "retrieval"]
        normalized_text = text.casefold()
        if "alpha" in normalized_text:
            tags.append("alpha")
        if "bravo" in normalized_text:
            tags.append("bravo")
        return SemanticSplitResult(strategy_label="fake_semantic", tags=tags, chunks=chunks)

    async def discover_research_candidates(self, *, query: str, max_candidates: int) -> ResearchDiscoveryResult:
        followup = "Follow-up discovery" in query
        prefix = "followup-reference" if followup else "reference"
        description_prefix = "followup reference" if followup else "example reference"
        title_prefix = "Follow-up reference" if followup else "Example reference"
        candidates = [
            ResearchDiscoveryCandidateDraft(
                url=f"{self.research_candidate_base_url.rstrip('/')}/{prefix}-{index}.txt",
                title=f"{title_prefix} {index}",
                source_type="url",
                description=f"Short description for {description_prefix} {index}.",
                summary=f"Summary for {description_prefix} {index} in a research library.",
                suggested_tags=["research", f"{prefix}-{index}"],
                authors=[f"Author {index}"],
                published_at="2024",
                rationale="Fake discovered reference for importer tests.",
                score=0.8,
            )
            for index in range(1, max_candidates + 1)
        ]
        return ResearchDiscoveryResult(candidates=candidates)

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

    async def attach_file_to_vector_store(
        self,
        *,
        vector_store_id: str,
        file_id: str,
        attributes: dict[str, str | float | bool],
    ) -> None:
        del vector_store_id
        if self.fail_during_vector_attach:
            raise RuntimeError("Fake vector attach failure.")
        filename, payload, _purpose = self._uploaded_files[file_id]
        self._chunks[file_id] = VectorSearchCandidate(
            openai_file_id=file_id,
            score=0.93,
            text=payload.decode("utf-8", errors="replace") or filename,
            attributes=attributes,
        )

    async def detach_file_from_vector_store(self, *, vector_store_id: str, file_id: str) -> None:
        self.detached_vector_store_file_ids.append((vector_store_id, file_id))

    async def delete_file(self, *, file_id: str) -> None:
        self.deleted_file_ids.append(file_id)
        self._chunks.pop(file_id, None)
        self._uploaded_files.pop(file_id, None)

    async def search_vector_store(
        self,
        *,
        vector_store_id: str,
        query: str,
        max_results: int,
        filters: object,
    ) -> list[VectorSearchCandidate]:
        del vector_store_id, query
        if self.ignore_filters:
            return list(self._chunks.values())[:max_results]
        return [candidate for candidate in self._chunks.values() if _matches_filter(filters, candidate.attributes)][
            :max_results
        ]

    async def answer_with_chunks(self, *, prompt: str, hits: list[ChunkHit]) -> OpenAITextResult:
        return OpenAITextResult(
            text=f"Fake grounded answer to '{prompt}' using {len(hits)} chunks.",
            response_id=f"resp_answer_{self._counter}",
            conversation_id=None,
            request_id=f"req_answer_{self._counter}",
            model=self.settings.openai_agent_model,
            usage={
                "requests": 1,
                "input_tokens": 1_000,
                "input_tokens_details": {"cached_tokens": 100},
                "output_tokens": 250,
                "total_tokens": 1_250,
            },
        )

    async def freeform_with_chunks(self, *, prompt: str, hits: list[ChunkHit], mode: str) -> OpenAITextResult:
        return OpenAITextResult(
            text=f"Fake {mode} response to '{prompt}' using {len(hits)} chunks.",
            response_id=f"resp_freeform_{self._counter}",
            conversation_id=None,
            request_id=f"req_freeform_{self._counter}",
            model=self.settings.openai_agent_model,
            usage={
                "requests": 1,
                "input_tokens": 1_200,
                "input_tokens_details": {"cached_tokens": 100},
                "output_tokens": 300,
                "total_tokens": 1_500,
            },
        )

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
    monkeypatch.setenv("ALLOW_LOCAL_DEV_AUTH", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "dist"))
    yield AppSettings()


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openai_vectorstore2_backend.app.bootstrap.OpenAIGateway", FakeOpenAIGateway)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer local-dev"}


def _matches_filter(filters: object, attributes: dict[str, str | float | bool]) -> bool:
    if filters is None:
        return True
    if not isinstance(filters, dict):
        return False
    filter_type = filters.get("type")
    if filter_type in {"eq", "ne", "gt", "gte", "lt", "lte", "in", "nin"}:
        key = filters.get("key")
        if not isinstance(key, str):
            return False
        candidate = attributes.get(key)
        expected = filters.get("value")
        if filter_type == "eq":
            return candidate == expected
        if filter_type == "ne":
            return candidate != expected
        if filter_type == "in":
            return isinstance(expected, list) and candidate in expected
        if filter_type == "nin":
            return isinstance(expected, list) and candidate not in expected
        if not isinstance(candidate, (int, float)) or not isinstance(expected, (int, float)):
            return False
        if filter_type == "gt":
            return candidate > expected
        if filter_type == "gte":
            return candidate >= expected
        if filter_type == "lt":
            return candidate < expected
        if filter_type == "lte":
            return candidate <= expected
        return False
    if filter_type == "and":
        nested_filters = filters.get("filters")
        return isinstance(nested_filters, list) and all(
            _matches_filter(nested, attributes) for nested in nested_filters
        )
    if filter_type == "or":
        nested_filters = filters.get("filters")
        return isinstance(nested_filters, list) and any(
            _matches_filter(nested, attributes) for nested in nested_filters
        )
    return False

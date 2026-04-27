from __future__ import annotations

import base64
from dataclasses import dataclass
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.file_purpose import FilePurpose
from openai.types.shared_params.comparison_filter import ComparisonFilter
from openai.types.shared_params.compound_filter import CompoundFilter

from backend.app.core.config import AppSettings
from backend.app.core.openai_observability import openai_platform_log_url
from backend.app.schemas import ChunkHit, ResearchDiscoveryResult, SemanticSplitResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VectorSearchCandidate:
    openai_file_id: str
    score: float
    text: str
    attributes: dict[str, str | float | bool]


@dataclass(frozen=True, slots=True)
class OpenAITextResult:
    text: str
    response_id: str | None
    conversation_id: str | None
    request_id: str | None
    model: str | None
    usage: object | None


class OpenAIGateway:
    """OpenAI-backed operations isolated behind a fakeable service boundary."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())

    async def close(self) -> None:
        await self._client.close()

    async def create_conversation(self, *, metadata: dict[str, str]) -> str:
        started_at = perf_counter()
        conversation = await cast(Any, self._client.conversations.create)(metadata=metadata)
        conversation_id = str(conversation.id)
        logger.info(
            "openai_conversation_created conversation=%s conversation_log_url=%s duration_ms=%.1f",
            conversation_id,
            openai_platform_log_url(conversation_id),
            (perf_counter() - started_at) * 1000,
        )
        return conversation_id

    async def _create_response(self, *, operation: str, **kwargs: Any) -> Any:
        started_at = perf_counter()
        try:
            response = await cast(Any, self._client.responses.create)(**kwargs)
        except Exception:
            logger.error(
                "openai_response_failed operation=%s model=%s duration_ms=%.1f",
                operation,
                kwargs.get("model"),
                (perf_counter() - started_at) * 1000,
            )
            raise
        log_openai_response(
            operation=operation,
            response=response,
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return response

    async def _parse_response(self, *, operation: str, **kwargs: Any) -> Any:
        started_at = perf_counter()
        try:
            response = await cast(Any, self._client.responses.parse)(**kwargs)
        except Exception:
            logger.error(
                "openai_response_failed operation=%s model=%s duration_ms=%.1f",
                operation,
                kwargs.get("model"),
                (perf_counter() - started_at) * 1000,
            )
            raise
        log_openai_response(
            operation=operation,
            response=response,
            duration_ms=(perf_counter() - started_at) * 1000,
        )
        return response

    async def create_vector_store(self, *, name: str, metadata: dict[str, str]) -> str:
        started_at = perf_counter()
        vector_store = await cast(Any, self._client.vector_stores.create)(name=name, metadata=metadata)
        logger.info(
            "openai_vector_store_created vector_store_id=%s name=%s duration_ms=%.1f",
            vector_store.id,
            name,
            (perf_counter() - started_at) * 1000,
        )
        return str(vector_store.id)

    async def upload_file_bytes(
        self,
        *,
        filename: str,
        payload: bytes,
        purpose: FilePurpose,
    ) -> str:
        started_at = perf_counter()
        suffix = Path(filename).suffix or ".bin"
        with NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(payload)
        try:
            with temp_path.open("rb") as file_handle:
                uploaded = await self._client.files.create(file=file_handle, purpose=purpose)
        finally:
            temp_path.unlink(missing_ok=True)
        logger.info(
            "openai_file_uploaded file_id=%s filename=%s purpose=%s bytes=%s duration_ms=%.1f",
            uploaded.id,
            filename,
            purpose,
            len(payload),
            (perf_counter() - started_at) * 1000,
        )
        return str(uploaded.id)

    async def transcribe_audio_bytes(
        self,
        *,
        filename: str,
        payload: bytes,
    ) -> tuple[str, dict[str, object]]:
        suffix = Path(filename).suffix or ".bin"
        with NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(payload)
        try:
            with temp_path.open("rb") as file_handle:
                transcription = await cast(Any, self._client.audio.transcriptions).create(
                    file=file_handle,
                    model=self._settings.openai_transcription_model,
                    response_format="diarized_json",
                    chunking_strategy="auto",
                )
        finally:
            temp_path.unlink(missing_ok=True)

        segments = [
            {
                "id": getattr(segment, "id", index),
                "speaker": getattr(segment, "speaker", "speaker"),
                "start": getattr(segment, "start", None),
                "end": getattr(segment, "end", None),
                "text": getattr(segment, "text", ""),
                "type": getattr(segment, "type", "transcript.segment"),
            }
            for index, segment in enumerate(getattr(transcription, "segments", []) or [])
        ]
        transcript = "\n".join(
            f"[{segment['speaker']}] {segment['text']}".strip()
            for segment in segments
            if isinstance(segment.get("text"), str) and str(segment["text"]).strip()
        ).strip()
        if not transcript:
            transcript = str(getattr(transcription, "text", "") or "").strip()
        return transcript, {
            "text": str(getattr(transcription, "text", "") or ""),
            "duration": getattr(transcription, "duration", None),
            "segments": segments,
        }

    async def split_semantically(
        self,
        *,
        source_title: str,
        source_kind: str,
        text: str,
        user_guidance: str | None,
    ) -> SemanticSplitResult:
        response = await self._parse_response(
            operation="split_semantically",
            model=self._settings.openai_agent_model,
            text_format=SemanticSplitResult,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Split this source into semantically meaningful retrieval chunks. "
                                "Prefer complete ideas over fixed token windows. Return at most 3 broad, reusable tags that help filtering. "
                                "Avoid author names, venue names, generic words, and one-off phrases as tags. "
                                "For PDFs use page ranges when page markers are present; for conversations use time ranges when timestamps exist; "
                                "otherwise use line ranges. Keep chunk text faithful to the source.\n\n"
                                f"Source title: {source_title}\n"
                                f"Source kind: {source_kind}\n"
                                f"User guidance: {user_guidance or 'None'}\n\n"
                                f"Source text:\n{text}"
                            ),
                        }
                    ],
                }
            ],
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI did not return a semantic split payload.")
        return parsed

    async def discover_research_candidates(
        self,
        *,
        query: str,
        max_candidates: int,
    ) -> ResearchDiscoveryResult:
        response = await self._parse_response(
            operation="discover_research_candidates",
            model=self._settings.openai_fast_model,
            text_format=ResearchDiscoveryResult,
            tools=[cast(Any, {"type": "web_search_preview", "search_context_size": "medium"})],
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Find public reference materials that could help seed a research library. "
                                "If the seed is a paper title, include the likely primary paper first, then important cited or closely related public references. "
                                "Return only candidates that are likely to be publicly reachable URLs. "
                                "Prefer original sources, PDFs, arXiv pages, official docs, and high-signal articles. "
                                "For each candidate, include a concise description, a useful summary, 1-3 broad reusable suggested tags, authors, publication date, DOI, or arXiv ID when you can infer them from public metadata. "
                                "Avoid one-off, author, venue, and generic suggested tags. "
                                "Do not include login-gated or paywalled pages when a public alternative is available. "
                                f"Return at most {max_candidates} candidates.\n\nSeed:\n{query}"
                            ),
                        }
                    ],
                }
            ],
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI did not return research discovery candidates.")
        return parsed

    async def attach_chunk_to_vector_store(
        self,
        *,
        vector_store_id: str,
        filename: str,
        text_content: str,
        attributes: dict[str, str | float | bool],
    ) -> str:
        started_at = perf_counter()
        with NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(text_content)
        uploaded_id: str | None = None
        try:
            with temp_path.open("rb") as file_handle:
                uploaded = await self._client.files.create(file=file_handle, purpose="assistants")
                uploaded_id = str(uploaded.id)
            await self._client.vector_stores.files.create_and_poll(
                vector_store_id=vector_store_id,
                file_id=uploaded.id,
                attributes=attributes,
                poll_interval_ms=self._settings.openai_poll_interval_ms,
            )
        except Exception:
            if uploaded_id is not None:
                try:
                    await self._client.files.delete(uploaded_id)
                except Exception as cleanup_error:
                    logger.warning(
                        "openai_vector_chunk_attach_cleanup_failed vector_store_id=%s file_id=%s error=%s",
                        vector_store_id,
                        uploaded_id,
                        cleanup_error,
                    )
            raise
        finally:
            temp_path.unlink(missing_ok=True)
        logger.info(
            "openai_vector_chunk_attached vector_store_id=%s file_id=%s filename=%s duration_ms=%.1f",
            vector_store_id,
            uploaded.id,
            filename,
            (perf_counter() - started_at) * 1000,
        )
        return str(uploaded.id)

    async def attach_file_to_vector_store(
        self,
        *,
        vector_store_id: str,
        file_id: str,
        attributes: dict[str, str | float | bool],
    ) -> None:
        started_at = perf_counter()
        await self._client.vector_stores.files.create_and_poll(
            vector_store_id=vector_store_id,
            file_id=file_id,
            attributes=attributes,
            poll_interval_ms=self._settings.openai_poll_interval_ms,
        )
        logger.info(
            "openai_vector_file_attached vector_store_id=%s file_id=%s duration_ms=%.1f",
            vector_store_id,
            file_id,
            (perf_counter() - started_at) * 1000,
        )

    async def detach_file_from_vector_store(self, *, vector_store_id: str, file_id: str) -> None:
        started_at = perf_counter()
        await self._client.vector_stores.files.delete(vector_store_id=vector_store_id, file_id=file_id)
        logger.info(
            "openai_vector_file_detached vector_store_id=%s file_id=%s duration_ms=%.1f",
            vector_store_id,
            file_id,
            (perf_counter() - started_at) * 1000,
        )

    async def delete_file(self, *, file_id: str) -> None:
        started_at = perf_counter()
        await self._client.files.delete(file_id)
        logger.info(
            "openai_file_deleted file_id=%s duration_ms=%.1f",
            file_id,
            (perf_counter() - started_at) * 1000,
        )

    async def search_vector_store(
        self,
        *,
        vector_store_id: str,
        query: str,
        max_results: int,
        filters: ComparisonFilter | CompoundFilter | None,
    ) -> list[VectorSearchCandidate]:
        search_arguments: dict[str, object] = {
            "vector_store_id": vector_store_id,
            "query": query,
            "max_num_results": max_results,
            "rewrite_query": True,
        }
        if filters is not None:
            search_arguments["filters"] = filters
        page = await cast(Any, self._client.vector_stores.search)(**search_arguments)
        candidates: list[VectorSearchCandidate] = []
        for item in page.data:
            text = "\n".join(content.text for content in item.content if content.type == "text").strip()
            candidates.append(
                VectorSearchCandidate(
                    openai_file_id=str(item.file_id),
                    score=float(item.score),
                    text=text,
                    attributes=dict(item.attributes or {}),
                )
            )
        return candidates

    async def answer_with_chunks(
        self,
        *,
        prompt: str,
        hits: list[ChunkHit],
    ) -> OpenAITextResult:
        evidence = _render_hit_evidence(hits)
        if not evidence:
            return OpenAITextResult(
                text="I could not find relevant indexed file matches in the current library.",
                response_id=None,
                conversation_id=None,
                request_id=None,
                model=None,
                usage=None,
            )
        response = await self._create_response(
            operation="answer_with_chunks",
            model=self._settings.openai_agent_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Answer using only the retrieved indexed file matches. Cite source titles inline when useful. "
                                "If the evidence is thin, say so clearly.\n\n"
                                f"Question: {prompt}\n\nEvidence:\n{evidence}"
                            ),
                        }
                    ],
                }
            ],
        )
        output_text = getattr(response, "output_text", "")
        if not isinstance(output_text, str) or not output_text.strip():
            raise RuntimeError("OpenAI did not return answer text.")
        return _text_result_from_response(response, text=output_text.strip())

    async def freeform_with_chunks(
        self,
        *,
        prompt: str,
        hits: list[ChunkHit],
        mode: str,
    ) -> OpenAITextResult:
        evidence = _render_hit_evidence(hits)
        grounding = (
            "Use the retrieved chunks as hard evidence and avoid unsupported claims."
            if mode == "grounded"
            else "Use the chunks as inspiration, but clearly separate grounded details from creative extrapolation."
        )
        response = await self._create_response(
            operation="freeform_with_chunks",
            model=self._settings.openai_agent_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"{grounding}\n\nUser request: {prompt}\n\nRetrieved context:\n{evidence or '(none)'}",
                        }
                    ],
                }
            ],
        )
        output_text = getattr(response, "output_text", "")
        if not isinstance(output_text, str) or not output_text.strip():
            raise RuntimeError("OpenAI did not return free-form text.")
        return _text_result_from_response(response, text=output_text.strip())

    async def generate_image_bytes(
        self,
        *,
        prompt: str,
        size: str,
    ) -> tuple[bytes, dict[str, object]]:
        result = await cast(Any, self._client.images.generate)(
            model=self._settings.openai_image_generation_model,
            prompt=prompt,
            response_format="b64_json",
            size=size,
        )
        first_image = result.data[0] if getattr(result, "data", None) else None
        if first_image is None:
            raise RuntimeError("OpenAI did not return generated image data.")
        b64_json = getattr(first_image, "b64_json", None)
        if not isinstance(b64_json, str) or not b64_json.strip():
            raise RuntimeError("OpenAI image response did not include base64 data.")
        return base64.b64decode(b64_json), {
            "revised_prompt": getattr(first_image, "revised_prompt", None),
            "model": self._settings.openai_image_generation_model,
        }

    async def generate_voice_bytes(
        self,
        *,
        text: str,
        voice: str,
        response_format: str,
    ) -> tuple[bytes, dict[str, object]]:
        response = await cast(Any, self._client.audio.speech).create(
            model=self._settings.openai_speech_model,
            voice=voice,
            input=text,
            response_format=response_format,
        )
        content = getattr(response, "content", None)
        if isinstance(content, bytes):
            payload = content
        elif hasattr(response, "read"):
            payload = cast(bytes, response.read())
        else:
            payload = bytes(response)
        return payload, {
            "model": self._settings.openai_speech_model,
            "voice": voice,
            "response_format": response_format,
        }


def _render_hit_evidence(hits: list[ChunkHit]) -> str:
    return "\n\n".join(
        f"{index}. {hit.source_title} ({hit.locator.label()})\n"
        f"Match: {hit.title}\n"
        f"Summary: {hit.summary}\n"
        f"Text:\n{hit.text}"
        for index, hit in enumerate(hits, start=1)
    ).strip()


def log_openai_response(*, operation: str, response: object, duration_ms: float) -> None:
    response_id = _string_attr(response, "id")
    conversation_id = _conversation_id_from_response(response)
    usage = getattr(response, "usage", None)
    logger.info(
        "openai response operation=%s response=%s openai_log_url=%s conversation=%s "
        "conversation_log_url=%s model=%s status=%s request=%s tokens=%s (%.1fms)",
        operation,
        response_id,
        openai_platform_log_url(response_id),
        conversation_id,
        openai_platform_log_url(conversation_id),
        _string_attr(response, "model"),
        _string_attr(response, "status"),
        _string_attr(response, "_request_id") or _string_attr(response, "request_id"),
        getattr(usage, "total_tokens", None),
        duration_ms,
    )


def _text_result_from_response(response: object, *, text: str) -> OpenAITextResult:
    return OpenAITextResult(
        text=text,
        response_id=_string_attr(response, "id"),
        conversation_id=_conversation_id_from_response(response),
        request_id=_string_attr(response, "_request_id") or _string_attr(response, "request_id"),
        model=_string_attr(response, "model"),
        usage=getattr(response, "usage", None),
    )


def _conversation_id_from_response(response: object) -> str | None:
    direct_id = _string_attr(response, "conversation_id")
    if direct_id is not None:
        return direct_id
    conversation = getattr(response, "conversation", None)
    if isinstance(conversation, str):
        return conversation
    return _string_attr(conversation, "id")


def _string_attr(value: object, attr_name: str) -> str | None:
    raw_value = getattr(value, attr_name, None)
    if isinstance(raw_value, str) and raw_value.strip():
        return raw_value
    return None

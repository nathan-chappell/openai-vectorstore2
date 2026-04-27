from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import logging
import math
from time import perf_counter
from typing import Any, Literal, NotRequired, TypedDict, cast

import httpx
from openai import AsyncOpenAI

from backend.app.core.config import AppSettings

logger = logging.getLogger(__name__)

ChatCompletionRole = Literal["system", "developer", "user", "assistant", "tool"]


class ChatCompletionFunctionPayload(TypedDict):
    name: str
    arguments: str


class ChatCompletionToolCallPayload(TypedDict):
    id: str
    type: Literal["function"]
    function: ChatCompletionFunctionPayload


class ChatCompletionMessagePayload(TypedDict):
    role: ChatCompletionRole
    content: str | None
    name: NotRequired[str]
    tool_call_id: NotRequired[str]
    tool_calls: NotRequired[list[ChatCompletionToolCallPayload]]


class ChatCompletionFunctionTool(TypedDict):
    name: str
    description: NotRequired[str]
    parameters: dict[str, object]


class ChatCompletionToolPayload(TypedDict):
    type: Literal["function"]
    function: ChatCompletionFunctionTool


class WebSearchRequestPayload(TypedDict):
    query: str
    max_results: NotRequired[int]
    locale: NotRequired[str]
    freshness: NotRequired[str]


class WebSearchResultPayload(TypedDict):
    title: str
    url: str
    snippet: str
    source: NotRequired[str]
    published_at: NotRequired[str]


class WebSearchResponsePayload(TypedDict):
    query: str
    results: list[WebSearchResultPayload]
    summary: NotRequired[str]


@dataclass(frozen=True, slots=True)
class ChatCompletionsModelContext:
    model: str
    context_window_tokens: int
    output_token_reserve: int
    history_token_budget: int
    compaction_trigger_tokens: int
    compaction_target_tokens: int


@dataclass(frozen=True, slots=True)
class ChatCompletionsCompactionSummary:
    data: str
    conversation: str
    remarks: str


@dataclass(frozen=True, slots=True)
class ChatCompletionsToolCall:
    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class ChatCompletionsResult:
    text: str
    tool_calls: list[ChatCompletionsToolCall]
    response_id: str | None
    request_id: str | None
    model: str | None
    usage: object | None


KNOWN_CHAT_COMPLETIONS_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-5.5": 1_000_000,
    "gpt-5.4": 1_000_000,
    "gpt-5.4-mini": 1_000_000,
    "gpt-5.4-nano": 1_000_000,
    "gpt-5.3": 1_000_000,
    "gpt-5.3-chat-latest": 1_000_000,
    "gpt-5.2": 1_000_000,
    "gpt-4.1": 1_000_000,
    "gpt-4.1-mini": 1_000_000,
    "gpt-4.1-nano": 1_000_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o4-mini": 200_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "oss-small": 131_072,
    "gpt-oss-20b": 131_072,
    "openai/gpt-oss-20b": 131_072,
}


def known_chat_completions_context_window(
    model: str,
    *,
    configured_context_window_tokens: int | None,
) -> int:
    if configured_context_window_tokens is not None:
        if configured_context_window_tokens <= 0:
            raise ValueError("Configured chat-completions context window must be positive.")
        return configured_context_window_tokens

    normalized_model = model.strip().casefold()
    if normalized_model in KNOWN_CHAT_COMPLETIONS_CONTEXT_WINDOWS:
        return KNOWN_CHAT_COMPLETIONS_CONTEXT_WINDOWS[normalized_model]
    for known_model, context_window_tokens in KNOWN_CHAT_COMPLETIONS_CONTEXT_WINDOWS.items():
        if normalized_model.startswith(f"{known_model}-"):
            return context_window_tokens
    raise ValueError(
        f"Unknown chat_completions_v1 context window for model {model!r}. "
        "Set CHAT_COMPLETIONS_CONTEXT_WINDOW_TOKENS before using this model."
    )


def chat_completions_model_context(
    model: str,
    *,
    configured_context_window_tokens: int | None,
    output_token_reserve: int,
    compaction_remaining_ratio: float,
    compaction_compress_ratio: float,
) -> ChatCompletionsModelContext:
    context_window_tokens = known_chat_completions_context_window(
        model,
        configured_context_window_tokens=configured_context_window_tokens,
    )
    normalized_reserve = min(max(output_token_reserve, 1_024), max(1_024, context_window_tokens // 2))
    if not 0.05 <= compaction_remaining_ratio <= 0.80:
        raise ValueError("Chat-completions compaction remaining ratio must be between 0.05 and 0.80.")
    if not 0.10 <= compaction_compress_ratio <= 0.90:
        raise ValueError("Chat-completions compaction compress ratio must be between 0.10 and 0.90.")
    history_token_budget = max(1, context_window_tokens - normalized_reserve)
    compaction_trigger_tokens = max(1, math.floor(history_token_budget * (1.0 - compaction_remaining_ratio)))
    compaction_target_tokens = max(1, math.floor(compaction_trigger_tokens * (1.0 - compaction_compress_ratio)))
    return ChatCompletionsModelContext(
        model=model,
        context_window_tokens=context_window_tokens,
        output_token_reserve=normalized_reserve,
        history_token_budget=history_token_budget,
        compaction_trigger_tokens=compaction_trigger_tokens,
        compaction_target_tokens=compaction_target_tokens,
    )


def estimate_chat_completion_message_tokens(messages: Sequence[ChatCompletionMessagePayload]) -> int:
    # This intentionally avoids adding a tokenizer dependency before the provider path is proven.
    # It slightly overcounts structure so compaction fires early instead of failing at request time.
    total = 0
    for message in messages:
        serialized = json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        total += 8 + math.ceil(len(serialized) / 4)
    return total


def should_compact_chat_completion_messages(
    messages: Sequence[ChatCompletionMessagePayload],
    *,
    context: ChatCompletionsModelContext,
) -> bool:
    return estimate_chat_completion_message_tokens(messages) >= context.compaction_trigger_tokens


def render_compaction_summary(summary: ChatCompletionsCompactionSummary) -> str:
    return (
        "Prior conversation history was compacted. Preserve these facts while continuing the thread.\n\n"
        "## Data\n"
        f"{summary.data.strip() or '- No durable data was identified.'}\n\n"
        "## Conversation\n"
        f"{summary.conversation.strip() or '- No prior conversational state was identified.'}\n\n"
        "## Remarks\n"
        f"{summary.remarks.strip() or '- No unresolved remarks.'}"
    )


def compaction_summary_message(summary: ChatCompletionsCompactionSummary) -> ChatCompletionMessagePayload:
    return {
        "role": "system",
        "content": render_compaction_summary(summary),
    }


class ChatCompletionsGateway:
    """OpenAI-compatible `/v1/chat/completions` boundary for OpenAI and OSS providers."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        api_key = settings.chat_completions_api_key or settings.openai_api_key
        kwargs: dict[str, object] = {"api_key": api_key.get_secret_value()}
        if settings.chat_completions_base_url is not None:
            kwargs["base_url"] = str(settings.chat_completions_base_url).rstrip("/")
        self._client = AsyncOpenAI(**cast(Any, kwargs))

    async def close(self) -> None:
        await self._client.close()

    def model_context(self, model: str | None = None) -> ChatCompletionsModelContext:
        return chat_completions_model_context(
            model or self._settings.chat_completions_model,
            configured_context_window_tokens=self._settings.chat_completions_context_window_tokens,
            output_token_reserve=self._settings.chat_completions_output_token_reserve,
            compaction_remaining_ratio=self._settings.chat_completions_compaction_remaining_ratio,
            compaction_compress_ratio=self._settings.chat_completions_compaction_compress_ratio,
        )

    async def create_completion(
        self,
        *,
        model: str,
        messages: Sequence[ChatCompletionMessagePayload],
        tools: Sequence[ChatCompletionToolPayload] | None = None,
    ) -> ChatCompletionsResult:
        started_at = perf_counter()
        request_kwargs: dict[str, object] = {
            "model": model,
            "messages": list(messages),
        }
        if tools is not None:
            request_kwargs["tools"] = list(tools)
        try:
            response = await cast(Any, self._client.chat.completions).create(**request_kwargs)
        except Exception:
            logger.error(
                "chat_completions_request_failed model=%s messages=%s tools=%s duration_ms=%.1f",
                model,
                len(messages),
                len(tools or []),
                (perf_counter() - started_at) * 1000,
            )
            raise

        choice = response.choices[0]
        message = choice.message
        result = ChatCompletionsResult(
            text=str(message.content or ""),
            tool_calls=_tool_calls_from_openai_message(message),
            response_id=str(response.id),
            request_id=cast(str | None, getattr(response, "_request_id", None)),
            model=str(response.model),
            usage=response.usage,
        )
        logger.info(
            "chat_completions_request_completed model=%s response=%s tool_calls=%s duration_ms=%.1f",
            model,
            result.response_id,
            len(result.tool_calls),
            (perf_counter() - started_at) * 1000,
        )
        return result


class WebSearchGateway:
    """Small typed POST boundary for compatibility-mode web search providers."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    async def search(
        self,
        *,
        query: str,
        max_results: int = 8,
        locale: str | None = None,
        freshness: str | None = None,
    ) -> WebSearchResponsePayload:
        if self._settings.chat_completions_web_search_url is None:
            raise RuntimeError("CHAT_COMPLETIONS_WEB_SEARCH_URL is required before using compatibility web search.")
        payload: WebSearchRequestPayload = {"query": query, "max_results": max(1, min(max_results, 20))}
        if locale is not None:
            payload["locale"] = locale
        if freshness is not None:
            payload["freshness"] = freshness
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(str(self._settings.chat_completions_web_search_url), json=payload)
            response.raise_for_status()
        parsed = response.json()
        if not isinstance(parsed, Mapping):
            raise RuntimeError("Web-search provider returned a non-object payload.")
        return _web_search_response_from_mapping(parsed, fallback_query=query)


def _tool_calls_from_openai_message(message: Any) -> list[ChatCompletionsToolCall]:
    output: list[ChatCompletionsToolCall] = []
    for item in cast(list[Any], getattr(message, "tool_calls", None) or []):
        function = getattr(item, "function", None)
        raw_arguments = str(getattr(function, "arguments", "") or "{}")
        try:
            parsed_arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            parsed_arguments = {"raw_arguments": raw_arguments}
        arguments: dict[str, object]
        if isinstance(parsed_arguments, dict):
            arguments = {str(key): value for key, value in parsed_arguments.items()}
        else:
            arguments = {"value": parsed_arguments}
        output.append(
            ChatCompletionsToolCall(
                id=str(getattr(item, "id", "") or ""),
                name=str(getattr(function, "name", "") or ""),
                arguments=arguments,
            )
        )
    return output


def _web_search_response_from_mapping(
    payload: Mapping[str, object],
    *,
    fallback_query: str,
) -> WebSearchResponsePayload:
    results: list[WebSearchResultPayload] = []
    raw_results = payload.get("results")
    if isinstance(raw_results, list):
        for raw_result in raw_results:
            if not isinstance(raw_result, Mapping):
                continue
            title = _string_value(raw_result.get("title"))
            url = _string_value(raw_result.get("url"))
            snippet = _string_value(raw_result.get("snippet")) or _string_value(raw_result.get("description"))
            if title is None or url is None or snippet is None:
                continue
            result: WebSearchResultPayload = {"title": title, "url": url, "snippet": snippet}
            source = _string_value(raw_result.get("source"))
            published_at = _string_value(raw_result.get("published_at"))
            if source is not None:
                result["source"] = source
            if published_at is not None:
                result["published_at"] = published_at
            results.append(result)
    response: WebSearchResponsePayload = {
        "query": _string_value(payload.get("query")) or fallback_query,
        "results": results,
    }
    summary = _string_value(payload.get("summary"))
    if summary is not None:
        response["summary"] = summary
    return response


def _string_value(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote

OPENAI_PLATFORM_LOG_BASE_URL = "https://platform.openai.com/logs"


def openai_platform_log_url(openai_id: str | None) -> str | None:
    normalized_id = (openai_id or "").strip()
    if not normalized_id:
        return None
    return f"{OPENAI_PLATFORM_LOG_BASE_URL}/{quote(normalized_id, safe='')}"


def openai_platform_log_urls(openai_ids: Iterable[str | None]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for openai_id in openai_ids:
        url = openai_platform_log_url(openai_id)
        if url is not None and url not in seen:
            urls.append(url)
            seen.add(url)
    return urls

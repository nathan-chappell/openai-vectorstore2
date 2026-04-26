from __future__ import annotations

import logging
from pathlib import Path

from backend.app.core.logging import configure_logging
from backend.app.core.openai_observability import openai_platform_log_url, openai_platform_log_urls
from backend.app.integrations.openai_gateway import log_openai_response


def test_configure_logging_writes_plain_file(tmp_path: Path) -> None:
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    previous_disabled = root.disabled
    previous_disabled_level = root.manager.disable
    log_path = tmp_path / "app.log"

    try:
        configure_logging("INFO", file_path=log_path)
        logging.getLogger("tests.logging").info("file logging smoke")

        for handler in logging.getLogger().handlers:
            handler.flush()

        contents = log_path.read_text(encoding="utf-8")
        assert "INFO" in contents
        assert "tests.logging file logging smoke" in contents
        assert "\x1b[" not in contents
    finally:
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)
            handler.close()
        root.setLevel(previous_level)
        root.disabled = previous_disabled
        logging.disable(previous_disabled_level)
        for handler in previous_handlers:
            root.addHandler(handler)


def test_openai_platform_log_urls_are_clickable_and_deduplicated() -> None:
    assert openai_platform_log_url("resp_abc123") == "https://platform.openai.com/logs/resp_abc123"
    assert openai_platform_log_url(None) is None
    assert openai_platform_log_urls(["resp_abc123", "resp_abc123", "conv_xyz"]) == [
        "https://platform.openai.com/logs/resp_abc123",
        "https://platform.openai.com/logs/conv_xyz",
    ]


def test_openai_response_logging_includes_platform_url(tmp_path: Path) -> None:
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
    previous_disabled = root.disabled
    previous_disabled_level = root.manager.disable
    log_path = tmp_path / "app.log"

    class Usage:
        total_tokens: int = 42

    class Response:
        id: str = "resp_test123"
        model: str = "gpt-test"
        status: str = "completed"
        usage: Usage = Usage()
        _request_id: str = "req_test123"

    try:
        configure_logging("INFO", file_path=log_path)
        log_openai_response(operation="test_operation", response=Response(), duration_ms=12.3)

        for handler in logging.getLogger().handlers:
            handler.flush()

        contents = log_path.read_text(encoding="utf-8")
        assert "openai_response_completed operation=test_operation" in contents
        assert "response_id=resp_test123" in contents
        assert "openai_log_url=https://platform.openai.com/logs/resp_test123" in contents
        assert "request_id=req_test123" in contents
    finally:
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)
            handler.close()
        root.setLevel(previous_level)
        root.disabled = previous_disabled
        logging.disable(previous_disabled_level)
        for handler in previous_handlers:
            root.addHandler(handler)

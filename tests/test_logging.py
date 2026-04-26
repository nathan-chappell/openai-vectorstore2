from __future__ import annotations

import logging
from pathlib import Path

from backend.app.core.logging import configure_logging


def test_configure_logging_writes_plain_file(tmp_path: Path) -> None:
    root = logging.getLogger()
    previous_handlers = root.handlers[:]
    previous_level = root.level
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
        for handler in previous_handlers:
            root.addHandler(handler)

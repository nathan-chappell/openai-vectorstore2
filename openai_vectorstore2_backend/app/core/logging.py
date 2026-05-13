from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import sys

import colorlog

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class AnsiStrippingFormatter(logging.Formatter):
    """Formatter for non-TTY captures where color escape codes make assertions noisy."""

    def format(self, record: logging.LogRecord) -> str:
        return ANSI_RE.sub("", super().format(record))


def configure_logging(
    level_name: str,
    *,
    file_path: Path | None = None,
    file_max_bytes: int = 5_000_000,
    file_backup_count: int = 3,
) -> None:
    logging.disable(logging.NOTSET)
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    root.disabled = False
    for existing_handler in root.handlers[:]:
        root.removeHandler(existing_handler)
        existing_handler.close()
    root.setLevel(level)

    if sys.stderr.isatty():
        formatter: logging.Formatter = colorlog.ColoredFormatter(
            "%(log_color)s%(levelname)-8s%(reset)s %(name)s %(message)s",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    else:
        formatter = AnsiStrippingFormatter("%(levelname)-8s %(name)s %(message)s")

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)

    if file_path is not None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=max(0, file_max_bytes),
            backupCount=max(0, file_backup_count),
            encoding="utf-8",
        )
        file_handler.setFormatter(AnsiStrippingFormatter("%(asctime)s %(levelname)-8s %(name)s %(message)s"))
        root.addHandler(file_handler)

    noisy_logger_names = {"httpx", "openai", "sqlalchemy", "sqlalchemy.engine", "uvicorn.access"}
    framework_logger_names = {
        "alembic",
        "fastapi",
        "httpx",
        "openai",
        "sqlalchemy",
        "sqlalchemy.engine",
        "starlette",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
    }
    for logger_name in framework_logger_names:
        logger_value = logging.getLogger(logger_name)
        for existing_handler in logger_value.handlers[:]:
            logger_value.removeHandler(existing_handler)
            existing_handler.close()
        logger_value.disabled = False
        logger_value.propagate = True
        logger_value.setLevel(max(level, logging.WARNING) if logger_name in noisy_logger_names else logging.NOTSET)

    for logger_name, logger_value in list(root.manager.loggerDict.items()):
        if not isinstance(logger_value, logging.Logger):
            continue
        if logger_name == "backend" or logger_name.startswith("backend.") or logger_name.startswith("chatkit."):
            logger_value.disabled = False
            logger_value.propagate = True
            logger_value.setLevel(logging.NOTSET)

    logging.getLogger(__name__).info(
        "logging_configured level=%s file_path=%s",
        logging.getLevelName(level),
        str(file_path) if file_path is not None else "disabled",
    )

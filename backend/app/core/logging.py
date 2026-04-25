from __future__ import annotations

import logging
import re
import sys

import colorlog

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class AnsiStrippingFormatter(logging.Formatter):
    """Formatter for non-TTY captures where color escape codes make assertions noisy."""

    def format(self, record: logging.LogRecord) -> str:
        return ANSI_RE.sub("", super().format(record))


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    root.handlers.clear()
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

    for logger_name in ["httpx", "openai", "sqlalchemy.engine"]:
        logging.getLogger(logger_name).setLevel(max(level, logging.WARNING))

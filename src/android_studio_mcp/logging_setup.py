"""Logging configuration.

stdio MCP servers must never write to stdout (it carries the JSON-RPC stream),
so all logging goes to stderr and a rotating file under the user data dir.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import CONFIG

_CONFIGURED = False


def get_logger(name: str = "android_studio_mcp") -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("android_studio_mcp")
    if not _CONFIGURED:
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        stderr = logging.StreamHandler(stream=sys.stderr)
        stderr.setFormatter(fmt)
        logger.addHandler(stderr)

        try:
            CONFIG.log_dir.mkdir(parents=True, exist_ok=True)
            fileh = RotatingFileHandler(
                CONFIG.log_dir / "android-studio-mcp.log",
                maxBytes=2_000_000,
                backupCount=5,
                encoding="utf-8",
            )
            fileh.setFormatter(fmt)
            logger.addHandler(fileh)
        except Exception as exc:  # pragma: no cover - logging must never crash startup
            logger.warning("Could not attach file log handler: %s", exc)

        logger.propagate = False
        _CONFIGURED = True
    return logger


LOG = get_logger()

from __future__ import annotations

import logging
import sys
from loguru import logger

_LOGGING_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    logging.basicConfig(level=level)

    logger.remove()
    logger.add(
        sys.stdout,
        level=level.upper(),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} - {message}",
        enqueue=True,
        diagnose=False,
        backtrace=False,
    )

    _LOGGING_CONFIGURED = True

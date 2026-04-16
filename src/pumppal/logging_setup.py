from __future__ import annotations

import logging
import sys

from loguru import logger


class _InterceptHandler(logging.Handler):
    """Route stdlib logging records into loguru.

    This makes uvicorn, FastAPI, httpx, and python-telegram-bot all appear
    in the same loguru stream with consistent formatting.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk up the call stack to find the real caller outside of logging internals
        frame = sys._getframe(6)
        depth = 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back  # type: ignore[assignment]
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure loguru as the single logging sink for the entire application.

    - Removes the default loguru handler and replaces it with a coloured stderr sink.
    - Intercepts all stdlib ``logging`` calls (uvicorn, FastAPI, PTB, httpx).
    - Optionally writes structured logs to *log_file* (one JSON record per line).
    """
    # Remove loguru's default sink
    logger.remove()

    # Human-readable coloured output to stderr
    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        backtrace=True,
        diagnose=True,
    )

    # Optional file sink (structured JSON for later analysis)
    if log_file:
        logger.add(
            log_file,
            level=level,
            serialize=True,  # JSON lines
            rotation="10 MB",
            retention=5,
            compression="gz",
        )

    # Intercept all stdlib logging so third-party libs appear in loguru
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

    # Silence overly chatty libraries
    for noisy in ("httpx", "httpcore", "telegram.ext", "telegram.bot"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

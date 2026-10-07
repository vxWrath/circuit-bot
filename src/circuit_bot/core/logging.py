"""Structured logging via structlog."""

import asyncio
import logging
import os
import sys
from typing import Any

import structlog

__all__ = (
    "configure_root_logger",
    "get_logger",
    "install_asyncio_exception_handler",
    "install_excepthook",
)

IS_ROOT_CONFIGURED: bool = False


def configure_root_logger(*, force: bool = False) -> None:
    """Configure structlog for the bot process.

    *dev* mode (the default) uses coloured console output.  Set *dev* to
    ``False`` for JSON output suitable for log aggregation.
    """
    global IS_ROOT_CONFIGURED

    if IS_ROOT_CONFIGURED and not force:
        return

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if _is_dev_mode():
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging → structlog.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    IS_ROOT_CONFIGURED = True


original_excepthook = None
original_loop_exception_handler = None
installed_excepthook: bool = False
installed_asyncio_handler: bool = False


def install_excepthook() -> None:
    """Install ``sys.excepthook`` that logs unhandled exceptions as JSON."""
    global original_excepthook, installed_excepthook

    if installed_excepthook:
        return

    original_excepthook = sys.excepthook

    def excepthook(exc_type: type[BaseException], exc_value: BaseException, exc_tb: Any) -> None:
        logger = get_logger("unhandled")
        logger.critical(
            "Unhandled exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )

        # Call the original hook so Python still does its default handling
        if original_excepthook and original_excepthook is not sys.__excepthook__:
            original_excepthook(exc_type, exc_value, exc_tb)
        else:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook
    installed_excepthook = True


def install_asyncio_exception_handler() -> None:
    """Set an asyncio loop exception handler that logs with full context."""
    global original_loop_exception_handler, installed_asyncio_handler

    if installed_asyncio_handler:
        return

    loop = asyncio.get_running_loop()
    original_loop_exception_handler = loop.get_exception_handler()

    def handle_exception(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        logger = get_logger("asyncio")
        message = context.get("message", "Unhandled asyncio exception")
        exception = context.get("exception")
        future = context.get("future")

        if isinstance(future, asyncio.Task):
            name = future.get_name()
            logger.error(
                f"{message} (task: {name})",
                exc_info=exception,
            )
        else:
            logger.error(message, exc_info=exception)

        # Forward to original handler if one existed
        if original_loop_exception_handler:
            original_loop_exception_handler(loop, context)

    loop.set_exception_handler(handle_exception)
    installed_asyncio_handler = True


def get_logger(name: str, *, level: int | None = logging.INFO) -> logging.Logger:
    if not IS_ROOT_CONFIGURED:
        configure_root_logger()

    logger = logging.getLogger(name)

    if level is not None:
        logger.setLevel(level)
    else:
        # Inherit root level so propagation works
        logger.setLevel(logging.NOTSET)

    logger.propagate = True
    return logger


# this function exists to avoid circular imports
def _is_dev_mode() -> bool:
    val = os.environ.get("STAGE", "dev")
    return val.casefold() == "dev"

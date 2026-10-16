"""Structured logging via structlog."""

import asyncio
import logging
import sys
from typing import Any

import structlog

from .env import is_dev

__all__ = (
    "configure_root_logger",
    "get_logger",
    "install_asyncio_exception_handler",
    "install_excepthook",
)

IS_ROOT_CONFIGURED: bool = False
installed_handler: logging.Handler | None = None


def console_renderer() -> structlog.dev.ConsoleRenderer:
    """The dev renderer, with the logger name moved in front of the event.

    structlog's own column order trails it, where it drifts with the width of
    the message.
    """

    renderer = structlog.dev.ConsoleRenderer()
    by_key = {column.key: column for column in renderer.columns}
    # "" is the formatter every field without a column of its own falls through to.
    renderer.columns = [by_key[""], by_key["timestamp"], by_key["level"], by_key["logger"], by_key["event"]]

    return renderer


def configure_root_logger(*, force: bool = False) -> None:
    """Configure structlog and the root logger for the bot process.

    Coloured in dev (:func:`~circuit_bot.core.env.is_dev`), JSON everywhere
    else.  Called on demand by :func:`get_logger`, which does it once; *force*
    rebuilds the pipeline, swapping the installed handler so only one is ever
    attached.
    """
    global IS_ROOT_CONFIGURED, installed_handler

    if IS_ROOT_CONFIGURED and not force:
        return

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        # The name of the logger the call came through, ours or a library's.
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        # Fields a stdlib logger was handed in ``extra`` - the only way one carries them.
        structlog.stdlib.ExtraAdder(),
        # An exception becomes text here - left as a tuple, the renderer writes a repr and loses the frames.
        structlog.processors.format_exc_info,
    ]

    if is_dev():
        renderer = console_renderer()
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

    if installed_handler is not None:
        root.removeHandler(installed_handler)

    root.addHandler(handler)
    root.setLevel(logging.INFO)

    installed_handler = handler
    IS_ROOT_CONFIGURED = True


original_excepthook = None
original_loop_exception_handler = None
installed_excepthook: bool = False
installed_asyncio_handler: bool = False


def install_excepthook() -> None:
    """Install a ``sys.excepthook`` that logs unhandled exceptions."""
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

        if original_loop_exception_handler:
            original_loop_exception_handler(loop, context)

    loop.set_exception_handler(handle_exception)
    installed_asyncio_handler = True


def get_logger(name: str, *, level: int | None = logging.INFO) -> logging.Logger:
    """The logger for *name*, configuring the root logger the first time.

    What comes back is a stdlib logger, rendered by structlog's pipeline on the
    root, so a field of its own travels in ``extra``.  *level* sets the
    logger's own level, and ``None`` leaves it to the root.
    """

    if not IS_ROOT_CONFIGURED:
        configure_root_logger()

    logger = logging.getLogger(name)

    if level is not None:
        logger.setLevel(level)
    else:
        logger.setLevel(logging.NOTSET)

    logger.propagate = True
    return logger

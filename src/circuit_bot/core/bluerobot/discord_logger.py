import logging

from ..logging import get_logger

__all__ = ("setup_discord_logger",)

logger = get_logger("discord")


def setup_discord_logger() -> None:
    """Configure discord.py loggers to propagate to the root logging system.

    ``configure_root_logger()`` must be called before this function.
    """

    discord_logger = logging.getLogger("discord")

    # Let records bubble up to the root logger so they pass through the
    # ContextFilter and use the same formatted output as everything else.
    discord_logger.propagate = True

    # Keep INFO so we get gateway connect/disconnect, shard events, etc.
    discord_logger.setLevel(logging.INFO)

    logger.info("Discord logging attached to root logger")

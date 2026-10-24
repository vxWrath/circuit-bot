import io
import traceback
from typing import TYPE_CHECKING

import asyncpg
import discord
from discord.app_commands.errors import CommandInvokeError

from ..api import APIRateLimitError
from ..bloxlink import BloxlinkError
from ..cache import CacheError
from ..database import DatabaseNotConnected, RecordNotFoundError
from ..logging import get_logger
from ..roblox import RobloxError
from .color import Color
from .formatter import ErrorFormatter, format_fail, format_retry
from .interaction import Interaction

if TYPE_CHECKING:
    from .bot import CircuitBot

__all__ = ("ErrorFormatter", "ErrorHandler")

logger = get_logger("errors")

# API failures commands let propagate - each gets a reply naming the service.
API_ERRORS = (
    (BloxlinkError, "Bloxlink"),
    (RobloxError, "Roblox"),
)


class ApiErrorFormatter(ErrorFormatter):
    """The reply for a third-party API failure, naming the service.

    Lives here rather than beside either service: the table spans Bloxlink and
    Roblox, and the reply is Discord-facing, which the API packages never are.
    """

    def format(self, _: Interaction, error: Exception) -> str | None:
        for error_type, service in API_ERRORS:
            if not isinstance(error, error_type):
                continue

            if isinstance(error, APIRateLimitError):
                return format_fail(f"{service} is rate limited. Try again {format_retry(error.retry_after)}.")

            return format_fail(f"{service} is unreachable right now. Try again later.")

        return None


class InternalErrorFormatter(ErrorFormatter):
    """The reply for a failure in the bot's own storage - Redis or Postgres.

    These are never the command's fault, so the reply doesn't name a service
    or offer a retry time.  Only connection-level Postgres failures are owned
    - a query error is a bug and stays reported.
    """

    def format(self, _: Interaction, error: Exception) -> str | None:
        if isinstance(
            error,
            (CacheError, DatabaseNotConnected, asyncpg.PostgresConnectionError, asyncpg.InterfaceError),
        ):
            return format_fail("The bot's internal services are unavailable right now. Try again later.")

        return None


class RecordNotFoundFormatter(ErrorFormatter):
    """The reply for a write whose record disappeared between the read and the write.

    The exception knows which table, key, and value came up missing, so the
    reply names them.
    """

    def format(self, _: Interaction, error: Exception) -> str | None:
        if not isinstance(error, RecordNotFoundError):
            return None
        return format_fail(f"Couldn't find a `{error.table}` with `{error.key}` equal to `{error.value}`")


class ErrorHandler:
    """Routes every failure to a user-facing reply or to a webhook report.

    Each formatter owns one kind of failure; the first that returns a reply
    wins, and a formatter that sets ``report`` is also posted to the error
    webhook as a handled failure.  Anything none of them own is reported as
    an unhandled error.
    """

    def __init__(self, bot: "CircuitBot") -> None:
        self.bot = bot
        self._formatters: tuple[ErrorFormatter, ...] | None = None

    @property
    def formatters(self) -> tuple[ErrorFormatter, ...]:
        """One instance of every :class:`ErrorFormatter` subclass.

        Formatters register by existing - they are never imported here - so a
        formatter defined in any module the bot has loaded joins the chain.
        """
        if self._formatters is not None:
            return self._formatters

        self._formatters = tuple(cls() for cls in ErrorFormatter.__subclasses__())
        return self._formatters

    async def handle(self, interaction: Interaction | None, error: Exception, *, source: str | None = None) -> None:
        """Respond to *error* on *interaction*, or report it when no formatter owns it.

        An interaction-less error (an event listener's) skips the formatters -
        there is no reply to send - and goes straight to :meth:`report_error`.
        """

        if source is None:
            source = interaction.command.qualified_name if interaction and interaction.command else "interaction"

        if interaction is not None:
            error = error.original if isinstance(error, CommandInvokeError) else error

            for formatter in self.formatters:
                message = formatter.format(interaction, error)

                if message is not None:
                    await self.respond(interaction, message)

                    if formatter.report:
                        await self.report_notice(source, error)

                    return

        await self.report_error(source, error, interaction=interaction)

    async def respond(self, interaction: Interaction, message: str) -> None:
        """Respond to *interaction* with *message*, quietly when Discord refuses it."""

        try:
            await interaction.failure(message)
        except discord.HTTPException:
            pass

    async def report_notice(self, source: str, error: Exception) -> None:
        """Log *error* and post it to the error webhook without the unhandled framing.

        For a failure a formatter owned on purpose but the operator should
        still see - a race the database settled, say.  No traceback file: it
        isn't a bug.
        """

        logger.warning("Handled failure in %s: %s", source, error)

        embed = discord.Embed(
            description=f"# Handled Failure\n> **Source:** `{source}`\n```py\n{type(error).__name__}: {error}\n```",
            color=Color.yellow(),
            timestamp=discord.utils.utcnow(),
        )

        try:
            await self.bot.stored_webhooks["error"].send(embed=embed)
        except Exception as e:
            logger.error("Failed to send error webhook", exc_info=e)

    async def report_error(
        self,
        source: str,
        error: Exception,
        *,
        interaction: Interaction | None = None,
    ) -> None:
        """Log *error*, post it to the error webhook, and reply to *interaction* with the traceback.

        The reply goes out first, so a slow webhook never delays it.
        """

        logger.error("Unhandled error in %s", source, exc_info=error)

        trace = "".join(traceback.format_exception(error))

        if interaction is not None:
            file = discord.File(io.BytesIO(trace.encode()), filename="traceback.txt")

            try:
                await interaction.respond(file=file, ephemeral=True)
            except discord.HTTPException:
                pass

        embed = discord.Embed(
            description=f"# Unhandled Error\n> **Source:** `{source}`\n```py\n{type(error).__name__}: {error}\n```",
            color=Color.brand_red(),
            timestamp=discord.utils.utcnow(),
        )

        file = discord.File(io.BytesIO(trace.encode()), filename="traceback.py")

        try:
            await self.bot.stored_webhooks["error"].send(embed=embed)
            await self.bot.stored_webhooks["error"].send(file=file)
        except Exception as e:
            logger.error("Failed to send error webhook", exc_info=e)

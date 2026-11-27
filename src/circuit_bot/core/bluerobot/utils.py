import asyncio
from collections.abc import Awaitable
from types import TracebackType
from typing import TYPE_CHECKING, Literal
from unicodedata import normalize as unicode_normalize

import discord

from ..logging import get_logger
from .app_commands import CheckFailure, CheckType, check
from .interaction import Interaction

if TYPE_CHECKING:
    from .bot import CircuitBot

__all__ = (
    "MINIMUM_PERMISSIONS",
    "context",
    "approver_only",
    "create_logged_task",
    "developer_only",
    "ensure_approver",
    "guild_owner_only",
    "is_developer",
    "is_managed_role",
    "normalize",
    "verified_only",
)

logger = get_logger("utils")

MINIMUM_PERMISSIONS = {
    p: status
    for p, status in discord.Permissions(
        view_channel=True, send_messages=True, embed_links=True, read_message_history=True
    )
    if status
}


def is_developer(bot: "CircuitBot", user: discord.User | discord.Member) -> bool:
    """Whether *user* is one of the developer ids in ``config.toml``."""
    return user.id in bot.config.league.developer_ids


def is_managed_role(role: discord.Role) -> bool:
    """Whether Discord owns *role*, not the guild.

    Consumers unlink such a role instead of claiming it: the bot cannot
    edit or delete one Discord manages, and its identity (a bot's, an
    integration's, the boost role, a purchasable or linked role) belongs
    to someone else.
    """
    if role.managed:
        return True

    if role.is_default():
        return True

    if not role.tags:
        return False

    if role.tags.is_bot_managed():
        return True

    if role.tags.is_premium_subscriber():
        return True

    if role.tags.is_integration():
        return True

    if role.tags.is_available_for_purchase():
        return True

    if role.tags.is_guild_connection():
        return True

    return False


def normalize(text: str | None) -> str:
    """*text* folded to plain ASCII, or an empty string for ``None``.

    NFKD splits accents into the base letter plus a combining mark, and the
    ascii encode drops the mark, so comparisons see Café and Cafe as one
    spelling.
    """
    if text is None:
        return ""

    return unicode_normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")


def create_logged_task[T](
    coro: Awaitable[T], *, name: str | None = None, suppress_cancelled: bool = False
) -> asyncio.Task[T]:
    """Start *coro* as a task that logs its own failure.

    An unawaited task's exception is dropped after asyncio's one warning, so
    a fire-and-forget follow-up would fail silently.  The exception is
    re-raised too, so callers that do await see it as normal.  Deliberate
    cancels are the expected end of many jobs, so *suppress_cancelled* keeps
    them out of the logs.
    """
    name = name or getattr(coro, "__name__", repr(coro))

    async def wrapper() -> T:
        try:
            return await coro
        except asyncio.CancelledError:
            if not suppress_cancelled:
                logger.warning("Task '%s' was cancelled", name)
            raise
        except Exception as e:
            logger.error("Task '%s' raised an exception", name, exc_info=e)
            raise

    return asyncio.create_task(wrapper(), name=name)


class context:
    """Name the operation a block is running, so any exception it raises
    carries that context to whoever handles it.

    The note rides the exception to the task log, the error webhook, and
    the user-facing reply, so a call site never needs its own try/except
    just to say what it was doing.  *suppress* lists the exceptions that
    are expected here and get a warning instead of propagating.  *details*
    join the action as ``key=value`` pairs, for the ids and names a log
    or reply should carry.
    """

    def __init__(
        self, action: str, *, suppress: tuple[type[Exception], ...] = (), **details: object
    ) -> None:
        if details:
            action += " (" + ", ".join(f"{key}={value}" for key, value in details.items()) + ")"

        self.action = action
        self.suppress = suppress

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> bool:
        # Cancels are control flow, not failures, so they pass through un-noted.
        if exc is None or not isinstance(exc, Exception):
            return False

        exc.add_note(self.action)

        if isinstance(exc, self.suppress):
            logger.warning("%s failed: %s", self.action, exc)
            return True

        return False


def developer_only():
    async def pred(interaction: Interaction) -> Literal[True]:
        if is_developer(interaction.client, interaction.user):
            return True
        raise CheckFailure(CheckType.DEVELOPER)

    return check(pred)


def guild_owner_only():
    def pred(interaction: Interaction) -> Literal[True]:
        if not interaction.guild:
            raise CheckFailure(CheckType.GUILD_OWNER)

        if interaction.user.id != interaction.guild.owner_id:
            raise CheckFailure(CheckType.GUILD_OWNER)

        return True

    return check(pred)


def ensure_approver(interaction: Interaction) -> Literal[True]:
    """The approver predicate, shared by the commands and the decision buttons."""
    # Developers are individuals, not holders of the approver role, so the
    # role check would wrongly refuse them.
    if is_developer(interaction.client, interaction.user):
        return True

    if not isinstance(interaction.user, discord.Member):
        raise CheckFailure(CheckType.APPROVER)

    if interaction.user.get_role(interaction.client.config.league.approver_role_id) is None:
        raise CheckFailure(CheckType.APPROVER)

    return True


def approver_only():
    return check(ensure_approver)


def verified_only():
    def pred(interaction: Interaction) -> Literal[True]:
        if is_developer(interaction.client, interaction.user):
            return True

        if not isinstance(interaction.user, discord.Member):
            raise CheckFailure(CheckType.VERIFIED)

        if interaction.user.get_role(interaction.client.config.league.verified_role_id) is None:
            raise CheckFailure(CheckType.VERIFIED)

        return True

    return check(pred)

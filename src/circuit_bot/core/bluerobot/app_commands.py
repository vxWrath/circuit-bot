from collections.abc import Callable, Coroutine
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar, cast

from discord import app_commands

from ..exception import CircuitException
from ..model import Model
from .formatter import ErrorFormatter, format_fail

if TYPE_CHECKING:
    from .interaction import Interaction

__all__ = (
    "CachedAppCommand",
    "CheckFailure",
    "CheckType",
    "CircuitGroup",
    "CommandNotFound",
    "autocomplete",
    "check",
    "command",
)

CommandCallback = Callable[..., Coroutine[Any, Any, Any]]
CommandDecorator = Callable[[CommandCallback], app_commands.Command[Any, ..., Any]]

F = TypeVar("F", bound=CommandCallback)
T = TypeVar("T")

# discord.py requires the callback's interaction parameter to be exactly
# discord.Interaction; the tree hands every callback the subclass instead.
command = cast(Callable[..., CommandDecorator], app_commands.command)


def autocomplete(**parameters: Any) -> Callable[[F], F]:
    """``app_commands.autocomplete`` that accepts the bot's interaction subclass."""
    return cast(Callable[[F], F], app_commands.autocomplete(**parameters))


def check(predicate: Callable[..., bool | Coroutine[Any, Any, bool]]) -> Callable[[T], T]:
    """``app_commands.check`` that accepts checks taking the bot's interaction subclass."""
    return cast(Callable[[T], T], app_commands.check(predicate))


class CheckType(Enum):
    DEVELOPER = "developer_only"
    GUILD_OWNER = "guild_owner_only"


class CheckFailure(app_commands.CheckFailure):
    def __init__(self, check_type: CheckType, **kwargs: Any):
        self.check_type = check_type
        self.kwargs = kwargs


# The reply for each check that can refuse a command before it runs.
CHECK_FAILURE_MESSAGES = {
    CheckType.DEVELOPER: "This command can only be used by developers.",
    CheckType.GUILD_OWNER: "This command can only be used by the guild owner.",
}


class CheckFailureFormatter(ErrorFormatter):
    """The reply for a command refused by one of the bot's own checks."""

    def format(self, _: "Interaction", error: Exception) -> str | None:
        if not isinstance(error, CheckFailure):
            return None

        message = CHECK_FAILURE_MESSAGES.get(error.check_type)
        return format_fail(message) if message is not None else None


class CircuitGroup(app_commands.Group):
    """An app command group whose subcommands accept the bot's interaction."""

    def command(  # type: ignore
        self, **kwargs: Any
    ) -> CommandDecorator:
        return cast(CommandDecorator, super().command(**kwargs))


class CommandNotFound(CircuitException):
    """Raised when a command is not found."""


class CachedAppCommand(Model):
    """An app command that has been cached from Discord."""

    id: int
    name: str

    @property
    def mention(self) -> str:
        return f"</{self.name}:{self.id}>"

    def __str__(self) -> str:
        return self.mention

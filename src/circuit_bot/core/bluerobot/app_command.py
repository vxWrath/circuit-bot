from collections.abc import Callable, Coroutine
from typing import Any, TypeVar, cast

from discord import app_commands

from ..exception import CircuitException
from ..model import Model

__all__ = ("CachedAppCommand", "CircuitGroup", "CommandNotFound", "autocomplete", "command")

CommandCallback = Callable[..., Coroutine[Any, Any, Any]]
CommandDecorator = Callable[[CommandCallback], app_commands.Command[Any, ..., Any]]

F = TypeVar("F", bound=CommandCallback)

# discord.py requires the callback's interaction parameter to be exactly
# discord.Interaction; the tree hands every callback the subclass instead.
command = cast(Callable[..., CommandDecorator], app_commands.command)


def autocomplete(**parameters: Any) -> Callable[[F], F]:
    """``app_commands.autocomplete`` that accepts the bot's interaction subclass."""
    return cast(Callable[[F], F], app_commands.autocomplete(**parameters))


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

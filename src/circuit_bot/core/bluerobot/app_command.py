from ..exception import CircuitException
from ..model import Model

__all__ = ("CachedAppCommand", "CommandNotFound")


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

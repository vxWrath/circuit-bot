from typing import TYPE_CHECKING

from .database import Repository, Row
from .exception import CircuitException

if TYPE_CHECKING:
    from .bluerobot import CircuitBot

__all__ = ("SERVICES", "Service", "register_service")


class ServiceNotFound(CircuitException):
    """Raised when a service is requested but not found."""


class Service:
    """Base for a module's service: the one thing that module's commands talk to.

    Holds the bot and nothing else, no method here takes a connection.
    Repositories come from :meth:`get_repository`, built on the pool.  A
    method spanning several statements opens its own transaction and builds on
    that instead, and a service never reaches into another module's tables or
    service.
    """

    def __init__(self, *, bot: "CircuitBot") -> None:
        self.bot = bot

    def get_repository[R: Row](self, shape: type[R]) -> Repository[R]:
        """The repository for *shape*, built on the pool."""

        return self.bot.database.get_repository(shape)


SERVICES: list[type[Service]] = []


def register_service(cls: type[Service]) -> None:
    """Add *cls* to the services the bot builds at startup.

    Called by ``modules/__init__``, so only services a module declares are
    built - a test double that subclasses one is never picked up, and an
    intermediate base class can't silently drop its children.
    """

    if cls not in SERVICES:
        SERVICES.append(cls)

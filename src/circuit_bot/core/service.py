from typing import TYPE_CHECKING

from .database import Repository, Row

if TYPE_CHECKING:
    from .bluerobot import CircuitBot

__all__ = ("Service",)


class Service:
    """Base for a module's service: the one thing that module's commands talk to.

    Holds the bot and nothing else; no method here takes a connection.
    Repositories come from :meth:`get_repository`, built on the pool; a method
    spanning several statements opens its own transaction and builds on that
    instead, and a service never reaches into another module's tables or
    service.
    """

    def __init__(self, *, bot: "CircuitBot") -> None:
        self.bot = bot

    def get_repository[R: Row](self, shape: type[R]) -> Repository[R]:
        """The repository for *shape*, built on the pool.

        Shorthand for :meth:`DatabasePool.get_repository`; a method that needs
        its statements on one connection builds them from its transaction
        instead.
        """

        return self.bot.database.get_repository(shape)

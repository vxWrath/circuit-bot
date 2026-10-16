from typing import TYPE_CHECKING

from .database import Repository, Row

if TYPE_CHECKING:
    from .bluerobot import CircuitBot

__all__ = ("Service",)


class Service:
    """Base for a module's service: the one thing that module's commands talk to.

    A service holds the bot and nothing else - no connection, no client of its
    own, no cache - so every question it answers is asked of the database and
    the API clients underneath, which are the only things that hold state.  It
    is built by its module and reached through the bot::

        class PlayerService(Service):
            def __init__(self, *, bot: "CircuitBot") -> None:
                super().__init__(bot=bot)
                self.players = self.repository(PlayerRow)

    Repositories come from :meth:`repository`, which builds them on the pool, so
    a method that has to span several statements opens its own transaction and
    builds its repositories on that instead - the shape
    :class:`~circuit_bot.core.database.Database` describes.  Either way no
    method here takes a connection, and no call it writes carries one.

    A service never reaches into another module's tables or another module's
    service: what belongs to a module goes through the service that owns it.
    """

    def __init__(self, *, bot: "CircuitBot") -> None:
        self.bot = bot

    def get_repository[R: Row](self, shape: type[R]) -> Repository[R]:
        """The repository for *shape*, built on the pool.

        Shorthand for :meth:`DatabasePool.get_repository`, and the one way a
        service reaches its own tables - a method that needs its statements on
        one connection builds them from the transaction it opened instead.
        """

        return self.bot.database.get_repository(shape)

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg

from .database import Database, IsolationLevel
from .repository import Repository
from .row import Row

__all__ = ("DatabaseConnection",)


class DatabaseConnection(Database):
    """One connection checked out of the pool, held for the length of a block.

    Yielded by :meth:`DatabasePool.acquire` and :meth:`DatabasePool.transaction`,
    and wearing the pool's surface, so a block holding one writes its statements
    the way the top level does::

        async with database.transaction() as conn:
            await conn.execute("UPDATE rosters SET points = points + $1 WHERE id = $2", won, roster_id)
            await conn.execute("INSERT INTO roster_history (roster_id, points) VALUES ($1, $2)", roster_id, won)

    Statements run where the connection stands rather than being checked out for
    the call, which is the whole difference between this and the pool: two
    statements here are two statements on one connection, and one transaction if
    one is open.  A connection goes back to the pool when its block exits,
    including when it exits with an exception, so this wrapper only lives inside
    the block that yielded it - a reference kept past that is a bug rather than
    a held connection.

    ``raw`` reaches the ``asyncpg`` connection underneath, for the rare
    statement this surface doesn't cover.
    """

    def __init__(self, connection: asyncpg.Connection) -> None:
        self._connection = connection

    @property
    def raw(self) -> asyncpg.Connection:
        """The ``asyncpg`` connection underneath."""
        return self._connection

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator["DatabaseConnection"]:
        """Hand back this connection, which is already checked out.

        The counterpart of :meth:`DatabasePool.acquire`.  A scope that already
        holds a connection has nothing left to check out, so a block written
        against either one gets the connection its statements belong on - which
        is what lets a repository run a statement without knowing which scope
        built it.

        There is no *timeout*, either: nothing left to wait for.
        """

        yield self

    @asynccontextmanager
    async def transaction(
        self,
        *,
        isolation: IsolationLevel = "read_committed",
        readonly: bool = False,
        deferrable: bool = False,
    ) -> AsyncGenerator["DatabaseConnection"]:
        """Hold a transaction on this connection, yielding the connection itself.

        The transaction is committed when the block exits normally and rolled
        back when it raises, so the exception still reaches the caller.  The
        options are the pool's - *isolation* sets the level, *readonly* lets
        PostgreSQL skip work a writing transaction would need, and *deferrable*
        is only meaningful for a ``serializable``, ``readonly`` transaction.

        Blocks nest: an inner transaction becomes a savepoint of the outer one
        rather than a second transaction, which is ``asyncpg`` tracking the
        transaction state on the connection underneath.
        """

        async with self._connection.transaction(isolation=isolation, readonly=readonly, deferrable=deferrable):
            yield self

    def get_repository[R: Row](self, row: type[R]) -> Repository[R]:
        """A repository whose statements run on this connection::

            async with database.transaction() as conn:
                await conn.get_repository(PlayerRow).update(player, name="kyle")

        Built on demand and not kept, unlike the pool's - a connection lives for
        one block, and building a repository only reads the shape: the ``Meta``
        the row's table base declared, and the fields the row declares.
        """

        return Repository(self, row_cls=row)

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg

from .database import Database, IsolationLevel
from .repository import Repository
from .row import Row

__all__ = ("DatabaseConnection",)


class DatabaseConnection(Database):
    """One connection checked out of the pool, held for the length of a block.

    Yielded by :meth:`DatabasePool.acquire` and :meth:`DatabasePool.transaction`.
    Statements run where the connection stands rather than being checked out for
    the call, so two statements here are two statements on one connection.  It
    goes back to the pool when the block exits - a reference kept past that is a
    bug, not a held connection.
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

        The counterpart of :meth:`DatabasePool.acquire`, so a block written
        against either scope gets the connection its statements belong on.  There
        is no *timeout*: nothing left to wait for.
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

        Committed when the block exits normally, rolled back when it raises - the
        exception still reaches the caller.  Blocks nest: an inner transaction is
        a savepoint of the outer one, not a second transaction.
        """

        async with self._connection.transaction(isolation=isolation, readonly=readonly, deferrable=deferrable):
            yield self

    def get_repository[R: Row](self, row: type[R]) -> Repository[R]:
        """A repository whose statements run on this connection.

        Built on demand and not kept, unlike the pool's - a connection lives for
        one block, and building a repository only reads the row's shape.
        """

        return Repository(self, row_cls=row)

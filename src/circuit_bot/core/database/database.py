from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, Literal

import asyncpg

if TYPE_CHECKING:
    from .connection import DatabaseConnection
    from .repository import Repository
    from .row import Row

__all__ = ("Database",)

IsolationLevel = Literal[
    "read_committed",
    "repeatable_read",
    "serializable",
    "read_uncommitted",
]


class Database(ABC):
    """A scope statements run in: the pool, or a connection checked out of it.

    Both answer :meth:`acquire` with the connection a piece of work belongs on -
    the pool by checking one out for the block, a connection by handing back
    itself, already checked out - and :meth:`transaction` with a transaction
    around it.  Which one a :class:`Repository` was built on is the only thing
    that decides where its statements land::

        players = database.get_repository(PlayerRow)    # a connection per statement
        await players.get_by_pk(snowflake)

        async with database.transaction() as conn:      # one connection, one transaction
            await conn.get_repository(PlayerRow).update(player, name="kyle")

    That is what lets a service method take one of these and be done - it builds
    its repositories on whatever it was handed, and none of its calls carry a
    connection.

    The scope members - :meth:`acquire`, :meth:`transaction` and
    :meth:`get_repository` - are abstract, so a class that inherits this and
    drops one fails at construction rather than silently answering ``None``.
    The statements below them are written once, against whatever :meth:`acquire`
    hands back, which is why neither scope spells any SQL out: a pool owns no
    SQL, it only hands out the connection that runs it.
    """

    @abstractmethod
    def acquire(self) -> AbstractAsyncContextManager["DatabaseConnection"]:
        """The connection this scope's statements run on, for the block."""
        ...

    @abstractmethod
    def transaction(
        self,
        *,
        isolation: IsolationLevel = "read_committed",
        readonly: bool = False,
        deferrable: bool = False,
    ) -> AbstractAsyncContextManager["DatabaseConnection"]:
        """A transaction on that connection, yielding it for the block."""
        ...

    @abstractmethod
    def get_repository[R: Row](self, row: type[R]) -> "Repository[R]":
        """The repository for *row*, whose statements run in this scope."""
        ...

    # -- statements --------------------------------------------------------

    async def fetch(self, query: str, *args: Any, timeout: float | None = None) -> list[asyncpg.Record]:
        """Run *query* and return every row it produced::

            rows = await database.fetch("SELECT * FROM rosters WHERE guild_id = $1", guild_id)

        Returns an empty list when the query matches nothing.
        """

        async with self.acquire() as conn:
            return await conn.raw.fetch(query, *args, timeout=timeout)

    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> asyncpg.Record | None:
        """Run *query* and return its first row, or ``None`` if it produced none::

            row = await database.fetchrow("SELECT * FROM rosters WHERE guild_id = $1", guild_id)

        Any further rows are discarded, so pair it with ``LIMIT 1`` - or order
        the query - when the result set can hold more than one.
        """

        async with self.acquire() as conn:
            return await conn.raw.fetchrow(query, *args, timeout=timeout)

    async def fetchval(self, query: str, *args: Any, column: int = 0, timeout: float | None = None) -> Any:
        """Run *query* and return the first column of its first row, or ``None``::

            count = await database.fetchval("SELECT count(*) FROM rosters")
            name = await database.fetchval("SELECT name FROM rosters WHERE id = $1", roster_id, column=0)

        *column* picks a column by position for queries selecting more than one.
        """

        async with self.acquire() as conn:
            return await conn.raw.fetchval(query, *args, column=column, timeout=timeout)

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        """Run *query* and return its status string, such as ``"UPDATE 3"``::

            status = await database.execute("DELETE FROM sessions WHERE expires_at < now()")

        *query* may hold several statements separated by semicolons; the result
        is then the status of the last one.  Use :meth:`executemany` to run one
        statement against many argument sets, or :meth:`copy_records_to_table`
        to load rows in bulk.
        """

        async with self.acquire() as conn:
            return await conn.raw.execute(query, *args, timeout=timeout)

    async def executemany(self, query: str, args: Sequence[Sequence[Any]], timeout: float | None = None) -> None:
        """Run *query* once for each argument set in *args*::

            await database.executemany(
                "INSERT INTO roster_history (roster_id, user_id) VALUES ($1, $2)",
                [(roster_id, user_id) for user_id in user_ids],
            )

        Every set runs in order, but not as a transaction - wrap the call in
        :meth:`transaction` when the batch has to land all at once.
        """

        async with self.acquire() as conn:
            await conn.raw.executemany(query, args, timeout=timeout)

    async def copy_records_to_table(
        self,
        table: str,
        records: Sequence[Sequence[Any]],
        columns: Sequence[str],
        *,
        schema_name: str | None = None,
        where: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """Load *records* into *table* with binary ``COPY``, returning its status string.

        *records* holds one sequence per row, with values positioned to match
        *columns*, and *schema_name* qualifies the table when it isn't on the
        search path::

            await database.copy_records_to_table(
                "player_stats",
                records=[(player_id, {"goals": 2}) for player_id in player_ids],
                columns=("player_id", "stats"),
            )

        This is the fastest way to load many rows, and it stays one statement:
        triggers and check constraints on the table still apply, but rules
        don't, ``ON CONFLICT`` isn't available, and a row the server rejects
        fails the whole load rather than just itself.  *where* is a server-side
        filter - only rows it accepts are inserted - and needs PostgreSQL 12+.
        """

        async with self.acquire() as conn:
            return await conn.raw.copy_records_to_table(
                table,
                records=records,
                columns=columns,
                schema_name=schema_name,
                where=where,
                timeout=timeout,
            )

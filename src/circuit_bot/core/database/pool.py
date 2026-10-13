from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Literal

import asyncpg
import orjson

from ..env import get_env
from ..logging import get_logger
from .exceptions import DatabaseNotConnected

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("DatabasePool",)

logger = get_logger("database")

IsolationLevel = Literal[
    "read_committed",
    "repeatable_read",
    "serializable",
    "read_uncommitted",
]

JSONB_VERSION = b"\x01"


def jsonb_encode(obj: Any) -> bytes:
    """Encode *obj* into the binary wire format PostgreSQL expects for ``jsonb``.

    The leading ``\\x01`` is the version marker the binary format requires in
    front of the JSON text.
    """

    return JSONB_VERSION + orjson.dumps(obj)


def jsonb_decode(data: bytes) -> Any:
    """Decode a binary ``jsonb`` payload into Python objects.

    A payload that is itself a JSON *string* - ``"{}"`` written into the column
    as text by an earlier writer - decodes to ``{}`` rather than the string, as
    those rows were meant to be read.  TODO: drop the special case once they
    have been rewritten.

    Raises ``ValueError`` if the payload doesn't start with the version marker,
    the way ``asyncpg``'s own codec does, rather than parsing the marker as JSON.
    """

    version, payload = data[:1], data[1:]

    if version != JSONB_VERSION:
        raise ValueError(f"unexpected jsonb format version: {version!r}")
    return orjson.loads(payload)


async def postgres_init(connection: asyncpg.Connection) -> None:
    """Register the ``orjson`` codec for ``jsonb`` on a new connection.

    Passed to ``asyncpg.create_pool(init=...)``, so it runs once per connection
    rather than once per checkout: ``set_type_codec`` is per-connection state,
    and registering it costs an introspection query.
    """

    await connection.set_type_codec(
        "jsonb",
        encoder=jsonb_encode,
        decoder=jsonb_decode,
        schema="pg_catalog",
        format="binary",
    )


class DatabasePool:
    """Async PostgreSQL pool, available on the bot as ``bot.database``.

    Wraps a single ``asyncpg`` pool, created by :meth:`connect` and torn down by
    :meth:`close`.  Statements that stand alone go through the convenience
    methods - one connection is checked out, the statement runs, and the
    connection goes back::

        await database.execute("DELETE FROM sessions WHERE expires_at < now()")
        rows = await database.fetch("SELECT * FROM rosters WHERE guild_id = $1", guild_id)

    Statements that must share a connection - because they're one transaction,
    or because they depend on connection state - hold one with :meth:`acquire`
    or :meth:`transaction`::

        async with database.transaction() as c:
            await c.execute("UPDATE rosters SET ...")
            await c.execute("INSERT INTO roster_history ...")

    ``jsonb`` values are encoded and decoded with ``orjson`` in PostgreSQL's
    binary format, via the codec :func:`postgres_init` registers on every new
    connection, so ``dict`` parameters can be passed straight to a ``jsonb``
    column and come back as ``dict``.

    Calling anything before :meth:`connect` raises :class:`DatabaseNotConnected`.
    """

    MIN_SIZE = 2  # connections kept open at all times
    MAX_SIZE = 10  # connections the pool will grow to under load
    COMMAND_TIMEOUT = 30.0  # seconds, the default timeout for statements on a connection
    MAX_INACTIVE_CONNECTION_LIFETIME = 300.0  # seconds before an idle connection is recycled

    def __init__(self, *, bot: "CircuitBot"):
        self.bot = bot

        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Create the pool, connecting ``MIN_SIZE`` connections to Postgres.

        Reads the DSN from the ``DATABASE_URL`` environment variable and is a
        no-op if the pool already exists, so it is safe to call more than once.
        Raises :class:`DatabaseNotConnected` if the database can't be reached,
        or if the DSN is missing or malformed.
        """

        if self._pool is not None:
            return

        try:
            self._pool = await asyncpg.create_pool(
                get_env("DATABASE_URL"),
                min_size=2,
                max_size=10,
                max_inactive_connection_lifetime=300.0,
                command_timeout=30.0,
                init=postgres_init,
            )
        except (OSError, asyncpg.PostgresError, ValueError) as e:
            raise DatabaseNotConnected(f"Failed to connect to PostgreSQL: {e}") from e

        logger.info(
            "PostgreSQL connected (pool allows %d connections, %d open)",
            self._pool.get_max_size(),
            self._pool.get_size(),
        )

    async def close(self) -> None:
        """Close the pool, waiting for checked-out connections to come back.

        No-op if the pool was never created or is already closed, and safe to
        call twice.  ``asyncpg`` waits for connections a caller is still holding
        before closing them, warning after 60 seconds, so a caller that never
        releases one stalls shutdown instead of losing it silently.
        """

        if self._pool is None:
            return

        await self._pool.close()
        self._pool = None

        logger.info("PostgreSQL pool closed")

    @property
    def pool(self) -> asyncpg.Pool:
        """The underlying ``asyncpg`` pool.  Raises :class:`DatabaseNotConnected` if not connected."""
        if self._pool is None:
            raise DatabaseNotConnected("DatabasePool has not been initialized.")
        return self._pool

    @asynccontextmanager
    async def acquire(self, *, timeout: float | None = None) -> AsyncGenerator[asyncpg.Connection]:
        """Check a connection out of the pool for the duration of the block.

        The connection goes back to the pool when the block exits - including
        when it exits with an exception.  *timeout* (in seconds) caps how long
        to wait for a connection to become available, and is remembered by
        ``asyncpg`` as the default timeout for operations on that connection.

        Reach for this over the convenience methods when statements have to land
        on the same connection::

            async with database.acquire() as c:
                await c.execute(...)
                await c.execute(...)
        """

        async with self.pool.acquire(timeout=timeout) as connection:
            # The pool hands out a dynamically-created Connection subclass that
            # pyright can't match against the annotation.
            yield connection  # type: ignore

    async def fetch(self, query: str, *args: Any, timeout: float | None = None) -> list[asyncpg.Record]:
        """Run *query* and return every row it produced::

            rows = await database.fetch("SELECT * FROM rosters WHERE guild_id = $1", guild_id)

        Returns an empty list when the query matches nothing.  Runs on a
        connection checked out for the call alone, so use :meth:`acquire` when
        several statements share one.
        """

        async with self.acquire() as c:
            return await c.fetch(query, *args, timeout=timeout)

    async def fetchrow(self, query: str, *args: Any, timeout: float | None = None) -> asyncpg.Record | None:
        """Run *query* and return its first row, or ``None`` if it produced none::

            row = await database.fetchrow("SELECT * FROM rosters WHERE guild_id = $1", guild_id)

        Any further rows are discarded, so pair it with ``LIMIT 1`` - or order
        the query - when the result set can hold more than one.
        """

        async with self.acquire() as c:
            return await c.fetchrow(query, *args, timeout=timeout)

    async def fetchval(self, query: str, *args: Any, column: int = 0, timeout: float | None = None) -> Any:
        """Run *query* and return the first column of its first row, or ``None``::

            count = await database.fetchval("SELECT count(*) FROM rosters")
            name = await database.fetchval("SELECT name FROM rosters WHERE id = $1", roster_id, column=0)

        *column* picks a column by position for queries selecting more than one.
        """

        async with self.acquire() as c:
            return await c.fetchval(query, *args, column=column, timeout=timeout)

    async def execute(self, query: str, *args: Any, timeout: float | None = None) -> str:
        """Run *query* and return its status string, such as ``"UPDATE 3"``::

            status = await database.execute("DELETE FROM sessions WHERE expires_at < now()")

        *query* may hold several statements separated by semicolons; the result
        is then the status of the last one.  Use :meth:`executemany` to run one
        statement against many argument sets, or :meth:`copy_records_to_table`
        to load rows in bulk.
        """

        async with self.acquire() as c:
            return await c.execute(query, *args, timeout=timeout)

    async def executemany(self, query: str, args: Sequence[Sequence[Any]], timeout: float | None = None) -> None:
        """Run *query* once for each argument set in *args*::

            await database.executemany(
                "INSERT INTO roster_history (roster_id, user_id) VALUES ($1, $2)",
                [(roster_id, user_id) for user_id in user_ids],
            )

        Every set is executed on one connection, in order, but not as a
        transaction - wrap the call in :meth:`transaction` when the batch has to
        land all at once.
        """

        async with self.acquire() as c:
            await c.executemany(query, args, timeout=timeout)

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

        async with self.acquire() as c:
            return await c.copy_records_to_table(
                table,
                records=records,
                columns=columns,
                schema_name=schema_name,
                where=where,
                timeout=timeout,
            )

    @asynccontextmanager
    async def transaction(
        self,
        *,
        isolation: IsolationLevel = "read_committed",
        readonly: bool = False,
        deferrable: bool = False,
    ) -> AsyncGenerator[asyncpg.Connection]:
        """Hold one connection for a transaction, yielding it for the block.

        The transaction is committed when the block exits normally and rolled
        back when it raises, so the exception still reaches the caller::

            async with database.transaction() as c:
                await c.execute("UPDATE rosters SET points = points + $1 WHERE id = $2", won, roster_id)
                await c.execute("INSERT INTO roster_history (roster_id, points) VALUES ($1, $2)", roster_id, won)

        *isolation* sets the level for the transaction, and *readonly* lets
        PostgreSQL skip work a writing transaction would need.  *deferrable* is
        only meaningful for a ``serializable``, ``readonly`` transaction, where
        it trades a longer wait for a lower chance of a serialization failure.

        Blocks nest: an inner :meth:`transaction` becomes a savepoint of the
        outer one rather than a second transaction.
        """

        async with self.acquire() as c:
            async with c.transaction(isolation=isolation, readonly=readonly, deferrable=deferrable):
                yield c

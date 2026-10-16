from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

import asyncpg
import orjson

from ...config import get_postgres_settings
from ..logging import get_logger
from .connection import DatabaseConnection
from .database import Database, IsolationLevel
from .exceptions import DatabaseNotConnected
from .repository import Repository
from .row import Row

if TYPE_CHECKING:
    from ..bluerobot import CircuitBot

__all__ = ("DatabasePool",)

logger = get_logger("database")

JSONB_VERSION = b"\x01"


def jsonb_encode(obj: Any) -> bytes:
    """Encode *obj* into the binary wire format PostgreSQL expects for ``jsonb``.

    The leading ``\\x01`` is the version marker the binary format requires in
    front of the JSON text.
    """

    return JSONB_VERSION + orjson.dumps(obj)


def jsonb_decode(data: bytes) -> Any:
    """Decode a binary ``jsonb`` payload into Python objects.

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


class DatabasePool(Database):
    """Async PostgreSQL pool, available on the bot as ``bot.database``.

    Wraps a single ``asyncpg`` pool, created by :meth:`connect` and torn down by
    :meth:`close`.  SQL that no row shape describes goes through the statement
    surface - one connection is checked out for the call, and goes back the
    moment it returns::

        rows = await database.fetch("SELECT * FROM rosters WHERE guild_id = $1", guild_id)
        await database.execute("DELETE FROM sessions WHERE expires_at < now()")

    Statements that have to share a connection - because they're one
    transaction, or because they depend on connection state - hold one with
    :meth:`acquire` or :meth:`transaction`::

        async with database.transaction() as conn:
            await conn.execute("UPDATE rosters SET ...")
            await conn.execute("INSERT INTO roster_history ...")

    A repository built here acquires a connection for every statement it runs,
    so one read costs one checkout too.  Build it on a connection instead
    (:meth:`DatabaseConnection.get_repository`) and everything it runs lands
    there, inside whatever transaction that connection has open::

        async with database.transaction() as conn:
            await conn.get_repository(PlayerRow).update(player, name="kyle")

    ``jsonb`` values are encoded and decoded with ``orjson`` in PostgreSQL's
    binary format, via the codec :func:`postgres_init` registers on every new
    connection, so ``dict`` parameters can be passed straight to a ``jsonb``
    column and come back as ``dict``.

    Calling anything before :meth:`connect` raises :class:`DatabaseNotConnected`.
    """

    def __init__(self, *, bot: "CircuitBot"):
        self.bot = bot

        self._pool: asyncpg.Pool | None = None
        self._repositories: dict[type[Row], Repository[Any]] = {}

    async def connect(self) -> None:
        """Create the pool, opening its minimum connections to Postgres.

        Reads the connection settings from the ``POSTGRES_*`` environment
        variables and is a no-op if the pool already exists, so it is safe to call
        more than once.  Raises :class:`DatabaseNotConnected` if the database can't
        be reached, or if a setting is missing or malformed.
        """

        if self._pool is not None:
            return

        try:
            # Inside the block deliberately: get_env raises OSError for a missing
            # variable, and that is one of the failures this translates.
            settings = get_postgres_settings()

            self._pool = await asyncpg.create_pool(
                host=settings.host,
                port=settings.port,
                user=settings.user,
                password=settings.password,
                database=settings.database,
                min_size=2,  # connections kept open at all times
                max_size=10,  # connections the pool grows to under load
                max_inactive_connection_lifetime=300.0,  # seconds before an idle connection is recycled
                command_timeout=30.0,  # seconds, the default timeout for statements on a connection
                timeout=10.0,  # seconds to wait for each connection to open
                init=postgres_init,
            )
        except (OSError, asyncpg.PostgresError, ValueError) as e:
            raise DatabaseNotConnected(f"Failed to connect to PostgreSQL: {e}") from e

        logger.info(
            "PostgreSQL connected to %s:%d (pool allows %d connections, %d open)",
            settings.host,
            settings.port,
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
    async def acquire(self, *, timeout: float | None = None) -> AsyncGenerator[DatabaseConnection]:
        """Check a connection out of the pool for the duration of the block.

        The connection goes back to the pool when the block exits - including
        when it exits with an exception.  *timeout* (in seconds) caps how long
        to wait for a connection to become available, and is remembered by
        ``asyncpg`` as the default timeout for operations on that connection.

        Reach for this when statements have to land on the same connection - or
        when you want the SQL itself rather than a repository::

            async with database.acquire() as conn:
                await conn.execute(...)
                await conn.execute(...)

        One connection is not one *view*.  With no transaction open, each
        statement still takes its own snapshot, so a query here can see a change
        another transaction committed in between.  When several reads have to
        agree with each other, that's :meth:`transaction` with
        ``isolation="repeatable_read"`` - which pins one snapshot for the block
        - not this.
        """

        async with self.pool.acquire(timeout=timeout) as connection:
            # asyncpg hands out a PoolConnectionProxy, which forwards every
            # attribute to the connection behind it but isn't annotated as one.
            yield DatabaseConnection(connection)  # type: ignore

    @asynccontextmanager
    async def transaction(
        self,
        *,
        isolation: IsolationLevel = "read_committed",
        readonly: bool = False,
        deferrable: bool = False,
    ) -> AsyncGenerator[DatabaseConnection]:
        """Hold one connection for a transaction, yielding it for the block.

        The transaction is committed when the block exits normally and rolled
        back when it raises, so the exception still reaches the caller::

            async with database.transaction() as conn:
                await conn.execute("UPDATE rosters SET points = points + $1 WHERE id = $2", won, roster_id)
                await conn.execute("INSERT INTO roster_history (roster_id, points) VALUES ($1, $2)", roster_id, won)

        *isolation* sets the level for the transaction, and *readonly* lets
        PostgreSQL skip work a writing transaction would need.  *deferrable* is
        only meaningful for a ``serializable``, ``readonly`` transaction, where
        it trades a longer wait for a lower chance of a serialization failure.

        The transaction itself is :meth:`DatabaseConnection.transaction` on the
        connection that was just checked out - which is where a nested block
        becomes a savepoint of the outer one rather than a second transaction.
        """

        async with self.acquire() as conn:
            async with conn.transaction(isolation=isolation, readonly=readonly, deferrable=deferrable):
                yield conn

    def get_repository[R: Row](self, row: type[R]) -> Repository[R]:
        """Return the repository for *row*, building it the first time it's asked for.

        Built on demand rather than up front, so it doesn't matter whether the
        module a row lives in was imported before or after the pool was created.
        Keyed by shape: two shapes over one table get a repository each, so a
        read and a write each keep the shape they were asked for.

        Everything it runs lands on a connection checked out for the statement
        alone - a connection's own repository
        (:meth:`DatabaseConnection.get_repository`) is the one that stays put.
        """

        repository = self._repositories.get(row)

        if repository is None:
            repository = Repository(self, row_cls=row)
            self._repositories[row] = repository

        return cast(Repository[R], repository)

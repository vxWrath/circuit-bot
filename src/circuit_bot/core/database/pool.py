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
    """Encode *obj* into the binary ``jsonb`` wire format, version marker included."""

    return JSONB_VERSION + orjson.dumps(obj)


def jsonb_decode(data: bytes) -> Any:
    """Decode a binary ``jsonb`` payload into Python objects.

    Raises ``ValueError`` if the payload doesn't start with the version marker.
    """

    version, payload = data[:1], data[1:]

    if version != JSONB_VERSION:
        raise ValueError(f"unexpected jsonb format version: {version!r}")
    return orjson.loads(payload)


async def postgres_init(connection: asyncpg.Connection) -> None:
    """Register the ``orjson`` codec for ``jsonb`` on a new connection.

    The pool's ``init`` callback, so it runs once per connection.
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

    Statements check a connection out of the pool for the call; :meth:`acquire`
    and :meth:`transaction` pin one for a block.  Anything called before
    :meth:`connect` raises :class:`DatabaseNotConnected`.
    """

    def __init__(self, *, bot: "CircuitBot"):
        self.bot = bot

        self._pool: asyncpg.Pool | None = None
        self._repositories: dict[type[Row], Repository[Any]] = {}

    async def connect(self) -> None:
        """Create the pool, opening its minimum connections to Postgres.

        Safe to call more than once.  Raises :class:`DatabaseNotConnected` if the
        database can't be reached or a setting is missing or malformed.
        """

        if self._pool is not None:
            return

        try:
            # Inside the try: a missing variable raises OSError, which this translates.
            settings = get_postgres_settings()

            self._pool = await asyncpg.create_pool(
                host=settings.host,
                port=settings.port,
                user=settings.user,
                password=settings.password,
                database=settings.database,
                min_size=2,
                max_size=10,
                max_inactive_connection_lifetime=300.0,
                command_timeout=30.0,
                timeout=10.0,
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

        Safe to call twice.  A caller that never releases its connection stalls
        shutdown here.
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

        It goes back when the block exits, including on an exception.  With no
        transaction open each statement still takes its own snapshot; call
        :meth:`transaction` with ``isolation="repeatable_read"`` when several reads
        have to agree.
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

        Committed when the block exits normally, rolled back when it raises - the
        exception still reaches the caller.  *deferrable* does nothing unless the
        transaction is ``serializable`` and ``readonly``.  A nested block is a
        savepoint of the outer one, not a second transaction.
        """

        async with self.acquire() as conn:
            async with conn.transaction(isolation=isolation, readonly=readonly, deferrable=deferrable):
                yield conn

    def get_repository[R: Row](self, row: type[R]) -> Repository[R]:
        """Return the repository for *row*, built on first ask and kept per shape.

        Two shapes over one table get a repository each.
        """

        repository = self._repositories.get(row)

        if repository is None:
            repository = Repository(self, row_cls=row)
            self._repositories[row] = repository

        return cast(Repository[R], repository)

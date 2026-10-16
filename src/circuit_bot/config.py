"""Where the bot's connection settings come from.

The parts are the configuration.  A ``DATABASE_URL`` sitting in the environment
beside the ``POSTGRES_*`` variables it was built from is a second copy of the same
credentials, and the two drift - so nothing is read as a URL, and the drivers are
handed the pieces instead.

Both services are described as discrete parameters rather than a DSN because that
is what the drivers accept, and because a generated password containing ``@``,
``:`` or ``/`` needs no escaping when no string is ever joined together.  The same
settings serve a bot on the host and a bot in a container: only the host changes,
and compose is what changes it.
"""

from typing import NamedTuple

from .core.env import get_env

__all__ = (
    "PostgresSettings",
    "RedisSettings",
    "get_postgres_settings",
    "get_redis_settings",
)


class PostgresSettings(NamedTuple):
    """What :meth:`~circuit_bot.core.database.DatabasePool.connect` hands to
    ``asyncpg.create_pool``."""

    host: str
    port: int
    user: str
    password: str
    database: str


class RedisSettings(NamedTuple):
    """What :meth:`~circuit_bot.core.cache.CacheClient.connect` hands to
    ``redis.asyncio.ConnectionPool``."""

    host: str
    port: int
    password: str
    db: int


def get_postgres_settings() -> PostgresSettings:
    """Read the ``POSTGRES_*`` environment variables.

    Host and port fall back to where a local server listens; the credentials and
    the database name do not, so a missing password stops the bot at startup
    rather than connecting as whatever the driver would have picked.

    A missing variable raises ``OSError`` and a port that isn't a number raises
    ``ValueError`` - both surfacing as the ``DatabaseNotConnected``
    :meth:`~circuit_bot.core.database.DatabasePool.connect` raises, since it reads
    these from inside the block that translates them.
    """

    return PostgresSettings(
        host=get_env("POSTGRES_HOST", "localhost"),
        port=int(get_env("POSTGRES_PORT", "5432")),
        user=get_env("POSTGRES_USER"),
        password=get_env("POSTGRES_PASSWORD"),
        database=get_env("POSTGRES_DB"),
    )


def get_redis_settings() -> RedisSettings:
    """Read the ``REDIS_*`` environment variables.

    As above: host, port and database index have defaults, the password does not.
    """

    return RedisSettings(
        host=get_env("REDIS_HOST", "localhost"),
        port=int(get_env("REDIS_PORT", "6379")),
        password=get_env("REDIS_PASSWORD"),
        db=int(get_env("REDIS_DB", "0")),
    )

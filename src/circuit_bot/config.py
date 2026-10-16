"""Where the bot's connection settings come from, one field per part."""

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

    Host and port default to a local server; the credentials and database name do
    not.  A missing variable raises ``OSError``, a non-numeric port ``ValueError``.
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

    Host, port and database index default; the password does not.
    """

    return RedisSettings(
        host=get_env("REDIS_HOST", "localhost"),
        port=int(get_env("REDIS_PORT", "6379")),
        password=get_env("REDIS_PASSWORD"),
        db=int(get_env("REDIS_DB", "0")),
    )

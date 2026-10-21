"""Where the bot's settings come from, one field per part."""

import tempfile
from pathlib import Path
from typing import NamedTuple

from .core.env import get_env

PACKAGE_PATH = Path(__file__).resolve().parent
DEFAULT_SYNC_SENTINEL = Path(tempfile.gettempdir()) / "circuit-bot.sync"

__all__ = (
    "BotConfig",
    "PostgresSettings",
    "RedisSettings",
    "get_bot_config",
    "get_postgres_settings",
    "get_redis_settings",
)


class BotConfig(NamedTuple):
    """What :class:`~circuit_bot.core.bluerobot.CircuitBot` reads to log in, find
    its guild, announce itself, load extensions and call the APIs it depends on."""

    token: str
    guild_id: int
    connection_webhook_url: str
    roblox_api_key: str
    bloxlink_api_key: str
    sync_sentinel: Path
    command_path: Path = PACKAGE_PATH / "commands"
    event_path: Path = PACKAGE_PATH / "events"

    verified_role_id: int = 1553923281239941261
    roblox_link_log_channel_id: int = 1561244309788037170


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


def get_bot_config() -> BotConfig:
    """Read the Discord credentials and the ``ROBLOX_API_KEY`` and ``BLOXLINK_API_KEY`` keys.

    ``SYNC_SENTINEL`` overrides where the one-time command-sync marker is kept;
    it defaults to a file in the system temp directory.
    """

    return BotConfig(
        token=get_env("DISCORD_TOKEN"),
        guild_id=int(get_env("GUILD_ID")),
        connection_webhook_url=get_env("CONNECTION_WEBHOOK_URL"),
        roblox_api_key=get_env("ROBLOX_API_KEY"),
        bloxlink_api_key=get_env("BLOXLINK_API_KEY"),
        sync_sentinel=Path(get_env("SYNC_SENTINEL", str(DEFAULT_SYNC_SENTINEL))),
    )


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

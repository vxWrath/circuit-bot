"""Where the bot's settings come from, one field per part."""

import tempfile
import tomllib
from pathlib import Path
from typing import Any, NamedTuple

from .core.env import get_env

PACKAGE_PATH = Path(__file__).resolve().parent

__all__ = (
    "BotConfig",
    "LeagueSettings",
    "PostgresSettings",
    "RedisSettings",
    "get_bot_config",
    "get_league_settings",
    "get_postgres_settings",
    "get_redis_settings",
)


class LeagueSettings(NamedTuple):
    """The league's own numbers, read from ``config.toml``.

    Everything here is league-domain and public by nature - role and channel
    ids, never credentials.  Secrets stay in the environment (see
    :class:`BotConfig`).
    """

    verified_role_id: int
    roblox_link_log_channel_id: int
    approver_role_id: int
    pending_teams_channel_id: int
    new_teams_channel_id: int


def get_league_settings() -> LeagueSettings:
    """Read the league's settings from ``CONFIG_PATH`` (default ``config.toml``).

    The file is required - the bot cannot honestly run a guild it doesn't know
    how to govern.  A missing file, section, or key raises ``OSError`` naming
    what's missing.
    """

    path = Path(get_env("CONFIG_PATH", "config.toml"))

    if not path.is_file():
        raise OSError(f"Config file '{path}' not found. Create it with the league's role and channel ids.")

    with path.open("rb") as file:
        data = tomllib.load(file)

    def section(name: str) -> dict[str, Any]:
        raw = data.get(name)

        if not isinstance(raw, dict):
            raise OSError(f"Config file '{path}' is missing the [{name}] section.")

        sections[name] = raw
        return raw

    sections: dict[str, dict[str, Any]] = {
        "guild": section("guild"),
        "roles": section("roles"),
        "teams": section("teams"),
    }

    def id_of(section: str, key: str) -> int:
        value = sections[section].get(key)

        if not isinstance(value, int):
            raise OSError(f"Config file '{path}' is missing an integer '{key}' in [{section}].")

        return value

    return LeagueSettings(
        verified_role_id=id_of("guild", "verified_role"),
        roblox_link_log_channel_id=id_of("guild", "roblox_link_log_channel"),
        approver_role_id=id_of("roles", "approver"),
        pending_teams_channel_id=id_of("teams", "pending_teams_channel"),
        new_teams_channel_id=id_of("teams", "new_teams_channel"),
    )


class BotConfig(NamedTuple):
    """What :class:`~circuit_bot.core.bluerobot.CircuitBot` reads to log in, find
    its guild, announce itself, load extensions and call the APIs it depends on.

    The credentials here come from the environment; the league's settings come
    from ``config.toml`` through :attr:`league`."""

    token: str
    guild_id: int

    connection_webhook_url: str
    error_webhook_url: str
    roblox_api_key: str
    bloxlink_api_key: str

    league: LeagueSettings

    sync_sentinel: Path = Path(tempfile.gettempdir()) / "circuit-bot.sync"
    command_path: Path = PACKAGE_PATH / "commands"
    event_path: Path = PACKAGE_PATH / "events"


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
    """Read the Discord credentials, the webhook URLs and the API keys.

    ``SYNC_SENTINEL`` overrides where the one-time command-sync marker is kept;
    it defaults to a file in the system temp directory.
    """

    return BotConfig(
        token=get_env("DISCORD_TOKEN"),
        guild_id=int(get_env("GUILD_ID")),
        connection_webhook_url=get_env("CONNECTION_WEBHOOK_URL"),
        error_webhook_url=get_env("ERROR_WEBHOOK_URL"),
        roblox_api_key=get_env("ROBLOX_API_KEY"),
        bloxlink_api_key=get_env("BLOXLINK_API_KEY"),
        league=get_league_settings(),
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

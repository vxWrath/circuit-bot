import datetime

from ...core.database import Row

__all__ = ("CreatePlayer", "PlayerRow", "PlayerTable")


class PlayerTable(Row):
    """The ``players`` table: one row per player, keyed by their Discord snowflake.

    Nothing names a player here - Roblox names resolve through the API and Discord
    names through the member cache, both from the snowflake.
    """

    class Meta:
        abstract = True
        table = "players"
        primary_key = "snowflake"
        generated = ("created_at", "updated_at")


class PlayerRow(PlayerTable):
    """A player, whole.  What reads return."""

    snowflake: int
    roblox_id: int | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CreatePlayer(PlayerTable, kw_only=True):
    """A player, as an insert sends one.

    Built with keywords, the inherited fields included.
    """

    snowflake: int
    roblox_id: int | None = None

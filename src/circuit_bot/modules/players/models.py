from ...core.database import Row

__all__ = ("NewPlayer", "PlayerRef", "PlayerRow", "PlayerTable")


class PlayerTable(Row):
    """The ``players`` table: one row per player, keyed by their Discord snowflake.

    Columns are provisional; reconcile them with the real schema before
    anything writes.
    """

    class Meta:
        abstract = True
        table = "players"
        primary_key = "snowflake"


class PlayerRow(PlayerTable):
    """A player, whole.  What reads return."""

    snowflake: int
    roblox_id: int | None
    name: str


class NewPlayer(PlayerTable, kw_only=True):
    """A player, as an insert sends one.

    Built with keywords, the inherited fields included.
    """

    snowflake: int
    name: str
    roblox_id: int | None = None


class PlayerRef(PlayerTable):
    """A player, just enough to name them."""

    snowflake: int
    name: str

from typing import TYPE_CHECKING

from ...core.roblox import RobloxUser
from ...core.service import Service
from .models import CreatePlayer, PlayerRow
from .profile import PlayerProfile

if TYPE_CHECKING:
    from ...core.bluerobot import CircuitBot

__all__ = ("PlayerService",)


class PlayerService(Service):
    """Player reads and writes over the database and the cached clients.

    The one place that composes a player out of the row in Postgres and the
    Roblox account, so commands have a single surface to ask.  A method spanning
    several statements opens the
    transaction itself and builds its repositories on it, so a caller can't
    half-commit it.
    """

    def __init__(self, *, bot: "CircuitBot") -> None:
        super().__init__(bot=bot)

        self.player = self.get_repository(PlayerRow)
        self.create_player = self.get_repository(CreatePlayer)

    def profile(self, snowflake: int) -> PlayerProfile:
        """Open a player's profile.  Touches nothing until a part is asked for."""
        return PlayerProfile(self, snowflake)

    async def find_player(self, *, snowflake: int) -> PlayerRow | None:
        """The player with *snowflake*, or ``None`` if there's none."""
        players = await self.player.find_by(snowflake=snowflake)
        return players[0] if players else None

    async def profile_from_roblox_id(self, roblox_id: int) -> PlayerProfile | None:
        """Open the profile of the player linked to *roblox_id*, or ``None`` when there's no such player.

        Unlike :meth:`profile`, this one has to read the row to learn the
        snowflake the profile is keyed by.  The row it read is seeded into the
        profile, so asking for it again costs nothing.
        """
        players = await self.player.find_by(roblox_id=roblox_id)

        if not players:
            return None

        row = players[0]

        profile = self.profile(row.snowflake)
        profile.set_part(PlayerRow, row)

        return profile

    async def create(self, *, snowflake: int, roblox_id: int | None = None) -> CreatePlayer:
        """Add a player, returning the row the database read back."""
        return await self.create_player.insert(CreatePlayer(snowflake=snowflake, roblox_id=roblox_id))

    async def link_roblox(self, player: PlayerRow, roblox_id: int) -> PlayerRow | None:
        """Attach a Roblox account to *player*, or ``None`` if the row is gone.

        *player* is the row the write is keyed by, so a caller that already read
        the player passes that read.
        """

        return await self.player.update(player, roblox_id=roblox_id)

    async def unlink_roblox(self, player: PlayerRow) -> PlayerRow | None:
        """Drop the Roblox account on *player*, or ``None`` if the row is gone.

        Mirrors :meth:`link_roblox` - *player* is the row the write is keyed by.
        """

        return await self.player.update(player, roblox_id=None)

    async def roblox_user(self, *, roblox_id: int) -> RobloxUser | None:
        """The Roblox user info of *roblox_id*, or ``None`` when roblox id doesn't exist."""
        return await self.bot.roblox.get_user(roblox_id)

    async def headshot_url(self, *, roblox_id: int) -> str | None:
        """The headshot of *roblox_id*, or ``None`` when roblox id doesn't exist."""
        return await self.bot.roblox.get_headshot(roblox_id)

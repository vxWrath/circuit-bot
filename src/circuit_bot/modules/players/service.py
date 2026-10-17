from typing import TYPE_CHECKING

from ...core.database import Row
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

    async def find[R: Row](self, shape: type[R], *, snowflake: int) -> R | None:
        """The player with *snowflake*, as *shape*, or ``None`` if there's none.

        *shape* has to declare ``snowflake`` - it's the column being looked up.
        """

        players = await self.get_repository(shape).find_by(snowflake=snowflake)
        return players[0] if players else None

    async def create(self, *, snowflake: int, roblox_id: int | None = None) -> CreatePlayer:
        """Add a player, returning the row the database read back."""
        return await self.create_player.insert(CreatePlayer(snowflake=snowflake, roblox_id=roblox_id))

    async def link_roblox(self, player: PlayerRow, roblox_id: int) -> PlayerRow | None:
        """Attach a Roblox account to *player*, or ``None`` if the row is gone.

        *player* is the row the write is keyed by, so a caller that already read
        the player passes that read.
        """

        return await self.player.update(player, roblox_id=roblox_id)

    async def roblox_user(self, *, roblox_id: int) -> RobloxUser | None:
        """The Roblox user info of *roblox_id*, or ``None`` when roblox id doesn't exist."""
        return await self.bot.roblox.get_user(roblox_id)

    async def headshot_url(self, *, roblox_id: int) -> str | None:
        """The headshot of *roblox_id*, or ``None`` when roblox id doesn't exist."""
        return await self.bot.roblox.get_headshot(roblox_id)

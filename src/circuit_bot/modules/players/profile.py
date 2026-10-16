from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, cast

from ...core.database import RecordNotFoundError, Row
from ...core.profile import Profile
from ...core.roblox import RobloxUser
from .models import PlayerRow

if TYPE_CHECKING:
    from .service import PlayerService

__all__ = ("PlayerProfile",)


class PlayerProfile(Profile["PlayerService"]):
    """One player, filled in on demand.

    Open it through :meth:`PlayerService.profile` and ask for the parts you
    render - each is fetched once and kept.  Methods that take a snowflake on
    the service have a counterpart here with it filled in, and a profile never
    reaches for a client itself.
    """

    def __init__(self, service: "PlayerService", snowflake: int) -> None:
        super().__init__(service=service)

        self.snowflake = snowflake

    async def row[R: Row](self, shape: type[R]) -> R | None:
        """The player's row as *shape*, or ``None`` if there's no such player.

        The shape is the cache key, so asking twice for the same one fetches
        once, and a narrower shape costs a narrower read.
        """

        # pyright can't carry `find`'s own type parameter through a callable
        # passed by value, so it's spelled out here.
        find = cast("Callable[..., Coroutine[Any, Any, R | None]]", self.service.find)
        return await self.ensure_part(shape, find, shape, snowflake=self.snowflake)

    async def player(self) -> PlayerRow | None:
        """The player's full row, or ``None`` if there's no such player."""
        return await self.row(PlayerRow)

    async def roblox_id(self) -> int | None:
        """The Roblox account linked to this player, or ``None`` when there's none."""
        return await self.ensure_part("roblox_id", self.service.roblox_id, snowflake=self.snowflake)

    async def roblox_user(self) -> RobloxUser | None:
        """The player's Roblox account, or ``None`` when none is linked."""
        return await self.ensure_part(RobloxUser, self.service.roblox_user, snowflake=self.snowflake)

    async def headshot_url(self) -> str | None:
        """The player's Roblox headshot, or ``None`` when none is available."""
        return await self.ensure_part("headshot_url", self.service.headshot_url, snowflake=self.snowflake)

    async def link_roblox(self, roblox_id: int) -> PlayerRow:
        """Link a Roblox account to this player, or raise ``RecordNotFoundError``.

        The raise covers both no such player and the row going between the read
        and the write.  The write is keyed by the row :meth:`player` kept, or
        fetched here if this is the first part anyone wanted, and the row it
        returns is kept.  Every other part is dropped.
        """

        player = await self.player()

        if player is None:
            raise RecordNotFoundError(f"players.snowflake={self.snowflake!r} not found")

        # The row went between the read and the write, so the parts read for it
        # are stale.
        linked = await self.service.link_roblox(player, roblox_id=roblox_id)

        if linked is None:
            self.invalidate_parts()
            raise RecordNotFoundError(f"players.snowflake={self.snowflake!r} not found")

        self.invalidate_parts()
        self.set_part(PlayerRow, linked)

        return linked

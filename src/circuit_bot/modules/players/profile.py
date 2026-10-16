from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, cast

from ...core.database import Row
from ...core.profile import Profile
from ...core.roblox import RobloxUser
from .models import PlayerRow

if TYPE_CHECKING:
    from .service import PlayerService

__all__ = ("PlayerProfile",)


class PlayerProfile(Profile["PlayerService"]):
    """One player, filled in on demand.

    Open it through :meth:`PlayerService.profile` and ask for the parts you
    render - each is fetched once and kept, so a command that only shows a name
    never touches the Roblox API or the stats sheet::

        profile = players.profile(member.id)

        player = await profile.player()
        headshot = await profile.headshot_url()

    Whatever the service does to a specific player has a counterpart here with
    the snowflake already filled in - :meth:`link_roblox`, mirroring
    :meth:`PlayerService.link_roblox`.  A part is whatever the service says it
    is, so a profile never reaches for a client itself: the composition stays
    where it was written.
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

    async def link_roblox(self, roblox_id: int) -> PlayerRow | None:
        """Link a Roblox account to this player, or ``None`` if there's no such player.

        The write is keyed by the row already asked for - kept by
        :meth:`player`, or fetched here if this is the first part anyone wanted
        - so linking costs one statement of its own.  Everything else the
        profile held is dropped, because a new account changes the Roblox parts
        too - then the row the write returned is kept as-is, so the next read
        doesn't have to fetch it back.
        """

        player = await self.player()

        if player is None:
            return None

        # None here is the row having gone between the read and the write.
        linked = await self.service.link_roblox(player, roblox_id=roblox_id)

        if linked is None:
            return None

        self.invalidate_parts()
        self.set_part(PlayerRow, linked)

        return linked

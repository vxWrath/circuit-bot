from typing import TYPE_CHECKING

import discord

from ...core.database import RecordNotFoundError
from ...core.logging import get_logger
from ...core.profile import Profile
from ...core.roblox import RobloxError, RobloxUser
from .models import PlayerRow

if TYPE_CHECKING:
    from .service import PlayerService

__all__ = (
    "PlayerProfile",
    "RobloxProfile",
)

logger = get_logger("player_profile")


class PlayerProfile(Profile["PlayerService"]):
    """One player, filled in on demand.

    Open it through :meth:`PlayerService.profile` or
    :meth:`PlayerService.profile_from_roblox_id`, which reads the row once up
    front.  Each part is fetched once and kept.  The Roblox parts live on
    :attr:`roblox` and read the row first, raising ``RecordNotFoundError``
    when there is no such player, and a profile never reaches for a client
    itself.
    """

    def __init__(self, service: "PlayerService", snowflake: int) -> None:
        super().__init__(service=service)

        self.snowflake = snowflake
        self._roblox: RobloxProfile | None = None

    @property
    def roblox(self) -> RobloxProfile:
        """The player's Roblox view: the linked account, its headshot, and the helpers around them."""
        if self._roblox is None:
            self._roblox = RobloxProfile(self)

        return self._roblox

    async def player(self) -> PlayerRow | None:
        """The player's full row, or ``None`` if there's no such player."""
        return await self.ensure_part(PlayerRow, self.service.find_player, snowflake=self.snowflake)

    async def require_player(self) -> PlayerRow:
        """The player's full row, or raise ``RecordNotFoundError``."""
        player = await self.player()

        if player is None:
            raise RecordNotFoundError("players", "snowflake", self.snowflake)

        return player

    async def ensure_player(self) -> PlayerRow:
        """The player's full row, or create it if there's no such player."""
        player = await self.player()

        if player is None:
            await self.service.create(snowflake=self.snowflake)
            # player() cached the miss as None, so drop it before re-reading
            self.invalidate_parts(PlayerRow)
            player = await self.require_player()

        return player

    async def member(self) -> discord.Member | None:
        """The player's guild member, or ``None`` when they aren't in the guild."""
        return await self.ensure_part(discord.Member, self.service.member, snowflake=self.snowflake)

    async def set_nickname_from_roblox(self) -> None:
        """Set the player's nickname to Bloxlink's format from the linked Roblox account.

        Does nothing when no account is linked, and skips with a warning when
        the player is no longer in the guild.  Raises ``RecordNotFoundError``
        when there is no such player.
        """
        rblx = await self.roblox.user()

        if rblx is None:
            return

        member = await self.member()

        if member is None:
            logger.warning("Could not set nickname for player %s: not in the guild", self.snowflake)
            return

        if rblx.display_name == rblx.username:
            nickname = f"{rblx.username} ✓"
        else:
            # Discord caps nicknames at 32 characters, so trim the display name
            # to keep the username and check mark intact.
            suffix = f" (@{rblx.username}) ✓"
            limit = 32 - len(suffix)
            nickname = f"{rblx.display_name[:limit]}{suffix}"

        await self.service.set_nickname(member, nickname)

    async def reset_nickname(self) -> None:
        """Reset the player's nickname, undoing :meth:`set_nickname_from_roblox`.

        Skips with a warning when the player is no longer in the guild.
        """
        member = await self.member()

        if member is None:
            logger.warning("Could not reset nickname for player %s: not in the guild", self.snowflake)
            return

        await self.service.set_nickname(member, None)

    async def link_roblox(self, roblox_id: int) -> PlayerRow:
        """Link a Roblox account to this player, or raise ``RecordNotFoundError``.

        The raise covers both no such player and the row going between the read
        and the write.  The write is keyed by the row :meth:`player` kept, or
        fetched here if this is the first part anyone wanted, and the row it
        returns is kept.  The Roblox parts are dropped; the member survives,
        since the write can't have changed it.
        """

        player = await self.player()

        if player is None:
            raise RecordNotFoundError("players", "snowflake", self.snowflake)

        # The row went between the read and the write, so the parts read for it
        # are stale.
        linked = await self.service.link_roblox(player, roblox_id=roblox_id)

        if linked is None:
            self.invalidate_parts()
            raise RecordNotFoundError("players", "snowflake", self.snowflake)

        self.invalidate_parts(RobloxUser, "headshot_url")
        self.set_part(PlayerRow, linked)

        return linked

    async def unlink_roblox(self) -> PlayerRow:
        """Drop the player's linked Roblox account, or raise ``RecordNotFoundError``.

        Mirrors :meth:`link_roblox` - the raise covers both no such player and
        the row going between the read and the write, and the Roblox parts are
        dropped.
        """

        player = await self.player()

        if player is None:
            raise RecordNotFoundError("players", "snowflake", self.snowflake)

        # The row went between the read and the write, so the parts read for it
        # are stale.
        unlinked = await self.service.unlink_roblox(player)

        if unlinked is None:
            self.invalidate_parts()
            raise RecordNotFoundError("players", "snowflake", self.snowflake)

        self.invalidate_parts(RobloxUser, "headshot_url")
        self.set_part(PlayerRow, unlinked)

        return unlinked


class RobloxProfile:
    """The Roblox view of a player.

    Open it through :attr:`PlayerProfile.roblox`.  Each part is fetched once
    and kept with the rest of the profile's parts, and the view never reaches
    for a client itself.
    """

    def __init__(self, player: PlayerProfile) -> None:
        self.player = player

    async def roblox_id(self) -> int | None:
        """The linked account's id, or raise ``RecordNotFoundError`` when there is no such player."""
        player = await self.player.player()

        if player is None:
            raise RecordNotFoundError("players", "snowflake", self.player.snowflake)

        return player.roblox_id

    async def user(self) -> RobloxUser | None:
        """The player's Roblox account, or ``None`` when none is linked.

        Raises ``RecordNotFoundError`` when there is no such player.
        """
        roblox_id = await self.roblox_id()

        if roblox_id is None:
            return None

        return await self.player.ensure_part(RobloxUser, self.player.service.roblox_user, roblox_id=roblox_id)

    async def headshot(self) -> str | None:
        """The player's Roblox headshot, or ``None`` when none is available.

        Raises ``RecordNotFoundError`` when there is no such player.
        """
        roblox_id = await self.roblox_id()

        if roblox_id is None:
            return None

        return await self.player.ensure_part("headshot_url", self.player.service.headshot_url, roblox_id=roblox_id)

    async def headshot_or_none(self) -> str | None:
        """The player's headshot, or ``None`` when it can't be fetched."""
        try:
            return await self.headshot()
        except RobloxError as e:
            logger.warning("Could not fetch Roblox headshot for player %s: %s", self.player.snowflake, e)
            return None
